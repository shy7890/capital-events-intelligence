"""建库脚本：可重复执行（幂等）。用法：.venv/bin/python scripts/init_db.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuang_monitor import db  # noqa: E402
from zhuang_monitor.config import settings  # noqa: E402

EXPECTED_TABLES = {
    "stock", "announcement", "event", "flow_node", "pool_entry",
    "holder_num", "kline_daily", "longhu", "deviation_daily",
    "watch_note", "notification", "config", "scan_run",
}


def main() -> int:
    with db.connect() as conn:
        db.init_schema(conn)
        tables = db.table_names(conn)
    missing = EXPECTED_TABLES - tables
    if missing:
        print(f"FAIL: 缺少表 {sorted(missing)}")
        return 1
    print(f"OK: 数据库 {settings.db_path} 就绪，{len(tables)} 张表：{sorted(tables)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
