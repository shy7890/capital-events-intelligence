"""M1 数据层流水线编排：回填 / 每日增量 / 主备切换 / scan_run 全量记账（PRD §4.2、§7）。

所有源函数一律可注入（真实接线在 scripts 与 batch）；阶段函数不向上抛异常，
失败写入 scan_run(red) 并返回 {"ok": False, "error": ...}，由调用方统计红单。
"""

from __future__ import annotations

import sqlite3
import time
import uuid
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from . import ingest, notify


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _run_stage(conn: sqlite3.Connection, run_date: str, stage: str, work: Callable[[], dict]) -> dict:
    started = _now_iso()
    try:
        stats = work()
    except Exception as exc:  # noqa: BLE001 —— 阶段失败必须落 scan_run 而非冒泡
        ingest.record_scan_run(conn, run_date, stage, "red",
                               stats={"started_at": started}, errors=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "stage": stage}
    ingest.record_scan_run(conn, run_date, stage, "green",
                           stats={"started_at": started, **stats})
    return {"ok": True, "stage": stage, **stats}


def backfill_kline(
    conn: sqlite3.Connection,
    code: str,
    start: str,
    end: str,
    *,
    kline_fn: Callable[[str, str, str], list[dict]],
    sina_fn: Callable[[str, str, str], list[dict]] | None = None,
    tencent_fn: Callable[[str, str, str], list[dict]] | None = None,
    run_date: str | None = None,
) -> dict:
    """日K回填：主源→新浪→腾讯三链降级；三路皆败记红单（2026-09-03+ 第三兜底）。"""

    def work() -> dict:
        try:
            rows, used = _fetch_kline_once(
                code, start, end, kline_fn=kline_fn, sina_fn=sina_fn, tencent_fn=tencent_fn
            )
        except Exception:
            raise
        inserted = ingest.upsert_kline(conn, rows)
        stats = {"code": code, "start": start, "end": end, "rows": len(rows), "inserted": inserted}
        if used:
            stats["fallback"] = "sina|tencent"
        return stats

    return _run_stage(conn, run_date or _today(), "kline", work)


def _short_source_error(exc: Exception) -> str:
    """错误只保留类别和短消息，避免把完整请求细节带进状态接口。"""
    message = " ".join(str(exc).split())[:160]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _refresh_lease_until(minutes: int = 15) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


# 单票失败重试（2026-09-03 用户要求 B 方案）：一轮=主源→兜底皆失败后，等 RETRY_DELAY 秒重试整轮。
# 单只耗时上限约 retry_delay*RETRY_TIMES，失败批次大的最坏情形约多花
# (5600 只 × 4 分钟) 可接受；每只失败都会先试完才继续下一只。
RETRY_TIMES = 1
RETRY_DELAY_SECONDS = 300  # 5 分钟


def _fetch_kline_once(
    code: str,
    start: str,
    run_date: str,
    *,
    kline_fn: Callable[[str, str, str], list[dict]],
    sina_fn: Callable[[str, str, str], list[dict]] | None,
    tencent_fn: Callable[[str, str, str], list[dict]] | None = None,
) -> tuple[list[dict], bool]:
    """主源→新浪兜底→腾讯第三兜底；返回（rows, used_fallback）。

    语义保持原状：某路返回非空即成功并计入 used_fallback；一路抛错继续降级；
    全部降级仍抛错则抛最后一个错误；某路明确返回空（非抛错）继续下一路，
    全部为空返回空（由调用方记为 skipped）。
    """
    source_chain: list[Callable[[str, str, str], list[dict]]] = [kline_fn]
    if sina_fn is not None:
        source_chain.append(sina_fn)
    if tencent_fn is not None:
        source_chain.append(tencent_fn)
    errors: list[Exception] = []
    for i, fetch in enumerate(source_chain):
        try:
            rows = list(fetch(code, start, run_date) or [])
        except Exception as exc:
            errors.append(exc)
            continue
        if rows:
            return rows, i > 0  # 任何非首链命中都算兜底
        # 明确空响应：继续下一路；全部为空最终返回空（skipped）
    if errors:
        raise errors[-1]
    return [], False


def _fetch_kline_with_retry(
    code: str,
    start: str,
    run_date: str,
    *,
    kline_fn: Callable[[str, str, str], list[dict]],
    sina_fn: Callable[[str, str, str], list[dict]] | None,
    tencent_fn: Callable[[str, str, str], list[dict]] | None = None,
    retry_times: int,
    retry_delay: float,
    idle: Callable[[], None] | None = None,
) -> tuple[list[dict], bool]:
    """抓取并执行失败重试；重试耗尽后抛出**最后一次**异常。

    单票保险：三链这一轮皆败时，等待 retry_delay 后整轮重试；
    retry_times 次仍失败才交给调用方记 failed（次日 16:00 定时还会续跑）。
    idle 回调在每次冷却前调用（用于续租约）。
    """
    try:
        return _fetch_kline_once(
            code, start, run_date, kline_fn=kline_fn, sina_fn=sina_fn, tencent_fn=tencent_fn
        )
    except Exception as first_exc:
        last_exc = first_exc
        for _ in range(retry_times):
            if retry_delay > 0:
                if idle is not None:
                    idle()
                time.sleep(retry_delay)
            try:
                rows, used_fallback = _fetch_kline_once(
                    code, start, run_date, kline_fn=kline_fn, sina_fn=sina_fn,
                    tencent_fn=tencent_fn,
                )
                return rows, used_fallback
            except Exception as exc:
                last_exc = exc
        raise last_exc


def refresh_market_kline(
    conn: sqlite3.Connection,
    run_date: str,
    codes: list[str],
    *,
    kline_fn: Callable[[str, str, str], list[dict]],
    sina_fn: Callable[[str, str, str], list[dict]] | None = None,
    tencent_fn: Callable[[str, str, str], list[dict]] | None = None,
    retry_times: int = RETRY_TIMES,
    retry_delay: float = RETRY_DELAY_SECONDS,
) -> dict:
    """按目标日逐代码增量刷新全市场 K 线，支持中断续跑和主键幂等。

    这里的“成功”表示该代码本次已经确认写入或已有数据无需新增；“跳过”表示全部
    来源均明确返回空结果；来源异常则先重试（retry_delay 冷却后整轮重试 retry_times 次），
    仍失败留在 failed，供下次触发点续跑。
    """
    unique_codes = list(dict.fromkeys(str(code) for code in codes))
    owner = uuid.uuid4().hex
    if not ingest.claim_market_refresh(
        conn, run_date, unique_codes, owner, _refresh_lease_until()
    ):
        summary = ingest.market_refresh_counts(conn, run_date)
        summary.update({"ok": True, "stage": "market_kline", "busy": True})
        ingest.record_scan_run(conn, run_date, "market_kline", "pending", stats=summary)
        return summary

    try:
        items = conn.execute(
            "SELECT code FROM market_refresh_item "
            "WHERE run_date=? AND kind='kline' AND status IN ('pending','failed','running') "
            "ORDER BY code",
            (run_date,),
        ).fetchall()
        inserted_total = 0
        fallback_total = 0
        for item in items:
            code = item["code"]
            lease = _refresh_lease_until()
            ingest.touch_market_refresh(conn, run_date, owner, lease)
            ingest.mark_market_refresh_item(
                conn, run_date, code, "running", increment_attempt=True
            )
            start = incremental_kline_start(
                conn, code, default_start="2023-01-01", today=date.fromisoformat(run_date)
            )
            rows: list[dict] = []
            used_fallback = False
            try:
                rows, used_fallback = _fetch_kline_with_retry(
                    code, start, run_date,
                    kline_fn=kline_fn, sina_fn=sina_fn, tencent_fn=tencent_fn,
                    retry_times=retry_times, retry_delay=retry_delay,
                    idle=lambda: ingest.touch_market_refresh(
                        conn, run_date, owner, _refresh_lease_until()
                    ),
                )
            except Exception as source_exc:  # noqa: BLE001 —— 单代码失败留在明细，不中断全市场
                ingest.mark_market_refresh_item(
                    conn, run_date, code, "failed", error=_short_source_error(source_exc)
                )
                continue
            try:
                if rows:
                    inserted_total += ingest.upsert_kline(conn, rows)
                    if used_fallback:
                        fallback_total += 1
                    ingest.mark_market_refresh_item(
                        conn, run_date, code, "success", rows_count=len(rows)
                    )
                else:
                    ingest.mark_market_refresh_item(
                        conn, run_date, code, "skipped", error="两路来源均无数据"
                    )
            except Exception as exc:  # noqa: BLE001 —— 单代码写入异常也要可续跑
                ingest.mark_market_refresh_item(
                    conn, run_date, code, "failed", error=_short_source_error(exc)
                )

        summary = ingest.update_market_refresh_summary(conn, run_date, release=True)
        summary.update({
            "ok": summary["kline_failed"] == 0,
            "stage": "market_kline",
            "busy": False,
            "success": summary["kline_success"],
            "skipped": summary["kline_skipped"],
            "failed": summary["kline_failed"],
            "pending": summary["kline_pending"],
            "inserted": inserted_total,
            "fallback": fallback_total,
        })
        ingest.record_scan_run(
            conn, run_date, "market_kline", "red" if summary["kline_failed"] else "green",
            stats=summary,
            errors=summary["blocking_reason"] if summary["kline_failed"] else None,
        )
        return summary
    except Exception as exc:  # noqa: BLE001 —— 状态表异常仍需释放租约并记红
        try:
            ingest.update_market_refresh_summary(conn, run_date, release=True)
        except Exception:
            conn.rollback()
        error = _short_source_error(exc)
        ingest.record_scan_run(conn, run_date, "market_kline", "red", errors=error)
        return {"ok": False, "stage": "market_kline", "error": error, "busy": False}


def market_refresh_ready(conn: sqlite3.Connection, run_date: str) -> bool:
    """只有 K 线明细全为成功/跳过且龙虎榜已确认，目标日才可发布新扫描。"""
    row = conn.execute(
        "SELECT 1 FROM market_refresh WHERE run_date=?", (run_date,)
    ).fetchone()
    if row is None:
        return False
    summary = ingest.market_refresh_counts(conn, run_date)
    return not summary["blocking"]


def backfill_holder(
    conn: sqlite3.Connection,
    code: str,
    *,
    holder_fn: Callable[[str], list[dict]],
    run_date: str | None = None,
) -> dict:
    """股东户数全历史回填（东财 dc 通道经 pace 闸门）。"""

    def work() -> dict:
        rows = holder_fn(code)
        inserted = ingest.upsert_holder_num(conn, rows)
        return {"code": code, "rows": len(rows), "inserted": inserted}

    return _run_stage(conn, run_date or _today(), "holder", work)


def backfill_longhu(
    conn: sqlite3.Connection,
    dates: list[str],
    *,
    longhu_fn: Callable[[str], list[dict]],
    run_date: str | None = None,
) -> dict:
    """龙虎榜逐日拉取（全市场行入库，供流水线 B 判"起涨后是否上榜"）。"""

    def work() -> dict:
        total = 0
        errors: list[str] = []
        for day in dates:
            try:
                total += ingest.upsert_longhu(conn, longhu_fn(day))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{day}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return {"days": len(dates), "inserted": total}

    return _run_stage(conn, run_date or _today(), "longhu", work)


def backfill_announcements(
    conn: sqlite3.Connection,
    code: str,
    start: str,
    end: str,
    *,
    fetch_fn: Callable[[str, str, str], list[dict]],
    run_date: str | None = None,
) -> dict:
    """单票区间公告回填（巨潮；重复执行按自然键去重）。"""

    def work() -> dict:
        rows = fetch_fn(code, start, end)
        inserted = ingest.upsert_announcement(conn, rows)
        return {"code": code, "start": start, "end": end, "rows": len(rows), "inserted": inserted}

    return _run_stage(conn, run_date or _today(), "announce", work)


def refresh_stock(
    conn: sqlite3.Connection,
    code: str,
    *,
    basic_fn: Callable[[str], dict],
    snapshot_fn: Callable[[list[str]], dict[str, dict]] | None = None,
    run_date: str | None = None,
) -> dict:
    """股票档案：baostock 基本资料 + 腾讯市值快照合并 upsert（市值列可缺，COALESCE 保旧值）。"""

    def work() -> dict:
        row: dict[str, Any] = dict(basic_fn(code))
        if snapshot_fn is not None:
            try:
                snap = snapshot_fn([code]).get(code)
            except Exception:  # noqa: BLE001 —— 行情失败不阻塞档案写入
                snap = None
            if snap:
                row["circ_mv"] = snap.get("circ_mv")
                row["total_mv"] = snap.get("total_mv")
        inserted = ingest.upsert_stock(conn, [row])
        return {"code": code, "new": inserted, "has_mv": row.get("circ_mv") is not None}

    return _run_stage(conn, run_date or _today(), "stock_meta", work)


def backfill_market_announcements(
    conn: sqlite3.Connection,
    dates: list[str],
    *,
    fetch_fn: Callable[[str], list[dict]],
    run_date: str | None = None,
) -> dict:
    """全市场当日公告（东财 dc 通道）逐日回填（每日批任务：补昨天终稿 + 拉今天初稿）。"""

    def work() -> dict:
        rows_total = 0
        inserted_total = 0
        errors: list[str] = []
        for day in dates:
            try:
                rows = fetch_fn(day)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{day}: {type(exc).__name__}: {exc}")
                continue
            rows_total += len(rows)
            inserted_total += ingest.upsert_announcement(conn, rows)
        if errors:
            raise RuntimeError("; ".join(errors))
        return {"days": len(dates), "rows": rows_total, "inserted": inserted_total}

    return _run_stage(conn, run_date or _today(), "announce_market", work)


def backfill_events(
    conn: sqlite3.Connection,
    *,
    codes: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    run_date: str | None = None,
) -> dict:
    """M2 事件流水线阶段：公告→事件/节点/入池/提醒（水位线增量）。"""
    from .events import classifier

    def work() -> dict:
        s = classifier.classify_events(conn, codes=codes, start=start, end=end)
        return {"processed": s["processed"], "new_events": s["new_events"],
                "new_nodes": s["new_nodes"], "notices": s["notices"], "exits": s["exits"]}

    return _run_stage(conn, run_date or _today(), "events", work)


def archive_pool_now(conn: sqlite3.Connection, *, today: str,
                     params: dict | None = None) -> int:
    """v1.1 候选池自动归档清扫（纯库操作，PRD F2）：只降权不删除，返回本次归档行数。

    活跃度以最后一个流程节点日为准（updated_at 会被历史重建污染，不作依据）：
    ① 事件终止 → 即归档；② 已完成/已发行且 30 天内无新节点 → 归档（刚了结保留看解禁）；
    ③ 进行中但 90 天无节点=僵尸 → 归档；④ 事件起始超 365 天 → 归档（时效优先）；
    ⑤ 离场/退出信号静置满 30 天 → 归档；⑥ 流通市值超 pool_mv_max（含入池后涨超）→ 归档，
    市值未知不误伤。
    """
    from . import scanner

    p = scanner.resolve_params(conn, params)
    t = date.fromisoformat(today)
    day = lambda n: (t - timedelta(days=int(p[n]))).isoformat()  # noqa: E731
    stale_cut, zombie_cut, age_cut = day("archive_after_days"), \
        day("archive_zombie_days"), day("archive_event_max_age_days")
    last_act = ("COALESCE((SELECT MAX(fn.node_date) FROM flow_node fn"
                "  WHERE fn.event_id = e.id), e.start_date)")
    cur = conn.execute(
        "UPDATE pool_entry SET status='归档', updated_at=? WHERE status <> '归档' AND ("
        " EXISTS (SELECT 1 FROM event e WHERE e.id=pool_entry.event_id AND e.status='终止')"
        " OR (event_id IS NOT NULL AND EXISTS (SELECT 1 FROM event e"
        "   WHERE e.id=pool_entry.event_id AND e.status IN ('已完成','已发行')"
        f"   AND {last_act} < ?))"
        " OR (event_id IS NOT NULL AND EXISTS (SELECT 1 FROM event e"
        "   WHERE e.id=pool_entry.event_id AND e.status='进行中'"
        f"   AND ({last_act} < ? OR e.start_date < ?)))"
        " OR (status='离场' AND updated_at < ?)"
        " OR (exit_signals IS NOT NULL AND exit_signals NOT IN ('', '[]')"
        "   AND updated_at < ?)"
        " OR (COALESCE((SELECT s.circ_mv FROM stock s WHERE s.code=pool_entry.code), -1) > ?)"
        ")",
        (f"{today}T00:00:00", stale_cut, zombie_cut, age_cut,
         stale_cut, stale_cut, float(p["pool_mv_max"])))
    conn.commit()
    return int(cur.rowcount)


def backfill_scan(
    conn: sqlite3.Connection,
    *,
    today: str | None = None,
    params: dict | None = None,
    codes: list[str] | None = None,
    run_date: str | None = None,
) -> dict:
    """M4 因子扫描阶段（流水线 B）：全量因子计算→scan_result 落榜→池共振回写→池归档清扫。

    失败不冒泡：写 scan_run(stage="scan", red)；统计（universe_size/hits/tier_counts…）
    原样并入 scan_run 的 stats_json。
    """
    from . import scanner

    t = today or _today()
    # 旧的单票/手工扫描没有 market_refresh 记录时保持兼容；全市场批任务一旦建立
    # 目标日刷新记录，就必须等到所有明细完成后再替换该日榜单。
    refresh_exists = conn.execute(
        "SELECT 1 FROM market_refresh WHERE run_date=?", (t,)
    ).fetchone()
    if refresh_exists and not market_refresh_ready(conn, t):
        summary = ingest.market_refresh_counts(conn, t)
        ingest.record_scan_run(
            conn, run_date or t, "scan", "pending",
            stats={"market_refresh": summary},
        )
        return {
            "ok": True,
            "stage": "scan",
            "status": "pending",
            "market_refresh": summary,
        }

    def work() -> dict:
        stats = scanner.scan_factors(conn, today=t, params=params, codes=codes)
        stats["archived"] = archive_pool_now(conn, today=t, params=params)
        return stats

    return _run_stage(conn, run_date or today or _today(), "scan", work)


def last_kline_date(conn: sqlite3.Connection, code: str) -> str | None:
    row = conn.execute("SELECT MAX(trade_date) AS d FROM kline_daily WHERE code=?", (code,)).fetchone()
    return row["d"]


def incremental_kline_start(conn: sqlite3.Connection, code: str, *, default_start: str, today: date) -> str:
    """增量水位：库内最后 K 线日的次日；无数据用 default_start；不早于 today 夹逼。"""
    last = last_kline_date(conn, code)
    if last is None:
        return default_start
    start = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
    return min(start, today.isoformat())


def _json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _watchlist_prices(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    rows = [dict(r) for r in conn.execute(
        "SELECT trade_date, close, adj_factor FROM kline_daily "
        "WHERE code=? AND close IS NOT NULL ORDER BY trade_date", (code,)
    ).fetchall()]
    factors = [r["adj_factor"] for r in rows if r["adj_factor"] is not None]
    base = float(factors[-1]) if factors and float(factors[-1]) != 0 else None
    out: list[dict[str, Any]] = []
    for row in rows:
        factor = row["adj_factor"]
        close = float(row["close"])
        qfq = close
        if base is not None:
            qfq = close * (float(factor) if factor is not None else base) / base
        out.append({"date": row["trade_date"], "close": close, "close_qfq": round(qfq, 4)})
    return out


def _watchlist_changes(
    conn: sqlite3.Connection,
    code: str,
    scan: sqlite3.Row | None,
    prices: list[dict[str, Any]],
    *,
    as_of: str,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], list[str]]:
    """汇总单票关键变化；普通价格波动单独返回，不进入提醒变化清单。"""
    changes: list[dict[str, Any]] = []
    missing: list[str] = []

    if scan is None:
        missing.append("scan_result")
    else:
        missing.extend(str(v) for v in _json_list(scan["missing_fields_json"]) if v)
    if not prices:
        missing.append("kline_daily")
    if conn.execute("SELECT 1 FROM holder_num WHERE code=? LIMIT 1", (code,)).fetchone() is None:
        missing.append("holder_num")
    missing = list(dict.fromkeys(missing))

    events = conn.execute(
        "SELECT id, event_type, grade, start_date, status FROM event "
        "WHERE code=? ORDER BY start_date DESC, id DESC LIMIT 10", (code,)
    ).fetchall()
    for event in events:
        changes.append({
            "type": "event", "date": event["start_date"], "actionable": True,
            "title": "新资本运作事件",
            "detail": f"{event['event_type']}（{event['grade']}级，{event['status']}）",
        })
        nodes = conn.execute(
            "SELECT f.node_type, f.node_date, a.title FROM flow_node f "
            "LEFT JOIN announcement a ON a.id=f.announcement_id "
            "WHERE f.event_id=? ORDER BY f.node_date DESC, f.id DESC LIMIT 10",
            (event["id"],),
        ).fetchall()
        for node in nodes:
            changes.append({
                "type": "node", "date": node["node_date"], "actionable": True,
                "title": "流程节点推进",
                "detail": f"{event['event_type']} → {node['node_type']}",
                "announcement_title": node["title"],
            })

    rebound = conn.execute(
        "SELECT disclose_date, pct_change FROM holder_num "
        "WHERE code=? AND pct_change >= 15 ORDER BY disclose_date DESC LIMIT 5", (code,)
    ).fetchall()
    for row in rebound:
        changes.append({
            "type": "holder_rebound", "date": row["disclose_date"], "actionable": True,
            "title": "股东户数回升",
            "detail": f"户数环比 {float(row['pct_change']):.2f}%",
        })

    if len(prices) >= 2:
        peak = max(prices, key=lambda row: row["close_qfq"])
        last = prices[-1]
        drawdown = (last["close_qfq"] / peak["close_qfq"] - 1) * 100 if peak["close_qfq"] else 0
        if drawdown <= -30:
            changes.append({
                "type": "drawdown", "date": last["date"], "actionable": True,
                "title": "从阶段高点回撤超过30%",
                "detail": f"当前回撤 {drawdown:.2f}%（高点 {peak['date']}）",
            })

    start_date = None
    if scan is not None:
        start_date = scan["confirmed_start_date"] or scan["mechanical_start_date"] or scan["start_date"]
    if start_date:
        lhb = conn.execute(
            "SELECT MIN(trade_date) AS trade_date FROM longhu "
            "WHERE code=? AND trade_date>?", (code, start_date)
        ).fetchone()
        if lhb and lhb["trade_date"]:
            changes.append({
                "type": "lhb_after_start", "date": lhb["trade_date"], "actionable": True,
                "title": "起涨后首次上龙虎榜",
                "detail": f"起涨点 {start_date} 之后首次上榜",
            })

    reductions = conn.execute(
        "SELECT pub_date, title FROM announcement WHERE code=? AND title LIKE '%减持%' "
        "ORDER BY pub_date DESC, id DESC LIMIT 5", (code,)
    ).fetchall()
    for row in reductions:
        changes.append({
            "type": "reduction", "date": row["pub_date"], "actionable": True,
            "title": "重要减持公告",
            "detail": row["title"][:100],
        })

    for field in missing:
        changes.append({
            "type": "missing_data", "date": as_of, "actionable": True,
            "title": "跟踪数据待补",
            "detail": f"缺少字段：{field}",
        })

    order = {"event": 0, "node": 1, "holder_rebound": 2, "drawdown": 3,
             "lhb_after_start": 4, "reduction": 5, "missing_data": 6}
    changes.sort(key=lambda row: (row["date"] or "", -order.get(row["type"], 99)), reverse=True)
    return changes[:max(1, int(limit))], missing


def watchlist_snapshot(
    conn: sqlite3.Connection,
    code: str,
    *,
    entry: sqlite3.Row | None = None,
    today: str | None = None,
    changes_limit: int = 20,
) -> dict[str, Any]:
    """返回跟踪名单单票视图，供 API、批任务和素材导出共用。"""
    entry = entry or conn.execute("SELECT * FROM watchlist_entry WHERE code=?", (code,)).fetchone()
    stock = conn.execute(
        "SELECT code, name, board, list_date, is_st, circ_mv, updated_at FROM stock WHERE code=?",
        (code,),
    ).fetchone()
    scan = conn.execute(
        "SELECT scan_date, lane, tier, start_date, start_age_days, mechanical_start_date, "
        "confirmed_start_date, missing_fields_json FROM scan_result WHERE code=? "
        "ORDER BY scan_date DESC LIMIT 1", (code,)
    ).fetchone()
    as_of = today or _today()
    prices = _watchlist_prices(conn, code)
    changes, missing = _watchlist_changes(conn, code, scan, prices, as_of=as_of,
                                           limit=changes_limit)

    price_change = None
    if prices:
        last = prices[-1]
        pct = None
        if len(prices) >= 2 and prices[-2]["close_qfq"]:
            pct = round((last["close_qfq"] / prices[-2]["close_qfq"] - 1) * 100, 2)
        price_change = {"date": last["date"], "close": last["close_qfq"], "pct": pct}

    freshness = {
        "stock": stock["updated_at"] if stock else None,
        "scan": scan["scan_date"] if scan else None,
        "kline": prices[-1]["date"] if prices else None,
        "holder": conn.execute(
            "SELECT MAX(disclose_date) AS d FROM holder_num WHERE code=?", (code,)
        ).fetchone()["d"],
        "longhu": conn.execute(
            "SELECT MAX(trade_date) AS d FROM longhu WHERE code=?", (code,)
        ).fetchone()["d"],
        "announcement": conn.execute(
            "SELECT MAX(pub_date) AS d FROM announcement WHERE code=?", (code,)
        ).fetchone()["d"],
    }
    return {
        "code": code,
        "name": stock["name"] if stock else None,
        "board": stock["board"] if stock else None,
        "status": entry["status"] if entry else None,
        "note": entry["note"] if entry else None,
        "added_at": entry["added_at"] if entry else None,
        "updated_at": entry["updated_at"] if entry else None,
        "removed_at": entry["removed_at"] if entry else None,
        "lane": scan["lane"] if scan else None,
        "tier": scan["tier"] if scan else None,
        "scan_date": scan["scan_date"] if scan else None,
        "start_date": scan["start_date"] if scan else None,
        "start_age_days": scan["start_age_days"] if scan else None,
        "mechanical_start_date": scan["mechanical_start_date"] if scan else None,
        "confirmed_start_date": scan["confirmed_start_date"] if scan else None,
        "missing_fields": missing,
        "freshness": freshness,
        "price_change": price_change,
        "changes": changes,
        "generated_for": as_of,
    }


def evaluate_watchlist(
    conn: sqlite3.Connection,
    *,
    run_date: str | None = None,
    codes: list[str] | None = None,
    changes_limit: int = 20,
) -> dict[str, Any]:
    """评估活动跟踪名单并发送去重的定向提醒；不生成日报汇总提醒。"""
    day = run_date or _today()
    if codes is None:
        entries = conn.execute(
            "SELECT * FROM watchlist_entry WHERE status='active' ORDER BY updated_at DESC"
        ).fetchall()
    else:
        unique_codes = list(dict.fromkeys(str(code) for code in codes))
        if not unique_codes:
            entries = []
        else:
            marks = ",".join("?" * len(unique_codes))
            entries = conn.execute(
                f"SELECT * FROM watchlist_entry WHERE status='active' AND code IN ({marks}) "
                "ORDER BY updated_at DESC", unique_codes
            ).fetchall()

    views: list[dict[str, Any]] = []
    notified = 0
    for entry in entries:
        view = watchlist_snapshot(conn, entry["code"], entry=entry, today=day,
                                  changes_limit=changes_limit)
        views.append(view)
        for change in view["changes"]:
            if not change.get("actionable"):
                continue
            title = f"跟踪变化：{entry['code']} {change['title']}"
            body = change["detail"]
            if change.get("date"):
                body += f"（日期：{change['date']}）"
            if notify.record_once(conn, change.get("date") or day, "warn", title, body, today=day):
                notified += 1
    conn.commit()
    return {"ok": True, "run_date": day, "tracked": len(views),
            "notified": notified, "rows": views}
