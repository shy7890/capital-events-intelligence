"""M1 批任务编排测试：水位推进、幂等、红单汇总。注入假源函数，零联网。"""

from __future__ import annotations

import pytest

from zhuang_monitor import batch, db, ingest, pipeline


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_schema(c)
    yield c
    c.close()


def _sources(spy):
    def kline(code, start, end):
        spy.append(("kline", code, start, end))
        return [{"code": code, "trade_date": end, "open": 1, "high": 1, "low": 1,
                 "close": 1, "volume": 1, "amount": 1, "turnover": 1, "adj_factor": 1, "source": "baostock"}]

    def holder(code):
        spy.append(("holder", code))
        return [{"code": code, "disclose_date": "2024-03-15", "holder_num": 1.0,
                 "pct_change": 0.0, "avg_hold": None, "total_mv": None, "source": "x"}]

    def longhu(day):
        spy.append(("longhu", day))
        return []

    def announce(code, start, end):
        spy.append(("announce", code, start, end))
        return []

    return {"kline_fn": kline, "sina_fn": kline, "holder_fn": holder,
            "longhu_fn": longhu, "announce_fn": announce}


def test_run_daily_happy_path_advances_watermark(conn):
    spy: list = []
    result = batch.run_daily(conn, "2024-06-03", codes=["002348"], **_sources(spy))
    assert result["ok"] and result["red"] == 0
    assert batch.KLINE_WATERMARK in ingest.get_config(conn, "watermarks", {})
    assert ingest.get_config(conn, "watermarks")[batch.LONGHU_WATERMARK] == "2024-06-03"
    assert ingest.get_config(conn, "watermarks")["announce"]["002348"] == "2024-06-03"


def test_run_daily_rerun_idempotent(conn):
    spy1: list = []
    batch.run_daily(conn, "2024-06-03", codes=["002348"], **_sources(spy1))
    spy2: list = []
    result = batch.run_daily(conn, "2024-06-03", codes=["002348"], **_sources(spy2))
    assert result["ok"]
    k_calls = [c for c in spy2 if c[0] == "kline"]
    # K 线增量从水位之后开始（不会重拉整段历史）
    assert k_calls and k_calls[0][2] > "2024-06-03" or k_calls[0][2] == k_calls[0][3]
    a_calls = [c for c in spy2 if c[0] == "announce"]
    assert a_calls and a_calls[0][2] == "2024-06-03"


def test_run_daily_counts_reds_and_continues(conn):
    src = _sources([])

    def bad_holder(code):
        raise RuntimeError("户数源失败")

    src["holder_fn"] = bad_holder
    result = batch.run_daily(conn, "2024-06-03", codes=["002348"], **src)
    assert result["ok"] is False and result["red"] == 1
    # 失败阶段不推进水位，其余阶段照常
    assert "holder" not in ingest.get_config(conn, "watermarks")
    assert ingest.red_run_count(conn, "2024-06-03") == 1
    assert any(s["stage"] == "kline" and s["status"] == "green"
               for s in [dict(r) for r in conn.execute("SELECT * FROM scan_run").fetchall()])


def test_run_daily_trading_gap_kline_start(conn):
    spy: list = []
    batch.run_daily(conn, "2024-06-03", codes=["002348"], **_sources(spy))
    first = [c for c in spy if c[0] == "kline"][0]
    assert first == ("kline", "002348", "2023-01-01", "2024-06-03")  # 首次默认起点


def test_run_daily_includes_events_stage(conn):
    spy: list = []
    def announce(code, s, e):
        return [{"code": code, "pub_date": s, "title": "关于公司第一大股东筹划控制权变更的停牌公告",
                 "pdf_url": None, "category": None, "source": "cninfo"}]
    src = _sources(spy)
    src["announce_fn"] = announce
    result = batch.run_daily(conn, "2024-06-03", codes=["002348"], **src)
    assert result["ok"]
    stages = [s["stage"] for s in result["stages"]]
    assert "events" in stages
    assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] >= 1


def test_run_daily_market_announce_stage_and_skip_next_day(conn):
    calls: list = []

    def market_fn(day):
        calls.append(day)
        return []

    src = _sources([])
    result = batch.run_daily(conn, "2024-06-03", codes=["002348"], market_announce_fn=market_fn, **src)
    assert result["ok"]
    assert calls == ["2024-06-02", "2024-06-03"]
    calls.clear()
    batch.run_daily(conn, "2024-06-04", codes=["002348"], market_announce_fn=market_fn, **src)
    assert calls == ["2024-06-03", "2024-06-04"]  # 次日重补今天（盘后数据补全）


def test_run_daily_separates_full_market_kline_from_targeted_holder(conn):
    spy: list = []
    src = _sources(spy)
    result = batch.run_daily(
        conn,
        "2024-06-05",
        codes=["002348"],
        kline_codes=["000001", "000002"],
        holder_codes=["000002"],
        **src,
    )
    assert result["ok"]
    assert [x[1] for x in spy if x[0] == "kline"] == ["000001", "000002"]
    assert [x[1] for x in spy if x[0] == "holder"] == ["000002"]
