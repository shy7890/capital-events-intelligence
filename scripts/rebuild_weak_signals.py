"""从本地公告表重建独立弱信号记录（不联网、不改事件和候选池）。

用法：
  .venv/bin/python scripts/rebuild_weak_signals.py
  .venv/bin/python scripts/rebuild_weak_signals.py --start 2024-01-01 --end 2026-09-02

脚本必须显式运行；重复运行同一范围只会得到同样的弱信号自然键结果。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

sys.path.insert(0, "src")

from zhuang_monitor import db, ingest  # noqa: E402
from zhuang_monitor.events.rules import WEAK_SIGNAL_NODE_TYPES, match_title  # noqa: E402


def rebuild(conn, *, start: str | None = None, end: str | None = None,
            codes: list[str] | None = None) -> dict[str, int]:
    where: list[str] = []
    args: list[str] = []
    if start:
        where.append("pub_date >= ?")
        args.append(start)
    if end:
        where.append("pub_date <= ?")
        args.append(end)
    if codes:
        marks = ",".join("?" * len(codes))
        where.append(f"code IN ({marks})")
        args.extend(codes)
    condition = f" WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        "SELECT id,code,pub_date,title,source FROM announcement" + condition +
        " ORDER BY pub_date,id", args
    ).fetchall()

    # 仅重建本表，历史公告、事件和 pool_entry 均不删除。
    existing = conn.execute(
        "SELECT code,signal_type,signal_date,announcement_id FROM weak_signal"
        + condition.replace("pub_date", "signal_date"), args
    ).fetchall()
    existing_keys = {
        (row["code"], row["signal_type"], row["signal_date"], row["announcement_id"])
        for row in existing
    }
    conn.execute(
        "DELETE FROM weak_signal" + condition.replace("pub_date", "signal_date"), args
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inserted = 0
    matched = 0
    for ann in rows:
        hit = match_title(ann["title"])
        if hit is None or hit.node_type not in WEAK_SIGNAL_NODE_TYPES:
            continue
        matched += 1
        key = (ann["code"], hit.node_type, ann["pub_date"], ann["id"])
        conn.execute(
            "INSERT OR IGNORE INTO weak_signal"
            "(code,signal_type,signal_date,announcement_id,title,source,created_at)"
            "VALUES(?,?,?,?,?,?,?)",
            (ann["code"], hit.node_type, ann["pub_date"], ann["id"],
             ann["title"], ann["source"], now),
        )
        if key not in existing_keys:
            inserted += 1
    conn.commit()
    return {"announcements": len(rows), "matched": matched, "inserted": inserted}


def main() -> int:
    parser = argparse.ArgumentParser(description="从本地公告重建独立弱信号记录")
    parser.add_argument("--start", help="公告起始日（含）")
    parser.add_argument("--end", help="公告结束日（含）")
    parser.add_argument("--code", action="append", dest="codes", help="只重建指定代码，可重复")
    args = parser.parse_args()
    conn = db.connect()
    db.init_schema(conn)
    run_date = args.end or datetime.now().date().isoformat()
    try:
        stats = rebuild(conn, start=args.start, end=args.end, codes=args.codes)
        ingest.record_scan_run(conn, run_date, "weak_signal_rebuild", "green", stats=stats)
        print(f"完成：公告 {stats['announcements']} 条，命中 {stats['matched']} 条，新增 {stats['inserted']} 条")
        return 0
    except Exception as exc:  # noqa: BLE001
        ingest.record_scan_run(
            conn, run_date, "weak_signal_rebuild", "red",
            errors=f"{type(exc).__name__}: {str(exc)[:160]}",
        )
        print(f"失败：{type(exc).__name__}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
