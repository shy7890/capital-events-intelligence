"""M1 pipeline 层测试：回填编排、主备切换、scan_run 全量记账、增量水位。注入假源函数，零联网。"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from zhuang_monitor import db, ingest, pipeline

KLINE_ROWS = [
    {"code": "002348", "trade_date": "2024-01-02", "open": 6.8, "high": 7.0, "low": 6.7,
     "close": 6.9, "volume": 1.2e6, "amount": 8.2e8, "turnover": 1.26, "adj_factor": 1.25, "source": "baostock"},
    {"code": "002348", "trade_date": "2024-01-03", "open": 6.9, "high": 7.1, "low": 6.85,
     "close": 7.05, "volume": 9.8e5, "amount": 6.9e8, "turnover": 1.03, "adj_factor": 1.25, "source": "baostock"},
]

HOLDER_ROWS = [
    {"code": "002348", "disclose_date": "2024-03-15", "holder_num": 44000.0, "pct_change": -12.5,
     "avg_hold": 1.8e6, "total_mv": 7.92e9, "source": "akshare_em"},
]

ANN_ROWS = [
    {"code": "002348", "pub_date": "2024-03-01", "title": "关于控股股东变更进展公告", "pdf_url": "http://s/x.PDF",
     "category": None, "source": "cninfo"},
]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_schema(c)
    yield c
    c.close()


def _runs(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM scan_run ORDER BY id").fetchall()]


def _boom(*args, **kwargs):
    raise RuntimeError("上游炸了")


def test_backfill_kline_green(conn):
    stats = pipeline.backfill_kline(conn, "002348", "2024-01-01", "2024-01-31",
                                    kline_fn=lambda c, s, e: KLINE_ROWS, run_date="2024-06-01")
    assert stats["ok"] and stats["inserted"] == 2
    run = _runs(conn)[-1]
    assert run["status"] == "green" and run["stage"] == "kline"


def test_backfill_kline_rerun_idempotent(conn):
    fn = lambda c, s, e: KLINE_ROWS
    pipeline.backfill_kline(conn, "002348", "2024-01-01", "2024-01-31", kline_fn=fn, run_date="2024-06-01")
    stats2 = pipeline.backfill_kline(conn, "002348", "2024-01-01", "2024-01-31", kline_fn=fn, run_date="2024-06-01")
    assert stats2["inserted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0] == 2


def test_backfill_kline_falls_back_to_sina(conn):
    sina = [dict(r, source="sina") for r in KLINE_ROWS]
    stats = pipeline.backfill_kline(conn, "002348", "2024-01-01", "2024-01-31",
                                    kline_fn=_boom, sina_fn=lambda c, s, e: sina, run_date="2024-06-01")
    assert stats["ok"] and stats["fallback"] == "sina|tencent"
    assert conn.execute("SELECT COUNT(*) FROM kline_daily WHERE source='sina'").fetchone()[0] == 2
    assert _runs(conn)[-1]["status"] == "green"


def test_backfill_kline_falls_back_to_tencent_after_sina_fails(conn):
    """主源抛错、新浪抛错：第三路腾讯接管，标记 fallback。"""
    tct = [dict(r, source="tencent") for r in KLINE_ROWS]
    stats = pipeline.backfill_kline(conn, "002348", "2024-01-01", "2024-01-31",
                                    kline_fn=_boom, sina_fn=_boom,
                                    tencent_fn=lambda c, s, e: tct, run_date="2024-06-01")
    assert stats["ok"] and stats["fallback"] == "sina|tencent"
    assert conn.execute("SELECT COUNT(*) FROM kline_daily WHERE source='tencent'").fetchone()[0] == 2


def test_backfill_kline_all_fail_red_no_raise(conn):
    stats = pipeline.backfill_kline(conn, "002348", "2024-01-01", "2024-01-31",
                                    kline_fn=_boom, sina_fn=_boom, run_date="2024-06-01")
    assert stats["ok"] is False and "上游炸了" in stats["error"]
    run = _runs(conn)[-1]
    assert run["status"] == "red" and "上游炸了" in (run["errors"] or "")


def test_backfill_holder(conn):
    stats = pipeline.backfill_holder(conn, "002348", holder_fn=lambda c: HOLDER_ROWS, run_date="2024-06-01")
    assert stats["ok"] and stats["inserted"] == 1
    bad = pipeline.backfill_holder(conn, "002348", holder_fn=_boom, run_date="2024-06-01")
    assert bad["ok"] is False
    assert ingest.red_run_count(conn, "2024-06-01") == 1


def test_backfill_longhu_accumulates_dates(conn):
    called = []

    def fn(day):
        called.append(day)
        return [{"code": "002348", "trade_date": day, "reason": "涨幅偏离", "net_buy": 100.0, "source": "akshare_em"}] if day.endswith("03") else []

    stats = pipeline.backfill_longhu(conn, ["2024-02-02", "2024-02-03"], longhu_fn=fn, run_date="2024-06-01")
    assert stats["ok"] and stats["inserted"] == 1 and called == ["2024-02-02", "2024-02-03"]
    assert _runs(conn)[-1]["status"] == "green"


def test_backfill_announcements_dedup(conn):
    fn = lambda c, s, e: ANN_ROWS
    s1 = pipeline.backfill_announcements(conn, "002348", "2024-01-01", "2026-09-01", fetch_fn=fn, run_date="2024-06-01")
    s2 = pipeline.backfill_announcements(conn, "002348", "2024-01-01", "2026-09-01", fetch_fn=fn, run_date="2024-06-01")
    assert s1["inserted"] == 1 and s2["inserted"] == 0


def test_refresh_stock_merges_basic_and_snapshot(conn):
    basic = lambda c: {"code": "002348", "name": "高乐股份", "board": "深市主板", "list_date": "2010-01-28", "is_st": 0}
    snap = lambda codes: {"002348": {"name": "高乐股份", "price": 6.88, "circ_mv": 72.5, "total_mv": 80.1}}
    stats = pipeline.refresh_stock(conn, "002348", basic_fn=basic, snapshot_fn=snap, run_date="2024-06-01")
    assert stats["ok"]
    row = conn.execute("SELECT list_date, circ_mv, board FROM stock").fetchone()
    assert row["list_date"] == "2010-01-28" and row["circ_mv"] == 72.5 and row["board"] == "深市主板"
    snap2 = lambda codes: {"002348": {"name": "高乐股份", "price": 6.5, "circ_mv": 68.0, "total_mv": 79.0}}
    pipeline.refresh_stock(conn, "002348", basic_fn=basic, snapshot_fn=snap2, run_date="2024-06-02")
    row = conn.execute("SELECT list_date, circ_mv FROM stock").fetchone()
    assert row["list_date"] == "2010-01-28" and row["circ_mv"] == 68.0
    assert conn.execute("SELECT COUNT(*) FROM stock").fetchone()[0] == 1


def test_last_kline_date_and_incremental_window(conn):
    assert pipeline.last_kline_date(conn, "002348") is None
    ingest.upsert_kline(conn, KLINE_ROWS)
    assert pipeline.last_kline_date(conn, "002348") == "2024-01-03"
    start = pipeline.incremental_kline_start(conn, "002348", default_start="2023-01-01", today=date(2024, 1, 10))
    assert start == "2024-01-04"
    empty = pipeline.incremental_kline_start(conn, "600000", default_start="2023-01-01", today=date(2024, 1, 10))
    assert empty == "2023-01-01"


def test_stage_records_fresh_started_at_each_run(conn):
    pipeline.backfill_holder(conn, "002348", holder_fn=lambda c: HOLDER_ROWS, run_date="2024-06-05")
    run = _runs(conn)[-1]
    assert run["started_at"].startswith("20")  # ISO 时间戳
    assert run["finished_at"]


def test_backfill_events_stage_records_and_idempotent(conn):
    ingest.upsert_announcement(conn, [{
        "code": "002348", "pub_date": "2025-11-25", "title": "关于公司第一大股东筹划控制权变更的停牌公告",
        "pdf_url": None, "category": None, "source": "cninfo",
    }])
    r1 = pipeline.backfill_events(conn, run_date="2026-09-02")
    assert r1["ok"] and r1["new_events"] == 1
    run = _runs(conn)[-1]
    assert run["stage"] == "events" and run["status"] == "green"
    r2 = pipeline.backfill_events(conn, run_date="2026-09-02")
    assert r2["ok"] and r2["processed"] == 0


def test_backfill_market_announcements(conn):
    def fn(day):
        return [{"code": "000001", "pub_date": day, "title": f"公告{day}", "pdf_url": None,
                 "category": None, "source": "akshare_dc"}]
    r = pipeline.backfill_market_announcements(conn, ["2024-06-02", "2024-06-03"],
                                               fetch_fn=fn, run_date="2024-06-03")
    assert r["ok"] and r["inserted"] == 2
    r2 = pipeline.backfill_market_announcements(conn, ["2024-06-02", "2024-06-03"],
                                                fetch_fn=fn, run_date="2024-06-03")
    assert r2["ok"] and r2["inserted"] == 0
    def bad(day):
        raise RuntimeError("403")
    r3 = pipeline.backfill_market_announcements(conn, ["2024-06-04"], fetch_fn=bad, run_date="2024-06-04")
    assert r3["ok"] is False


def _market_rows(code: str, day: str = "2024-06-03") -> list[dict]:
    return [dict(KLINE_ROWS[0], code=code, trade_date=day)]


def test_refresh_market_kline_resume_skips_success_and_records_empty(conn):
    calls: list[tuple[str, str]] = []

    def primary(code, start, end):
        calls.append(("primary", code))
        if code == "000002":
            return []
        return _market_rows(code, end)

    def fallback(code, start, end):
        calls.append(("fallback", code))
        return []

    first = pipeline.refresh_market_kline(
        conn, "2024-06-03", ["000001", "000002"],
        kline_fn=primary, sina_fn=fallback, retry_delay=0,
    )
    assert first["success"] == 1 and first["skipped"] == 1 and first["failed"] == 0
    assert calls == [("primary", "000001"), ("primary", "000002"), ("fallback", "000002")]
    empty = conn.execute(
        "SELECT status, last_error FROM market_refresh_item "
        "WHERE run_date=? AND code=? AND kind='kline'", ("2024-06-03", "000002")
    ).fetchone()
    assert empty["status"] == "skipped" and "无数据" in empty["last_error"]

    def should_not_fetch(code, start, end):
        raise AssertionError(f"成功项被重复抓取: {code}")

    second = pipeline.refresh_market_kline(
        conn, "2024-06-03", ["000001", "000002"],
        kline_fn=should_not_fetch, sina_fn=should_not_fetch, retry_delay=0,
    )
    assert second["success"] == 1 and second["skipped"] == 1
    assert conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM market_refresh_item WHERE run_date=?", ("2024-06-03",)
    ).fetchone()[0] == 2


def test_refresh_market_kline_retries_failed_only_and_stays_incomplete(conn):
    calls: list[str] = []

    def bad(code, start, end):
        calls.append(code)
        if code == "000002":
            raise TimeoutError("上游超时")
        return _market_rows(code, end)

    def no_fallback(code, start, end):
        raise TimeoutError("备用源也失败")

    first = pipeline.refresh_market_kline(
        conn, "2024-06-04", ["000001", "000002"],
        kline_fn=bad, sina_fn=no_fallback, retry_delay=0,
    )
    assert first["success"] == 1 and first["failed"] == 1
    assert pipeline.market_refresh_ready(conn, "2024-06-04") is False
    assert conn.execute("SELECT COUNT(*) FROM scan_result").fetchone()[0] == 0
    scan = pipeline.backfill_scan(conn, today="2024-06-04", run_date="2024-06-04")
    assert scan["ok"] is True and scan["status"] == "pending"
    assert conn.execute(
        "SELECT status FROM scan_run WHERE run_date=? AND stage='scan' ORDER BY id DESC LIMIT 1",
        ("2024-06-04",),
    ).fetchone()[0] == "pending"

    def fixed(code, start, end):
        calls.append(f"retry:{code}")
        return _market_rows(code, end)

    second = pipeline.refresh_market_kline(
        conn, "2024-06-04", ["000001", "000002"],
        kline_fn=fixed, sina_fn=no_fallback, retry_delay=0,
    )
    assert second["success"] == 2 and second["failed"] == 0
    # 首轮对 000002 失败后即时重试（retry_delay=0）仍失败；二次运行时再续跑
    assert calls == ["000001", "000002", "000002", "retry:000002"]
    assert conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0] == 2


def test_refresh_market_kline_completed_rerun_is_idempotent(conn):
    calls: list[str] = []

    def primary(code, start, end):
        calls.append(code)
        return _market_rows(code, end)

    pipeline.refresh_market_kline(
        conn, "2024-06-05", ["000001", "000002"],
        kline_fn=primary, sina_fn=lambda *args: [], retry_delay=0,
    )
    before = conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0]
    again = pipeline.refresh_market_kline(
        conn, "2024-06-05", ["000001", "000002"],
        kline_fn=lambda *args: (_ for _ in ()).throw(AssertionError("不应重抓")),
        sina_fn=lambda *args: [], retry_delay=0,
    )
    assert again["success"] == 2 and again["failed"] == 0
    assert calls == ["000001", "000002"]
    assert conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0] == before
    assert conn.execute(
        "SELECT COUNT(*) FROM market_refresh WHERE run_date=?", ("2024-06-05",)
    ).fetchone()[0] == 1


def test_refresh_market_kline_does_not_call_fallback_twice_after_empty_failure(conn):
    calls: list[str] = []

    def primary(code, start, end):
        calls.append("primary")
        return []

    def fallback(code, start, end):
        calls.append("fallback")
        raise TimeoutError("备用源超时")

    result = pipeline.refresh_market_kline(
        conn, "2024-06-06", ["000001"], kline_fn=primary, sina_fn=fallback,
        retry_delay=0,
    )
    # 主源空响应→兜底失败→整轮重试一次（主源再空→兜底再失败）→记 failed
    assert result["failed"] == 1
    assert calls == ["primary", "fallback", "primary", "fallback"]


def test_refresh_market_kline_retries_once_after_cold_failure(conn):
    """保险闸门：双源首轮皆败 → 冷却后整轮重试一次 → 成功则不再记 failed。"""
    calls: list[str] = []

    def flaky_primary(code, start, end):
        calls.append("primary")
        if len([c for c in calls if c == "primary"]) == 1:  # 首轮抛错
            raise TimeoutError("主源抖动")
        return _market_rows(code, end)

    def hard_fallback(code, start, end):
        calls.append("fallback")
        raise TimeoutError("兜底源也抖了一下")

    result = pipeline.refresh_market_kline(
        conn, "2024-06-07", ["000001"], kline_fn=flaky_primary, sina_fn=hard_fallback,
        retry_delay=0,
    )
    # 首轮 primary→fallback 失败；冷却 0ms 后重试 primary 成功
    assert calls == ["primary", "fallback", "primary"]
    assert result["success"] == 1 and result["failed"] == 0
    row = conn.execute(
        "SELECT status, attempts FROM market_refresh_item WHERE run_date=? AND code=?",
        ("2024-06-07", "000001"),
    ).fetchone()
    assert row["status"] == "success"
    # 一次失败也计入 attempts（running+1，重试成功后不再加）
    assert row["attempts"] >= 1


def test_refresh_market_kline_fails_finally_after_retry_exhausted(conn):
    """重试后仍失败：才记 failed，且下次续跑只抓 failed 项。"""
    calls: list[str] = []

    def always_bad_primary(code, start, end):
        calls.append("primary")
        raise TimeoutError("主源持续故障")

    def always_bad_fallback(code, start, end):
        calls.append("fallback")
        raise TimeoutError("兜底源持续故障")

    result = pipeline.refresh_market_kline(
        conn, "2024-06-08", ["000001"], kline_fn=always_bad_primary, sina_fn=always_bad_fallback,
        retry_delay=0,
    )
    # 首轮 + 重试轮，两轮都是 primary→fallback 全败
    assert calls == ["primary", "fallback"] * 2
    assert result["failed"] == 1
    assert result["success"] == 0
    row = conn.execute(
        "SELECT status, last_error FROM market_refresh_item WHERE run_date=? AND code=?",
        ("2024-06-08", "000001"),
    ).fetchone()
    assert row["status"] == "failed" and "TimeoutError" in row["last_error"]


def test_refresh_market_kline_retry_skips_sleep_when_zero_delay(conn):
    """retry_delay=0 不产生等待（测试与快跑场景），且不抛异常。"""
    def bad_primary(code, start, end):
        raise TimeoutError("主源故障")

    def good_fallback(code, start, end):
        return _market_rows(code, end)

    result = pipeline.refresh_market_kline(
        conn, "2024-06-09", ["000001"], kline_fn=bad_primary, sina_fn=good_fallback,
        retry_delay=0, retry_times=2,
    )
    assert result["success"] == 1 and result["failed"] == 0
