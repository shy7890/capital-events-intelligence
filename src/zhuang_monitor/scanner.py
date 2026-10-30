"""M4 流水线 B：因子扫描（PRD §3.2/§3.3/F5/F10）。

每日对"有 K 线数据的代码宇宙"（kline_daily 实际覆盖范围）跑四类因子 + 基础排除，
产出 scan_result 疑似庄股榜（同日重跑整日覆盖刷新，幂等），并把在榜标的的共振数
回写 pool_entry.resonance_count（只更新在榜的；命中但不在池不自动入池，入池归 M2）。

内存约定：全市场 K 线按 BATCH_SIZE 只（默认 200）分批读入 pandas 计算，
龙虎榜/户数/弱信号仅对存活候选做小查询，绝不一次 load 全表。

判定链（e→a 短路出局；a 过后 b/c/d 全部实测记录，b 命中上榜才最终出局）：
  e 基础排除（ST / 上市<1年 / 流通市值缺失或≥300亿 / 窗口日均成交额<3000万）
  → a 涨幅（窗口最低价日=起涨点，终点=窗口最后收盘，qfq 口径 ≥25%）
  → b 龙虎榜静默（起涨点之后 0 次上榜；有则整票出局）
  → c 户数集中（近 2 披露期 ≥1 次环比≤-10% 或两期累计≤-15%）
  → d 弱信号共振（近 90 天 ≥2 类独立弱信号，按类型去重）
入榜前提 = a+b 同时命中；tier 按流通市值 <150亿→A / 150–300亿→观察。
resonance = 命中条数（涨幅+户数+静默+弱信号+小市值 各算 1）。

参数默认 DEFAULT_PARAMS，可被 config 表 key "scan_params"（JSON）覆盖，
再被调用方显式 params 实参覆盖（F10 即时生效）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

import pandas as pd

from . import ingest

BATCH_SIZE = 200  # 每批代码数上限（内存流式约束）

DEFAULT_PARAMS: dict[str, Any] = {
    "window": 120,                  # 回看交易日窗口
    "gain_min_pct": 25.0,           # 起涨点→终点最小涨幅 %
    "circ_mv_a_max": 150.0,         # 小市值命中线（亿元）
    "circ_mv_watch_max": 300.0,     # ≥ 此值直接出局（亿元）
    "min_list_days": 365,           # 上市不足此天数出局
    "min_avg_amount": 3.0e7,        # 窗口日均成交额下限（元）
    "holder_single_drop_pct": -10.0,  # 近 2 期单期降幅阈值
    "holder_cum_drop_pct": -15.0,     # 近 2 期累计降幅阈值（两期 pct 相加）
    "weak_days": 90,                # 弱信号回看自然日窗口
    "weak_min_types": 2,            # 弱信号命中所需最少类型数
    "start_max_days": 40,           # 新起涨路线：确认起点距基准日最大交易日数
    "continuation_max_days": 120,   # 延续观察路线最大确认起点年龄
    "start_s_days": 30,             # v1.1 S 级新鲜度线（交易日）
    "min_resonance": 3,             # v1.1 上榜最少命中项数
    "pool_mv_max": 300.0,           # v1.1 候选池市值上限（亿元，入池与归档共用）
    "archive_after_days": 30,       # v1.1 终态池行静置 N 天自动归档
    "archive_zombie_days": 90,      # v1.1 进行中事件 90 天无节点=僵尸归档
    "archive_event_max_age_days": 365,  # v1.1 事件起始超 1 年归档（时效优先）
}

# 弱信号五类（与 events/rules.py 的 B 级 node_type 口径一致）
WEAK_SIGNAL_TYPES = ("高管变动", "设立子公司", "经营范围变更", "激励计划", "质押变动")

# 实质共振：命中任一项即视为"升入观察"级信号（upgraded_watch 统计口径，不自动入池）
SUBSTANTIAL_HITS = ("户数集中", "弱信号共振")


def resolve_params(conn: sqlite3.Connection,
                   params: dict[str, Any] | None = None) -> dict[str, Any]:
    """默认值 ← config.scan_params（JSON）← 显式实参，逐层覆盖。"""
    merged = dict(DEFAULT_PARAMS)
    cfg = ingest.get_config(conn, "scan_params", None)
    if isinstance(cfg, dict):
        merged.update(cfg)
    if params:
        merged.update(params)
    return merged


def _chunks(seq: list[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _universe(conn: sqlite3.Connection, codes: list[str] | None) -> list[str]:
    """宇宙 = kline_daily 里有数据的代码（夜间全量 K 线回填完成前的设计内限制）。"""
    if codes:
        marks = ",".join("?" * len(codes))
        rows = conn.execute(
            f"SELECT DISTINCT code FROM kline_daily WHERE code IN ({marks}) "
            "ORDER BY code", codes).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT code FROM kline_daily ORDER BY code").fetchall()
    return [r["code"] for r in rows]


def _stock_meta(conn: sqlite3.Connection, batch: list[str]) -> dict[str, sqlite3.Row]:
    marks = ",".join("?" * len(batch))
    rows = conn.execute(
        f"SELECT code, name, board, list_date, is_st, circ_mv FROM stock "
        f"WHERE code IN ({marks})", batch).fetchall()
    return {r["code"]: r for r in rows}


def _load_kline(conn: sqlite3.Connection, batch: list[str], today: str) -> pd.DataFrame:
    marks = ",".join("?" * len(batch))
    df = pd.read_sql_query(
        f"SELECT code, trade_date, close, adj_factor, amount FROM kline_daily "
        f"WHERE code IN ({marks}) AND trade_date <= ? ORDER BY code, trade_date",
        conn, params=[*batch, today])
    if df.empty:
        return df
    # 前复权收盘：close × 当日因子 ÷ 该股最新非空因子（与 api /kline 口径一致；
    # 因子缺失退化为原始价）。比值判定中基准因子可约去。
    df = df[df["close"].notna()].copy()
    df["close_qfq"] = df["close"].astype(float)
    for code, idx in df.groupby("code").groups.items():
        sub = df.loc[idx]
        factors = sub["adj_factor"].dropna()
        if factors.empty:
            continue
        base = float(factors.iloc[-1])
        if base == 0:
            continue
        df.loc[idx, "close_qfq"] = sub["close"] * sub["adj_factor"].fillna(base) / base
    return df


def _holder_stats(conn: sqlite3.Connection, code: str, today: str,
                  p: dict) -> tuple[float | None, bool]:
    """返回 (近 2 披露期最大单期降幅, 是否命中户数集中)。只看披露日 ≤ 基准日。"""
    rows = conn.execute(
        "SELECT pct_change FROM holder_num "
        "WHERE code=? AND pct_change IS NOT NULL AND disclose_date <= ? "
        "ORDER BY disclose_date DESC LIMIT 2", (code, today)).fetchall()
    pcts = [float(r["pct_change"]) for r in rows]
    if not pcts:
        return None, False
    worst = min(pcts)
    hit = worst <= p["holder_single_drop_pct"] or (
        len(pcts) >= 2 and sum(pcts) <= p["holder_cum_drop_pct"])
    return worst, hit


def _weak_signal_types(conn: sqlite3.Connection, code: str, today: str,
                       p: dict) -> list[str]:
    """近 weak_days 自然日内命中的不同独立弱信号类型。"""
    start = (date.fromisoformat(today) - timedelta(days=int(p["weak_days"]))).isoformat()
    marks = ",".join("?" * len(WEAK_SIGNAL_TYPES))
    rows = conn.execute(
        f"SELECT DISTINCT signal_type AS t FROM weak_signal "
        f"WHERE code = ? AND signal_date BETWEEN ? AND ? AND signal_type IN ({marks}) "
        "ORDER BY signal_type",
        [code, start, today, *WEAK_SIGNAL_TYPES],
    ).fetchall()
    return [r["t"] for r in rows]


def _confirmed_start_index(w: pd.DataFrame, mechanical_index: int, low: float) -> int | None:
    """机械低点后首次站上 +10%，并有连续三个后续交易日不跌破机械低点。"""
    close = w["close_qfq"].astype(float).reset_index(drop=True)
    for i in range(mechanical_index + 1, len(close) - 3):
        if close.iloc[i] < low * 1.10:
            continue
        if (close.iloc[i + 1:i + 4] >= low).all():
            return i
    return None


def _evaluate(conn: sqlite3.Connection, code: str, today: str, p: dict,
              meta: sqlite3.Row | None, win: pd.DataFrame | None) -> dict[str, Any]:
    """单票判定；返回明细 dict（listed 标记是否入榜，excluded_reason 出局原因）。"""
    det: dict[str, Any] = {
        "code": code, "listed": False, "excluded_reason": None,
        "lane": None, "start_date": None, "start_age_days": None,
        "mechanical_start_date": None, "confirmed_start_date": None,
        "missing_fields": [],
        "gain_pct": None, "lhb_after_start": None,
        "holder_drop_pct": None, "weak_types": [], "circ_mv": None,
        "avg_amount": None, "tier": None, "resonance": 0, "hits": [],
    }

    def out(reason: str) -> dict[str, Any]:
        det["excluded_reason"] = reason
        return det

    # e) 基础排除
    if meta is None:
        return out("无股票档案")
    circ_mv = meta["circ_mv"]
    det["circ_mv"] = circ_mv
    if meta["is_st"]:
        return out("ST")
    if not meta["list_date"]:
        return out("上市日期缺失")
    listed_days = (date.fromisoformat(today) - date.fromisoformat(meta["list_date"])).days
    if listed_days < p["min_list_days"]:
        return out("上市不满1年")
    if circ_mv is None or float(circ_mv) >= p["circ_mv_watch_max"]:
        return out("流通市值缺失或≥300亿")
    if win is None or len(win) < 2:
        return out("K线数据不足")

    avg_amount = win["amount"].mean()
    if pd.isna(avg_amount) or float(avg_amount) < p["min_avg_amount"]:
        det["avg_amount"] = None if pd.isna(avg_amount) else float(avg_amount)
        return out("日均成交额不足")
    det["avg_amount"] = round(float(avg_amount), 2)

    # a) 涨幅：先保留机械最低点；路线年龄改用确认起点，避免把一次探底日当成参与日。
    w = win.reset_index(drop=True)
    min_i = int(w["close_qfq"].values.argmin())
    low_row = w.iloc[min_i]
    end_qfq = float(w["close_qfq"].iloc[-1])
    start_qfq = float(low_row["close_qfq"])
    gain = (end_qfq / start_qfq - 1.0) * 100.0 if start_qfq > 0 else 0.0
    mechanical_age = len(w) - 1 - min_i
    det["mechanical_start_date"] = str(low_row["trade_date"])
    det["gain_pct"] = round(gain, 2)
    if gain < p["gain_min_pct"]:
        return out("涨幅不足")

    confirmed_i = _confirmed_start_index(w, min_i, start_qfq)
    if confirmed_i is None:
        det["lane"] = "待确认"
        det["start_date"] = str(low_row["trade_date"])
        det["start_age_days"] = int(mechanical_age)
    else:
        confirmed_row = w.iloc[confirmed_i]
        confirmed_age = len(w) - 1 - confirmed_i
        det["confirmed_start_date"] = str(confirmed_row["trade_date"])
        det["start_date"] = det["confirmed_start_date"]
        det["start_age_days"] = int(confirmed_age)
        if confirmed_age <= int(p["start_max_days"]):
            det["lane"] = "新起涨"
        elif confirmed_age <= int(p["continuation_max_days"]):
            det["lane"] = "延续观察"
        else:
            return out(f"起涨点过旧（{confirmed_age}＞{p['continuation_max_days']}交易日）")
    hits = ["涨幅"]

    # b) 龙虎榜静默：起涨点之后有上榜 → 整票出局（c/d 实测值仍记录，供 probe 核对）
    n_lhb = conn.execute(
        "SELECT COUNT(*) AS n FROM longhu WHERE code=? AND trade_date>?",
        (code, det["mechanical_start_date"])).fetchone()["n"]
    det["lhb_after_start"] = int(n_lhb)

    # c) 户数集中
    drop, holder_hit = _holder_stats(conn, code, today, p)
    det["holder_drop_pct"] = None if drop is None else round(drop, 2)
    if drop is None:
        det["missing_fields"].append("holder_num")

    # d) 弱信号共振
    weak = _weak_signal_types(conn, code, today, p)
    det["weak_types"] = weak

    if n_lhb > 0:
        return out("起涨后上过龙虎榜")
    hits.append("龙虎榜静默")
    if holder_hit:
        hits.append("户数集中")
    if len(weak) >= int(p["weak_min_types"]):
        hits.append("弱信号共振")
    small_mv = float(circ_mv) < p["circ_mv_a_max"]
    if small_mv:
        hits.append("小市值")
    det["hits"] = hits
    det["resonance"] = len(hits)

    # v1.1 定级：最少命中数闸门 + 命中组合×市值×新鲜度四级（见 PRD §3.2）
    if det["resonance"] < int(p["min_resonance"]):
        return out(f"命中不足{int(p['min_resonance'])}项")
    holder_ok = "户数集中" in hits
    weak_ok = "弱信号共振" in hits
    if det["lane"] == "延续观察":
        det["tier"] = "延续观察"
    elif det["lane"] == "待确认":
        det["tier"] = "待确认"
    elif det["holder_drop_pct"] is None:
        # 已知因子仍可展示，但未知户数不能把一只股票抬到 S/A。
        det["tier"] = "B" if small_mv else "观察"
    elif small_mv and holder_ok and weak_ok and det["start_age_days"] <= int(p["start_s_days"]):
        det["tier"] = "S"
    elif small_mv and (holder_ok or weak_ok):
        det["tier"] = "A"
    elif small_mv or (holder_ok and weak_ok):
        det["tier"] = "B"
    else:
        det["tier"] = "观察"
    det["listed"] = True
    return det


def scan_factors(conn: sqlite3.Connection, *, today: str,
                 params: dict[str, Any] | None = None,
                 codes: list[str] | None = None) -> dict[str, Any]:
    """全量因子扫描（流水线 B）。返回统计 dict（键见下方 return）。"""
    p = resolve_params(conn, params)
    universe = _universe(conn, codes)
    rows_all: list[dict[str, Any]] = []
    scanned = 0

    for batch in _chunks(universe, BATCH_SIZE):
        metas = _stock_meta(conn, batch)
        kdf = _load_kline(conn, batch, today)
        grouped = ({code: g.tail(int(p["window"])) for code, g in kdf.groupby("code")}
                   if not kdf.empty else {})
        for code in batch:
            scanned += 1
            det = _evaluate(conn, code, today, p, metas.get(code), grouped.get(code))
            if det["listed"]:
                rows_all.append(det)

    # 整日覆盖刷新（幂等：同日重跑行数不变）
    conn.execute("DELETE FROM scan_result WHERE scan_date=?", (today,))
    conn.executemany(
        "INSERT OR REPLACE INTO scan_result(scan_date,code,gain_pct,lhb_after_start,"
        "holder_drop_pct,circ_mv,avg_amount,resonance,tier,hits_json,"
        "start_date,start_age_days,lane,mechanical_start_date,confirmed_start_date,"
        "missing_fields_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(today, r["code"], r["gain_pct"], r["lhb_after_start"], r["holder_drop_pct"],
          r["circ_mv"], r["avg_amount"], r["resonance"], r["tier"],
          json.dumps(r["hits"], ensure_ascii=False),
          r["start_date"], r["start_age_days"], r["lane"],
          r["mechanical_start_date"], r["confirmed_start_date"],
          json.dumps(r["missing_fields"], ensure_ascii=False)) for r in rows_all])
    conn.commit()

    # 共振回写：只更新在榜的池内标的（不在榜保留原值；命中不在池不自动入池）
    now = today
    resonance_updated = 0
    for r in rows_all:
        cur = conn.execute(
            "UPDATE pool_entry SET resonance_count=?, updated_at=? WHERE code=?",
            (r["resonance"], now, r["code"]))
        resonance_updated += cur.rowcount
    conn.commit()

    tier_counts = {"S": 0, "A": 0, "B": 0, "观察": 0, "延续观察": 0, "待确认": 0}
    upgraded_watch = 0
    for r in rows_all:
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1
        if any(h in SUBSTANTIAL_HITS for h in r["hits"]):
            upgraded_watch += 1

    return {
        "universe_size": len(universe),
        "scanned": scanned,
        "hits": len(rows_all),
        "upgraded_watch": upgraded_watch,
        "resonance_updated": resonance_updated,
        "tier_counts": tier_counts,
    }


def explain(conn: sqlite3.Connection, code: str, *, today: str,
            params: dict[str, Any] | None = None) -> dict[str, Any]:
    """单票各因子实测明细（含出局原因），供 CLI --probe 与人工核对。"""
    p = resolve_params(conn, params)
    meta = _stock_meta(conn, [code]).get(code)
    kdf = _load_kline(conn, [code], today)
    win = kdf.tail(int(p["window"])) if not kdf.empty else None
    return _evaluate(conn, code, today, p, meta, win)
