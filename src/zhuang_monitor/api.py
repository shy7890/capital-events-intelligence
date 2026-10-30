"""M3 REST API 层（PRD §4）：池/事件/K线/股东户数/笔记/通知/日报，只读为主 + 少量写入。

连接管理：每个端点 `with contextlib.closing(db.connect(DB_PATH))`，不引入依赖注入框架。
DB_PATH 为模块级注入点（默认 None → settings.db_path；测试用 monkeypatch 指向临时库）。
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from . import db, ingest, material, pipeline

router = APIRouter(prefix="/api")

DB_PATH: str | Path | None = None

_LIMIT = Query(50, ge=1, le=500)  # 列表端点共用：默认 50，上限 500

# 事件属性归类（用户筛选用）：LIKE 从左到右命中任一即计入该属性。
# 匹配前缀用于吞掉 v6: 脏值（如 "v6:重大资产重组"）。
_EVENT_KIND_MATCH: dict[str, list[str]] = {
    "重组": ["%重组%", "%资产置换%", "%吸收合并%", "%收购资产%"],
    "定增": ["%定增%", "%发行股份购买%", "%非公开发行%", "%向特定对象发行%"],
    "控制权": ["%控制权%", "%实际控制人%", "%协议转让%", "%表决权%", "%要约收购%"],
}


def _conn() -> contextlib.closing[sqlite3.Connection]:
    """统一的只读连接包装（db.connect 已带 WAL/busy_timeout）。"""
    return contextlib.closing(db.connect(DB_PATH))


def _parse_json(raw: Any, default: Any) -> Any:
    """exit_signals/note 等 JSON 文本字段可能存坏数据：解析失败一律回退默认值，不抛异常。"""
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _json_list(raw: Any) -> list:
    v = _parse_json(raw, [])
    return v if isinstance(v, list) else []


def _json_str(raw: Any) -> str:
    v = _parse_json(raw, "")
    return v if isinstance(v, str) else ""


def _pool_row(r: sqlite3.Row) -> dict:
    return {
        "code": r["code"],
        "name": r["name"],
        "board": r["board"],
        "circ_mv": r["circ_mv"],
        "grade": r["grade"],
        "status": r["status"],
        "event_type": r["event_type"],
        "event_status": r["event_status"],
        "start_date": r["start_date"],
        "resonance_count": r["resonance_count"],
        "exit_signals": _json_list(r["exit_signals"]),
        "note": _json_str(r["note"]),
        "entered_at": r["entered_at"],
    }


@router.get("/pool")
def get_pool(
    status: str | None = None,
    grade: str | None = None,
    q: str | None = None,
    mv_max: float | None = Query(None, ge=1, le=10000,
                                 description="流通市值上限（亿元）；超限移出主列表"),
    include_archived: int = Query(0, ge=0, le=1,
                                  description="1=含已归档；默认 0 隐藏（退出信号/终态/超市值已归档票）"),
    event_kind: str | None = Query(None, description="事件属性：重组/定增/控制权"),
    hide_reviewed: int = Query(0, ge=0, le=1,
                               description="1=隐藏已评级（已看过）的股票"),
    reviewed: Literal["reviewed", "unreviewed"] | None = Query(
        None, description="看过状态：reviewed=已看过，unreviewed=未看过"
    ),
    limit: int = _LIMIT,
    offset: int = Query(0, ge=0),
) -> dict:
    """候选池列表：pool_entry LEFT JOIN event/stock，支持状态/等级/市值过滤与代码/名称搜索。

    默认排除已归档（v1.1 自动归档）与市值超限票——主列表只留在追踪的活跃标的。
    event_kind 按事件类型归类过滤；reviewed 过滤用户已看过/未看过股票。
    hide_reviewed 保留兼容旧客户端，等同于 reviewed=unreviewed。
    """
    where: list[str] = []
    params: list[Any] = []
    if not include_archived:
        where.append("pe.status <> '归档'")
    if status:
        where.append("pe.status = ?")
        params.append(status)
    if grade:
        where.append("pe.grade = ?")
        params.append(grade)
    if q:
        where.append("(pe.code LIKE ? OR s.name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if event_kind:
        kinds = _EVENT_KIND_MATCH.get(event_kind)
        if kinds is None:
            raise HTTPException(status_code=400, detail=f"未知事件属性：{event_kind}")
        parts = " OR ".join(["e.event_type LIKE ?"] * len(kinds))
        where.append(f"({parts})")
        params += kinds
    if reviewed == "reviewed":
        cond_rev = "pe.code IN (SELECT code FROM stock_rating)"
        where.append(cond_rev)
    elif reviewed == "unreviewed" or hide_reviewed:
        cond_rev = "pe.code NOT IN (SELECT code FROM stock_rating)"
        where.append(cond_rev)
    cond = f" WHERE {' AND '.join(where)}" if where else ""
    from_clause = (
        "FROM pool_entry pe "
        "LEFT JOIN event e ON pe.event_id = e.id "
        "LEFT JOIN stock s ON pe.code = s.code"
    )
    # 市值上限在 JOIN 后过滤（circ_mv 可能缺失，NULL 不视为超限，保留）；已校验为数字，安全内插
    join_where = cond
    if mv_max:
        cap = float(mv_max)
        cond_mv = f"s.circ_mv IS NULL OR s.circ_mv <= {cap}"
        join_where = f"{cond} AND ({cond_mv})" if cond else f" WHERE ({cond_mv})"
    with _conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n {from_clause}{join_where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            "SELECT pe.code, s.name, s.board, s.circ_mv, pe.grade, pe.status, "
            "e.event_type, e.status AS event_status, e.start_date, "
            "pe.resonance_count, pe.exit_signals, pe.note, pe.entered_at "
            f"{from_clause}{join_where} "
            "ORDER BY CASE pe.grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 "
            "WHEN 'B' THEN 2 ELSE 3 END, pe.resonance_count DESC, pe.entered_at DESC, "
            "pe.id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {"total": total, "rows": [_pool_row(r) for r in rows]}


@router.get("/events/{code}")
def get_events(code: str) -> dict:
    """单只股票的事件清单及其流程节点（节点标题经 announcement 关联带出）。"""
    with _conn() as conn:
        events = conn.execute(
            "SELECT id, event_type, grade, start_date, status "
            "FROM event WHERE code = ? ORDER BY start_date ASC, id ASC",
            (code,),
        ).fetchall()
        out: list[dict] = []
        for ev in events:
            nodes = conn.execute(
                "SELECT fn.node_type, fn.node_date, a.title, a.pdf_url "
                "FROM flow_node fn LEFT JOIN announcement a ON fn.announcement_id = a.id "
                "WHERE fn.event_id = ? ORDER BY fn.node_date ASC, fn.id ASC",
                (ev["id"],),
            ).fetchall()
            out.append(
                {
                    "id": ev["id"],
                    "event_type": ev["event_type"],
                    "grade": ev["grade"],
                    "start_date": ev["start_date"],
                    "status": ev["status"],
                    "nodes": [dict(n) for n in nodes],
                }
            )
    return {"events": out}


@router.get("/kline/{code}")
def get_kline(code: str, start: str | None = None, end: str | None = None) -> dict:
    """日 K 线（原始 OHLCV）+ 后复权收盘价 close_qfq = close × adj_factor ÷ 最新非空因子。"""
    where = ["code = ?"]
    params: list[Any] = [code]
    if start:
        where.append("trade_date >= ?")
        params.append(start)
    if end:
        where.append("trade_date <= ?")
        params.append(end)
    cond = " AND ".join(where)
    with _conn() as conn:
        stock = conn.execute("SELECT name FROM stock WHERE code = ?", (code,)).fetchone()
        base_row = conn.execute(
            "SELECT adj_factor FROM kline_daily "
            "WHERE code = ? AND adj_factor IS NOT NULL ORDER BY trade_date DESC LIMIT 1",
            (code,),
        ).fetchone()
        rows = conn.execute(
            "SELECT trade_date, open, high, low, close, volume, amount, turnover, adj_factor "
            f"FROM kline_daily WHERE {cond} ORDER BY trade_date ASC",
            params,
        ).fetchall()
    base = base_row["adj_factor"] if base_row else None
    out: list[dict] = []
    for r in rows:
        item = dict(r)
        item["close_qfq"] = _close_qfq(r["close"], r["adj_factor"], base)
        del item["adj_factor"]
        out.append(item)
    return {"code": code, "name": stock["name"] if stock else None, "rows": out}


def _close_qfq(close: float | None, factor: float | None, base: float | None) -> float | None:
    """复权因子缺失（或基准缺失/为 0）时退化为原始收盘价，保证不抛异常。"""
    if close is None:
        return None
    if factor is None or base is None or base == 0:
        return close
    return round(close * factor / base, 4)


@router.get("/holder/{code}")
def get_holder(code: str) -> dict:
    """股东户数序列（按披露日升序）。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT disclose_date, holder_num, pct_change FROM holder_num "
            "WHERE code = ? ORDER BY disclose_date ASC",
            (code,),
        ).fetchall()
    return {"rows": [dict(r) for r in rows]}


@router.get("/holder-pct/{code}")
def get_holder_pct(code: str) -> dict:
    """v6 复盘资产：事件锚定户数波动序列（披露日升序；baseline_pct 事件日=0）。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT disclose_date, chain_pct, baseline_pct, anchor_date, source "
            "FROM holder_pct WHERE code = ? ORDER BY disclose_date ASC",
            (code,),
        ).fetchall()
    return {"code": code, "rows": [dict(r) for r in rows]}


class NoteCreate(BaseModel):
    """POST /api/notes 请求体：code/content 必填，content 不允许空白。"""

    code: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: str | None = None
    trade_date: str | None = None

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content 不能为空")
        return v


class WatchlistCreate(BaseModel):
    """我的跟踪名单新增或恢复请求。"""

    code: str = Field(min_length=1)
    note: str | None = Field(None, max_length=500)

    @field_validator("code")
    @classmethod
    def _code_trim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("code 不能为空")
        return v

    @field_validator("note")
    @classmethod
    def _note_trim(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class WatchlistUpdate(BaseModel):
    """我的跟踪名单备注和状态更新请求。"""

    note: str | None = Field(None, max_length=500)
    status: Literal["active", "history"] | None = None

    @field_validator("note")
    @classmethod
    def _note_trim(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class RatingCreate(BaseModel):
    """人工评级（重要程度）：high=高（自动进我的跟踪）、medium=中、low=低（中低自动进待观察）。"""

    rating: Literal["high", "medium", "low"]


@router.get("/ratings")
def get_ratings(limit: int = _LIMIT, offset: int = Query(0, ge=0)) -> dict:
    """全部人工评级（已看过名单）；按评级置顶、时间倒序。"""
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM stock_rating").fetchone()["n"]
        rows = conn.execute(
            "SELECT r.code, r.rating, r.reviewed_at, r.updated_at, r.note, "
            "s.name, s.board "
            "FROM stock_rating r LEFT JOIN stock s ON r.code = s.code "
            "ORDER BY CASE r.rating WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
            "r.updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return {"total": total, "rows": [dict(r) for r in rows]}


@router.get("/rating/{code}")
def get_rating(code: str) -> dict:
    """单只股票评级（未评级返回 rating=None）。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT code, rating, reviewed_at, updated_at, note "
            "FROM stock_rating WHERE code=?", (code,)
        ).fetchone()
    return dict(row) if row else {"code": code, "rating": None}


@router.put("/rating/{code}")
def set_rating(code: str, payload: RatingCreate, response: Response) -> dict:
    """评级一只股票；rated_at 首次评级时打点。高评级自动加入“我的跟踪”。"""
    now = datetime.now().isoformat(timespec="seconds")
    result: dict | None = None
    with _conn() as conn:
        prev = conn.execute(
            "SELECT reviewed_at FROM stock_rating WHERE code=?", (code,)
        ).fetchone()
        reviewed = prev["reviewed_at"] if prev else now
        conn.execute(
            "INSERT INTO stock_rating(code, rating, reviewed_at, updated_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET rating=excluded.rating, "
            "updated_at=excluded.updated_at",
            (code, payload.rating, reviewed, now),
        )
        # 高评级 → 自动进“我的跟踪”（active）
        if payload.rating == "high":
            wl = conn.execute(
                "SELECT * FROM watchlist_entry WHERE code=?", (code,)
            ).fetchone()
            if wl is None:
                conn.execute(
                    "INSERT INTO watchlist_entry(code,status,added_at,updated_at) "
                    "VALUES (?,'active',?,?)", (code, now, now)
                )
            elif wl["status"] != "active":
                conn.execute(
                    "UPDATE watchlist_entry SET status='active', updated_at=?, "
                    "removed_at=NULL WHERE code=?", (now, code)
                )
        conn.commit()
        row = conn.execute(
            "SELECT r.code, r.rating, r.reviewed_at, r.updated_at, r.note, "
            "s.name, s.board "
            "FROM stock_rating r LEFT JOIN stock s ON r.code = s.code WHERE r.code=?",
            (code,),
        ).fetchone()
        result = dict(row)
    response.headers["X-New-Review"] = "1" if not prev else "0"
    return result


@router.delete("/rating/{code}")
def clear_rating(code: str) -> dict:
    """取消评级（视为重新看过）；不影响已自动加入的跟踪名单。"""
    with _conn() as conn:
        conn.execute("DELETE FROM stock_rating WHERE code=?", (code,))
        conn.commit()
    return {"code": code, "rating": None}


@router.get("/watchlist")
def get_watchlist(
    include_history: int = Query(0, ge=0, le=1),
    limit: int = _LIMIT,
    offset: int = Query(0, ge=0),
) -> dict:
    """用户独立跟踪名单；默认只看 active，history 记录从库内保留。"""
    where = "" if include_history else " WHERE status='active'"
    with _conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM watchlist_entry{where}"
        ).fetchone()["n"]
        entries = conn.execute(
            "SELECT * FROM watchlist_entry" + where +
            " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, updated_at DESC "
            "LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        rows = [pipeline.watchlist_snapshot(
            conn, row["code"], entry=row, today=date.today().isoformat()
        ) for row in entries]
    return {"total": total, "rows": rows}


@router.post("/watchlist")
def add_watchlist(payload: WatchlistCreate, response: Response) -> dict:
    """加入或恢复跟踪；重复调用幂等，备注只在请求明确提供时覆盖。"""
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        if conn.execute("SELECT 1 FROM stock WHERE code=?", (payload.code,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="股票代码不存在于股票档案")
        old = conn.execute(
            "SELECT * FROM watchlist_entry WHERE code=?", (payload.code,)
        ).fetchone()
        if old is None:
            conn.execute(
                "INSERT INTO watchlist_entry(code,status,note,added_at,updated_at,removed_at) "
                "VALUES(?,?,?,?,?,NULL)",
                (payload.code, "active", payload.note, now, now),
            )
            response.status_code = 201
        else:
            note = payload.note if "note" in payload.model_fields_set else old["note"]
            conn.execute(
                "UPDATE watchlist_entry SET status='active', note=?, updated_at=?, removed_at=NULL "
                "WHERE code=?", (note, now, payload.code)
            )
            response.status_code = 200
        conn.commit()
        entry = conn.execute(
            "SELECT * FROM watchlist_entry WHERE code=?", (payload.code,)
        ).fetchone()
        return pipeline.watchlist_snapshot(
            conn, payload.code, entry=entry, today=date.today().isoformat()
        )


@router.patch("/watchlist/{code}")
def update_watchlist(code: str, payload: WatchlistUpdate) -> dict:
    """更新备注或把跟踪条目标记为 active/history；不物理删除。"""
    code = code.strip()
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        old = conn.execute(
            "SELECT * FROM watchlist_entry WHERE code=?", (code,)
        ).fetchone()
        if old is None:
            raise HTTPException(status_code=404, detail="该股票不在我的跟踪名单")
        status = payload.status or old["status"]
        note = payload.note if "note" in payload.model_fields_set else old["note"]
        removed_at = now if status == "history" else None
        conn.execute(
            "UPDATE watchlist_entry SET status=?, note=?, updated_at=?, removed_at=? WHERE code=?",
            (status, note, now, removed_at, code),
        )
        conn.commit()
        entry = conn.execute(
            "SELECT * FROM watchlist_entry WHERE code=?", (code,)
        ).fetchone()
        return pipeline.watchlist_snapshot(
            conn, code, entry=entry, today=date.today().isoformat()
        )


@router.delete("/watchlist/{code}")
def remove_watchlist(code: str) -> dict:
    """移入 history；重复移除保持幂等，记录本身永不物理删除。"""
    code = code.strip()
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        old = conn.execute(
            "SELECT * FROM watchlist_entry WHERE code=?", (code,)
        ).fetchone()
        if old is None:
            raise HTTPException(status_code=404, detail="该股票不在我的跟踪名单")
        if old["status"] == "active":
            conn.execute(
                "UPDATE watchlist_entry SET status='history', updated_at=?, removed_at=? WHERE code=?",
                (now, now, code),
            )
            conn.commit()
        entry = conn.execute(
            "SELECT * FROM watchlist_entry WHERE code=?", (code,)
        ).fetchone()
        return pipeline.watchlist_snapshot(
            conn, code, entry=entry, today=date.today().isoformat()
        )


@router.get("/notes/{code}")
def get_notes(code: str) -> dict:
    """个股盯盘笔记（新→旧）。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, code, trade_date, content, tags, created_at FROM watch_note "
            "WHERE code = ? ORDER BY created_at DESC, id DESC",
            (code,),
        ).fetchall()
    return {"rows": [dict(r) for r in rows]}


@router.post("/notes", status_code=201)
def create_note(payload: NoteCreate) -> dict:
    """新增盯盘笔记。"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO watch_note(code, trade_date, content, tags, created_at) "
            "VALUES(?,?,?,?,?)",
            (payload.code, payload.trade_date, payload.content, payload.tags,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        new_id = cur.lastrowid
    return {"id": new_id}


def _notif_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "ts": r["ts"],
        "level": r["level"],
        "title": r["title"],
        "body": r["body"],
        "is_read": r["is_read"],
    }


@router.get("/notifications")
def get_notifications(limit: int = _LIMIT, unread: int = Query(0, ge=0, le=1)) -> dict:
    """通知列表（新→旧）；unread=1 时只回未读。"""
    cond = " WHERE is_read = 0" if unread else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, ts, level, title, body, is_read FROM notification{cond} "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"rows": [_notif_row(r) for r in rows]}


@router.post("/notifications/read")
def mark_notifications_read() -> dict:
    """全部置为已读，返回本次实际更新的条数（幂等：再调一次为 0）。"""
    with _conn() as conn:
        cur = conn.execute("UPDATE notification SET is_read = 1 WHERE is_read = 0")
        conn.commit()
        updated = cur.rowcount
    return {"updated": updated}


@router.get("/report/daily")
def get_report_daily() -> dict:
    """每日报告页数据：今天批处理阶段状态 + 池/事件计数 + 最近通知。"""
    today = date.today().isoformat()
    with _conn() as conn:
        stages = conn.execute(
            "SELECT stage, status, stats_json FROM scan_run WHERE run_date = ? "
            "ORDER BY id ASC",
            (today,),
        ).fetchall()
        pool_status_rows = conn.execute(
            "SELECT status, COUNT(DISTINCT code) AS n FROM pool_entry GROUP BY status"
        ).fetchall()
        events_total = conn.execute("SELECT COUNT(*) AS n FROM event").fetchone()["n"]
        pool_total = conn.execute("SELECT COUNT(*) AS n FROM pool_entry").fetchone()["n"]
        market_refresh = ingest.market_refresh_counts(conn, today)
        notifs = conn.execute(
            "SELECT id, ts, level, title, body, is_read FROM notification "
            "ORDER BY ts DESC, id DESC LIMIT 20"
        ).fetchall()
    return {
        "date": today,
        "today_stages": [
            {"stage": s["stage"], "status": s["status"], "stats": _parse_json(s["stats_json"], {})}
            for s in stages
        ],
        "pool_by_status": {r["status"]: r["n"] for r in pool_status_rows},
        "events_total": events_total,
        "pool_total": pool_total,
        "market_refresh": market_refresh,
        "recent_notifications": [_notif_row(r) for r in notifs],
    }


# ---------- M4 流水线 B：疑似庄股榜（PRD §3.2/§3.3/F5） ----------


@router.get("/scan")
def get_scan(
    scan_date: str | None = Query(None, alias="date"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """疑似庄股榜：scan_result LEFT JOIN stock + 池内存在性。

    date 省略时取最新有结果的扫描日；指定无数据日期 → 空 rows（不 500）。
    排序：resonance 降序 → gain_pct 降序 → code。
    """
    with _conn() as conn:
        day = scan_date or conn.execute(
            "SELECT MAX(scan_date) FROM scan_result").fetchone()[0]
        total = 0
        out: list[dict] = []
        if day:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM scan_result WHERE scan_date = ?",
                (day,)).fetchone()["n"]
            rows = conn.execute(
                "SELECT sr.code, s.name, s.board, sr.circ_mv, sr.gain_pct, "
                "sr.holder_drop_pct, sr.resonance, sr.tier, sr.hits_json, "
                "sr.start_date, sr.start_age_days, sr.lane, sr.mechanical_start_date, "
                "sr.confirmed_start_date, sr.missing_fields_json, "
                "EXISTS(SELECT 1 FROM pool_entry pe WHERE pe.code = sr.code"
                "   AND pe.status <> '归档') AS in_pool "
                "FROM scan_result sr LEFT JOIN stock s ON sr.code = s.code "
                "WHERE sr.scan_date = ? "
                "ORDER BY CASE sr.tier WHEN 'S' THEN 0 WHEN 'A' THEN 1 "
                "WHEN 'B' THEN 2 WHEN '观察' THEN 3 WHEN '待确认' THEN 4 "
                "WHEN '延续观察' THEN 5 ELSE 6 END, sr.resonance DESC, sr.gain_pct DESC, "
                "sr.code ASC "
                "LIMIT ? OFFSET ?",
                (day, limit, offset),
            ).fetchall()
            out = [
                {
                    "code": r["code"],
                    "name": r["name"],
                    "board": r["board"],
                    "circ_mv": r["circ_mv"],
                    "gain_pct": r["gain_pct"],
                    "holder_drop_pct": r["holder_drop_pct"],
                    "resonance": r["resonance"],
                    "tier": r["tier"],
                    "hits": _json_list(r["hits_json"]),
                    "start_date": r["start_date"],
                    "start_age_days": r["start_age_days"],
                    "lane": r["lane"],
                    "mechanical_start_date": r["mechanical_start_date"],
                    "confirmed_start_date": r["confirmed_start_date"],
                    "missing_fields": _json_list(r["missing_fields_json"]),
                    "in_pool": bool(r["in_pool"]),
                }
                for r in rows
            ]
    return {"date": day, "total": total, "rows": out}


@router.get("/scan/summary")
def get_scan_summary() -> dict:
    """榜单概况：最新扫描日 + 宇宙规模（取自当日 scan 阶段 scan_run 统计，缺则 0）。"""
    with _conn() as conn:
        day = conn.execute("SELECT MAX(scan_date) FROM scan_result").fetchone()[0]
        if not day:
            return {"latest_date": None, "universe": 0, "hits": 0, "tier_counts": {}}
        hits = conn.execute(
            "SELECT COUNT(*) AS n FROM scan_result WHERE scan_date = ?",
            (day,)).fetchone()["n"]
        tiers = conn.execute(
            "SELECT tier, COUNT(*) AS n FROM scan_result WHERE scan_date = ? "
            "GROUP BY tier", (day,)).fetchall()
        run = conn.execute(
            "SELECT stats_json FROM scan_run WHERE stage = 'scan' AND run_date = ? "
            "ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    universe = 0
    if run is not None:
        stats = _parse_json(run["stats_json"], {})
        if isinstance(stats, dict):
            universe = stats.get("universe_size", 0) or 0
    # tier 为 NULL 的异常行归入 "无"（正常扫描不会产生）
    tier_counts = {(t["tier"] or "无"): t["n"] for t in tiers}
    return {"latest_date": day, "universe": universe, "hits": hits,
            "tier_counts": tier_counts}


# ---------- M5 素材包导出（PRD F8：单票一键导出 MD+JSON） ----------


@router.get("/export/{code}")
def export_material(code: str, fmt: str = Query("md", pattern="^(md|json)$")) -> Response:
    """附件下载单票素材包；fmt=md 喂聊天 AI，fmt=json 喂程序。无数据 404。"""
    with _conn() as conn:
        m = material.build_material(conn, code)
    if m is None:
        raise HTTPException(status_code=404, detail="库内没有该股票的任何数据")
    if fmt == "json":
        body, media, ext = json.dumps(m, ensure_ascii=False, indent=2, default=str), \
            "application/json", "json"
    else:
        body, media, ext = material.to_markdown(m), "text/markdown", "md"
    fname = f"material_{code}_{m['generated_for'].replace('-', '')}.{ext}"
    return Response(content=body.encode("utf-8"), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---------- M6 配置管理（PRD F10：白名单键 + 范围校验，即时生效） ----------

from . import ingest, recon, scanner  # noqa: E402

# 可编辑因子参数白名单与合理范围（PRD §3.2；键与 scanner.DEFAULT_PARAMS 对齐）
SCAN_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "window": (20, 250),
    "gain_min_pct": (0, 1000),
    "circ_mv_a_max": (1, 10000),
    "circ_mv_watch_max": (1, 100000),
    "min_list_days": (0, 3650),
    "min_avg_amount": (0, 1e11),
    "holder_single_drop_pct": (-100, 0),
    "holder_cum_drop_pct": (-100, 0),
    "weak_days": (1, 730),
    "weak_min_types": (1, 5),
    "start_max_days": (5, 250),
    "continuation_max_days": (40, 365),
    "start_s_days": (5, 250),
    "min_resonance": (1, 5),
    "pool_mv_max": (10, 10000),
    "archive_after_days": (1, 3650),
    "archive_zombie_days": (10, 1000),
    "archive_event_max_age_days": (30, 3650),
}
SCAN_PARAM_INT_KEYS = {"window", "min_list_days", "weak_days", "weak_min_types",
                       "start_max_days", "continuation_max_days", "start_s_days", "min_resonance",
                       "archive_after_days", "archive_zombie_days",
                       "archive_event_max_age_days"}


class ConfigUpdate(BaseModel):
    scan_params: dict[str, Any] | None = None
    notify_macos: int | None = Field(None, ge=0, le=1)


def _bad(v: str) -> HTTPException:
    return HTTPException(status_code=400, detail=v)


@router.get("/config")
def get_config_api() -> dict:
    """当前配置：defaults（代码默认）/ saved（库内已存）/ effective（生效值）/ bounds。"""
    with _conn() as conn:
        saved = ingest.get_config(conn, "scan_params", None)
        notify_on = bool(ingest.get_config(conn, "notify_macos", 1))
    saved = {k: v for k, v in (saved or {}).items()
             if k in SCAN_PARAM_BOUNDS} if isinstance(saved, dict) else {}
    return {"defaults": dict(scanner.DEFAULT_PARAMS), "saved": saved,
            "effective": {**scanner.DEFAULT_PARAMS, **saved},
            "notify_macos": notify_on, "bounds": SCAN_PARAM_BOUNDS}


@router.put("/config")
def put_config(body: ConfigUpdate) -> dict:
    """部分更新：scan_params 逐键并入既有值；非法键/类型/范围一律 400。"""
    if body.scan_params is not None:
        for k, v in body.scan_params.items():
            if k not in SCAN_PARAM_BOUNDS:
                raise _bad(f"不允许的配置键: {k}")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise _bad(f"{k} 必须是数字")
            lo, hi = SCAN_PARAM_BOUNDS[k]
            if not lo <= v <= hi:
                raise _bad(f"{k} 超出范围 [{lo}, {hi}]")
            if k in SCAN_PARAM_INT_KEYS and not float(v).is_integer():
                raise _bad(f"{k} 必须是整数")
    with _conn() as conn:
        if body.scan_params:
            cur = ingest.get_config(conn, "scan_params", None)
            cur = dict(cur) if isinstance(cur, dict) else {}
            cur.update({k: (int(v) if k in SCAN_PARAM_INT_KEYS else float(v))
                        for k, v in body.scan_params.items()})
            if cur.get("circ_mv_a_max", scanner.DEFAULT_PARAMS["circ_mv_a_max"]) >= \
                    cur.get("circ_mv_watch_max", scanner.DEFAULT_PARAMS["circ_mv_watch_max"]):
                raise _bad("A 级市值上限必须小于观察出局线")
            ingest.set_config(conn, "scan_params", cur)
        if body.notify_macos is not None:
            ingest.set_config(conn, "notify_macos", body.notify_macos)
        conn.commit()
    return get_config_api()


# ---------- M6 数据对账面板（PRD §7：巨潮 vs 东财dc 召回差异，只读离线） ----------


@router.get("/recon/dates")
def get_recon_dates(limit: int = Query(30, ge=1, le=90)) -> dict:
    """可对账日期候选（有全市场公告的日期，倒序）。"""
    with _conn() as conn:
        return {"dates": recon.available_dates(conn, limit)}


@router.get("/recon")
def get_recon(day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
              codes: str | None = Query(None, description="逗号分隔；省略=候选池全部")) -> dict:
    """池内个股当日两源公告差异；day 省略取最新有全市场公告的日期。"""
    with _conn() as conn:
        date = day or (recon.available_dates(conn, 1) or [None])[0]
        if not date:
            return {"date": None, "universe": 0, "compared": 0, "rows": []}
        code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
        return recon.recon_day(conn, date, code_list)
