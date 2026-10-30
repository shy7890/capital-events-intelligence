"""M3 REST API 验收测试：/api 八组端点契约（与前端组共享同一份契约，逐字段校验）。

手法沿用 test_m0：tmp_path 建库 + monkeypatch 注入 api.DB_PATH，TestClient(main.app)。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from zhuang_monitor import api, db, main  # noqa: E402

TODAY = date.today().isoformat()

POOL_ROW_KEYS = {
    "code", "name", "board", "circ_mv", "grade", "status", "event_type",
    "event_status", "start_date", "resonance_count", "exit_signals", "note",
    "entered_at",
}


def _seed(conn) -> None:
    """各表塞样例数据；覆盖 LEFT JOIN 空位（900001 无 stock 无 event）与坏 JSON。"""
    conn.executemany(
        "INSERT INTO stock(code,name,board,list_date,is_st,total_mv,circ_mv,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        [
            ("002348", "高乐股份", "深市主板", "2009-09-22", 0, 45.2, 33.1, TODAY),
            ("600000", "浦发银行", "沪市主板", "1999-11-10", 0, 3000.0, 2900.0, TODAY),
        ],
    )
    conn.executemany(
        "INSERT INTO announcement(code,pub_date,title,pdf_url,category,source,fetched_at) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            ("002348", "2024-03-01", "高乐股份:2024年度向特定对象发行股票预案",
             "http://static.cninfo.com.cn/finalpage/2024/03/01/1.pdf", "dz", "cninfo", TODAY),
            ("002348", "2024-04-02", "高乐股份:关于向特定对象发行股票申请收到受理函的公告",
             "http://static.cninfo.com.cn/finalpage/2024/04/02/2.pdf", "dz", "cninfo", TODAY),
        ],
    )
    conn.executemany(
        "INSERT INTO event(id,code,event_type,grade,start_date,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        [
            (1, "002348", "定增", "A", "2024-03-01", "进行中", TODAY, TODAY),
            (2, "002348", "重组", "S", "2024-06-01", "终止", TODAY, TODAY),
            (3, "600000", "控制权变更", "S", "2024-04-20", "进行中", TODAY, TODAY),
        ],
    )
    conn.executemany(
        "INSERT INTO flow_node(id,event_id,node_type,node_date,announcement_id,remark,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            (1, 1, "筹划", "2024-03-01", 1, None, TODAY),
            (2, 1, "受理", "2024-04-02", 2, None, TODAY),
            (3, 2, "方案披露", "2024-06-10", None, None, TODAY),
            (4, 2, "终止", "2024-07-01", None, None, TODAY),
        ],
    )
    conn.executemany(
        "INSERT INTO pool_entry(id,code,event_id,status,grade,entered_at,resonance_count,"
        "exit_signals,note,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "002348", 1, "观察中", "A", "2024-03-02T10:00:00", 2,
             '["户数集中", "放量异动"]', '"关注 4 月解禁"', TODAY),
            (2, "600000", 3, "新入池", "S", "2024-05-01T09:00:00", 1,
             "[]", '"等协议转让过户"', TODAY),
            (3, "900001", None, "重点跟踪", "B", "2024-01-01T00:00:00", 0,
             "坏数据{", "坏数据{", TODAY),
        ],
    )
    conn.executemany(
        "INSERT INTO holder_num(code,disclose_date,holder_num,pct_change,avg_hold,total_mv,source) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            ("002348", "2023-12-31", 50000.0, 3.0, 1600000.0, 79.2e8, "akshare"),
            ("002348", "2024-03-15", 44000.0, -12.5, 1800000.0, 79.2e8, "akshare"),
        ],
    )
    conn.executemany(
        "INSERT INTO kline_daily(code,trade_date,open,high,low,close,volume,amount,turnover,"
        "adj_factor,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("002348", "2024-01-02", 6.8, 7.0, 6.7, 6.9, 1200000.0, 8.2e8, 1.26, 1.25, "baostock"),
            ("002348", "2024-01-03", 6.9, 7.1, 6.85, 7.05, 980000.0, 6.9e8, 1.03, 1.25, "baostock"),
            ("002348", "2024-01-04", 7.05, 7.3, 7.0, 7.2, 1100000.0, 8.0e8, 1.15, 1.30, "baostock"),
            ("002348", "2024-01-05", 7.2, 7.4, 7.15, 7.3, 1050000.0, 7.7e8, 1.10, None, "baostock"),
        ],
    )
    conn.executemany(
        "INSERT INTO watch_note(code,trade_date,content,tags,created_at) VALUES(?,?,?,?,?)",
        [
            ("002348", "2024-03-01", "开始观察定增节奏", "定增", "2024-03-01T10:00:00"),
            ("002348", "2024-03-05", "户数继续下降，疑似收集筹码", "户数,集中", "2024-03-05T12:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO notification(ts,level,title,body,is_read) VALUES(?,?,?,?,?)",
        [
            ("2024-03-01T09:00:00", "info", "入池：002348 定增", "新入池", 0),
            ("2024-03-02T09:00:00", "warn", "户数异动：002348", "环比 -12.5%", 0),
            ("2024-03-03T09:00:00", "alert", "已读样例", "昨天看过了", 1),
        ],
    )
    conn.executemany(
        "INSERT INTO scan_run(run_date,stage,status,stats_json,errors,started_at,finished_at) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            (TODAY, "fetch", "green", '{"announcements": 120}', None,
             f"{TODAY}T17:30:00", f"{TODAY}T17:35:00"),
            (TODAY, "scan", "red", None, "timeout", f"{TODAY}T21:30:00", None),
            (TODAY, "notify", "green", "坏数据{", None, f"{TODAY}T21:40:00", f"{TODAY}T21:41:00"),
            ("2020-01-01", "refresh", "green", '{"old": 1}', None, "2020-01-01T08:30:00",
             "2020-01-01T08:31:00"),
        ],
    )
    conn.commit()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    dbfile = tmp_path / "m3.db"
    with db.connect(dbfile) as conn:
        db.init_schema(conn)
        _seed(conn)
    monkeypatch.setattr(api, "DB_PATH", dbfile)
    return TestClient(main.app)


# ---------- GET /api/pool ----------

def test_pool_basic(client: TestClient) -> None:
    body = client.get("/api/pool").json()
    assert set(body) == {"total", "rows"}
    assert body["total"] == 3
    assert len(body["rows"]) == 3
    assert set(body["rows"][0]) == POOL_ROW_KEYS
    # 默认按 entered_at 降序：002348(03-02) > 600000(05-01 更晚? ) —— 按值断言而非顺序巧合
    entered = [r["entered_at"] for r in body["rows"]]
    assert entered == sorted(entered, reverse=True)
    by_code = {r["code"]: r for r in body["rows"]}
    r1 = by_code["002348"]
    assert r1["name"] == "高乐股份" and r1["board"] == "深市主板" and r1["circ_mv"] == 33.1
    assert r1["grade"] == "A" and r1["status"] == "观察中"
    assert r1["event_type"] == "定增" and r1["event_status"] == "进行中"
    assert r1["start_date"] == "2024-03-01" and r1["resonance_count"] == 2
    assert r1["exit_signals"] == ["户数集中", "放量异动"]
    assert r1["note"] == "关注 4 月解禁"
    # LEFT JOIN 两侧都缺行的 900001：字段为 None 且坏 JSON 容错为默认值，不 500
    r3 = by_code["900001"]
    assert r3["name"] is None and r3["board"] is None and r3["circ_mv"] is None
    assert r3["event_type"] is None and r3["event_status"] is None and r3["start_date"] is None
    assert r3["exit_signals"] == [] and r3["note"] == ""


def test_pool_status_filter(client: TestClient) -> None:
    body = client.get("/api/pool", params={"status": "观察中"}).json()
    assert body["total"] == 1
    assert [r["code"] for r in body["rows"]] == ["002348"]


def test_pool_grade_and_q_filters(client: TestClient) -> None:
    assert client.get("/api/pool", params={"grade": "S"}).json()["total"] == 1
    # q 同时模糊匹配 code 与 name
    assert client.get("/api/pool", params={"q": "高乐"}).json()["total"] == 1
    assert client.get("/api/pool", params={"q": "9000"}).json()["total"] == 1
    assert client.get("/api/pool", params={"q": "乐股"}).json()["total"] == 1
    assert client.get("/api/pool", params={"q": "不存在的词"}).json() == {"total": 0, "rows": []}
    # 组合过滤
    body = client.get("/api/pool", params={"status": "重点跟踪", "q": "900001"}).json()
    assert body["total"] == 1


def test_pool_reviewed_filter_separates_seen_and_unseen(client: TestClient) -> None:
    """看过筛选只按 stock_rating 区分，且保留全部候选的默认行为。"""
    with db.connect(api.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO stock_rating(code,rating,reviewed_at,updated_at) VALUES(?,?,?,?)",
            ("002348", "medium", TODAY, TODAY),
        )
        conn.commit()

    assert client.get("/api/pool").json()["total"] == 3
    seen = client.get("/api/pool", params={"reviewed": "reviewed"}).json()
    unseen = client.get("/api/pool", params={"reviewed": "unreviewed"}).json()
    legacy_unseen = client.get("/api/pool", params={"hide_reviewed": 1}).json()
    assert [r["code"] for r in seen["rows"]] == ["002348"]
    assert {r["code"] for r in unseen["rows"]} == {"600000", "900001"}
    assert [r["code"] for r in legacy_unseen["rows"]] == [r["code"] for r in unseen["rows"]]
    assert client.get("/api/pool", params={"reviewed": "invalid"}).status_code == 422


def test_pool_pagination_and_limit_cap(client: TestClient) -> None:
    body = client.get("/api/pool", params={"limit": 1, "offset": 2}).json()
    assert body["total"] == 3 and len(body["rows"]) == 1
    assert client.get("/api/pool", params={"limit": 999}).status_code == 422


# ---------- GET /api/events/{code} ----------

def test_events_by_code(client: TestClient) -> None:
    body = client.get("/api/events/002348").json()
    assert set(body) == {"events"}
    events = body["events"]
    assert [e["id"] for e in events] == [1, 2]  # start_date 升序
    assert set(events[0]) == {"id", "event_type", "grade", "start_date", "status", "nodes"}
    assert events[0]["event_type"] == "定增" and events[0]["status"] == "进行中"
    assert set(events[0]["nodes"][0]) == {"node_type", "node_date", "title", "pdf_url"}
    assert [n["node_date"] for n in events[0]["nodes"]] == ["2024-03-01", "2024-04-02"]
    assert events[0]["nodes"][0]["title"] == "高乐股份:2024年度向特定对象发行股票预案"
    assert events[0]["nodes"][0]["pdf_url"].endswith("1.pdf")
    # 无关联公告的节点：title/pdf_url 为 None 而非 500
    assert [n["title"] for n in events[1]["nodes"]] == [None, None]


def test_events_unknown_code_returns_empty(client: TestClient) -> None:
    assert client.get("/api/events/000000").json() == {"events": []}


# ---------- GET /api/kline/{code} ----------

def test_kline_close_qfq(client: TestClient) -> None:
    body = client.get("/api/kline/002348").json()
    assert set(body) == {"code", "name", "rows"}
    assert body["code"] == "002348" and body["name"] == "高乐股份"
    rows = body["rows"]
    assert [r["trade_date"] for r in rows] == sorted(r["trade_date"] for r in rows)  # 升序
    assert set(rows[0]) == {
        "trade_date", "open", "high", "low", "close", "volume", "amount",
        "turnover", "close_qfq",
    }
    # 基准 = 最新非空因子 1.30；6.9*1.25/1.3=6.6346, 7.05*1.25/1.3=6.7788, 7.2*1.3/1.3=7.2
    assert [r["close_qfq"] for r in rows] == [6.6346, 6.7788, 7.2, 7.3]
    # adj_factor 为空的行：close_qfq = close 原值
    assert rows[3]["close"] == 7.3


def test_kline_range_filter_and_empty_code(client: TestClient) -> None:
    body = client.get("/api/kline/002348", params={"start": "2024-01-03", "end": "2024-01-04"}).json()
    assert [r["trade_date"] for r in body["rows"]] == ["2024-01-03", "2024-01-04"]
    empty = client.get("/api/kline/999999").json()
    assert empty == {"code": "999999", "name": None, "rows": []}


# ---------- GET /api/holder/{code} ----------

def test_holder_ascending_and_empty_code(client: TestClient) -> None:
    rows = client.get("/api/holder/002348").json()["rows"]
    assert set(rows[0]) == {"disclose_date", "holder_num", "pct_change"}
    assert [r["disclose_date"] for r in rows] == ["2023-12-31", "2024-03-15"]  # 升序
    assert rows[1]["holder_num"] == 44000.0 and rows[1]["pct_change"] == -12.5
    assert client.get("/api/holder/999999").json() == {"rows": []}


# ---------- GET/POST /api/notes ----------

def test_notes_list_new_to_old(client: TestClient) -> None:
    body = client.get("/api/notes/002348").json()
    rows = body["rows"]
    assert set(rows[0]) == {"id", "code", "trade_date", "content", "tags", "created_at"}
    assert [r["content"] for r in rows] == ["户数继续下降，疑似收集筹码", "开始观察定增节奏"]
    assert rows[0]["tags"] == "户数,集中" and rows[0]["trade_date"] == "2024-03-05"
    assert client.get("/api/notes/999999").json() == {"rows": []}


def test_notes_create_query_and_validation(client: TestClient) -> None:
    resp = client.post("/api/notes", json={
        "code": "002348", "content": "过会了，注意减持公告", "tags": "过会",
        "trade_date": "2024-04-10",
    })
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    rows = client.get("/api/notes/002348").json()["rows"]
    assert rows[0]["id"] == new_id and rows[0]["content"] == "过会了，注意减持公告"
    # 未提供可选字段时落库为 None
    resp2 = client.post("/api/notes", json={"code": "600000", "content": "简单记一笔"})
    assert resp2.status_code == 201
    r2 = client.get("/api/notes/600000").json()["rows"][0]
    assert r2["tags"] is None and r2["trade_date"] is None
    # content 为空/纯空白 → 422
    assert client.post("/api/notes", json={"code": "002348", "content": ""}).status_code == 422
    assert client.post("/api/notes", json={"code": "002348", "content": "   "}).status_code == 422
    assert client.post("/api/notes", json={"code": "002348"}).status_code == 422


# ---------- GET/POST/PATCH/DELETE /api/watchlist ----------

def test_watchlist_crud_restore_and_history(client: TestClient) -> None:
    added = client.post("/api/watchlist", json={
        "code": "002348", "note": "重点观察控制权变更",
    })
    assert added.status_code == 201
    row = added.json()
    assert row["code"] == "002348" and row["status"] == "active"
    assert row["note"] == "重点观察控制权变更"
    assert row["name"] == "高乐股份" and "freshness" in row and "changes" in row

    # 重复加入不产生第二条活动记录，且允许顺手更新备注。
    again = client.post("/api/watchlist", json={"code": "002348", "note": "改后的备注"})
    assert again.status_code == 200
    assert again.json()["note"] == "改后的备注"
    assert client.get("/api/watchlist").json()["total"] == 1

    patched = client.patch("/api/watchlist/002348", json={"note": "最终备注"})
    assert patched.status_code == 200 and patched.json()["note"] == "最终备注"

    removed = client.delete("/api/watchlist/002348")
    assert removed.status_code == 200 and removed.json()["status"] == "history"
    assert client.get("/api/watchlist").json() == {"total": 0, "rows": []}
    history = client.get("/api/watchlist", params={"include_history": 1}).json()
    assert history["total"] == 1 and history["rows"][0]["status"] == "history"

    restored = client.post("/api/watchlist", json={"code": "002348"})
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    assert client.get("/api/watchlist", params={"include_history": 1}).json()["total"] == 1
    assert client.patch("/api/watchlist/002348", json={"status": "history"}).json()["status"] == "history"
    assert client.patch("/api/watchlist/002348", json={"status": "active"}).json()["status"] == "active"


def test_watchlist_rejects_unknown_stock_and_missing_entry(client: TestClient) -> None:
    missing = client.post("/api/watchlist", json={"code": "999999"})
    assert missing.status_code == 404 and "股票档案" in missing.json()["detail"]
    assert client.patch("/api/watchlist/002348", json={"note": "不存在"}).status_code == 404
    assert client.delete("/api/watchlist/002348").status_code == 404


# ---------- GET/POST /api/notifications ----------

def test_notifications_new_to_old_and_unread(client: TestClient) -> None:
    body = client.get("/api/notifications").json()
    rows = body["rows"]
    assert len(rows) == 3
    assert set(rows[0]) == {"id", "ts", "level", "title", "body", "is_read"}
    assert rows[0]["ts"] == "2024-03-03T09:00:00"  # 新→旧
    unread = client.get("/api/notifications", params={"unread": 1}).json()["rows"]
    assert [r["title"] for r in unread] == ["户数异动：002348", "入池：002348 定增"]
    assert all(r["is_read"] == 0 for r in unread)
    assert client.get("/api/notifications", params={"limit": 1}).json()["rows"].__len__() == 1
    assert client.get("/api/notifications", params={"limit": 501}).status_code == 422


def test_notifications_mark_all_read(client: TestClient) -> None:
    resp = client.post("/api/notifications/read")
    assert resp.status_code == 200
    assert resp.json() == {"updated": 2}
    assert client.get("/api/notifications", params={"unread": 1}).json() == {"rows": []}
    # 幂等：再次全部置已读，更新 0 条
    assert client.post("/api/notifications/read").json() == {"updated": 0}


# ---------- GET /api/report/daily ----------

def test_report_daily(client: TestClient) -> None:
    body = client.get("/api/report/daily").json()
    assert set(body) == {
        "date", "today_stages", "pool_by_status", "events_total",
        "pool_total", "recent_notifications", "market_refresh",
    }
    assert body["date"] == TODAY
    stages = body["today_stages"]  # 只含今天的 scan_run，旧记录不混入
    assert [s["stage"] for s in stages] == ["fetch", "scan", "notify"]
    assert set(stages[0]) == {"stage", "status", "stats"}
    assert stages[0]["stats"] == {"announcements": 120}
    assert stages[1]["stats"] == {}  # stats_json 为 NULL → 空对象
    assert stages[2]["stats"] == {}  # 坏 JSON → 空对象
    assert body["pool_by_status"] == {"观察中": 1, "新入池": 1, "重点跟踪": 1}
    assert body["events_total"] == 3
    assert body["pool_total"] == 3
    recent = body["recent_notifications"]
    assert len(recent) == 3 and recent[0]["ts"] == "2024-03-03T09:00:00"
    assert body["market_refresh"] == {
        "run_date": TODAY, "status": "unknown", "kline_total": 0,
        "kline_success": 0, "kline_skipped": 0, "kline_failed": 0,
        "kline_pending": 0, "longhu_status": "unknown", "blocking": False,
        "blocking_reason": None,
    }


def test_report_daily_market_refresh_counts_are_safe(client: TestClient) -> None:
    with db.connect(api.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO market_refresh(run_date,status,kline_total,kline_success,kline_skipped,"
            "kline_failed,longhu_status,blocking_reason,started_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (TODAY, "partial", 3, 1, 1, 1, "pending", "1 只 K 线失败", TODAY, TODAY),
        )
        conn.executemany(
            "INSERT INTO market_refresh_item(run_date,code,kind,status,updated_at) VALUES(?,?,?,?,?)",
            [(TODAY, "000001", "kline", "success", TODAY),
             (TODAY, "000002", "kline", "skipped", TODAY),
             (TODAY, "000003", "kline", "failed", TODAY)],
        )
        conn.commit()
    body = client.get("/api/report/daily").json()["market_refresh"]
    assert body["status"] == "partial" and body["blocking"] is True
    assert body["kline_total"] == 3 and body["kline_success"] == 1
    assert body["kline_skipped"] == 1 and body["kline_failed"] == 1
    assert body["kline_pending"] == 0
    assert body["blocking_reason"] == "有 1 只 K 线刷新失败，需稍后重试"


def test_report_daily_market_refresh_pending_longhu_has_actionable_reason(client: TestClient) -> None:
    with db.connect(api.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO market_refresh(run_date,status,kline_total,kline_success,kline_skipped,"
            "kline_failed,longhu_status,started_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (TODAY, "complete", 1, 1, 0, 0, "pending", TODAY, TODAY),
        )
        conn.execute(
            "INSERT INTO market_refresh_item(run_date,code,kind,status,updated_at) "
            "VALUES(?,?,?,?,?)", (TODAY, "000001", "kline", "success", TODAY)
        )
        conn.commit()
    body = client.get("/api/report/daily").json()["market_refresh"]
    assert body["blocking"] is True and body["blocking_reason"] == "龙虎榜尚未完成"


# ---------- 契约第 8 条：空库下所有查询端点返回空结构、不 500 ----------

def test_all_query_endpoints_on_empty_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dbfile = tmp_path / "empty.db"
    with db.connect(dbfile) as conn:
        db.init_schema(conn)
    monkeypatch.setattr(api, "DB_PATH", dbfile)
    c = TestClient(main.app)
    assert c.get("/api/pool").json() == {"total": 0, "rows": []}
    assert c.get("/api/events/002348").json() == {"events": []}
    assert c.get("/api/kline/002348").json() == {"code": "002348", "name": None, "rows": []}
    assert c.get("/api/holder/002348").json() == {"rows": []}
    assert c.get("/api/notes/002348").json() == {"rows": []}
    assert c.get("/api/notifications").json() == {"rows": []}
    assert c.post("/api/notifications/read").json() == {"updated": 0}
    body = c.get("/api/report/daily").json()
    assert body["today_stages"] == [] and body["pool_by_status"] == {}
    assert body["events_total"] == 0 and body["pool_total"] == 0
    assert body["recent_notifications"] == []


def test_static_assets_served(tmp_path):
    """前端构建产物必须能被访问：/ 出 HTML，/assets/* 出 JS/CSS（曾漏挂载致白屏）。"""
    import pytest as _pytest
    if not main.FRONTEND_DIST.exists():
        _pytest.skip("前端未构建（dist 不存在），跳过静态资源验证")
    with db.connect(tmp_path / "s.db") as conn:
        db.init_schema(conn)
    client = TestClient(main.app)
    resp = client.get("/")
    assert resp.status_code == 200
    import re
    m = re.search(r'src="(/assets/[^"]+)"', resp.text)
    assert m, "首页应引用构建后的 /assets 资源"
    asset = client.get(m.group(1))
    assert asset.status_code == 200 and len(asset.content) > 1000


# ---------- v1.1 候选池默认隐藏归档 + 市值上限 ----------

def test_pool_archived_hidden_and_mv_cap(tmp_path, monkeypatch) -> None:
    dbfile = tmp_path / "poolfilter.db"
    with db.connect(dbfile) as c:
        db.init_schema(c)
        c.executemany(
            "INSERT INTO stock(code,name,board,list_date,is_st,circ_mv,updated_at)"
            " VALUES(?,?,?,?,0,?,'x')",
            [("800001", "活跃票", "沪市主板", "2010-01-01", 90.0),
             ("800002", "归档票", "沪市主板", "2010-01-01", 90.0),
             ("800003", "大盘票", "沪市主板", "2010-01-01", 800.0)])
        c.executemany(
            "INSERT INTO pool_entry(id,code,status,grade,entered_at,resonance_count,"
            "updated_at) VALUES(?,?,?,?,?,0,'2026-02-01')",
            [(1, "800001", "观察中", "S", "2026-01-01"),
             (2, "800002", "归档", "A", "2026-01-01"),
             (3, "800003", "观察中", "S", "2026-01-01")])
        c.commit()
    monkeypatch.setattr(api, "DB_PATH", dbfile)
    cl = TestClient(main.app)
    codes = {r["code"] for r in cl.get("/api/pool", params={"limit": 100}).json()["rows"]}
    assert codes == {"800001", "800003"}          # 归档默认隐藏
    allc = {r["code"] for r in cl.get("/api/pool", params={"limit": 100, "include_archived": 1}
                                      ).json()["rows"]}
    assert allc == {"800001", "800002", "800003"}
    capped = cl.get("/api/pool", params={"limit": 100, "mv_max": 300}).json()
    assert {r["code"] for r in capped["rows"]} == {"800001"} and capped["total"] == 1
