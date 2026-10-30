"""M5 AI 素材包导出（PRD F8）：单票一键导出 Markdown（喂聊天 AI）+ JSON（喂程序）。

系统不内嵌 AI；本模块只保证"数据层完全结构化、脚本可一键重生成"。
默认输出到 data/exports/（gitignore 内）。提醒非决策。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from . import pipeline
from .config import PROJECT_ROOT

MD_ANN_LIMIT = 60      # Markdown 公告清单条数上限
MD_HOLDER_LIMIT = 12   # Markdown 户数表期数上限（JSON 给全量）
KLINE_WINDOW = 120     # 摘要窗口（与流水线 B 默认窗口一致）


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _pct(close_a: float | None, close_b: float | None) -> float | None:
    if close_a is None or close_b is None or close_b == 0:
        return None
    return round((close_a / close_b - 1) * 100, 2)


def _qfq_series(conn: sqlite3.Connection, code: str) -> dict[str, float]:
    """trade_date → 前复权收盘（口径与 api/scanner 一致：close × 因子 ÷ 最新非空因子）。"""
    rows = _rows(conn,
                 "SELECT trade_date, close, adj_factor FROM kline_daily"
                 " WHERE code=? ORDER BY trade_date", (code,))
    factors = [r["adj_factor"] for r in rows if r["adj_factor"] is not None]
    base = factors[-1] if factors else None
    out: dict[str, float] = {}
    for r in rows:
        if r["close"] is None:
            continue
        f = r["adj_factor"] if r["adj_factor"] is not None else (base or 1.0)
        out[r["trade_date"]] = round(
            r["close"] * f / base, 4) if base else r["close"]
    return out


def _kline_summary(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    ks = _rows(conn,
               "SELECT trade_date, open, high, low, close, volume, amount"
               " FROM kline_daily WHERE code=? ORDER BY trade_date", (code,))
    if not ks:
        return {"rows": 0, "first_date": None, "last_date": None, "intervals_pct": {},
                "window_high": None, "window_low": None,
                "drawdown_from_window_high_pct": None, "recent": []}
    qfq = _qfq_series(conn, code)
    for k in ks:
        k["close_qfq"] = qfq.get(k["trade_date"])
    win = ks[-KLINE_WINDOW:]
    last = ks[-1]
    closes = [k["close_qfq"] for k in win if k["close_qfq"] is not None]
    lo, hi = min(closes), max(closes)
    out_win = round((hi / lo - 1) * 100, 2) if lo else None
    return {
        "rows": len(ks),
        "first_date": ks[0]["trade_date"],
        "last_date": last["trade_date"],
        "last_close_raw": last["close"],
        "last_close_qfq": last["close_qfq"],
        "window_days": len(win),
        "window_range_pct": out_win,
        "intervals_pct": {
            str(n): _pct(last["close_qfq"], closes[-n]) if len(closes) >= n else None
            for n in (20, 60, 120)
        },
        "window_high": hi,
        "window_low": lo,
        "drawdown_from_window_high_pct": _pct(last["close_qfq"], hi),
        "recent": win[-20:],
    }


def build_material(conn: sqlite3.Connection, code: str,
                   *, today: str | None = None) -> dict[str, Any] | None:
    """聚合单票结构化素材；stock/event/announcement/kline 全空时返回 None。"""
    stock = conn.execute("SELECT * FROM stock WHERE code=?", (code,)).fetchone()
    has_data = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM announcement WHERE code=?)"
        " OR EXISTS(SELECT 1 FROM event WHERE code=?)"
        " OR EXISTS(SELECT 1 FROM kline_daily WHERE code=?)", (code, code, code)
    ).fetchone()[0]
    if stock is None and not has_data:
        return None

    events = _rows(conn,
                   "SELECT id, event_type, grade, start_date, status FROM event"
                   " WHERE code=? ORDER BY start_date", (code,))
    for ev in events:
        ev["nodes"] = _rows(conn,
                            "SELECT f.node_type, f.node_date, a.title, a.pdf_url"
                            " FROM flow_node f LEFT JOIN announcement a ON a.id=f.announcement_id"
                            " WHERE f.event_id=? ORDER BY f.node_date, f.id", (ev["id"],))
    pool = _rows(conn,
                 "SELECT status, grade, entered_at, resonance_count, exit_signals, note"
                 " FROM pool_entry WHERE code=? ORDER BY entered_at", (code,))
    for pe in pool:
        try:
            pe["exit_signals"] = json.loads(pe["exit_signals"] or "[]")
        except (TypeError, ValueError):
            pe["exit_signals"] = []

    watchlist_row = conn.execute(
        "SELECT code, status, note, added_at, updated_at, removed_at "
        "FROM watchlist_entry WHERE code=?", (code,)
    ).fetchone()

    # 事件锚定收益：以事件起始日（含）之后首个交易日的前复权价为基准
    idx = _qfq_series(conn, code)
    anchor: list[dict] = []
    for ev in events:
        dates = sorted(d for d in idx if d >= ev["start_date"])
        if not dates:
            continue
        base, last_q = idx[dates[0]], idx[dates[-1]]
        anchor.append({"event_type": ev["event_type"], "grade": ev["grade"],
                       "start_date": ev["start_date"], "status": ev["status"],
                       "since_pct": _pct(last_q, base)})

    return {
        "code": code,
        "generated_for": today or date.today().isoformat(),
        "profile": dict(stock) if stock else None,
        "pool": pool,
        "watchlist": pipeline.watchlist_snapshot(
            conn, code, entry=watchlist_row, today=today
        ) if watchlist_row else None,
        "events": events,
        "announcements": _rows(conn,
                               "SELECT pub_date, title, pdf_url, source FROM announcement"
                               " WHERE code=? ORDER BY pub_date DESC, id DESC", (code,)),
        "holders": _rows(conn,
                         "SELECT disclose_date, holder_num, pct_change, avg_hold"
                         " FROM holder_num WHERE code=? ORDER BY disclose_date", (code,)),
        "kline": _kline_summary(conn, code),
        "event_anchor_returns": anchor,
        "notes": _rows(conn,
                       "SELECT trade_date, content, tags, created_at FROM watch_note"
                       " WHERE code=? ORDER BY trade_date DESC, id DESC", (code,)),
    }


def _md_table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(lines)


def to_markdown(m: dict[str, Any]) -> str:
    p = m.get("profile") or {}
    parts = [
        f"# 资本运作素材包：{p.get('name', '?')}（{m['code']}）",
        "",
        f"> 生成日 {m['generated_for']}｜数据截止 {m['kline'].get('last_date') or '无K线'}｜"
        "本素材由资本事件研究台本地导出，仅供个人研究，所有提醒均非投资决策。",
        "",
        "## 1. 股票档案",
    ]
    if p:
        parts.append(_md_table(
            ["代码", "名称", "板块", "上市日", "ST", "总市值(亿)", "流通市值(亿)"],
            [[p.get("code"), p.get("name"), p.get("board"), p.get("list_date"),
              "是" if p.get("is_st") else "否", p.get("total_mv"), p.get("circ_mv")]]))
    else:
        parts.append("（无档案记录）")

    parts += ["", "## 2. 候选池状态"]
    if m["pool"]:
        parts.append(_md_table(
            ["入池日", "等级", "池状态", "共振数", "离场信号", "备注"],
            [[pe["entered_at"], pe["grade"], pe["status"], pe["resonance_count"],
              "、".join(pe["exit_signals"]) or "-", pe["note"] or "-"] for pe in m["pool"]]))
    else:
        parts.append("不在候选池。")

    parts += ["", "## 2.1 我的跟踪"]
    if m.get("watchlist"):
        w = m["watchlist"]
        parts.append(f"- 状态：{w.get('status')}；备注：{w.get('note') or '-'}")
    else:
        parts.append("未加入我的跟踪名单。")

    parts += ["", "## 3. 资本运作事件与流程节点"]
    if not m["events"]:
        parts.append("无资本运作事件。")
    for ev in m["events"]:
        parts.append(f"### {ev['event_type']}（{ev['grade']} 级｜{ev['status']}｜起始 {ev['start_date']}）")
        if ev["nodes"]:
            parts.append(_md_table(
                ["节点", "日期", "公告标题", "PDF"],
                [[nd["node_type"], nd["node_date"], nd["title"] or "-",
                  nd["pdf_url"] or "-"] for nd in ev["nodes"]]))
        else:
            parts.append("（无节点）")

    k = m["kline"]
    parts += ["", "## 4. K 线摘要与区间收益"]
    if not k["rows"]:
        parts.append("无 K 线数据。")
    else:
        parts += [
            f"- K 线 {k['rows']} 根（{k['first_date']} ~ {k['last_date']}），"
            f"最新前复权收盘 {k.get('last_close_qfq')}，不复权 {k.get('last_close_raw')}",
            f"- 近 {k['window_days']} 交易日区间振幅 {k.get('window_range_pct')}%，"
            f"20/60/120 日涨幅 {k['intervals_pct'].get('20')}% / "
            f"{k['intervals_pct'].get('60')}% / {k['intervals_pct'].get('120')}%",
            f"- 窗口最高 {k.get('window_high')} / 最低 {k.get('window_low')}，"
            f"距窗口高点回撤 {k.get('drawdown_from_window_high_pct')}%",
        ]
    if m["event_anchor_returns"]:
        parts.append(_md_table(
            ["事件", "等级", "起始日", "状态", "事件日以来涨幅%"],
            [[a["event_type"], a["grade"], a["start_date"], a["status"], a["since_pct"]]
             for a in m["event_anchor_returns"]]))

    parts += ["", "## 5. 股东户数序列"]
    hs = m["holders"]
    if hs:
        parts.append(_md_table(
            ["披露日", "户数", "环比%", "户均持股"],
            [[h["disclose_date"], h["holder_num"], h["pct_change"], h["avg_hold"]]
             for h in hs[-MD_HOLDER_LIMIT:]]))
        if len(hs) > MD_HOLDER_LIMIT:
            parts.append(f"（此处仅列最近 {MD_HOLDER_LIMIT} 期，全量 {len(hs)} 期见 JSON）")
    else:
        parts.append("无户数数据。")

    parts += ["", f"## 6. 公告清单（最新 {MD_ANN_LIMIT} 条，全量见 JSON）"]
    anns = m["announcements"]
    if anns:
        parts.append(_md_table(["公告日", "标题", "来源"],
                               [[a["pub_date"], a["title"], a["source"]]
                                for a in anns[:MD_ANN_LIMIT]]))
    else:
        parts.append("无公告记录。")

    parts += ["", "## 7. 人工笔记"]
    if m["notes"]:
        parts.append(_md_table(["日期", "内容", "标签"],
                               [[n["trade_date"] or "-", n["content"], n["tags"] or "-"]
                                for n in m["notes"]]))
    else:
        parts.append("无人工笔记。")
    return "\n".join(parts) + "\n"


def export_files(conn: sqlite3.Connection, code: str,
                 outdir: str | Path | None = None) -> tuple[Path, Path] | None:
    """生成 .md + .json 双素材包；返回两文件路径，无该股票数据时返回 None。"""
    m = build_material(conn, code)
    if m is None:
        return None
    outdir = Path(outdir) if outdir else PROJECT_ROOT / "data" / "exports"
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"material_{code}_{m['generated_for'].replace('-', '')}"
    md_path, json_path = outdir / f"{stem}.md", outdir / f"{stem}.json"
    md_path.write_text(to_markdown(m), encoding="utf-8")
    json_path.write_text(json.dumps(m, ensure_ascii=False, indent=2, default=str),
                         encoding="utf-8")
    return md_path, json_path
