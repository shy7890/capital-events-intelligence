"""M5 素材包测试（PRD F8）：build/to_markdown/export_files 与 API 下载契约。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from zhuang_monitor import api, db, main, material  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m5m.db")
    db.init_schema(c)
    yield c
    c.close()


def _seed(conn) -> None:
    conn.execute(
        "INSERT INTO stock(code,name,board,list_date,is_st,total_mv,circ_mv,updated_at)"
        " VALUES('002348','高乐股份','深市主板','2010-02-03',0,120.5,101.1,'2026-09-01')")
    conn.execute(
        "INSERT INTO event(id,code,event_type,grade,start_date,status,"
        "first_announcement_id,created_at,updated_at)"
        " VALUES(1,'002348','控制权变更','S','2025-11-25','已完成',1,'2026-01-01','2026-02-13')")
    conn.execute(
        "INSERT INTO announcement(id,code,pub_date,title,pdf_url,category,source,fetched_at)"
        " VALUES(1,'002348','2025-11-25','关于筹划控制权变更的停牌公告',"
        "'http://x/1.pdf',NULL,'cninfo','2026-01-01')")
    conn.execute(
        "INSERT INTO flow_node(event_id,node_type,node_date,announcement_id,remark,created_at)"
        " VALUES(1,'筹划停牌','2025-11-25',1,NULL,'2026-01-01')")
    conn.execute(
        "INSERT INTO flow_node(event_id,node_type,node_date,announcement_id,remark,created_at)"
        " VALUES(1,'过户完成','2026-02-13',NULL,NULL,'2026-02-13')")
    conn.execute(
        "INSERT INTO pool_entry(code,event_id,status,grade,entered_at,resonance_count,"
        "exit_signals,note,updated_at)"
        " VALUES('002348',1,'催化兑现中','S','2025-11-25',3,'[\"户数回升\"]','跟踪定增节奏',"
        "'2026-02-13')")
    conn.executemany(
        "INSERT INTO holder_num(code,disclose_date,holder_num,pct_change,avg_hold,source)"
        " VALUES('002348',?,?,-8.5,12000,'em')",
        [("2026-03-31", 40000), ("2026-06-30", 36600)])
    for d, close in [("2026-08-27", 10.0), ("2026-08-28", 10.5),
                     ("2026-08-31", 12.0), ("2026-09-01", 13.0)]:
        conn.execute(
            "INSERT INTO kline_daily(code,trade_date,open,high,low,close,volume,amount,"
            "turnover,adj_factor,source) VALUES('002348',?,?,?,?,?,1000,1e8,1.5,1.0,'test')",
            (d, close, close, close, close))
    conn.execute(
        "INSERT INTO watch_note(code,trade_date,content,tags,created_at)"
        " VALUES('002348','2026-08-28','洗盘迹象明显','盘口','2026-08-28T20:00:00')")
    conn.execute(
        "INSERT INTO watchlist_entry(code,status,note,added_at,updated_at) "
        "VALUES('002348','active','素材备注','2026-08-27','2026-09-01')")
    conn.commit()


def test_build_material_none_for_unknown(conn):
    assert material.build_material(conn, "999999") is None


def test_build_material_full_shape(conn):
    _seed(conn)
    m = material.build_material(conn, "002348", today="2026-09-02")
    assert m["profile"]["name"] == "高乐股份"
    assert m["events"][0]["nodes"][0]["title"] == "关于筹划控制权变更的停牌公告"
    assert m["pool"][0]["exit_signals"] == ["户数回升"]
    assert len(m["holders"]) == 2
    assert m["kline"]["rows"] == 4 and m["kline"]["last_close_qfq"] == 13.0
    assert m["event_anchor_returns"][0]["since_pct"] == 30.0
    assert m["notes"][0]["content"] == "洗盘迹象明显"
    assert m["watchlist"]["status"] == "active" and m["watchlist"]["note"] == "素材备注"
    assert json.loads(json.dumps(m, ensure_ascii=False, default=str))


def test_markdown_sections_and_download_links(conn):
    _seed(conn)
    m = material.build_material(conn, "002348", today="2026-09-02")
    md = material.to_markdown(m)
    for probe in ["素材包：高乐股份（002348）", "## 1. 股票档案", "## 2. 候选池状态",
                  "控制权变更（S 级｜已完成", "过户完成", "## 4. K 线摘要与区间收益",
                  "30.0", "## 5. 股东户数序列", "## 6. 公告清单", "## 7. 人工笔记",
                  "洗盘迹象明显", "提醒均非投资决策"]:
        assert probe in md, probe


def test_export_files_writes_pair_to_outdir(conn, tmp_path):
    _seed(conn)
    out = material.export_files(conn, "002348", tmp_path / "ex")
    assert out is not None
    md_path, json_path = out
    assert md_path.name.startswith("material_002348_") and md_path.suffix == ".md"
    assert json_path.exists()
    assert "高乐股份" in md_path.read_text(encoding="utf-8")


# ---------- API 契约 ----------


@pytest.fixture
def client(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "m5api.db")
    db.init_schema(c)
    _seed(c)
    c.close()
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "m5api.db")
    with TestClient(main.app) as cl:
        yield cl


def test_api_export_md_attachment(client):
    r = client.get("/api/export/002348?fmt=md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"] and "material_002348" in r.headers["content-disposition"]
    assert "资本运作素材包" in r.text


def test_api_export_json_and_errors(client):
    r = client.get("/api/export/002348", params={"fmt": "json"})
    assert r.status_code == 200
    assert r.json()["profile"]["code"] == "002348"
    assert client.get("/api/export/999999").status_code == 404
    assert client.get("/api/export/002348", params={"fmt": "exe"}).status_code == 422
