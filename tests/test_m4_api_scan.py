"""M4 疑似庄股榜 REST API 契约测试：GET /api/scan 与 GET /api/scan/summary。

手法沿用 test_m3：tmp 库塞 scan_result/stock/pool_entry 行，monkeypatch api.DB_PATH。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from zhuang_monitor import api, db, main  # noqa: E402

ROW_KEYS = {"code", "name", "board", "circ_mv", "gain_pct", "holder_drop_pct",
            "resonance", "tier", "hits", "in_pool", "start_date", "start_age_days",
            "lane", "mechanical_start_date", "confirmed_start_date", "missing_fields"}


def _scan_row(scan_date, code, *, gain=30.0, holder=-12.0, mv=80.0,
              resonance=4, tier="A", hits='["涨幅", "龙虎榜静默", "户数集中", "小市值"]'):
    return (scan_date, code, gain, 0, holder, mv, 1.2e8, resonance, tier, hits)


def _seed(conn) -> None:
    conn.executemany(
        "INSERT INTO stock(code,name,board,list_date,is_st,circ_mv,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        [("002348", "高乐股份", "深市主板", "2010-02-03", 0, 101.11, "2026-09-01"),
         ("600001", "榜外乙", "沪市主板", "2011-01-01", 0, 90.0, "2026-09-01")],
    )
    conn.executemany(
        "INSERT INTO scan_result(scan_date,code,gain_pct,lhb_after_start,"
        "holder_drop_pct,circ_mv,avg_amount,resonance,tier,hits_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        [
            _scan_row("2026-08-31", "688001", gain=26.0, holder=None, mv=200.0,
                      resonance=2, tier="观察", hits='["涨幅", "龙虎榜静默"]'),
            _scan_row("2026-09-01", "002348", gain=35.0, mv=101.11, resonance=4),
            _scan_row("2026-09-01", "600001", gain=28.0, resonance=3,
                      hits='["涨幅", "龙虎榜静默", "户数集中"]'),
            _scan_row("2026-09-01", "999999", gain=99.0, resonance=5, tier=None,
                      hits="坏JSON{"),  # 无 stock 档案 + 坏 hits_json + tier NULL
        ],
    )
    conn.execute(
        "INSERT INTO pool_entry(code,event_id,status,grade,entered_at,resonance_count,"
        "updated_at) VALUES(?,?,?,?,?,?,?)",
        ("002348", None, "观察中", "S", "2026-08-01", 4, "2026-09-01"))
    conn.executemany(
        "INSERT INTO scan_run(run_date,stage,status,stats_json,errors,started_at,finished_at)"
        " VALUES(?,?,?,?,?,?,?)",
        [("2026-09-01", "scan", "green", '{"universe_size": 2, "hits": 3}', None,
          "2026-09-01T17:40:00", "2026-09-01T17:41:00"),
         ("2026-08-31", "scan", "green", '{"universe_size": 2, "hits": 1}', None,
          "2026-08-31T17:40:00", "2026-08-31T17:41:00")],
    )
    conn.commit()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    dbfile = tmp_path / "m4api.db"
    with db.connect(dbfile) as conn:
        db.init_schema(conn)
        _seed(conn)
    monkeypatch.setattr(api, "DB_PATH", dbfile)
    return TestClient(main.app)


# ---------- GET /api/scan ----------

def test_scan_default_latest_date(client):
    body = client.get("/api/scan").json()
    assert set(body) == {"date", "total", "rows"}
    assert body["date"] == "2026-09-01" and body["total"] == 3
    rows = body["rows"]
    assert set(rows[0]) == ROW_KEYS
    # v1.1 排序：层级优先（S>A>B>观察>NULL），层内 resonance、gain 降序
    assert [r["code"] for r in rows] == ["002348", "600001", "999999"]
    r = rows[0]
    assert r["name"] == "高乐股份" and r["board"] == "深市主板"
    assert r["circ_mv"] == 101.11 and r["gain_pct"] == 35.0
    assert r["holder_drop_pct"] == -12.0 and r["resonance"] == 4 and r["tier"] == "A"
    assert r["hits"] == ["涨幅", "龙虎榜静默", "户数集中", "小市值"]
    assert r["lane"] is None and r["mechanical_start_date"] is None
    assert r["confirmed_start_date"] is None and r["missing_fields"] == []
    assert r["in_pool"] is True
    assert rows[1]["in_pool"] is False
    # 无 stock 档案 LEFT JOIN 为 None；坏 JSON hits 容错为空数组；tier NULL 排最后；不 500
    assert rows[2]["name"] is None and rows[2]["board"] is None
    assert rows[2]["hits"] == [] and rows[2]["tier"] is None


def test_scan_date_param_pagination_and_empty(client):
    body = client.get("/api/scan", params={"date": "2026-08-31"}).json()
    assert body["date"] == "2026-08-31" and body["total"] == 1
    r = body["rows"][0]
    assert r["code"] == "688001" and r["tier"] == "观察" and r["holder_drop_pct"] is None
    # 有日期但当日无结果 → 空 rows 不 500
    empty = client.get("/api/scan", params={"date": "2020-01-01"}).json()
    assert empty == {"date": "2020-01-01", "total": 0, "rows": []}
    # limit/offset（层级排序下第 3 名是 tier NULL 的 999999）
    page = client.get("/api/scan", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 3 and [r["code"] for r in page["rows"]] == ["999999"]
    assert client.get("/api/scan", params={"limit": 999}).status_code == 422


# ---------- GET /api/scan/summary ----------

def test_scan_summary(client):
    body = client.get("/api/scan/summary").json()
    assert set(body) == {"latest_date", "universe", "hits", "tier_counts"}
    assert body["latest_date"] == "2026-09-01"
    assert body["universe"] == 2  # 来自 scan_run stage=scan 的 stats
    assert body["hits"] == 3
    assert body["tier_counts"] == {"A": 2, "无": 1}  # tier NULL 归入 "无"


def test_scan_summary_universe_without_scan_run(tmp_path, monkeypatch):
    """手工塞榜但没跑过批任务：universe 兜底 0，不 500。"""
    dbfile = tmp_path / "nosum.db"
    with db.connect(dbfile) as conn:
        db.init_schema(conn)
        conn.execute(
            "INSERT INTO scan_result(scan_date,code,gain_pct,lhb_after_start,"
            "holder_drop_pct,circ_mv,avg_amount,resonance,tier,hits_json) "
            "VALUES('2026-09-01','002348',30,0,NULL,80,1e8,2,'A','[]')")
        conn.commit()
    monkeypatch.setattr(api, "DB_PATH", dbfile)
    c = TestClient(main.app)
    assert c.get("/api/scan/summary").json() == {
        "latest_date": "2026-09-01", "universe": 0, "hits": 1, "tier_counts": {"A": 1}}


# ---------- 空库 ----------

def test_scan_endpoints_on_empty_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "empty4.db"
    with db.connect(dbfile) as conn:
        db.init_schema(conn)
    monkeypatch.setattr(api, "DB_PATH", dbfile)
    c = TestClient(main.app)
    assert c.get("/api/scan").json() == {"date": None, "total": 0, "rows": []}
    assert c.get("/api/scan/summary").json() == {
        "latest_date": None, "universe": 0, "hits": 0, "tier_counts": {}}
