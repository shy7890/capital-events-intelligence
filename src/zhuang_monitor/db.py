"""SQLite 连接与 schema 管理。单一事实源：data/zhuang.db（WAL 模式）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# v1.1 增列迁移：{表: [(列, 列类型)]}——旧库 init_schema 时自动 ALTER 补齐（幂等）
_MIGRATION_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "scan_result": [
        ("start_date", "TEXT"),
        ("start_age_days", "INTEGER"),
        ("lane", "TEXT"),
        ("mechanical_start_date", "TEXT"),
        ("confirmed_start_date", "TEXT"),
        ("missing_fields_json", "TEXT"),
    ],
}


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")  # 后台回填与前台并发写时自动等待
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """幂等建库：schema 全部 IF NOT EXISTS，重复执行无副作用；旧库自动补齐增列。"""
    conn.executescript(SCHEMA_PATH.read_text("utf-8"))
    for table, cols in _MIGRATION_COLUMNS.items():
        if table not in table_names(conn):
            continue
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for col, typ in cols:
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.commit()


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}
