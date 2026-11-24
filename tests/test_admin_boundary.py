from fastapi.testclient import TestClient
from pydantic import SecretStr

from zhuang_monitor import api, db, main


def test_research_api_requires_login_and_rejects_cross_origin_writes(monkeypatch, tmp_path):
    monkeypatch.delitem(main.app.dependency_overrides, main.require_admin, raising=False)
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "test.db")
    with db.connect(api.DB_PATH) as conn:
        db.init_schema(conn)
    client = TestClient(main.app)
    password = "long-test-only-password"
    monkeypatch.setattr(main.settings, "admin_password", None)
    assert client.get("/api/watchlist").status_code == 503
    monkeypatch.setattr(main.settings, "admin_password", SecretStr(password))
    assert client.get("/").status_code == 401
    assert client.get("/api/watchlist").status_code == 401
    auth = (main.settings.admin_username, password)
    assert client.get("/api/watchlist", auth=auth).status_code == 200
    assert client.post(
        "/api/notifications/read", auth=auth, headers={"Origin": "http://elsewhere.invalid"}
    ).status_code == 403
    assert client.post(
        "/api/notifications/read", auth=auth, headers={"Origin": "http://testserver"}
    ).status_code == 200
