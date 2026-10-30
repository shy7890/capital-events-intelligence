"""M3 v6 冷启动导入器测试（PRD §F7）。

全程离线：用 openpyxl 在 tmp_path 造合成 xlsx，不读 outputs/ 真实文件断言。
覆盖：stats 计数 / 重复导入幂等 / v6: 前缀与实时事件互不干扰 /
剔除表 UNIQUE 生效 / dry_run 不落库 / 表头错位（首行全 None）自适应。
"""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from zhuang_monitor import db, importer

POOL_HEADER = [
    "序号", "代码", "名称", "板块", "最新收盘价", "最初公告日", "事件大类",
    "当前进度", "大致情况", "后续催化", "命中路径",
]
FLOW_HEADER = [
    "所属池", "代码", "名称", "板块", "事件大类", "公告日期",
    "流程阶段", "公告标题", "来源", "PDF链接",
]
REJ_HEADER = [
    "序号", "代码", "名称", "板块", "事件大类", "最新公告日期",
    "当前进度", "剔除原因", "最新公告标题", "命中路径",
]

# 3 行候选（核心 2 + 观察 1）+ 4 行流程明细 + 1 行剔除
EXPECTED_STATS = {
    "core_pool": 2,
    "watch_pool": 1,
    "flow_nodes": 4,
    "announcements": 4,
    "rejected": 1,
    "events": 2,
    "holder_pct": 0,  # 合成簿无波动户数 sheet（真实导入见 test_f7_holder_pct）
}


def _make_xlsx(path, *, offset_first_row: bool = False):
    """构造合成 v6 工作簿。offset_first_row=True 时首行为全 None（模拟表头错位）。"""
    book = Workbook()
    book.remove(book.active)

    def add(name, header, rows):
        ws = book.create_sheet(name)
        if offset_first_row:
            ws.append([None] * len(header))
        ws.append(header)
        for r in rows:
            ws.append(list(r))

    add("核心候选_流程", POOL_HEADER, [
        ["1", "000688", "国城矿业", "深市主板", "43.5", "2024-12-21", "重大资产重组",
         "内部审议", "重组问询回复中", "看后续公告进展", "重大重组"],
        ["2", "300001", "测试股份", "创业板", "9.9", "2025-01-05", "股权收购/关联交易",
         "董事会审议", "收购子公司少数股权", "看审批与交割", "股权收购"],
    ])
    add("普通定增观察池_流程", POOL_HEADER, [
        ["1", "601611", "中国核建", "沪市主板", "12.38", "2026-05-23", "向特定对象发行/定增",
         "方案披露", "定增预案已披露", "看股东会、交易所审核", "定增/认购"],
    ])
    add("流程明细", FLOW_HEADER, [
        ["核心候选", "000688", "国城矿业", "深市主板", "重大资产重组", "2024-12-21",
         "交易方案披露", "重组预案公告", "巨潮公告标题", "http://x/1.pdf"],
        ["核心候选", "000688", "国城矿业", "深市主板", "重大资产重组", "2025-03-01",
         "问询回复/方案修订", "问询回复公告", "巨潮公告标题", "http://x/2.pdf"],
        ["核心候选", "000688", "国城矿业", "深市主板", "重大资产重组", "2025-06-01",
         "终止/撤回", "终止重组公告", "巨潮公告标题", None],
        ["普通定增观察池", "601611", "中国核建", "沪市主板", "向特定对象发行/定增", "2026-05-23",
         "方案/预案披露", "定增预案公告", "巨潮公告标题", None],
    ])
    add("剔除记录", REJ_HEADER, [
        ["1", "600281", "华阳新材", "沪市主板", "控制权/协议转让", "2026-05-23",
         "已完成/剔除", "已终止/已完成或不属于进行中", "华阳新材：关于终止协议转让的公告",
         "控制权/协议转让"],
    ])
    book.save(path)
    return path


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_schema(c)
    yield c
    c.close()


def _count(c, table):
    return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_import_stats(conn, tmp_path):
    """首导：六个计数全对。"""
    path = _make_xlsx(tmp_path / "v6.xlsx")
    stats = importer.import_v6_workbook(conn, path)
    assert stats == EXPECTED_STATS


def test_pool_entry_mapping(conn, tmp_path):
    """候选池：状态/评级/note/补档案映射正确，event_id 为 NULL。"""
    path = _make_xlsx(tmp_path / "v6.xlsx")
    importer.import_v6_workbook(conn, path)

    core = conn.execute("SELECT * FROM pool_entry WHERE code='000688'").fetchone()
    assert core["status"] == "重点跟踪"
    assert core["grade"] == "S"          # 事件大类含“重组”
    assert core["event_id"] is None
    assert "重组问询回复中" in core["note"] and "看后续公告进展" in core["note"]

    other = conn.execute("SELECT * FROM pool_entry WHERE code='300001'").fetchone()
    assert other["grade"] == "A"         # 股权收购/关联交易 不含关键词

    watch = conn.execute("SELECT * FROM pool_entry WHERE code='601611'").fetchone()
    assert watch["status"] == "观察中"
    assert watch["grade"] == "S"         # 含“定增”

    assert conn.execute("SELECT board FROM stock WHERE code='000688'").fetchone()["board"] == "深市主板"


def test_cold_start_events_and_nodes(conn, tmp_path):
    """流程明细：按 code+大类 建 v6: 前缀事件；节点/公告挂接正确。"""
    path = _make_xlsx(tmp_path / "v6.xlsx")
    importer.import_v6_workbook(conn, path)

    e1 = conn.execute("SELECT * FROM event WHERE code='000688'").fetchone()
    assert e1["event_type"] == "v6:重大资产重组"
    assert e1["grade"] == "S"
    assert e1["start_date"] == "2024-12-21"      # 该股该大类最早公告日
    assert e1["status"] == "终止"                 # 最新阶段含“终止”

    e2 = conn.execute("SELECT * FROM event WHERE code='601611'").fetchone()
    assert e2["event_type"] == "v6:向特定对象发行/定增"
    assert e2["status"] == "进行中"

    nodes = conn.execute(
        "SELECT fn.node_type, fn.node_date, fn.event_id, fn.announcement_id, a.source, a.pdf_url "
        "FROM flow_node fn JOIN announcement a ON a.id = fn.announcement_id "
        "WHERE fn.event_id = ? ORDER BY fn.node_date", (e1["id"],),
    ).fetchall()
    assert [n["node_type"] for n in nodes] == ["交易方案披露", "问询回复/方案修订", "终止/撤回"]
    assert all(n["source"] == "v6" for n in nodes)
    assert nodes[0]["pdf_url"] == "http://x/1.pdf"
    assert nodes[2]["pdf_url"] is None


def test_rejected_record_unique(conn, tmp_path):
    """剔除记录：导入一行后，再手工 INSERT OR IGNORE 同一自然键不新增。"""
    path = _make_xlsx(tmp_path / "v6.xlsx")
    importer.import_v6_workbook(conn, path)
    row = conn.execute("SELECT * FROM rejected_record").fetchone()
    assert row["code"] == "600281"
    assert row["pub_date"] == "2026-05-23"
    assert "已终止" in row["reason"]

    conn.execute(
        "INSERT OR IGNORE INTO rejected_record (code, pub_date, title, reason, created_at) "
        "VALUES ('600281', '2026-05-23', '华阳新材：关于终止协议转让的公告', 'dup', 'x')"
    )
    conn.commit()
    assert _count(conn, "rejected_record") == 1


def test_reimport_is_idempotent(conn, tmp_path):
    """重复导入：第二次全部 0 新增，各表行数不变。"""
    path = _make_xlsx(tmp_path / "v6.xlsx")
    importer.import_v6_workbook(conn, path)
    tables = ["stock", "announcement", "event", "flow_node", "pool_entry", "rejected_record"]
    before = {t: _count(conn, t) for t in tables}

    second = importer.import_v6_workbook(conn, path)
    assert second == {k: 0 for k in EXPECTED_STATS}
    after = {t: _count(conn, t) for t in tables}
    assert after == before


def test_v6_prefix_isolated_from_live_events(conn, tmp_path):
    """同 code 实时事件（无前缀）已存在时，v6 事件独立建、互不覆盖，重跑幂等。"""
    conn.execute(
        "INSERT INTO event (code, event_type, grade, start_date, status, created_at, updated_at) "
        "VALUES ('000688', '重大资产重组', 'S', '2024-01-01', '进行中', 't', 't')"
    )
    conn.commit()
    live_id = conn.execute("SELECT id FROM event WHERE event_type='重大资产重组'").fetchone()["id"]

    path = _make_xlsx(tmp_path / "v6.xlsx")
    importer.import_v6_workbook(conn, path)

    live = conn.execute("SELECT * FROM event WHERE id=?", (live_id,)).fetchone()
    assert live["status"] == "进行中" and live["start_date"] == "2024-01-01"  # 未被改动
    assert _count(conn, "event") == 3      # 1 实时 + 2 冷启动
    assert conn.execute("SELECT COUNT(*) FROM event WHERE event_type LIKE 'v6:%'").fetchone()[0] == 2

    second = importer.import_v6_workbook(conn, path)
    assert second["events"] == 0
    assert _count(conn, "event") == 3


def test_dry_run_writes_nothing(conn, tmp_path):
    """dry_run：统计照常返回，但不落库；随后正式导入可正常写入。"""
    path = _make_xlsx(tmp_path / "v6.xlsx")
    stats = importer.import_v6_workbook(conn, path, dry_run=True)
    assert stats == EXPECTED_STATS
    for t in ["stock", "announcement", "event", "flow_node", "pool_entry", "rejected_record"]:
        assert _count(conn, t) == 0

    assert importer.import_v6_workbook(conn, path) == EXPECTED_STATS
    assert _count(conn, "event") == 2


def test_header_row_offset_adaptive(conn, tmp_path):
    """首行全 None、真实表头在第二行：find_header 自适应，结果与正常文件一致。"""
    path = _make_xlsx(tmp_path / "v6_offset.xlsx", offset_first_row=True)
    stats = importer.import_v6_workbook(conn, path)
    assert stats == EXPECTED_STATS
    assert _count(conn, "pool_entry") == 3
