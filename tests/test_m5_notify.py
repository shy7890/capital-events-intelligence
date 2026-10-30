"""M5 提醒接线测试：日期闸/配置闸/环境闸、osascript 转义与失败吞掉、分类器静默历史。

conftest 已设 ZM_NOTIFY_OFF=1（hermetic）；需要放开时用 monkeypatch.delenv。
绝不真正调用 osascript。
"""

from __future__ import annotations

from datetime import date

import pytest

from zhuang_monitor import db, ingest, notify, pipeline
from zhuang_monitor.events import classifier

TODAY = date.today().isoformat()


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m5n.db")
    db.init_schema(c)
    yield c
    c.close()


class _R:
    returncode = 0


def test_should_push_date_gate(conn, monkeypatch):
    monkeypatch.delenv("ZM_NOTIFY_OFF", raising=False)
    assert notify.should_push(conn, TODAY, today=TODAY)
    assert notify.should_push(conn, f"{TODAY}T20:00:00", today=TODAY)
    assert not notify.should_push(conn, "2026-08-01", today=TODAY)
    assert not notify.should_push(conn, None, today=TODAY)


def test_env_and_config_gates(conn, monkeypatch):
    monkeypatch.setenv("ZM_NOTIFY_OFF", "1")
    assert not notify.should_push(conn, TODAY, today=TODAY)
    monkeypatch.delenv("ZM_NOTIFY_OFF")
    ingest.set_config(conn, "notify_macos", 0)
    assert not notify.should_push(conn, TODAY, today=TODAY)
    ingest.set_config(conn, "notify_macos", 1)
    assert notify.should_push(conn, TODAY, today=TODAY)


def test_push_escapes_quotes_and_calls_osascript(monkeypatch):
    cmds = []
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.subprocess, "run",
                        lambda cmd, **kw: cmds.append(cmd) or _R())
    assert notify.push('甲"定增"入池', "正文含\\反斜杠") is True
    assert cmds[0][0].endswith("osascript")
    script = cmds[0][2]
    assert '\\"定增\\"' in script and "\\\\反斜杠" in script
    assert "提醒非决策" in script


def test_push_failure_swallowed(monkeypatch):
    def boom(*a, **k):
        raise OSError("osascript 不存在")
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.subprocess, "run", boom)
    assert notify.push("t", "b") is False


def test_push_skips_non_darwin(monkeypatch):
    calls: list = []
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.subprocess, "run",
                        lambda *a, **k: calls.append(1))
    assert notify.push("t", "b") is False
    assert calls == []


def _ann(conn, code, pub_date, title):
    ingest.upsert_announcement(conn, [{
        "code": code, "pub_date": pub_date, "title": title,
        "pdf_url": None, "category": None, "source": "cninfo",
    }])


def test_classifier_history_stays_silent_today_pushes(conn, monkeypatch):
    monkeypatch.delenv("ZM_NOTIFY_OFF", raising=False)
    pushed: list = []
    monkeypatch.setattr(notify, "push", lambda t, b: pushed.append((t, b)) or True)

    _ann(conn, "000001", "2026-08-01", "关于公司2026年度向特定对象发行股票预案的公告")
    classifier.classify_events(conn, now="2026-08-01T12:00:00+00:00")
    assert pushed == []  # 历史公告：只落库不弹通知

    _ann(conn, "000002", TODAY, "关于公司2026年度向特定对象发行股票预案的公告")
    classifier.classify_events(conn, codes=["000002"], now=f"{TODAY}T12:00:00+00:00")
    assert len(pushed) == 1 and "000002" in pushed[0][0]
    assert conn.execute("SELECT COUNT(*) FROM notification").fetchone()[0] == 2


def test_classifier_today_config_off_no_push(conn, monkeypatch):
    monkeypatch.delenv("ZM_NOTIFY_OFF", raising=False)
    ingest.set_config(conn, "notify_macos", 0)
    pushed: list = []
    monkeypatch.setattr(notify, "push", lambda t, b: pushed.append((t, b)))
    _ann(conn, "000003", TODAY, "关于公司2026年度向特定对象发行股票预案的公告")
    classifier.classify_events(conn, now=f"{TODAY}T12:00:00+00:00")
    assert pushed == []


def _seed_watchlist_changes(conn):
    conn.execute(
        "INSERT INTO stock(code,name,board,list_date,is_st,circ_mv,updated_at) "
        "VALUES('002348','高乐股份','深市主板','2010-02-03',0,80,'2026-09-02')"
    )
    conn.execute(
        "INSERT INTO watchlist_entry(code,status,note,added_at,updated_at) "
        "VALUES('002348','active','跟踪备注','2026-09-01','2026-09-02')"
    )
    conn.execute(
        "INSERT INTO announcement(id,code,pub_date,title,source,fetched_at) "
        "VALUES(1,'002348','2026-09-02','关于股东减持股份计划的公告','cninfo','2026-09-02')"
    )
    conn.execute(
        "INSERT INTO event(id,code,event_type,grade,start_date,status,created_at,updated_at) "
        "VALUES(1,'002348','控制权变更','S','2026-08-20','进行中','2026-08-20','2026-09-02')"
    )
    conn.execute(
        "INSERT INTO flow_node(event_id,node_type,node_date,announcement_id,created_at) "
        "VALUES(1,'方案披露','2026-09-02',1,'2026-09-02')"
    )
    conn.executemany(
        "INSERT INTO holder_num(code,disclose_date,holder_num,pct_change,source) "
        "VALUES('002348',?,?,?,'test')",
        [("2026-06-30", 50000, -8.0), ("2026-09-02", 57500, 15.0)],
    )
    for d, close in [("2026-08-20", 100.0), ("2026-08-21", 140.0),
                     ("2026-08-22", 138.0), ("2026-09-02", 90.0)]:
        conn.execute(
            "INSERT INTO kline_daily(code,trade_date,open,high,low,close,amount,adj_factor,source) "
            "VALUES('002348',?,?,?,?,?,1000000,1.0,'test')",
            (d, close, close, close, close),
        )
    conn.execute("INSERT INTO longhu(code,trade_date,reason,source) VALUES('002348','2026-08-21','涨幅','test')")
    conn.execute(
        "INSERT INTO scan_result(scan_date,code,gain_pct,lhb_after_start,holder_drop_pct,circ_mv,"
        "avg_amount,resonance,tier,hits_json,start_date,start_age_days,lane,mechanical_start_date,"
        "confirmed_start_date,missing_fields_json) "
        "VALUES('2026-09-02','002348',-35,1,15,80,1000000,2,'B','[]','2026-08-20',9,'新起涨',"
        "'2026-08-20','2026-08-20','[]')"
    )
    conn.commit()


def test_watchlist_change_evaluation_is_actionable_and_idempotent(conn, monkeypatch):
    _seed_watchlist_changes(conn)
    monkeypatch.setattr(notify, "push", lambda *args: True)
    first = pipeline.evaluate_watchlist(conn, run_date=TODAY)
    kinds = {item["type"] for item in first["rows"][0]["changes"]}
    assert {"event", "node", "holder_rebound", "drawdown", "lhb_after_start", "reduction"} <= kinds
    assert first["notified"] >= 6
    assert first["rows"][0]["price_change"]["pct"] < 0
    assert conn.execute("SELECT COUNT(*) FROM notification").fetchone()[0] == first["notified"]

    second = pipeline.evaluate_watchlist(conn, run_date=TODAY)
    assert second["notified"] == 0
    assert conn.execute("SELECT COUNT(*) FROM notification").fetchone()[0] == first["notified"]
    assert not any("价格" in r["title"] for r in conn.execute("SELECT title FROM notification").fetchall())


def test_watchlist_missing_data_is_visible_and_targeted(conn):
    conn.execute(
        "INSERT INTO stock(code,name,is_st,updated_at) VALUES('000002','缺数样例',0,?)", (TODAY,)
    )
    conn.execute(
        "INSERT INTO watchlist_entry(code,status,added_at,updated_at) VALUES('000002','active',?,?)",
        (TODAY, TODAY),
    )
    conn.execute(
        "INSERT INTO scan_result(scan_date,code,resonance,tier,hits_json,missing_fields_json) "
        "VALUES(?,?,?,?,?,?)", (TODAY, "000002", 1, "待确认", "[]", '["holder_num"]'),
    )
    conn.commit()
    result = pipeline.evaluate_watchlist(conn, run_date=TODAY)
    row = next(r for r in result["rows"] if r["code"] == "000002")
    assert "holder_num" in row["missing_fields"]
    assert any(c["type"] == "missing_data" for c in row["changes"])
