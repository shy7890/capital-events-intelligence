"""v1.1 后端整改测试：候选池自动归档（以最后流程节点日为活跃度依据）。"""

from __future__ import annotations

import pytest

from zhuang_monitor import db, ingest, pipeline

TODAY = "2026-03-05"
STALE_NODE = "2025-12-01"    # < 30天线(2026-02-03) 且 < 90天线(2025-12-05)
RECENT_NODE = "2026-03-01"   # 近 30 天内


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "v11.db")
    db.init_schema(c)
    yield c
    c.close()


def _ev(conn, eid, code, status, start):
    conn.execute(
        "INSERT INTO event(id,code,event_type,grade,start_date,status,created_at,updated_at)"
        " VALUES(?,?,'定增','S',?,?,?,?)",
        (eid, code, start, status, "x", "2026-09-01"))
    return eid


def _node(conn, eid, day):
    conn.execute(
        "INSERT INTO flow_node(event_id,node_type,node_date,created_at) VALUES(?,?,?,?)",
        (eid, "问询回复", day, "x"))


def _pe(conn, code, eid, status, *, signals="[]", updated=TODAY, grade="S"):
    conn.execute(
        "INSERT INTO pool_entry(code,event_id,status,grade,entered_at,resonance_count,"
        "exit_signals,updated_at) VALUES(?,?,?,?,0,?,?,?)",
        (code, eid, status, grade, "2025-06-01", signals, updated))


def _statuses(conn) -> dict[str, str]:
    return {r["code"]: r["status"]
            for r in conn.execute("SELECT code,status FROM pool_entry").fetchall()}


def _seed(conn) -> None:
    # 1 终止→即归档；2 已了结但节点陈旧→归档；3 已了结且节点新鲜→保留(看解禁)；
    # 4 进行中且活跃→保留；5 退出信号静置30天→归档；6 800亿→归档；
    # 7 进行中活跃但事件起始超1年→归档(时效)；8 进行中90天无节点→归档(僵尸)
    _pe(conn, "700001", _ev(conn, 1, "700001", "终止", "2026-01-05"), "离场")
    _pe(conn, "700002", _ev(conn, 2, "700002", "已完成", "2026-01-05"), "催化兑现中")
    _node(conn, 2, STALE_NODE)
    _pe(conn, "700003", _ev(conn, 3, "700003", "已完成", "2026-01-05"), "催化兑现中")
    _node(conn, 3, RECENT_NODE)
    _pe(conn, "700004", _ev(conn, 4, "700004", "进行中", "2026-01-15"), "观察中")
    _node(conn, 4, "2026-02-20")
    _pe(conn, "700005", _ev(conn, 5, "700005", "进行中", "2026-02-01"), "已出信号",
        signals='["户数回升"]', updated="2026-01-01")
    _node(conn, 5, RECENT_NODE)
    _pe(conn, "700006", _ev(conn, 6, "700006", "进行中", "2026-02-01"), "观察中")
    _node(conn, 6, RECENT_NODE)
    _pe(conn, "700007", _ev(conn, 7, "700007", "进行中", "2024-06-01"), "新入池")
    _node(conn, 7, RECENT_NODE)
    _pe(conn, "700008", _ev(conn, 8, "700008", "进行中", "2025-10-01"), "新入池")
    _node(conn, 8, "2025-11-01")   # < 2025-12-05 僵尸线
    conn.execute(
        "INSERT INTO stock(code,name,board,list_date,is_st,circ_mv,updated_at) VALUES"
        "('700006','大盘6','沪市主板','2010-01-01',0,800.0,'x'),"
        "('700001','股1','沪市主板','2010-01-01',0,90.0,'x'),"
        "('700002','股2','沪市主板','2010-01-01',0,90.0,'x'),"
        "('700003','股3','沪市主板','2010-01-01',0,90.0,'x'),"
        "('700004','股4','沪市主板','2010-01-01',0,90.0,'x'),"
        "('700005','股5','沪市主板','2010-01-01',0,90.0,'x'),"
        "('700007','股7','沪市主板','2010-01-01',0,90.0,'x'),"
        "('700008','股8','沪市主板','2010-01-01',0,90.0,'x')")
    conn.commit()


def test_archive_rules(conn):
    _seed(conn)
    n = pipeline.archive_pool_now(conn, today=TODAY)
    st = _statuses(conn)
    assert n == 6
    assert st["700001"] == "归档"          # 终止即归档
    assert st["700002"] == "归档"          # 已了结+节点陈旧
    assert st["700003"] == "催化兑现中"     # 已了结但刚有节点 → 保留
    assert st["700004"] == "观察中"         # 进行中活跃 → 保留
    assert st["700005"] == "归档"          # 退出信号静置 30 天
    assert st["700006"] == "归档"          # 市值超限（即使进行中且活跃）
    assert st["700007"] == "归档"          # 活跃但事件起始超 365 天
    assert st["700008"] == "归档"          # 90 天无节点僵尸
    assert pipeline.archive_pool_now(conn, today=TODAY) == 0  # 幂等


def test_archive_respects_pool_mv_cap_config(conn):
    _ev(conn, 9, "700009", "进行中", "2026-02-01")
    _node(conn, 9, RECENT_NODE)
    _pe(conn, "700009", 9, "观察中")
    conn.execute(
        "INSERT INTO stock(code,name,board,list_date,is_st,circ_mv,updated_at)"
        " VALUES('700009','超限9','沪市主板','2010-01-01',0,150.0,'x')")
    conn.commit()
    assert pipeline.archive_pool_now(conn, today=TODAY) == 0  # 150<300 保留
    ingest.set_config(conn, "scan_params", {"pool_mv_max": 100})
    assert pipeline.archive_pool_now(conn, today=TODAY) == 1  # 阈值调低 → 归档
