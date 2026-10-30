"""F7 收尾：v6「一年内_波动户数标注」导入测试（PRD §6 holder_pct）。

_parse_pct_series 解析口径 + 合成工作簿幂等 + 真实 xlsx 冒烟（tmp 库）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from zhuang_monitor import db, importer

ROOT = Path(__file__).resolve().parents[1]
REAL_XLSX = (ROOT / "outputs/capital_scan_20260524_v6" /
             "主板科创板创业板重大资本运作筛选_2026-05-24_一年内波动户数版.xlsx")

HP_HEADER = [
    "序号", "代码", "名称", "板块", "资本运作日", "股东户数覆盖",
    "户数起算披露日", "户数最新披露日", "户数环比变化序列%", "户数相对基准序列%",
]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "f7.db")
    db.init_schema(c)
    yield c
    c.close()


def test_parse_pct_series_variants():
    assert importer._parse_pct_series(
        "2025-10-20:+4.81%；2025-11-28:-7.46%;2025-12-19：-0.54％"
    ) == {"2025-10-20": 4.81, "2025-11-28": -7.46, "2025-12-19": -0.54}
    assert importer._parse_pct_series("2026-01-01:基准；2026-02-02:+3%") == \
        {"2026-01-01": 0.0, "2026-02-02": 3.0}
    assert importer._parse_pct_series("垃圾文本 不含日期") == {}
    assert importer._parse_pct_series(None) == {}


def _make_hp_xlsx(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "一年内_波动户数标注"
    ws.append(HP_HEADER)
    for r in rows:
        ws.append(list(r))
    wb.save(path)


def test_import_holder_pct_idempotent(conn, tmp_path):
    x = tmp_path / "hp.xlsx"
    _make_hp_xlsx(x, [
        [1, "603213", "镇洋发展", "沪市主板", "2025-08-20", "已计算", "2025-10-20", "2026-04-30",
         "2025-10-20:+4.81%；2025-11-28:-7.46%",
         "2025-10-20:基准；2025-11-28:-7.46%"],
        [2, "002763", "汇洁股份", "深市主板", "2026-05-23", "起点后暂无新披露", None, None,
         "2025-12-31:+34.55%；2026-02-27:-26.42%", None],
    ])
    stats = importer.import_v6_workbook(conn, x)
    assert stats["holder_pct"] == 4
    r = conn.execute("SELECT * FROM holder_pct WHERE code='603213' AND disclose_date='2025-11-28'"
                     ).fetchone()
    assert r["chain_pct"] == -7.46 and r["baseline_pct"] == -7.46
    assert r["anchor_date"] == "2025-10-20" and r["source"] == "v6"
    # 幂等：重跑零新增
    assert importer.import_v6_workbook(conn, x)["holder_pct"] == 0
    assert conn.execute("SELECT COUNT(*) FROM holder_pct").fetchone()[0] == 4


@pytest.mark.skipif(not REAL_XLSX.exists(), reason="真实 v6 xlsx 不在本机")
def test_real_workbook_holder_populated(conn):
    stats = importer.import_v6_workbook(conn, REAL_XLSX)
    assert stats["holder_pct"] > 100  # v6 全表 ~400+ 只 × 多期
    n = conn.execute("SELECT COUNT(*) FROM holder_pct").fetchone()[0]
    assert n == stats["holder_pct"]
    row = conn.execute(
        "SELECT chain_pct, baseline_pct FROM holder_pct"
        " WHERE code='603213' AND disclose_date='2026-04-30'").fetchone()
    assert row is not None and row["baseline_pct"] == pytest.approx(-17.91)


def test_holder_pct_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from zhuang_monitor import api, main
    c = db.connect(tmp_path / "f7api.db")
    db.init_schema(c)
    c.execute(
        "INSERT INTO holder_pct(code,disclose_date,chain_pct,baseline_pct,anchor_date,"
        "source,imported_at) VALUES('603213','2025-11-28',-7.46,-7.46,'2025-10-20','v6','t')")
    c.commit()
    c.close()
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "f7api.db")
    with TestClient(main.app) as cl:
        body = cl.get("/api/holder-pct/603213").json()
        assert body["rows"][0]["baseline_pct"] == -7.46
        assert cl.get("/api/holder-pct/000000").json()["rows"] == []
