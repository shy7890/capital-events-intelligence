"""M1 入库层测试：自然键 upsert 幂等、重复跑不产生重复行、scan_run 记账。用真实 SQLite（临时库）。"""

from __future__ import annotations

import json

import pytest

from zhuang_monitor import db, ingest


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_schema(c)
    yield c
    c.close()


KLINE = [
    {"code": "002348", "trade_date": "2024-01-02", "open": 6.8, "high": 7.0, "low": 6.7,
     "close": 6.9, "volume": 1200000.0, "amount": 8.2e8, "turnover": 1.26, "adj_factor": 1.25, "source": "baostock"},
    {"code": "002348", "trade_date": "2024-01-03", "open": 6.9, "high": 7.1, "low": 6.85,
     "close": 7.05, "volume": 980000.0, "amount": 6.9e8, "turnover": 1.03, "adj_factor": 1.25, "source": "baostock"},
]

HOLDER = [
    {"code": "002348", "disclose_date": "2024-03-15", "holder_num": 44000.0, "pct_change": -12.5,
     "avg_hold": 1800000.0, "total_mv": 79.2e8, "source": "akshare_em"},
]

LONGHU = [
    {"code": "002348", "trade_date": "2024-02-28", "reason": "日涨幅偏离值达7%", "net_buy": 3500.0, "source": "akshare_em"},
    {"code": "002348", "trade_date": "2024-02-28", "reason": "连续三个交易日涨幅偏离", "net_buy": 5100.0, "source": "akshare_em"},
]

ANN = [
    {"code": "002348", "pub_date": "2024-03-01", "title": "关于控股股东拟变更的公告", "pdf_url": "http://static.cninfo.com.cn/x.PDF",
     "category": "gqbd", "source": "cninfo"},
]


def _count(c, sql):
    return c.execute(sql).fetchone()[0]


def test_kline_upsert_first_insert(conn):
    assert ingest.upsert_kline(conn, KLINE) == 2
    assert _count(conn, "SELECT COUNT(*) FROM kline_daily") == 2


def test_kline_reinsert_is_idempotent(conn):
    ingest.upsert_kline(conn, KLINE)
    assert ingest.upsert_kline(conn, KLINE) == 0
    assert _count(conn, "SELECT COUNT(*) FROM kline_daily") == 2


def test_kline_reinsert_updates_changed_price(conn):
    ingest.upsert_kline(conn, KLINE)
    changed = [dict(KLINE[0], close=6.95)]
    ingest.upsert_kline(conn, changed)
    row = conn.execute("SELECT close, source FROM kline_daily WHERE trade_date='2024-01-02'").fetchone()
    assert row["close"] == 6.95
    assert _count(conn, "SELECT COUNT(*) FROM kline_daily") == 2


def test_kline_empty_rows_noop(conn):
    assert ingest.upsert_kline(conn, []) == 0


def test_holder_upsert_idempotent(conn):
    assert ingest.upsert_holder_num(conn, HOLDER) == 1
    assert ingest.upsert_holder_num(conn, HOLDER) == 0
    assert _count(conn, "SELECT COUNT(*) FROM holder_num") == 1


def test_longhu_same_date_two_reasons_kept(conn):
    assert ingest.upsert_longhu(conn, LONGHU) == 2
    assert ingest.upsert_longhu(conn, LONGHU) == 0
    assert _count(conn, "SELECT COUNT(*) FROM longhu") == 2


def test_announcement_dedupe_by_title(conn):
    assert ingest.upsert_announcement(conn, ANN) == 1
    dup = [dict(ANN[0], pdf_url="其他.PDF")]
    assert ingest.upsert_announcement(conn, dup) == 0
    assert _count(conn, "SELECT COUNT(*) FROM announcement") == 1
    assert ingest.upsert_announcement(conn, [dict(ANN[0], title="2023年度业绩快报", pub_date="2024-01-20")]) == 1
    assert _count(conn, "SELECT COUNT(*) FROM announcement") == 2


def test_stock_upsert_updates_mv(conn):
    rows = [{"code": "002348", "name": "高乐股份", "board": "深市主板", "list_date": "2010-01-28",
             "is_st": 0, "total_mv": 80.0, "circ_mv": 72.0}]
    assert ingest.upsert_stock(conn, rows) == 1
    assert ingest.upsert_stock(conn, [dict(rows[0], circ_mv=69.5, is_st=1)]) == 0
    assert _count(conn, "SELECT COUNT(*) FROM stock") == 1
    row = conn.execute("SELECT circ_mv, is_st FROM stock").fetchone()
    assert row["circ_mv"] == 69.5 and row["is_st"] == 1


def test_scan_run_record_with_json_stats(conn):
    run_id = ingest.record_scan_run(conn, "2024-06-01", "kline", "green", stats={"rows": 500, "source": "baostock"})
    row = conn.execute("SELECT run_date, stage, status, stats_json, started_at, finished_at FROM scan_run WHERE id=?", (run_id,)).fetchone()
    assert row["run_date"] == "2024-06-01"
    assert row["stage"] == "kline"
    assert row["status"] == "green"
    assert json.loads(row["stats_json"]) == {"rows": 500, "source": "baostock"}
    assert row["started_at"] and row["finished_at"]


def test_scan_run_error_keeps_text(conn):
    run_id = ingest.record_scan_run(conn, "2024-06-01", "kline", "red", errors="东财403")
    row = conn.execute("SELECT errors FROM scan_run WHERE id=?", (run_id,)).fetchone()
    assert row["errors"] == "东财403"


def test_red_scan_run_counting_helper(conn):
    ingest.record_scan_run(conn, "2024-06-02", "kline", "green")
    ingest.record_scan_run(conn, "2024-06-02", "holder", "red", errors="网络超时")
    assert ingest.red_run_count(conn) == 1
    assert ingest.red_run_count(conn, run_date="2024-06-02") == 1
    assert ingest.red_run_count(conn, run_date="2024-06-03") == 0


def test_config_roundtrip_json(conn):
    ingest.set_config(conn, "watermark_announcement", {"cninfo": "2024-06-01"})
    assert ingest.get_config(conn, "watermark_announcement") == {"cninfo": "2024-06-01"}
    ingest.set_config(conn, "watermark_announcement", {"cninfo": "2024-06-02"})
    assert ingest.get_config(conn, "watermark_announcement") == {"cninfo": "2024-06-02"}
    assert _count(conn, "SELECT COUNT(*) FROM config") == 1


def test_config_missing_returns_default(conn):
    assert ingest.get_config(conn, "nope", "缺省") == "缺省"


def test_stock_partial_row_preserves_existing(conn):
    rows = [{"code": "002348", "name": "高乐股份", "board": "深市主板", "list_date": "2010-01-28",
             "is_st": 0, "total_mv": 80.0, "circ_mv": 72.0}]
    ingest.upsert_stock(conn, rows)
    ingest.upsert_stock(conn, [{"code": "002348", "name": None, "board": None, "list_date": None,
                                "is_st": None, "total_mv": None, "circ_mv": 66.0}])
    row = conn.execute("SELECT name, board, is_st, circ_mv, total_mv FROM stock").fetchone()
    assert row["name"] == "高乐股份" and row["board"] == "深市主板"
    assert row["is_st"] == 0 and row["circ_mv"] == 66.0 and row["total_mv"] == 80.0
