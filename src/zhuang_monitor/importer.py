"""M3 v6 冷启动导入器（PRD §F7）：把 outputs v6 筛选 xlsx 的历史成果导入本地库。

设计要点：
- 冷启动事件 event_type 统一加 'v6:' 前缀，与实时引擎（M2）自动生成的事件分隔；
  按 (code, 'v6:'+事件大类) 幂等复用。
- 候选池行 event_id 为 NULL；幂等规则：该 code 已存在 event_id IS NULL 的池行则跳过
  （SQLite UNIQUE(code, event_id) 对 NULL 不去重，必须显式检查）。
- 事件状态取该股该大类"最新节点"（公告日最大）的阶段文本：含终止/失败 → '终止'。
- dry_run=True 时一切照常解析写入，但最后回滚事务，不落库。
- 用 openpyxl read_only 逐行流式迭代（values_only），不整表载入单元格对象。
- stock/announcement 的 upsert 语句复用 ingest.py 的自然键 COALESCE 约定；
  本模块不中途 commit，统一由 import_v6_workbook 末尾提交/回滚。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

from .ingest import _UPSERT_ANNOUNCEMENT, _UPSERT_STOCK

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")

# 事件大类关键词 → 评级粗映射（含以下任一词记 S，否则 A）
_S_GRADE_KEYWORDS = ("重组", "控制权", "定增")
# 阶段文本含以下任一词视为终止
_TERMINAL_KEYWORDS = ("终止", "失败")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_code(v: Any) -> str | None:
    """代码归一：去空白、去掉浮点尾巴、纯数字补足 6 位。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit():
        s = s.zfill(6)
    return s


def _norm_date(v: Any) -> str | None:
    """日期归一为 YYYY-MM-DD：兼容 datetime / Excel 序列号 / 'YYYY-MM-DD ...' 字符串。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            return from_excel(v).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OverflowError):
            return None
    s = str(v).strip()
    if not s:
        return None
    m = _DATE_PREFIX.match(s)
    return m.group(0) if m else s


def _grade_for(major_category: str | None) -> str:
    text = major_category or ""
    return "S" if any(k in text for k in _S_GRADE_KEYWORDS) else "A"


def _is_terminal(stage_text: str | None) -> bool:
    text = stage_text or ""
    return any(k in text for k in _TERMINAL_KEYWORDS)


def _find_sheet(wb: Any, exact_name: str, contains: tuple[str, ...]) -> Any:
    """按精确名优先、多关键词其次定位 sheet；缺失返回 None。"""
    if exact_name in wb.sheetnames:
        return wb[exact_name]
    for name in wb.sheetnames:
        if all(k in name for k in contains):
            return wb[name]
    return None


def _clean_cell(c: Any) -> Any:
    if isinstance(c, str):
        s = c.strip()
        return s or None
    return c


def _iter_sheet_rows(ws: Any) -> Iterator[tuple[dict[str, int], tuple]]:
    """流式迭代 sheet 数据行：自适应表头（首个含"代码"单元格的行为表头）。"""
    it = ws.iter_rows(values_only=True)
    for row in it:
        cells = [_clean_cell(c) for c in row]
        if any(isinstance(c, str) and "代码" in c for c in cells):
            header = {c: i for i, c in enumerate(cells) if isinstance(c, str) and c}
            for data in it:
                yield header, [_clean_cell(c) for c in data]
            return


def _get(row: list, header: dict[str, int], name: str) -> Any:
    idx = header.get(name)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _get_any(row: list, header: dict[str, int], *names: str) -> Any:
    for name in names:
        v = _get(row, header, name)
        if v is not None:
            return v
    return None


def _stock_row(code: str, name: Any, board: Any, updated_at: str) -> dict[str, Any]:
    return {"code": code, "name": name, "board": board, "list_date": None,
            "is_st": None, "total_mv": None, "circ_mv": None, "updated_at": updated_at}


def _import_pool_sheet(conn: sqlite3.Connection, ws: Any, status: str, run_date: str) -> int:
    """候选池 sheet → stock 补档案 + pool_entry（event_id NULL）。返回新插入行数。"""
    inserted = 0
    for header, row in _iter_sheet_rows(ws):
        code = _norm_code(_get(row, header, "代码"))
        if not code:
            continue
        conn.execute(_UPSERT_STOCK, _stock_row(code, _get(row, header, "名称"),
                                               _get(row, header, "板块"), run_date))
        exists = conn.execute(
            "SELECT 1 FROM pool_entry WHERE code = ? AND event_id IS NULL", (code,)
        ).fetchone()
        if exists:
            continue
        grade = _grade_for(_get(row, header, "事件大类"))
        parts = [v for v in (_get(row, header, "大致情况"), _get(row, header, "后续催化"))
                 if v is not None]
        note = "；".join(str(p) for p in parts) or None
        conn.execute(
            "INSERT INTO pool_entry (code, status, grade, entered_at, note, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (code, status, grade, run_date, note, run_date),
        )
        inserted += 1
    return inserted


def _import_flow_sheet(conn: sqlite3.Connection, ws: Any, run_date: str) -> tuple[int, int, int]:
    """流程明细 sheet → 冷启动事件 + 公告 upsert + flow_node。

    返回 (新事件数, 公告净新增, 节点净新增)。
    """
    rows: list[dict[str, Any]] = []
    for header, row in _iter_sheet_rows(ws):
        code = _norm_code(_get(row, header, "代码"))
        major = _get(row, header, "事件大类")
        title = _get(row, header, "公告标题")
        node_date = _norm_date(_get(row, header, "公告日期"))
        stage = _get(row, header, "流程阶段")
        rows.append({
            "code": code,
            "name": _get(row, header, "名称"),
            "board": _get(row, header, "板块"),
            "major": str(major) if major is not None else None,
            "date": node_date,
            "stage": str(stage) if stage is not None else None,
            "title": str(title) if title is not None else None,
            "pdf": _get(row, header, "PDF链接"),
        })

    # 按 (code, 事件大类) 分组
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        if not r["code"] or not r["major"]:
            continue
        groups.setdefault((r["code"], r["major"]), []).append(r)

    # 1) stock 补档案（按 code 去重）
    seen: set[str] = set()
    for r in rows:
        if r["code"] and r["code"] not in seen:
            seen.add(r["code"])
            conn.execute(_UPSERT_STOCK, _stock_row(r["code"], r["name"], r["board"], run_date))

    # 2) 公告 upsert
    ann_rows = [
        {"code": r["code"], "pub_date": r["date"], "title": r["title"], "pdf_url": r["pdf"],
         "category": None, "source": "v6", "fetched_at": _now_iso()}
        for r in rows
        if r["code"] and r["date"] and r["title"]
    ]
    ann_before = conn.execute("SELECT COUNT(*) FROM announcement").fetchone()[0]
    if ann_rows:
        conn.executemany(_UPSERT_ANNOUNCEMENT, ann_rows)
    ann_delta = conn.execute("SELECT COUNT(*) FROM announcement").fetchone()[0] - ann_before

    # 3) 冷启动事件（v6: 前缀，存在即复用）
    now = _now_iso()
    events_new = 0
    event_ids: dict[tuple[str, str], int] = {}
    for key, items in groups.items():
        code, major = key
        event_type = f"v6:{major}"
        dated = [it for it in items if it["date"]]
        start_date = min(it["date"] for it in dated) if dated else run_date
        latest = max(dated, key=lambda it: it["date"]) if dated else (items[-1] if items else None)
        status = "终止" if latest and _is_terminal(latest["stage"]) else "进行中"
        existing = conn.execute(
            "SELECT id FROM event WHERE code = ? AND event_type = ?", (code, event_type)
        ).fetchone()
        if existing:
            event_ids[key] = int(existing["id"])
            continue
        cur = conn.execute(
            "INSERT INTO event (code, event_type, grade, start_date, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code, event_type, _grade_for(major), start_date, status, now, now),
        )
        event_ids[key] = int(cur.lastrowid)
        events_new += 1

    # 4) flow_node（UNIQUE(event_id, node_type, node_date) 天然幂等）
    node_before = conn.execute("SELECT COUNT(*) FROM flow_node").fetchone()[0]
    for r in rows:
        if not r["code"] or not r["major"] or not r["date"] or not r["stage"]:
            continue
        event_id = event_ids[(r["code"], r["major"])]
        ann_id = None
        if r["title"]:
            row = conn.execute(
                "SELECT id FROM announcement WHERE code = ? AND pub_date = ? AND title = ?",
                (r["code"], r["date"], r["title"]),
            ).fetchone()
            ann_id = int(row["id"]) if row else None
        conn.execute(
            "INSERT OR IGNORE INTO flow_node"
            " (event_id, node_type, node_date, announcement_id, remark, created_at)"
            " VALUES (?, ?, ?, ?, NULL, ?)",
            (event_id, r["stage"], r["date"], ann_id, now),
        )
    node_delta = conn.execute("SELECT COUNT(*) FROM flow_node").fetchone()[0] - node_before
    return events_new, ann_delta, node_delta


_SEQ_TOKEN = re.compile(r"^(\d{4}-\d{2}-\d{2})[:：](.+)$")


def _parse_pct_series(text: Any) -> dict[str, float]:
    """解析 v6 户数序列文本："2025-10-20:+4.81%；2025-11-28:-7.46%"→{date: pct}。

    兼容全角/半角分号冒号与全角百分号；"基准"记 0.0；无法解析的 token 跳过。
    """
    out: dict[str, float] = {}
    if not isinstance(text, str):
        return out
    for token in re.split(r"[；;]\s*", text):
        m = _SEQ_TOKEN.match(token.strip())
        if not m:
            continue
        d, raw = m.group(1), m.group(2).strip()
        if raw in ("基准", "基准值"):
            out[d] = 0.0
            continue
        num = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*[%％]?$", raw)
        if num:
            out[d] = float(num.group(1))
    return out


def _import_holder_pct_sheet(conn: sqlite3.Connection, ws: Any) -> int:
    """一年内_波动户数标注 sheet → holder_pct（v6 复盘资产，价格列弃用）。净新增。"""
    before = conn.execute("SELECT COUNT(*) FROM holder_pct").fetchone()[0]
    now = _now_iso()
    for header, row in _iter_sheet_rows(ws):
        code = _norm_code(_get(row, header, "代码"))
        if not code:
            continue
        chain = _parse_pct_series(_get(row, header, "户数环比变化序列%"))
        base = _parse_pct_series(_get(row, header, "户数相对基准序列%"))
        anchor = _norm_date(_get_any(row, header, "户数起算披露日", "资本运作日"))
        for d in set(chain) | set(base):
            conn.execute(
                "INSERT OR IGNORE INTO holder_pct"
                " (code, disclose_date, chain_pct, baseline_pct, anchor_date, source, imported_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (code, d, chain.get(d), base.get(d), anchor, "v6", now),
            )
    return conn.execute("SELECT COUNT(*) FROM holder_pct").fetchone()[0] - before


def _import_rejected_sheet(conn: sqlite3.Connection, ws: Any) -> int:
    """剔除记录 sheet → rejected_record（INSERT OR IGNORE）。返回净新增。"""
    before = conn.execute("SELECT COUNT(*) FROM rejected_record").fetchone()[0]
    now = _now_iso()
    for header, row in _iter_sheet_rows(ws):
        code = _norm_code(_get(row, header, "代码"))
        if not code:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO rejected_record (code, pub_date, title, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (code,
             _norm_date(_get_any(row, header, "最新公告日期", "公告日期", "日期")),
             _get_any(row, header, "最新公告标题", "公告标题", "标题"),
             _get_any(row, header, "剔除原因", "原因"),
             now),
        )
    return conn.execute("SELECT COUNT(*) FROM rejected_record").fetchone()[0] - before


def import_v6_workbook(conn: sqlite3.Connection, path: str | Path, *, dry_run: bool = False) -> dict:
    """导入 v6 工作簿。返回 stats {"core_pool","watch_pool","flow_nodes",
    "announcements","rejected","events"}；dry_run 时解析统计后回滚，不落库。"""
    run_date = date.today().isoformat()
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        core_ws = _find_sheet(wb, "核心候选_流程", ("核心候选", "流程"))
        watch_ws = _find_sheet(wb, "普通定增观察池_流程", ("观察池", "流程"))
        flow_ws = _find_sheet(wb, "流程明细", ("流程明细",))
        rej_ws = _find_sheet(wb, "剔除记录", ("剔除记录",))
        hp_ws = _find_sheet(wb, "一年内_波动户数标注", ("波动户数",))

        core_pool = _import_pool_sheet(conn, core_ws, "重点跟踪", run_date) if core_ws else 0
        watch_pool = _import_pool_sheet(conn, watch_ws, "观察中", run_date) if watch_ws else 0
        events, ann, nodes = _import_flow_sheet(conn, flow_ws, run_date) if flow_ws else (0, 0, 0)
        rejected = _import_rejected_sheet(conn, rej_ws) if rej_ws else 0
        holder_pct = _import_holder_pct_sheet(conn, hp_ws) if hp_ws else 0
        stats = {
            "core_pool": core_pool,
            "watch_pool": watch_pool,
            "flow_nodes": nodes,
            "announcements": ann,
            "rejected": rejected,
            "events": events,
            "holder_pct": holder_pct,
        }
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return stats
    finally:
        wb.close()
