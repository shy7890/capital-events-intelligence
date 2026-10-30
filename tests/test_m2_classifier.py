"""M2 分类器测试：公告行 → 事件归并 / 节点推进 / 入池 / 提醒；幂等可重跑。真实 SQLite。"""

from __future__ import annotations

import pytest
import importlib.util
from pathlib import Path

from zhuang_monitor import db, ingest
from zhuang_monitor.events import classifier


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_schema(c)
    yield c
    c.close()


def _ann(conn, code, pub_date, title, source="cninfo"):
    ingest.upsert_announcement(conn, [{
        "code": code, "pub_date": pub_date, "title": title,
        "pdf_url": None, "category": None, "source": source,
    }])


def _events(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM event ORDER BY id").fetchall()]


def _nodes(conn):
    return [dict(r) for r in conn.execute(
        "SELECT f.node_type, f.node_date, f.event_id FROM flow_node f JOIN event e ON e.id=f.event_id"
        " ORDER BY f.node_date, f.id").fetchall()]


GAOLE_SAGA = [
    ("2025-11-25", "关于公司第一大股东筹划控制权变更的停牌公告"),
    ("2025-11-27", "关于公司第一大股东筹划控制权变更进展暨继续停牌的公告"),
    ("2025-12-02", "高乐股份:关于筹划公司控制权变更事项进展暨股票复牌公告"),
    ("2025-12-02", "关于控股股东签署《股份转让协议》及公司签署《附条件生效的股份认购协议》暨公司控股股东、实际控制人拟发生变更的提示性公告"),
    ("2025-12-02", "2025年度向特定对象发行A股股票预案"),
    ("2025-12-02", "广东高乐股份有限公司详式权益变动报告书"),
    ("2025-12-27", "关于筹划控制权变更的进展公告"),
    ("2026-01-28", "关于筹划控制权变更的进展公告"),
    ("2026-02-13", "关于股东协议转让过户完成暨公司控制权发生变更的提示性公告"),
    ("2026-04-04", "关于终止前次非公开发行股票事项的公告"),
    ("2026-04-04", "2026年度向特定对象发行A股股票预案"),
    ("2026-04-08", "关于召开2026年第一次临时股东会的通知"),
    ("2026-04-24", "2026年第一次临时股东会决议公告"),
]


def test_full_saga_events_and_flow(conn):
    for d, t in GAOLE_SAGA:
        _ann(conn, "002348", d, t)
    stats = classifier.classify_events(conn)
    assert stats["processed"] == len(GAOLE_SAGA)
    evs = _events(conn)
    types = {e["event_type"] for e in evs}
    assert {"控制权变更", "定增"} <= types
    cc = next(e for e in evs if e["event_type"] == "控制权变更")
    assert cc["grade"] == "S" and cc["start_date"] == "2025-11-25"
    assert cc["status"] == "已完成"
    nodes = [n["node_type"] for n in _nodes(conn) if n["event_id"] == cc["id"]]
    assert "筹划停牌" in nodes and "过户完成" in nodes
    # 定增两轮：2025-12 一轮被终止，2026-04 新开一轮进行中
    dds = [e for e in evs if e["event_type"] == "定增"]
    assert len(dds) == 2
    closed = next(e for e in dds if e["status"] == "终止")
    active = next(e for e in dds if e["status"] == "进行中")
    assert closed["start_date"] == "2025-12-02"
    assert active["start_date"] == "2026-04-04"
    # 股东大会通知/决议挂到新轮
    new_nodes = [n["node_type"] for n in _nodes(conn) if n["event_id"] == active["id"]]
    assert "方案披露" in new_nodes and "股东大会通过" in new_nodes


def test_pool_entry_and_exit(conn):
    for d, t in GAOLE_SAGA:
        _ann(conn, "002348", d, t)
    classifier.classify_events(conn)
    pools = [dict(r) for r in conn.execute("SELECT * FROM pool_entry ORDER BY id").fetchall()]
    assert pools and all(p["grade"] in ("S", "A") for p in pools)
    statuses = {p["status"] for p in pools}
    assert "新入池" in statuses or "观察中" in statuses


def test_notifications_generated(conn):
    for d, t in GAOLE_SAGA:
        _ann(conn, "002348", d, t)
    classifier.classify_events(conn)
    n = conn.execute("SELECT COUNT(*) FROM notification").fetchone()[0]
    assert n >= 5  # S 开事件与节点推进均有提醒
    title = conn.execute("SELECT title FROM notification WHERE level='alert' LIMIT 1").fetchone()
    assert title is not None


def test_rerun_is_idempotent(conn):
    for d, t in GAOLE_SAGA:
        _ann(conn, "002348", d, t)
    classifier.classify_events(conn)
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("event", "flow_node", "pool_entry", "notification")}
    stats2 = classifier.classify_events(conn)
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("event", "flow_node", "pool_entry", "notification")}
    assert before == after
    assert stats2["new_nodes"] == 0


def test_b_grade_only_attaches_to_open_events(conn):
    _ann(conn, "000001", "2024-05-01", "关于董事辞职的公告")           # 无进行中事件 → 不建事件
    _ann(conn, "000001", "2024-05-02", "2024年度向特定对象发行A股股票预案")  # 开定增
    _ann(conn, "000001", "2024-06-01", "关于董事辞职的公告")           # B 挂上
    stats = classifier.classify_events(conn)
    evs = _events(conn)
    assert [e["event_type"] for e in evs] == ["定增"]
    nodes = _nodes(conn)
    assert sum(1 for n in nodes if n["node_type"] == "高管变动") == 1


def test_termination_without_open_event_skipped(conn):
    _ann(conn, "600000", "2024-03-01", "关于终止前次非公开发行股票事项的公告")
    classifier.classify_events(conn)
    assert _events(conn) == []


def test_only_new_announcements_processed_with_watermark(conn):
    _ann(conn, "002348", "2025-11-25", "关于公司第一大股东筹划控制权变更的停牌公告")
    s1 = classifier.classify_events(conn)
    _ann(conn, "002348", "2025-12-02", "2025年度向特定对象发行A股股票预案")
    s2 = classifier.classify_events(conn)
    assert s2["processed"] == 1
    assert s2["new_events"] == 1


def test_completed_event_moves_pool_to_catalyst(conn):
    _ann(conn, "600000", "2024-05-02", "2024年度向特定对象发行A股股票预案")
    _ann(conn, "600000", "2024-08-01", "关于向特定对象发行股票发行情况报告书的公告")
    classifier.classify_events(conn)
    p = dict(conn.execute("SELECT * FROM pool_entry WHERE code='600000'").fetchone())
    assert p["status"] == "催化兑现中"
    e = dict(conn.execute("SELECT * FROM event WHERE code='600000'").fetchone())
    assert e["status"] == "已发行"


def test_termination_does_not_close_same_day_opened_event(conn):
    """同日"预案+终止前次"：终止不得误关当日新开事件（真实目标为更早轮次）。"""
    _ann(conn, "002348", "2025-12-02", "2025年度向特定对象发行A股股票预案")
    _ann(conn, "002348", "2025-12-02", "关于终止前次非公开发行股票事项的公告")
    _ann(conn, "002348", "2025-12-03", "2025年度向特定对象发行A股股票预案（修订稿）")
    classifier.classify_events(conn)
    evs = _events(conn)
    assert len(evs) == 1  # 只有一轮事件，未被同日终止误伤
    assert evs[0]["status"] == "进行中"
    # 次年真终止才关闭它
    _ann(conn, "002348", "2026-04-04", "关于终止前次非公开发行股票事项的公告")
    _ann(conn, "002348", "2026-04-04", "2026年度向特定对象发行A股股票预案")
    classifier.classify_events(conn)
    evs = _events(conn)
    assert len(evs) == 2
    assert next(e for e in evs if e["start_date"] == "2025-12-02")["status"] == "终止"
    assert next(e for e in evs if e["start_date"] == "2026-04-04")["status"] == "进行中"


# ---------- v1.1 候选池市值硬门槛 ----------

def test_pool_mv_cap_blocks_bigcap(conn):
    """S/A 事件但流通市值超 pool_mv_max（默认 300 亿）→ 不入池；未知市值不误伤。"""
    conn.executemany(
        "INSERT INTO stock(code,name,board,list_date,is_st,circ_mv,updated_at)"
        " VALUES(?,?,?,?,0,?,?)",
        [("600111", "股600111", "沪市主板", "2010-01-01", 80.0, "2026-01-01"),
         ("600222", "股600222", "沪市主板", "2010-01-01", 500.0, "2026-01-01"),
         ("600333", "股600333", "沪市主板", "2010-01-01", None, "2026-01-01")])
    conn.commit()
    _ann(conn, "600111", "2026-03-01", "关于2026年度向特定对象发行股票预案的公告")
    _ann(conn, "600222", "2026-03-01", "关于2026年度向特定对象发行股票预案的公告")
    _ann(conn, "600333", "2026-03-01", "关于2026年度向特定对象发行股票预案的公告")
    classifier.classify_events(conn, now="2026-03-01T12:00:00+00:00")
    in_pool = {r[0] for r in conn.execute("SELECT code FROM pool_entry").fetchall()}
    assert in_pool == {"600111", "600333"}   # 80 亿与未知可入，500 亿出局
    # 事件本身照常记录（不丢公告）
    assert conn.execute("SELECT COUNT(*) FROM event WHERE code='600222'").fetchone()[0] == 1


def test_weak_signal_without_capital_event_is_recorded_independently(conn):
    _ann(conn, "000001", "2024-05-01", "关于董事辞职的公告")
    stats = classifier.classify_events(conn)
    assert stats["processed"] == 1
    assert conn.execute("SELECT COUNT(*) FROM weak_signal").fetchone()[0] == 1
    assert conn.execute("SELECT signal_type FROM weak_signal").fetchone()[0] == "高管变动"
    assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pool_entry").fetchone()[0] == 0


def test_weak_signal_attaches_only_when_open_event_exists(conn):
    _ann(conn, "000001", "2024-05-01", "2024年度向特定对象发行A股股票预案")
    _ann(conn, "000001", "2024-05-02", "关于董事辞职的公告")
    classifier.classify_events(conn)
    assert conn.execute("SELECT COUNT(*) FROM weak_signal").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM flow_node WHERE node_type='高管变动'"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM pool_entry").fetchone()[0] == 1


def test_weak_signal_classification_is_idempotent(conn):
    _ann(conn, "000001", "2024-05-01", "关于董事辞职的公告")
    classifier.classify_events(conn)
    before = conn.execute("SELECT COUNT(*) FROM weak_signal").fetchone()[0]
    classifier.classify_events(conn)
    assert conn.execute("SELECT COUNT(*) FROM weak_signal").fetchone()[0] == before == 1


def test_rebuild_weak_signals_is_explicit_strict_and_idempotent(conn):
    script = Path(__file__).parents[1] / "scripts" / "rebuild_weak_signals.py"
    spec = importlib.util.spec_from_file_location("rebuild_weak_signals", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    _ann(conn, "000001", "2024-05-01", "关于设立全资子公司的公告")
    _ann(conn, "000001", "2024-05-02", "关于为全资子公司提供担保的公告")
    first = module.rebuild(conn, start="2024-05-01", end="2024-05-02")
    second = module.rebuild(conn, start="2024-05-01", end="2024-05-02")
    assert first["matched"] == 1 and first["inserted"] == 1
    assert second["matched"] == 1 and second["inserted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM weak_signal").fetchone()[0] == 1
