"""M0 验收测试：schema 幂等 + FastAPI 健康检查。"""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from zhuang_monitor import db, main  # noqa: E402
from zhuang_monitor.config import settings  # noqa: E402

EXPECTED_TABLES = {
    "stock", "announcement", "event", "flow_node", "pool_entry",
    "holder_num", "kline_daily", "longhu", "deviation_daily",
    "watch_note", "watchlist_entry", "notification", "config", "scan_run", "rejected_record",
    "scan_result", "holder_pct",
    "market_refresh", "market_refresh_item", "weak_signal",
    "stock_rating",
}


def test_schema_idempotent(tmp_path: Path) -> None:
    dbfile = tmp_path / "test.db"
    with db.connect(dbfile) as conn:
        db.init_schema(conn)
        assert db.table_names(conn) == EXPECTED_TABLES
    with db.connect(dbfile) as conn:  # 第二次执行不得报错、不得变化
        db.init_schema(conn)
        assert db.table_names(conn) == EXPECTED_TABLES


def test_health_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path / "nonexistent")
    dbfile = tmp_path / "t.db"
    monkeypatch.setattr(settings, "db_path", dbfile)
    with db.connect(dbfile) as conn:
        db.init_schema(conn)
    client = TestClient(main.app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["tables"] == len(EXPECTED_TABLES)


def test_connect_sets_busy_timeout(tmp_path) -> None:
    """多进程并发写（如后台回填 + 前台查询）时须自动等待而非立刻报"锁定"。"""
    conn = db.connect(tmp_path / "lock.db")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 10000
    conn.close()


def test_migration_adds_v11_columns(tmp_path: Path) -> None:
    """旧库（scan_result 无 v1.1 增列）再跑 init_schema 应自动 ALTER 补齐且幂等。"""
    dbfile = tmp_path / "old.db"
    with sqlite3.connect(dbfile) as conn:
        conn.execute(
            "CREATE TABLE scan_result (scan_date TEXT NOT NULL, code TEXT NOT NULL,"
            " gain_pct REAL, lhb_after_start INTEGER, holder_drop_pct REAL,"
            " circ_mv REAL, avg_amount REAL, resonance INTEGER NOT NULL, tier TEXT,"
            " hits_json TEXT, PRIMARY KEY (scan_date, code))")
    with db.connect(dbfile) as conn:
        db.init_schema(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(scan_result)")}
        assert {"start_date", "start_age_days", "lane", "mechanical_start_date",
                "confirmed_start_date", "missing_fields_json"} <= cols
        n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='scan_result'"
                         " AND type='table'").fetchone()[0]
        assert n == 1  # 旧表未被重建
    with db.connect(dbfile) as conn:  # 第三次幂等
        db.init_schema(conn)
