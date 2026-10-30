"""M6 数据对账测试（PRD §7）：normalize_title 前缀/空白归一 + 两源差异口径。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from zhuang_monitor import api, db, ingest, main, recon  # noqa: E402

DAY = "2026-09-01"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m6r.db")
    db.init_schema(c)
    yield c
    c.close()


def test_normalize_title():
    assert recon.normalize_title("  高乐股份:关于2026年度 定增预案的公告 ", "高乐股份") \
        == "关于2026年度定增预案的公告"
    assert recon.normalize_title("高乐股份：关于召开股东大会的通知", "高乐股份") \
        == "关于召开股东大会的通知"
    assert recon.normalize_title("无前缀标题", "高乐股份") == "无前缀标题"
    assert recon.normalize_title("其他公司:标题", "高乐股份") == "其他公司:标题"


def _ann(c, code, title, source, day=DAY):
    ingest.upsert_announcement(c, [{"code": code, "pub_date": day, "title": title,
                                    "pdf_url": None, "category": None, "source": source}])


def test_recon_day_diff_three_shapes(conn):
    conn.execute("INSERT INTO stock(code,name,board,is_st,circ_mv,updated_at)"
                 " VALUES('002348','高乐股份','深市主板',0,101,'2026-09-01')")
    _ann(conn, "002348", "高乐股份:关于A事项的公告", "akshare_dc")     # 两源同题（前缀差）
    _ann(conn, "002348", "关于A事项的公告", "cninfo")
    _ann(conn, "002348", "关于B事项的公告", "akshare_dc")             # 仅东财
    _ann(conn, "002348", "高乐股份:关于C事项的补充公告", "cninfo")     # 仅巨潮
    conn.commit()
    out = recon.recon_day(conn, DAY, ["002348"])
    assert out["universe"] == 1 and out["compared"] == 1
    row = out["rows"][0]
    assert row["both"] == 1
    assert row["only_dc"] == ["关于B事项的公告"]
    assert row["only_cninfo"] == ["关于C事项的补充公告"]


def test_recon_skips_uncovered_and_defaults_pool(conn):
    _ann(conn, "000001", "只有东财源的公告", "akshare_dc")
    _ann(conn, "000002", "巨潮独有", "cninfo")
    conn.execute(
        "INSERT INTO pool_entry(code,event_id,status,grade,entered_at,resonance_count,"
        "updated_at) VALUES('000001',NULL,'观察中','S','2026-09-01',0,'2026-09-01')")
    conn.commit()
    out = recon.recon_day(conn, DAY)  # 默认池内 = 只有 000001
    assert out["universe"] == 1 and out["compared"] == 1
    assert out["rows"][0]["only_dc"] == ["只有东财源的公告"]  # 单边覆盖如实展示
    assert recon.available_dates(conn) == [DAY]


@pytest.fixture
def client(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "m6api.db")
    db.init_schema(c)
    conn = c
    test_recon_day_diff_three_shapes(conn)
    conn.execute(
        "INSERT INTO pool_entry(code,event_id,status,grade,entered_at,resonance_count,"
        "updated_at) VALUES('002348',NULL,'观察中','S','2026-09-01',0,'2026-09-01')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "m6api.db")
    with TestClient(main.app) as cl:
        yield cl


def test_recon_api(client):
    dates = client.get("/api/recon/dates").json()["dates"]
    assert dates == [DAY]
    body = client.get("/api/recon").json()
    assert body["date"] == DAY and body["rows"][0]["code"] == "002348"
    assert client.get("/api/recon", params={"day": "2020-01-01"}).json()["compared"] == 0
    assert client.get("/api/recon", params={"day": "坏日期"}).status_code == 422
    one = client.get("/api/recon", params={"codes": "002348,999999"}).json()
    assert one["universe"] == 2 and one["compared"] == 1
