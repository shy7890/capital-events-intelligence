"""全市场公告历史回填：--start（默认 2024-01-01）逐工作日抓到今天。

水位存 config 表 key=watermark_announce_history（已完成日 YYYY-MM-DD），
重复执行从断点续传；upsert 幂等保证不重复入库。单日失败重试 2 次后记错跳过
（水位照常推进，错误明细写入 scan_run.errors 供人工补抓）；
EastmoneyBlocked 立即停止且不推进当日水位。逐日处理、当批丢弃，不累积内存。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuang_monitor import db, ingest
from zhuang_monitor.em_throttle import EastmoneyBlocked
from zhuang_monitor.sources.ak_notice import notice_day_rows

WATERMARK_KEY = "watermark_announce_history"
STAGE = "announce_history"
MAX_ATTEMPTS = 3


def workdays(start: date, end: date) -> Iterator[date]:
    """start..end（含端点）逐日推进，跳过周六日。"""
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def fetch_day_with_retry(day: str) -> list[dict]:
    """抓单日公告；瞬时失败最多重试 MAX_ATTEMPTS 次（间隔由 pace 闸门保证）。"""
    last_exc: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            return notice_day_rows(day)
        except EastmoneyBlocked:
            raise
        except Exception as exc:  # noqa: BLE001：单日失败只记录不中断整体回填
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def main() -> int:
    parser = argparse.ArgumentParser(description="全市场公告历史回填（水位续传）")
    parser.add_argument("--start", default="2024-01-01", help="回填起始日 YYYY-MM-DD")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    today = date.today()

    conn = db.connect()
    db.init_schema(conn)
    watermark = ingest.get_config(conn, WATERMARK_KEY)
    begin = start
    if watermark:
        begin = max(begin, date.fromisoformat(str(watermark)) + timedelta(days=1))

    days_done = 0
    inserted_total = 0
    errors: list[tuple[str, str]] = []
    blocked_on: str | None = None

    if begin > today:
        print(f"无需处理：水位 {watermark} 已到 {today}，本次无新增日期")
    for day in workdays(begin, today):
        day_str = day.isoformat()
        try:
            rows = fetch_day_with_retry(day_str)
        except EastmoneyBlocked as exc:
            blocked_on = day_str
            errors.append((day_str, f"EastmoneyBlocked: {exc}"))
            print(f"{day_str} 东财封禁，立即停止：{exc}", flush=True)
            break
        except Exception as exc:  # noqa: BLE001
            errors.append((day_str, repr(exc)))
            print(f"{day_str} 重试后仍失败，跳过：{exc!r}", flush=True)
        else:
            inserted = ingest.upsert_announcement(conn, rows)
            inserted_total += inserted
            days_done += 1
            print(f"{day_str}: 公告 {len(rows)} 条，净新增 {inserted} 行", flush=True)
        ingest.set_config(conn, WATERMARK_KEY, day_str)

    status = "green" if not errors else "red"
    run_date = today.isoformat()
    ingest.record_scan_run(
        conn,
        run_date,
        STAGE,
        status,
        stats={
            "days": days_done,
            "inserted_total": inserted_total,
            "errors": len(errors),
            "blocked_on": blocked_on,
        },
        errors=json.dumps(errors, ensure_ascii=False) if errors else None,
    )

    total = conn.execute("SELECT COUNT(*) FROM announcement").fetchone()[0]
    minmax = conn.execute("SELECT MIN(pub_date), MAX(pub_date) FROM announcement").fetchone()
    red_runs = conn.execute(
        "SELECT COUNT(*) FROM scan_run WHERE stage=? AND status='red'", (STAGE,)
    ).fetchone()[0]
    print(f"完成：处理工作日 {days_done} 天，净新增 {inserted_total} 行，错误 {len(errors)} 个")
    print(f"库内 announcement 总数 {total}，pub_date 范围 {minmax[0]} ~ {minmax[1]}")
    print(f"水位 {ingest.get_config(conn, WATERMARK_KEY)}，{STAGE} 阶段红单数 {red_runs}")
    conn.close()
    return 1 if (errors or blocked_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
