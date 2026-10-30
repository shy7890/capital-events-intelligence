"""批任务入口（systemd zhuang-batch-daily.timer 调用）：每日数据层流水线（PRD §4.1、§7 幂等规约）。

交易日 16:00（CST）单趟触发；同一 run_date 重复执行不产生重复数据——
K 线按库内水位增量、龙虎榜/公告按 config.watermarks 当日跳过。
"""

from __future__ import annotations

import logging
import sys
from datetime import date

from . import db, ingest, pipeline
from .sources import ak_adapters, ak_notice, baostock_k, cninfo, tencent

KLINE_WATERMARK = "kline"
HOLDER_WATERMARK = "holder"
LONGHU_WATERMARK = "longhu"
ANNOUNCE_WATERMARK = "announce"

# Public baseline: operators can populate watch_codes in SQLite or pass codes explicitly.
DEFAULT_WATCH_CODES: list[str] = []
DEFAULT_KLINE_START = "2023-01-01"
HOLDER_TARGET_LIMIT = 300

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zhuang_monitor.batch")


def run_daily(
    conn,
    today: str,
    *,
    codes: list[str],
    kline_codes: list[str] | None = None,
    holder_codes: list[str] | None = None,
    kline_fn,
    sina_fn,
    tencent_fn=None,
    holder_fn,
    longhu_fn,
    announce_fn,
    market_announce_fn=None,
    scan_fn=None,
    watchlist_fn=None,
) -> dict:
    """执行当日数据层流水线；阶段失败不冒泡、不推进该阶段水位。

    kline_codes：提供时走全市场可续跑刷新；省略时保留旧的逐票行为，便于测试和手工调用。
    holder_codes：股东户数定向补数名单；省略时沿用 codes。
    scan_fn：M4 因子扫描钩子（如 `lambda: pipeline.backfill_scan(conn, run_date=today)`），
    提供时在 events 阶段之后执行并计入 stages/red；默认 None 保持 M1 行为不变。
    """
    today_d = date.fromisoformat(today)
    wm: dict = dict(ingest.get_config(conn, "watermarks", {}) or {})
    stages: list[dict] = []

    if kline_codes is None:
        for code in codes:
            start = pipeline.incremental_kline_start(
                conn, code, default_start=DEFAULT_KLINE_START, today=today_d
            )
            r = pipeline.backfill_kline(conn, code, start, today,
                                        kline_fn=kline_fn, sina_fn=sina_fn,
                                        tencent_fn=tencent_fn, run_date=today)
            stages.append(r)
            if r["ok"]:
                wm.setdefault(KLINE_WATERMARK, {})[code] = today
    else:
        r = pipeline.refresh_market_kline(
            conn, today, kline_codes, kline_fn=kline_fn, sina_fn=sina_fn, tencent_fn=tencent_fn
        )
        stages.append(r)
        if r["ok"]:
            wm.setdefault(KLINE_WATERMARK, {})["market"] = today

    for code in holder_codes if holder_codes is not None else codes:
        r = pipeline.backfill_holder(conn, code, holder_fn=holder_fn, run_date=today)
        stages.append(r)
        if r["ok"]:
            wm.setdefault(HOLDER_WATERMARK, {})[code] = today

    if today_d.weekday() < 5 and wm.get(LONGHU_WATERMARK) != today:
        r = pipeline.backfill_longhu(conn, [today], longhu_fn=longhu_fn, run_date=today)
        stages.append(r)
        if r["ok"]:
            if kline_codes is not None:
                if r.get("inserted", 0):
                    wm[LONGHU_WATERMARK] = today
                    ingest.set_market_refresh_longhu(conn, today, "green")
                else:
                    # 第一次空响应可能只是龙虎榜尚未发布；同一目标日下一次触发
                    # 再确认，避免把“尚未有数据”误当成完整市场刷新。
                    prior_empty = conn.execute(
                        "SELECT COUNT(*) FROM scan_run WHERE run_date=? AND stage='longhu' "
                        "AND status='green'",
                        (today,),
                    ).fetchone()[0]
                    if prior_empty >= 2:
                        wm[LONGHU_WATERMARK] = today
                        ingest.set_market_refresh_longhu(
                            conn, today, "known_empty", "连续两次来源确认当天无龙虎榜记录"
                        )
                    else:
                        ingest.set_market_refresh_longhu(
                            conn, today, "pending", "龙虎榜暂时无返回，等待下一触发点确认"
                        )
            else:
                wm[LONGHU_WATERMARK] = today
        elif kline_codes is not None:
            ingest.set_market_refresh_longhu(conn, today, "pending", "龙虎榜刷新失败，等待重试")
    elif kline_codes is not None and today_d.weekday() >= 5:
        ingest.set_market_refresh_longhu(conn, today, "skipped", "非交易日")

    # 有全市场级公告源时，个股公告循环只保留给旧的定向调用；避免主任务逐票请求。
    announcement_codes = [] if (kline_codes is not None and market_announce_fn is not None) else codes
    for code in announcement_codes:
        start = (wm.get(ANNOUNCE_WATERMARK) or {}).get(code) or today
        r = pipeline.backfill_announcements(conn, code, start, today,
                                            fetch_fn=lambda c, s, e, fn=announce_fn: fn(c, s, e),
                                            run_date=today)
        stages.append(r)
        if r["ok"]:
            wm.setdefault(ANNOUNCE_WATERMARK, {})[code] = today

    if market_announce_fn is not None:
        from datetime import timedelta

        prev = (today_d - timedelta(days=1)).isoformat()
        r = pipeline.backfill_market_announcements(conn, [prev, today],
                                                   fetch_fn=market_announce_fn, run_date=today)
        stages.append(r)

    stages.append(pipeline.backfill_events(conn, run_date=today))

    if scan_fn is not None:
        stages.append(scan_fn())

    if watchlist_fn is not None:
        stages.append(watchlist_fn())

    ingest.set_config(conn, "watermarks", wm)
    red = sum(1 for s in stages if not s["ok"])
    return {"ok": red == 0, "red": red, "stages": stages}


def select_holder_codes(conn, market_codes: list[str], limit: int = HOLDER_TARGET_LIMIT) -> list[str]:
    """按用户关注优先级挑选户数补数对象，避免把逐票东财请求扩成全市场。"""
    universe = set(str(code) for code in market_codes)
    ordered: list[str] = []

    def add(rows) -> None:
        for row in rows:
            code = str(row[0])
            if code in universe and code not in ordered:
                ordered.append(code)

    tables = db.table_names(conn)
    if "watchlist_entry" in tables:
        add(conn.execute(
            "SELECT code FROM watchlist_entry WHERE status='active' ORDER BY updated_at DESC"
        ).fetchall())
    add(conn.execute(
        "SELECT DISTINCT code FROM pool_entry WHERE status <> '归档' ORDER BY updated_at DESC"
    ).fetchall())
    add(conn.execute(
        "SELECT code FROM scan_result WHERE scan_date=(SELECT MAX(scan_date) FROM scan_result) "
        "AND tier IN ('S','A') ORDER BY resonance DESC, gain_pct DESC"
    ).fetchall())
    add((code,) for code in DEFAULT_WATCH_CODES)
    return ordered[: max(0, int(limit))]


def main() -> int:
    conn = db.connect()
    db.init_schema(conn)
    codes = ingest.get_config(conn, "watch_codes", DEFAULT_WATCH_CODES)
    market_codes = [r["code"] for r in conn.execute(
        "SELECT code FROM stock ORDER BY code"
    ).fetchall()]
    if not market_codes:
        market_codes = list(codes)
    holder_codes = select_holder_codes(conn, market_codes)
    today = date.today().isoformat()
    result = run_daily(
        conn,
        today,
        codes=codes,
        kline_codes=market_codes,
        holder_codes=holder_codes,
        kline_fn=baostock_k.kline_rows,
        sina_fn=ak_adapters.kline_sina_rows,
        tencent_fn=tencent.kline_rows,
        holder_fn=ak_adapters.holder_num_rows,
        longhu_fn=ak_adapters.longhu_rows,
        announce_fn=cninfo.fetch_announcements,
        market_announce_fn=ak_notice.notice_day_rows,
        scan_fn=lambda: pipeline.backfill_scan(conn, run_date=today),
        watchlist_fn=lambda: pipeline.evaluate_watchlist(conn, run_date=today),
    )
    for s in result["stages"]:
        log.info("stage=%s ok=%s %s", s.get("stage"), s.get("ok"),
                 s.get("error", ""))
    log.info("批任务完成：全市场 K 线=%d、定向户数=%d、红灯=%d",
             len(market_codes), len(holder_codes), result["red"])
    conn.close()
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
