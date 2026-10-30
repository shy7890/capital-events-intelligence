"""v6 冷启动导入 CLI（PRD §F7）。用法：

    .venv/bin/python scripts/import_v6.py [xlsx路径] [--dry-run]

打印导入统计并写 scan_run（stage="import_v6"，green/red）。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuang_monitor import db, ingest  # noqa: E402
from zhuang_monitor.importer import import_v6_workbook  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XLSX = (
    PROJECT_ROOT
    / "outputs/capital_scan_20260524_v6/主板科创板创业板重大资本运作筛选_2026-05-24_一年内波动户数版.xlsx"
)

STATS_ORDER = ("core_pool", "watch_pool", "events", "announcements", "flow_nodes", "rejected", "holder_pct")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入 v6 冷启动 xlsx（幂等，可重复执行）")
    parser.add_argument("xlsx", nargs="?", default=str(DEFAULT_XLSX), help="v6 xlsx 路径")
    parser.add_argument("--dry-run", action="store_true", help="只解析统计，不落库")
    args = parser.parse_args(argv)

    path = Path(args.xlsx)
    if not path.exists():
        print(f"FAIL: 文件不存在 {path}")
        return 1

    conn = db.connect()
    db.init_schema(conn)
    run_date = date.today().isoformat()
    try:
        stats = import_v6_workbook(conn, path, dry_run=args.dry_run)
    except Exception as exc:
        conn.rollback()  # 丢弃半途的部分写入，避免 red 记账连带提交脏数据
        ingest.record_scan_run(conn, run_date, "import_v6", "red",
                               errors=f"{type(exc).__name__}: {exc}")
        print(f"FAIL: 导入失败 {type(exc).__name__}: {exc}")
        return 1

    ingest.record_scan_run(conn, run_date, "import_v6", "green",
                           dict(stats, dry_run=args.dry_run))
    prefix = "[dry-run 未落库] " if args.dry_run else ""
    print(f"{prefix}v6 冷启动导入统计：")
    for key in STATS_ORDER:
        print(f"  {key:<14} {stats[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
