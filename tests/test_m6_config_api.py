"""M6 配置管理 API 测试（PRD F10）：GET/PUT /api/config 白名单、范围、合并、即时生效。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from zhuang_monitor import api, db, ingest, main, scanner  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "m6.db")
    db.init_schema(c)
    c.close()
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "m6.db")
    with TestClient(main.app) as cl:
        yield cl


def test_get_config_shape_and_effective(client):
    body = client.get("/api/config").json()
    assert body["saved"] == {}
    assert body["effective"] == scanner.DEFAULT_PARAMS
    assert body["notify_macos"] is True
    assert "window" in body["bounds"]


def test_put_partial_update_merges_and_persists(client):
    r = client.put("/api/config", json={"scan_params": {"gain_min_pct": 30, "window": 90}})
    assert r.status_code == 200
    eff = r.json()["effective"]
    assert eff["gain_min_pct"] == 30 and eff["window"] == 90
    assert eff["holder_cum_drop_pct"] == scanner.DEFAULT_PARAMS["holder_cum_drop_pct"]
    # 再次部分更新不丢上次改动
    eff2 = client.put("/api/config", json={"scan_params": {"weak_days": 60}}).json()["effective"]
    assert eff2["gain_min_pct"] == 30 and eff2["weak_days"] == 60
    # 扫描器即时读到新值
    c = db.connect(Path(api.DB_PATH))
    assert scanner.resolve_params(c)["gain_min_pct"] == 30
    c.close()


def test_put_rejects_bad_keys_types_ranges(client):
    assert client.put("/api/config", json={"scan_params": {"evil": 1}}).status_code == 400
    assert client.put("/api/config", json={"scan_params": {"window": 3.5}}).status_code == 400
    assert client.put("/api/config", json={"scan_params": {"window": 5}}).status_code == 400
    assert client.put("/api/config", json={"scan_params": {"gain_min_pct": "x"}}).status_code == 400
    assert client.put("/api/config", json={"scan_params": {"gain_min_pct": True}}).status_code == 400
    assert client.put("/api/config", json={"notify_macos": 7}).status_code == 422


def test_put_mv_ordering_rule(client):
    r = client.put("/api/config", json={"scan_params": {"circ_mv_a_max": 400}})
    assert r.status_code == 400 and "市值" in r.json()["detail"]


def test_notify_switch_roundtrip(client):
    assert client.put("/api/config", json={"notify_macos": 0}).json()["notify_macos"] is False
    assert client.get("/api/config").json()["notify_macos"] is False
    c = db.connect(Path(api.DB_PATH))
    assert ingest.get_config(c, "notify_macos", 1) == 0
    c.close()


def test_get_config_ignores_dirty_saved_keys(client):
    c = db.connect(Path(api.DB_PATH))
    ingest.set_config(c, "scan_params", {"window": 100, "历史遗留": 1, "gain_min_pct": 200})
    c.commit()
    c.close()
    body = client.get("/api/config").json()
    assert "历史遗留" not in body["saved"] and body["effective"]["window"] == 100


# ---------- v1.1 新参数白名单 ----------

def test_v11_params_bounds(client):
    ok = client.put("/api/config", json={"scan_params": {
        "start_max_days": 30, "start_s_days": 15, "min_resonance": 4,
        "pool_mv_max": 200, "archive_after_days": 14}})
    assert ok.status_code == 200
    eff = ok.json()["effective"]
    assert eff["start_max_days"] == 30 and eff["min_resonance"] == 4 and eff["pool_mv_max"] == 200.0
    assert client.put("/api/config", json={"scan_params": {"start_max_days": 3.5}}).status_code == 400
    assert client.put("/api/config", json={"scan_params": {"start_max_days": 300}}).status_code == 400
