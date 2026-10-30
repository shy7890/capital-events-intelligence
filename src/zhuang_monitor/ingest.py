"""入库层：自然键 upsert（幂等，PRD §7）+ scan_run 记账 + config 水位线。

重复执行只更新不重复；数值列用 COALESCE 保护——新数据缺失时保留旧值。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

_UPSERT_KLINE = """
INSERT INTO kline_daily (code, trade_date, open, high, low, close, volume, amount,
                         turnover, adj_factor, source)
VALUES (:code, :trade_date, :open, :high, :low, :close, :volume, :amount,
        :turnover, :adj_factor, :source)
ON CONFLICT(code, trade_date) DO UPDATE SET
    open = COALESCE(excluded.open, open),
    high = COALESCE(excluded.high, high),
    low = COALESCE(excluded.low, low),
    close = COALESCE(excluded.close, close),
    volume = COALESCE(excluded.volume, volume),
    amount = COALESCE(excluded.amount, amount),
    turnover = COALESCE(excluded.turnover, turnover),
    adj_factor = COALESCE(excluded.adj_factor, adj_factor),
    source = COALESCE(excluded.source, source)
"""

_UPSERT_HOLDER = """
INSERT INTO holder_num (code, disclose_date, holder_num, pct_change, avg_hold, total_mv, source)
VALUES (:code, :disclose_date, :holder_num, :pct_change, :avg_hold, :total_mv, :source)
ON CONFLICT(code, disclose_date) DO UPDATE SET
    holder_num = COALESCE(excluded.holder_num, holder_num),
    pct_change = COALESCE(excluded.pct_change, pct_change),
    avg_hold = COALESCE(excluded.avg_hold, avg_hold),
    total_mv = COALESCE(excluded.total_mv, total_mv),
    source = COALESCE(excluded.source, source)
"""

_UPSERT_LONGHU = """
INSERT INTO longhu (code, trade_date, reason, net_buy, source)
VALUES (:code, :trade_date, :reason, :net_buy, :source)
ON CONFLICT(code, trade_date, reason) DO UPDATE SET
    net_buy = COALESCE(excluded.net_buy, net_buy),
    source = COALESCE(excluded.source, source)
"""

_UPSERT_ANNOUNCEMENT = """
INSERT INTO announcement (code, pub_date, title, pdf_url, category, source, fetched_at)
VALUES (:code, :pub_date, :title, :pdf_url, :category, :source, :fetched_at)
ON CONFLICT(code, pub_date, title) DO UPDATE SET
    pdf_url = COALESCE(excluded.pdf_url, pdf_url),
    category = COALESCE(excluded.category, category)
"""

_UPSERT_STOCK = """
INSERT INTO stock (code, name, board, list_date, is_st, total_mv, circ_mv, updated_at)
VALUES (:code,
        COALESCE(:name, (SELECT name FROM stock WHERE code=:code), ''),
        COALESCE(:board, (SELECT board FROM stock WHERE code=:code)),
        COALESCE(:list_date, (SELECT list_date FROM stock WHERE code=:code)),
        COALESCE(:is_st, (SELECT is_st FROM stock WHERE code=:code), 0),
        COALESCE(:total_mv, (SELECT total_mv FROM stock WHERE code=:code)),
        COALESCE(:circ_mv, (SELECT circ_mv FROM stock WHERE code=:code)),
        :updated_at)
ON CONFLICT(code) DO UPDATE SET
    name = COALESCE(excluded.name, name),
    board = COALESCE(excluded.board, board),
    list_date = COALESCE(excluded.list_date, list_date),
    is_st = COALESCE(excluded.is_st, is_st),
    total_mv = COALESCE(excluded.total_mv, total_mv),
    circ_mv = COALESCE(excluded.circ_mv, circ_mv),
    updated_at = excluded.updated_at
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _upsert(conn: sqlite3.Connection, sql: str, table: str, rows: list[dict]) -> int:
    """执行 upsert，返回净新增行数（幂等重跑为 0）。"""
    if not rows:
        return 0
    before = _table_count(conn, table)
    conn.executemany(sql, rows)
    conn.commit()
    return _table_count(conn, table) - before


def upsert_kline(conn: sqlite3.Connection, rows: list[dict]) -> int:
    return _upsert(conn, _UPSERT_KLINE, "kline_daily", rows)


def upsert_holder_num(conn: sqlite3.Connection, rows: list[dict]) -> int:
    return _upsert(conn, _UPSERT_HOLDER, "holder_num", rows)


def upsert_longhu(conn: sqlite3.Connection, rows: list[dict]) -> int:
    return _upsert(conn, _UPSERT_LONGHU, "longhu", rows)


def upsert_announcement(conn: sqlite3.Connection, rows: list[dict]) -> int:
    stamped = [dict(r, fetched_at=r.get("fetched_at") or _now_iso()) for r in rows]
    return _upsert(conn, _UPSERT_ANNOUNCEMENT, "announcement", stamped)


def upsert_stock(conn: sqlite3.Connection, rows: list[dict]) -> int:
    stamped = [dict(r, updated_at=r.get("updated_at") or _now_iso()) for r in rows]
    return _upsert(conn, _UPSERT_STOCK, "stock", stamped)


def ensure_market_refresh(conn: sqlite3.Connection, run_date: str, codes: list[str]) -> None:
    """建立目标日刷新主表和代码项；重复加入代码不会改变已有状态。"""
    now = _now_iso()
    conn.execute(
        "INSERT INTO market_refresh(run_date,status,started_at,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(run_date) DO UPDATE SET updated_at=excluded.updated_at",
        (run_date, "partial", now, now),
    )
    for code in dict.fromkeys(str(c) for c in codes):
        conn.execute(
            "INSERT INTO market_refresh_item(run_date,code,kind,status,updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(run_date,code,kind) DO NOTHING",
            (run_date, code, "kline", "pending", now),
        )
    conn.commit()


def claim_market_refresh(
    conn: sqlite3.Connection,
    run_date: str,
    codes: list[str],
    owner_token: str,
    lease_until: str,
) -> bool:
    """以短事务领取刷新租约；已有未过期租约时不允许第二个批任务抓取。"""
    ensure_market_refresh(conn, run_date, codes)
    now = _now_iso()
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT owner_token, lease_until FROM market_refresh WHERE run_date=?",
        (run_date,),
    ).fetchone()
    active = bool(row and row["owner_token"] and row["lease_until"] and row["lease_until"] > now)
    if active and row["owner_token"] != owner_token:
        conn.rollback()
        return False
    conn.execute(
        "UPDATE market_refresh SET status='partial', owner_token=?, lease_until=?, "
        "updated_at=? WHERE run_date=?",
        (owner_token, lease_until, now, run_date),
    )
    conn.commit()
    return True


def touch_market_refresh(
    conn: sqlite3.Connection, run_date: str, owner_token: str, lease_until: str
) -> None:
    conn.execute(
        "UPDATE market_refresh SET lease_until=?, updated_at=? "
        "WHERE run_date=? AND owner_token=?",
        (lease_until, _now_iso(), run_date, owner_token),
    )
    conn.commit()


def mark_market_refresh_item(
    conn: sqlite3.Connection,
    run_date: str,
    code: str,
    status: str,
    *,
    rows_count: int = 0,
    error: str | None = None,
    increment_attempt: bool = False,
) -> None:
    """更新单个刷新项；状态更新立即提交，进程中断后可从最后一项继续。"""
    now = _now_iso()
    if increment_attempt:
        conn.execute(
            "UPDATE market_refresh_item SET status='running', attempts=attempts+1, "
            "last_error=NULL, updated_at=? WHERE run_date=? AND code=? AND kind='kline'",
            (now, run_date, code),
        )
    else:
        conn.execute(
            "UPDATE market_refresh_item SET status=?, rows_count=?, last_error=?, updated_at=? "
            "WHERE run_date=? AND code=? AND kind='kline'",
            (status, int(rows_count), error, now, run_date, code),
        )
    conn.commit()


def market_refresh_counts(conn: sqlite3.Connection, run_date: str) -> dict[str, Any]:
    """返回刷新状态的安全聚合，不包含原始异常堆栈。"""
    row = conn.execute(
        "SELECT status,kline_total,kline_success,kline_skipped,kline_failed,longhu_status,"
        "blocking_reason,started_at,updated_at,completed_at "
        "FROM market_refresh WHERE run_date=?",
        (run_date,),
    ).fetchone()
    if row is None:
        return {
            "run_date": run_date, "status": "unknown", "kline_total": 0,
            "kline_success": 0, "kline_skipped": 0, "kline_failed": 0,
            "kline_pending": 0, "longhu_status": "unknown", "blocking": False,
            "blocking_reason": None,
        }
    counts = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success, "
        "SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) AS skipped, "
        "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
        "SUM(CASE WHEN status IN ('pending','running') THEN 1 ELSE 0 END) AS pending "
        "FROM market_refresh_item WHERE run_date=? AND kind='kline'",
        (run_date,),
    ).fetchone()
    total = int(counts["total"] or 0)
    success = int(counts["success"] or 0)
    skipped = int(counts["skipped"] or 0)
    failed = int(counts["failed"] or 0)
    pending = int(counts["pending"] or 0)
    reason = row["blocking_reason"]
    if failed:
        reason = f"有 {failed} 只 K 线刷新失败，需稍后重试"
    elif pending:
        reason = f"有 {pending} 只 K 线尚未完成"
    elif row["longhu_status"] not in ("green", "known_empty", "skipped"):
        reason = "龙虎榜尚未完成"
    return {
        "run_date": run_date,
        "status": row["status"],
        "kline_total": total,
        "kline_success": success,
        "kline_skipped": skipped,
        "kline_failed": failed,
        "kline_pending": pending,
        "longhu_status": row["longhu_status"],
        "blocking": bool(failed or pending or row["longhu_status"] not in ("green", "known_empty", "skipped")),
        "blocking_reason": reason,
    }


def update_market_refresh_summary(
    conn: sqlite3.Connection,
    run_date: str,
    *,
    owner_token: str | None = None,
    lease_until: str | None = None,
    release: bool = False,
) -> dict[str, Any]:
    """按明细重算主表统计；可选释放租约。"""
    counts = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success, "
        "SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) AS skipped, "
        "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
        "SUM(CASE WHEN status IN ('pending','running') THEN 1 ELSE 0 END) AS pending "
        "FROM market_refresh_item WHERE run_date=? AND kind='kline'",
        (run_date,),
    ).fetchone()
    values = {k: int(counts[k] or 0) for k in ("total", "success", "skipped", "failed", "pending")}
    complete = values["failed"] == 0 and values["pending"] == 0
    status = "complete" if complete else "partial"
    reason = None
    if values["failed"]:
        reason = f"有 {values['failed']} 只 K 线刷新失败，需稍后重试"
    elif values["pending"]:
        reason = f"有 {values['pending']} 只 K 线尚未完成"
    now = _now_iso()
    updates = [
        "status=?", "kline_total=?", "kline_success=?", "kline_skipped=?",
        "kline_failed=?", "blocking_reason=?", "updated_at=?",
        "completed_at=?",
    ]
    args: list[Any] = [status, values["total"], values["success"], values["skipped"],
                       values["failed"], reason, now, now if complete else None]
    if release:
        updates += ["owner_token=NULL", "lease_until=NULL"]
    elif owner_token is not None and lease_until is not None:
        updates += ["owner_token=?", "lease_until=?"]
        args += [owner_token, lease_until]
    args.append(run_date)
    conn.execute(f"UPDATE market_refresh SET {', '.join(updates)} WHERE run_date=?", args)
    conn.commit()
    return market_refresh_counts(conn, run_date)


def set_market_refresh_longhu(
    conn: sqlite3.Connection, run_date: str, status: str, reason: str | None = None
) -> None:
    conn.execute(
        "UPDATE market_refresh SET longhu_status=?, blocking_reason=COALESCE(?, blocking_reason), "
        "updated_at=? WHERE run_date=?",
        (status, reason, _now_iso(), run_date),
    )
    conn.commit()


def record_scan_run(
    conn: sqlite3.Connection,
    run_date: str,
    stage: str,
    status: str,
    stats: dict[str, Any] | None = None,
    errors: str | None = None,
) -> int:
    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO scan_run (run_date, stage, status, stats_json, errors, started_at, finished_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_date, stage, status, json.dumps(stats, ensure_ascii=False) if stats is not None else None,
         errors, now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def red_run_count(conn: sqlite3.Connection, run_date: str | None = None) -> int:
    if run_date:
        row = conn.execute(
            "SELECT COUNT(*) FROM scan_run WHERE status='red' AND run_date=?", (run_date,)
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM scan_run WHERE status='red'").fetchone()
    return int(row[0])


def set_config(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), _now_iso()),
    )
    conn.commit()


def get_config(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def announcement_window(conn: sqlite3.Connection, code: str, start: str, end: str) -> list[sqlite3.Row]:
    """按代码+日期区间读取公告（供对账与 M2 状态机使用）。"""
    return conn.execute(
        "SELECT * FROM announcement WHERE code=? AND pub_date BETWEEN ? AND ? ORDER BY pub_date",
        (code, start, end),
    ).fetchall()
