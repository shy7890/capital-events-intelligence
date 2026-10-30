"""M2 分类器：公告 → 事件归并 / 流程节点推进 / 入池 / 提醒（PRD F1–F3）。

幂等：事件按"同码同类型进行中"归并；flow_node 有 UNIQUE(event_id,node_type,node_date)；
池与通知按自然键查重；已处理公告以 max(announcement.id) 水位线跳过。
"""

from __future__ import annotations

import json
import sqlite3

from .. import ingest, notify, scanner
from .rules import ATTACH, CLOSE, OPEN, WEAK_SIGNAL_NODE_TYPES, match_title
from .state_machine import terminal_status_for

WATERMARK_KEY = "watermark_classify"


def _open_event(conn: sqlite3.Connection, code: str, event_type: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM event WHERE code=? AND event_type=? AND status='进行中'"
        " ORDER BY start_date DESC LIMIT 1",
        (code, event_type),
    ).fetchone()


def _latest_open_in(conn: sqlite3.Connection, code: str, types: tuple[str, ...],
                    before: str | None = None) -> sqlite3.Row | None:
    if not types:
        return None
    marks = ",".join("?" * len(types))
    sql = (f"SELECT * FROM event WHERE code=? AND status='进行中' AND event_type IN ({marks})")
    args: list = [code, *types]
    if before:
        sql += " AND start_date < ?"
        args.append(before)
    sql += " ORDER BY start_date DESC, id DESC LIMIT 1"
    return conn.execute(sql, args).fetchone()


def _create_event(conn: sqlite3.Connection, now: str, code: str, event_type: str,
                  grade: str, pub_date: str, ann_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO event (code, event_type, grade, start_date, status,"
        " first_announcement_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (code, event_type, grade, pub_date, "进行中", ann_id, now, now),
    )
    event_id = int(cur.lastrowid)
    if grade in ("S", "A"):
        # v1.1 市值硬门槛：流通市值超限（默认 300 亿）不入池；未知市值可入，待回填后归档兜底
        mv = conn.execute("SELECT circ_mv FROM stock WHERE code=?", (code,)).fetchone()
        cap = float(scanner.resolve_params(conn, None)["pool_mv_max"])
        if mv is None or mv["circ_mv"] is None or float(mv["circ_mv"]) < cap:
            cur2 = conn.execute(
                "INSERT OR IGNORE INTO pool_entry (code, event_id, status, grade, entered_at,"
                " resonance_count, updated_at) VALUES (?,?,?,?,?,0,?)",
                (code, event_id, "新入池", grade, now, now),
            )
            _ = cur2
    return event_id


def _add_node(conn: sqlite3.Connection, now: str, event_id: int, node_type: str,
              pub_date: str, ann_id: int) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO flow_node (event_id, node_type, node_date, announcement_id, created_at)"
        " VALUES (?,?,?,?,?)",
        (event_id, node_type, pub_date, ann_id, now),
    )
    return cur.rowcount > 0


def _record_weak_signal(conn: sqlite3.Connection, ann: sqlite3.Row | dict, now: str) -> bool:
    """先于事件挂接记录弱信号；没有进行中资本事件也不能丢失。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO weak_signal"
        " (code,signal_type,signal_date,announcement_id,title,source,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (ann["code"], ann["hit_node_type"], ann["pub_date"], ann["id"],
         ann["title"], ann["source"], now),
    )
    return cur.rowcount > 0


def _notify(conn: sqlite3.Connection, ts: str, level: str, title: str, body: str) -> bool:
    exists = conn.execute(
        "SELECT 1 FROM notification WHERE ts=? AND title=? LIMIT 1", (ts, title)
    ).fetchone()
    if exists:
        return False
    conn.execute(
        "INSERT INTO notification (ts, level, title, body, is_read) VALUES (?,?,?,?,0)",
        (ts, level, title, body),
    )
    notify.maybe_push(conn, ts, title, body)
    return True


def classify_events(
    conn: sqlite3.Connection,
    *,
    codes: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    use_watermark: bool = True,
    now: str | None = None,
) -> dict:
    """扫描公告表推进事件流水线；带过滤条件（start/end/codes）时不动水位线。"""
    now = now or ingest_now()
    filtered = codes is not None or start is not None or end is not None
    watermark = 0 if filtered or not use_watermark else int(ingest.get_config(conn, WATERMARK_KEY, 0) or 0)
    sql = "SELECT id, code, pub_date, title, source FROM announcement WHERE id > ?"
    args: list = [watermark]
    if codes:
        sql += f" AND code IN ({','.join('?' * len(codes))})"
        args.extend(codes)
    if start:
        sql += " AND pub_date >= ?"
        args.append(start)
    if end:
        sql += " AND pub_date <= ?"
        args.append(end)
    sql += " ORDER BY pub_date, id"
    rows = conn.execute(sql, args).fetchall()

    stats = {"processed": 0, "new_events": 0, "new_nodes": 0, "skipped": 0,
             "notices": 0, "exits": 0}
    max_id = watermark
    for ann in rows:
        stats["processed"] += 1
        max_id = max(max_id, ann["id"])
        hit = match_title(ann["title"])
        if hit is None:
            stats["skipped"] += 1
            continue
        code, pub_date, ann_id = ann["code"], ann["pub_date"], ann["id"]
        if hit.node_type in WEAK_SIGNAL_NODE_TYPES:
            weak_ann = dict(ann)
            weak_ann["hit_node_type"] = hit.node_type
            if _record_weak_signal(conn, weak_ann, now):
                stats.setdefault("weak_signals", 0)
                stats["weak_signals"] += 1
        search_types = (hit.event_type,) if not hit.targets else hit.targets

        if hit.behavior == OPEN:
            ev = _open_event(conn, code, hit.event_type)
            if ev is None:
                ev_id = _create_event(conn, now, code, hit.event_type, hit.grade, pub_date, ann_id)
                stats["new_events"] += 1
                stats["notices"] += int(_notify(
                    conn, pub_date, "alert",
                    f"S/A级事件入池：{code} {hit.event_type}",
                    f"节点：{hit.node_type}（提醒非决策）",
                ))
            else:
                ev_id = int(ev["id"])
            if _add_node(conn, now, ev_id, hit.node_type, pub_date, ann_id):
                stats["new_nodes"] += 1
            continue

        # CLOSE：终止类只能关严格早于公告日开起的事件（同日"预案+终止前次"不误伤）
        ev = _latest_open_in(conn, code, search_types,
                             before=pub_date if hit.grade == "T" else None)
        if hit.behavior == ATTACH:
            if ev is None:
                stats["skipped"] += 1
                continue
            if _add_node(conn, now, int(ev["id"]), hit.node_type, pub_date, ann_id):
                stats["new_nodes"] += 1
                if hit.grade in ("S", "A"):
                    stats["notices"] += int(_notify(
                        conn, pub_date, "info",
                        f"节点推进：{code} {ev['event_type']} → {hit.node_type}",
                        f"公告：{ann['title'][:50]}（提醒非决策）",
                    ))
            continue

        # CLOSE
        if ev is None:
            stats["skipped"] += 1
            continue
        _add_node(conn, now, int(ev["id"]), hit.node_type, pub_date, ann_id)
        status = terminal_status_for(ev["event_type"], hit.node_type) or "已完成"
        conn.execute("UPDATE event SET status=?, updated_at=? WHERE id=?", (status, now, ev["id"]))
        stats["new_nodes"] += 1
        if status == "终止":
            entry = conn.execute(
                "SELECT * FROM pool_entry WHERE event_id=? AND status NOT IN ('离场','归档')",
                (ev["id"],),
            ).fetchone()
            if entry:
                signals = json.loads(entry["exit_signals"] or "[]") + ["事件终止"]
                conn.execute(
                    "UPDATE pool_entry SET status='离场', exit_signals=?, updated_at=? WHERE id=?",
                    (json.dumps(signals, ensure_ascii=False), now, entry["id"]),
                )
                stats["exits"] += 1
            _notify(conn, pub_date, "warn",
                    f"事件终止：{code} {ev['event_type']}（提醒非决策）", ann["title"][:50])
        else:
            entry = conn.execute(
                "SELECT * FROM pool_entry WHERE event_id=? AND status IN ('新入池','观察中','重点跟踪')",
                (ev["id"],),
            ).fetchone()
            if entry:
                conn.execute("UPDATE pool_entry SET status='催化兑现中', updated_at=? WHERE id=?",
                             (now, entry["id"]))
            stats["notices"] += int(_notify(
                conn, pub_date, "info",
                f"事件收尾：{code} {ev['event_type']} → {status}",
                ann["title"][:50] + "（提醒非决策）",
            ))

    if not filtered and use_watermark and stats["processed"]:
        ingest.set_config(conn, WATERMARK_KEY, max_id)
    conn.commit()
    return stats


def ingest_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
