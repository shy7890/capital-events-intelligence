"""M4 流水线 B 因子扫描测试（PRD §3.2/§3.3/F5/F10）。

手法：tmp 库手工构造 K线/户数/龙虎榜/事件小样本，全部读库计算、零联网。
覆盖验收最低集合：完美庄股入选 / 上榜出局 / 市值降级观察 / 涨幅不足出局 / ST 出局 /
弱信号计数（同类不算）/ v6 冷启动排除 / 同日重跑行数不变 / config 参数覆盖 /
分批跑通 300 只 / 池内共振回写 / pipeline+batch 接线。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import pytest

from zhuang_monitor import batch, db, ingest, pipeline, scanner

TODAY = "2026-02-02"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m4.db")
    db.init_schema(c)
    yield c
    c.close()


def _dseq(end: str, n: int) -> list[str]:
    """连续日历日序列（升序，末日=end）。"""
    d0 = date.fromisoformat(end)
    return [(d0 - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def _stock(conn, code, *, circ_mv=80.0, is_st=0, list_date="2010-01-01") -> None:
    conn.execute(
        "INSERT INTO stock(code,name,board,list_date,is_st,circ_mv,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (code, f"股{code}", "深市主板", list_date, is_st, circ_mv, TODAY),
    )
    conn.commit()


def _klines(conn, code, closes, *, amount=1.0e8, adj=1.0) -> list[str]:
    """按收盘价序列写入升序 K 线（末日=TODAY），返回日期序列。"""
    dates = _dseq(TODAY, len(closes))
    conn.executemany(
        "INSERT INTO kline_daily(code,trade_date,open,high,low,close,volume,amount,"
        "turnover,adj_factor,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [(code, d, c, c, c, c, 1.0e6, amount, 1.0, adj, "test")
         for d, c in zip(dates, closes)],
    )
    conn.commit()
    return dates


def _gain_closes(gain_pct: float) -> list[float]:
    """最低点 10.0（第 150 根），期末按涨幅收高。"""
    end = round(10.0 * (1 + gain_pct / 100.0), 4)
    return [11.0] * 150 + [10.0] + [end] * 20


MIN_IDX = 150  # _gain_closes 的最低价（起涨点）下标


def _holders(conn, code, pcts: list[float | None]) -> None:
    """按给定环比序列写户数（末日=TODAY，升序）。"""
    dates = _dseq(TODAY, len(pcts))
    conn.executemany(
        "INSERT INTO holder_num(code,disclose_date,holder_num,pct_change,source) "
        "VALUES(?,?,?,?,?)",
        [(code, d, 50000.0, p, "test") for d, p in zip(dates, pcts)],
    )
    conn.commit()


def _longhu(conn, code, day: str) -> None:
    conn.execute(
        "INSERT INTO longhu(code,trade_date,reason,net_buy,source) VALUES(?,?,?,?,?)",
        (code, day, "涨幅偏离", 100.0, "test"),
    )
    conn.commit()


def _weak_nodes(conn, code, node_types: list[str], *, event_type: str = "定增",
                day_offset: int = -10) -> None:
    """挂 event + 若干 flow_node（首条 node_date = TODAY + day_offset，逐条往前错开 1 天）。"""
    base = date.fromisoformat(TODAY) + timedelta(days=day_offset)
    cur = conn.execute(
        "INSERT INTO event(code,event_type,grade,start_date,status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (code, event_type, "S", base.isoformat(), "进行中", TODAY, TODAY),
    )
    ev_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO flow_node(event_id,node_type,node_date,created_at) VALUES(?,?,?,?)",
        [(ev_id, t, (base - timedelta(days=i)).isoformat(), TODAY)
         for i, t in enumerate(node_types)],
    )
    if not event_type.startswith("v6:"):
        conn.executemany(
            "INSERT OR IGNORE INTO weak_signal"
            "(code,signal_type,signal_date,announcement_id,title,source,created_at)"
            "VALUES(?,?,?,?,?,?,?)",
            [(code, t, (base - timedelta(days=i)).isoformat(), 0,
              f"测试弱信号：{t}", "test", TODAY)
             for i, t in enumerate(node_types)],
        )
    conn.commit()


def _result_map(conn) -> dict[str, dict]:
    return {r["code"]: dict(r)
            for r in conn.execute("SELECT * FROM scan_result").fetchall()}


def _seed_A(conn) -> list[str]:
    """完美庄股：涨 35%、零上榜、户数 -12%、市值 80 亿。"""
    _stock(conn, "100001", circ_mv=80.0)
    return _klines(conn, "100001", _gain_closes(35))


# ---------- 核心判定 ----------

def test_perfect_zhuang_tier_A(conn):
    _seed_A(conn)
    _holders(conn, "100001", [2.0, -12.0])
    out = scanner.scan_factors(conn, today=TODAY)
    assert set(out) == {"universe_size", "scanned", "hits", "upgraded_watch",
                        "resonance_updated", "tier_counts"}
    assert out["universe_size"] == 1 and out["scanned"] == 1 and out["hits"] == 1
    assert out["tier_counts"]["A"] == 1 and out["tier_counts"]["观察"] == 0
    row = _result_map(conn)["100001"]
    assert row["tier"] == "A"
    assert row["gain_pct"] == pytest.approx(35.0, abs=0.01)
    assert row["lhb_after_start"] == 0
    assert row["holder_drop_pct"] == pytest.approx(-12.0)
    assert row["circ_mv"] == pytest.approx(80.0)
    assert row["avg_amount"] == pytest.approx(1.0e8)
    hits = json.loads(row["hits_json"])
    assert hits == ["涨幅", "龙虎榜静默", "户数集中", "小市值"]
    assert row["resonance"] == len(hits) >= 3
    assert out["upgraded_watch"] == 1  # 命中户数集中 = 实质共振


def test_B_longhu_after_start_excluded(conn):
    dates = _klines(conn, "100002", _gain_closes(30))
    _stock(conn, "100002", circ_mv=80.0)
    _longhu(conn, "100002", dates[MIN_IDX + 1])  # 起涨点之后 1 条 → 出局
    scanner.scan_factors(conn, today=TODAY)
    assert _result_map(conn) == {}


def test_longhu_before_start_does_not_disqualify(conn):
    dates = _klines(conn, "100012", _gain_closes(30))
    _stock(conn, "100012", circ_mv=80.0)
    _longhu(conn, "100012", dates[100])  # 起涨点之前 → 不算
    scanner.scan_factors(conn, today=TODAY)
    assert "100012" in _result_map(conn)


def test_C_watch_tier_200bn(conn):
    """v1.1：150–300 亿且三项命中（涨幅+静默+户数）= 观察；两项则整票出局。"""
    _stock(conn, "100003", circ_mv=200.0)
    _klines(conn, "100003", _gain_closes(30))
    _holders(conn, "100003", [2.0, -12.0])
    out = scanner.scan_factors(conn, today=TODAY)
    row = _result_map(conn)["100003"]
    assert row["tier"] == "观察"
    assert json.loads(row["hits_json"]) == ["涨幅", "龙虎榜静默", "户数集中"]
    assert row["resonance"] == 3
    assert out["upgraded_watch"] == 1  # 命中户数集中 = 实质共振
    # 两命中（仅涨幅+静默）→ 出局不上榜
    _stock(conn, "100003B", circ_mv=200.0)
    _klines(conn, "100003B", _gain_closes(30))
    scanner.scan_factors(conn, today=TODAY)
    assert "100003B" not in _result_map(conn)


def test_D_low_gain_and_E_ST_excluded(conn):
    _stock(conn, "100004", circ_mv=80.0)
    _klines(conn, "100004", _gain_closes(10))     # 涨幅不足
    _stock(conn, "100005", circ_mv=80.0, is_st=1)  # ST
    _klines(conn, "100005", _gain_closes(35))
    out = scanner.scan_factors(conn, today=TODAY)
    assert out["hits"] == 0 and _result_map(conn) == {}


def test_holder_cumulative_drop_hit(conn):
    _stock(conn, "100006", circ_mv=80.0)
    _klines(conn, "100006", _gain_closes(30))
    _holders(conn, "100006", [3.0, -8.0, -8.0])  # 单期未达 -10，两期累计 -16 ≤ -15
    scanner.scan_factors(conn, today=TODAY)
    assert "户数集中" in json.loads(_result_map(conn)["100006"]["hits_json"])


def test_holder_mild_drop_no_hit(conn):
    _stock(conn, "100007", circ_mv=80.0)
    _klines(conn, "100007", _gain_closes(30))
    _holders(conn, "100007", [-5.0, -5.0])  # 累计 -10 > -15
    scanner.scan_factors(conn, today=TODAY)
    assert "户数集中" not in json.loads(_result_map(conn)["100007"]["hits_json"])


# ---------- 弱信号共振 ----------

def test_weak_signal_two_distinct_types_hit(conn):
    _stock(conn, "100008", circ_mv=80.0)
    _klines(conn, "100008", _gain_closes(30))
    _weak_nodes(conn, "100008", ["高管变动", "设立子公司"])
    scanner.scan_factors(conn, today=TODAY)
    assert "弱信号共振" in json.loads(_result_map(conn)["100008"]["hits_json"])


def test_weak_signal_same_type_twice_no_hit(conn):
    _stock(conn, "100009", circ_mv=80.0)
    _klines(conn, "100009", _gain_closes(30))
    _weak_nodes(conn, "100009", ["设立子公司", "设立子公司"])
    scanner.scan_factors(conn, today=TODAY)
    assert "弱信号共振" not in json.loads(_result_map(conn)["100009"]["hits_json"])


def test_weak_signal_v6_cold_start_excluded_and_outside_window(conn):
    _stock(conn, "100010", circ_mv=80.0)
    _klines(conn, "100010", _gain_closes(30))
    _weak_nodes(conn, "100010", ["高管变动", "设立子公司"], event_type="v6:定增")
    _weak_nodes(conn, "100010", ["激励计划", "质押变动"], day_offset=-200)  # 90 天窗口外
    scanner.scan_factors(conn, today=TODAY)
    assert "弱信号共振" not in json.loads(_result_map(conn)["100010"]["hits_json"])


# ---------- 基础排除 ----------

def test_basic_exclusions_amount_mv_listdate(conn):
    _stock(conn, "200001", circ_mv=80.0)
    _klines(conn, "200001", _gain_closes(30), amount=1.0e7)  # 日均成交额 <3000万
    _stock(conn, "200002", circ_mv=None)                     # 流通市值缺失
    _klines(conn, "200002", _gain_closes(30))
    _stock(conn, "200003", circ_mv=80.0,
            list_date=(date.fromisoformat(TODAY) - timedelta(days=100)).isoformat())  # 上市<1年
    _klines(conn, "200003", _gain_closes(30))
    _stock(conn, "200004", circ_mv=350.0)                    # ≥300亿
    _klines(conn, "200004", _gain_closes(30))
    out = scanner.scan_factors(conn, today=TODAY)
    assert out["hits"] == 0 and _result_map(conn) == {}


# ---------- 幂等 / 参数 ----------

def test_rerun_same_day_row_count_unchanged(conn):
    _seed_A(conn)
    _holders(conn, "100001", [2.0, -12.0])
    _stock(conn, "100011", circ_mv=200.0)
    _klines(conn, "100011", _gain_closes(28))
    _holders(conn, "100011", [2.0, -11.0])   # 三项命中 → 观察级在榜
    scanner.scan_factors(conn, today=TODAY)
    n1 = conn.execute("SELECT COUNT(*) FROM scan_result").fetchone()[0]
    out2 = scanner.scan_factors(conn, today=TODAY)
    n2 = conn.execute("SELECT COUNT(*) FROM scan_result").fetchone()[0]
    assert n1 == n2 == 2
    assert out2["hits"] == 2
    assert _result_map(conn)["100001"]["resonance"] == 4  # 覆盖写不叠加


def test_params_from_config_overridden(conn):
    _seed_A(conn)
    _holders(conn, "100001", [2.0, -12.0])
    ingest.set_config(conn, "scan_params", {"gain_min_pct": 40})  # 阈值提到 40% → A 出局
    scanner.scan_factors(conn, today=TODAY)
    assert _result_map(conn) == {}
    # 显式 params 参数优先于 config
    out = scanner.scan_factors(conn, today=TODAY, params={"gain_min_pct": 30})
    assert out["hits"] == 1


def test_default_params_values():
    p = scanner.DEFAULT_PARAMS
    assert p["window"] == 120 and p["gain_min_pct"] == 25.0
    assert p["circ_mv_a_max"] == 150.0 and p["circ_mv_watch_max"] == 300.0
    assert p["min_avg_amount"] == 3.0e7 and p["holder_single_drop_pct"] == -10.0
    assert p["holder_cum_drop_pct"] == -15.0 and p["weak_days"] == 90


# ---------- 宇宙与分批 ----------

def test_universe_only_codes_with_kline(conn):
    _seed_A(conn)
    _stock(conn, "100020", circ_mv=80.0)  # 无 K 线 → 不在宇宙
    out = scanner.scan_factors(conn, today=TODAY)
    assert out["universe_size"] == 1 and out["scanned"] == 1
    out2 = scanner.scan_factors(conn, today=TODAY, codes=["100020"])  # 指定但无 K 线
    assert out2["universe_size"] == 0


def test_batch_300_codes_runs(conn):
    codes = [f"6{i:05d}" for i in range(300)]
    rows = []
    dates = _dseq(TODAY, 12)
    for code in codes:
        _stock(conn, code, circ_mv=80.0)
        closes = [11.0] * 5 + [10.0] + [13.5] * 6
        rows += [(code, d, c, c, c, c, 1.0e6, 1.0e8, 1.0, 1.0, "test")
                 for d, c in zip(dates, closes)]
    conn.executemany(
        "INSERT INTO kline_daily(code,trade_date,open,high,low,close,volume,amount,"
        "turnover,adj_factor,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    out = scanner.scan_factors(conn, today=TODAY)
    assert out["universe_size"] == 300 and out["scanned"] == 300 and out["hits"] == 300
    assert out["tier_counts"]["B"] == 300  # v1.1：小市值但无户数/弱信号 → B 级


# ---------- 池共振回写 ----------

def test_pool_resonance_writeback_only_listed(conn):
    _seed_A(conn)
    _holders(conn, "100001", [2.0, -12.0])
    # A 与不在榜的 Z 各一条池记录
    conn.execute(
        "INSERT INTO pool_entry(code,event_id,status,grade,entered_at,resonance_count,"
        "updated_at) VALUES(?,?,?,?,?,?,?)",
        ("100001", None, "观察中", "A", "2026-01-01", 0, TODAY))
    conn.execute(
        "INSERT INTO pool_entry(code,event_id,status,grade,entered_at,resonance_count,"
        "updated_at) VALUES(?,?,?,?,?,?,?)",
        ("100099", None, "观察中", "S", "2026-01-01", 7, TODAY))
    _stock(conn, "100099", circ_mv=80.0)   # 涨但不过阈值 → 不在榜
    _klines(conn, "100099", _gain_closes(5))
    out = scanner.scan_factors(conn, today=TODAY)
    assert out["resonance_updated"] == 1
    assert conn.execute("SELECT resonance_count FROM pool_entry WHERE code='100001'"
                        ).fetchone()[0] == 4
    assert conn.execute("SELECT resonance_count FROM pool_entry WHERE code='100099'"
                        ).fetchone()[0] == 7  # 不在榜保留原值
    # 命中但不在池 → 只入榜，不自动入池
    assert conn.execute("SELECT COUNT(*) FROM pool_entry WHERE code='100002'"
                        ).fetchone()[0] == 0


# ---------- 复权 / 明细 ----------

def test_gain_uses_qfq_not_raw_price(conn):
    """起涨后曾大比例除权：原始价看似不涨，qfq 口径涨 35% → 仍入选。"""
    _stock(conn, "100030", circ_mv=80.0)
    dates = _dseq(TODAY, 171)
    closes = [11.0] * 150 + [10.0] + [6.75] * 20  # 原始价 10 → 6.75 = -32.5%
    factors = [1.0] * 151 + [2.0] * 20            # 后段因子翻倍 → qfq 末价 13.5
    conn.executemany(
        "INSERT INTO kline_daily(code,trade_date,open,high,low,close,volume,amount,"
        "turnover,adj_factor,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [("100030", d, c, c, c, c, 1.0e6, 1.0e8, 1.0, f, "test")
         for d, c, f in zip(dates, closes, factors)])
    conn.commit()
    scanner.scan_factors(conn, today=TODAY)
    row = _result_map(conn)["100030"]
    assert row["gain_pct"] == pytest.approx(35.0, abs=0.1)


def test_explain_returns_factor_details(conn):
    dates = _seed_A(conn)
    _holders(conn, "100001", [2.0, -12.0])
    det = scanner.explain(conn, "100001", today=TODAY)
    assert det["mechanical_start_date"] == dates[MIN_IDX]
    assert det["start_date"] == dates[MIN_IDX + 1]
    assert det["confirmed_start_date"] == dates[MIN_IDX + 1]
    assert det["gain_pct"] == pytest.approx(35.0, abs=0.01)
    assert det["lhb_after_start"] == 0
    assert det["holder_drop_pct"] == pytest.approx(-12.0)
    assert det["tier"] == "A" and det["resonance"] == 4
    out = scanner.explain(conn, "999999", today=TODAY)
    assert out["listed"] is False and out["excluded_reason"]


# ---------- pipeline / batch 接线 ----------

def _runs(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM scan_run ORDER BY id").fetchall()]


def test_pipeline_backfill_scan_green_and_red(conn):
    _seed_A(conn)
    _holders(conn, "100001", [2.0, -12.0])
    r = pipeline.backfill_scan(conn, today=TODAY)
    assert r["ok"] and r["stage"] == "scan" and r["universe_size"] == 1
    run = _runs(conn)[-1]
    assert run["stage"] == "scan" and run["status"] == "green"
    assert run["run_date"] == TODAY
    assert json.loads(run["stats_json"])["hits"] == 1
    bad = pipeline.backfill_scan(conn, today=TODAY, params={"window": "坏参数"})
    assert bad["ok"] is False and _runs(conn)[-1]["status"] == "red"


def test_batch_run_daily_scan_stage(conn):
    calls: list[str] = []

    def scan_fn():
        calls.append("scan")
        return {"ok": True, "stage": "scan"}

    def kline(code, start, end):
        return [{"code": code, "trade_date": end, "open": 1, "high": 1, "low": 1,
                 "close": 1, "volume": 1, "amount": 1, "turnover": 1,
                 "adj_factor": 1, "source": "test"}]

    src = {"kline_fn": kline, "sina_fn": kline,
           "holder_fn": lambda c: [], "longhu_fn": lambda d: [],
           "announce_fn": lambda c, s, e: []}
    # 默认 None → 行为不变
    r0 = batch.run_daily(conn, "2026-02-02", codes=["002348"], **src)
    assert "scan" not in [s["stage"] for s in r0["stages"]] and r0["ok"]
    # 注入 scan_fn → events 之后执行并计入
    r1 = batch.run_daily(conn, "2026-02-03", codes=["002348"], scan_fn=scan_fn, **src)
    assert calls == ["scan"]
    stages = [s["stage"] for s in r1["stages"]]
    assert stages.index("scan") == stages.index("events") + 1
    # scan 失败计入 red
    r2 = batch.run_daily(conn, "2026-02-04", codes=["002348"],
                         scan_fn=lambda: {"ok": False, "stage": "scan"}, **src)
    assert r2["ok"] is False and r2["red"] >= 1


# ---------- v1.1 及时性与四级评级 ----------

def test_freshness_gate_excludes_stale_rise(conn):
    """确认起点较早时进入延续观察；放宽新起涨线后可切换路线。"""
    _stock(conn, "300001", circ_mv=80.0)
    _klines(conn, "300001", [11.0] * 50 + [10.0] + [13.5] * 45)
    det = scanner.explain(conn, "300001", today=TODAY)
    assert det["listed"] is True and det["lane"] == "延续观察"
    assert det["start_age_days"] == 44
    scanner.scan_factors(conn, today=TODAY)
    assert _result_map(conn)["300001"]["tier"] == "延续观察"
    ingest.set_config(conn, "scan_params", {"start_max_days": 120})
    scanner.scan_factors(conn, today=TODAY)
    assert _result_map(conn)["300001"]["lane"] == "新起涨"


def test_tier_S_five_hits_fresh_and_A_when_staler(conn):
    _stock(conn, "300002", circ_mv=80.0)
    _klines(conn, "300002", _gain_closes(30))            # age=20 ≤ S 线
    _holders(conn, "300002", [2.0, -12.0])
    _weak_nodes(conn, "300002", ["高管变动", "设立子公司"])
    # 同命中但 age=35（>30）：过 40 闸门、不过 30 新鲜线 → A
    _stock(conn, "300003", circ_mv=80.0)
    _klines(conn, "300003", [11.0] * 135 + [10.0] + [13.0] * 35)
    _holders(conn, "300003", [2.0, -12.0])
    _weak_nodes(conn, "300003", ["高管变动", "设立子公司"])
    out = scanner.scan_factors(conn, today=TODAY)
    r2, r3 = _result_map(conn)["300002"], _result_map(conn)["300003"]
    assert r2["start_age_days"] == 19 and r2["tier"] == "S" and r2["resonance"] == 5
    assert r3["start_age_days"] == 34 and r3["tier"] == "A"
    assert out["tier_counts"]["S"] == 1 and out["tier_counts"]["A"] == 1


def _confirmed_closes(confirmed_age: int, final: float = 13.0) -> list[float]:
    """机械低点在首日，第二日确认，后面至少三日不跌破低点。"""
    n = confirmed_age + 2
    return [10.0, 11.0, 10.5, 10.5, 10.5] + [final] * (n - 5)


def test_scan_has_fresh_and_continuation_lanes(conn):
    _stock(conn, "300010", circ_mv=80.0)
    _klines(conn, "300010", _confirmed_closes(40))
    _stock(conn, "300011", circ_mv=80.0)
    _klines(conn, "300011", _confirmed_closes(41))
    scanner.scan_factors(conn, today=TODAY)
    rows = _result_map(conn)
    assert rows["300010"]["lane"] == "新起涨"
    assert rows["300010"]["tier"] in {"S", "A", "B", "观察"}
    assert rows["300011"]["lane"] == "延续观察"
    assert rows["300011"]["tier"] == "延续观察"
    assert rows["300011"]["start_age_days"] == 41
    assert rows["300011"]["mechanical_start_date"] != rows["300011"]["confirmed_start_date"]


def test_scan_excludes_start_older_than_120_days(conn):
    _stock(conn, "300012", circ_mv=80.0)
    _klines(conn, "300012", _confirmed_closes(121))
    det = scanner.explain(conn, "300012", today=TODAY, params={"window": 200})
    assert det["listed"] is False and "过旧" in (det["excluded_reason"] or "")
    scanner.scan_factors(conn, today=TODAY, params={"window": 200})
    assert "300012" not in _result_map(conn)


def test_scan_marks_unconfirmed_start_without_promoting_it(conn):
    _stock(conn, "300013", circ_mv=80.0)
    _klines(conn, "300013", [10.0, 11.0, 9.0, 13.0])
    det = scanner.explain(conn, "300013", today=TODAY)
    assert det["listed"] is True
    assert det["lane"] == "待确认" and det["confirmed_start_date"] is None
    scanner.scan_factors(conn, today=TODAY)
    row = _result_map(conn)["300013"]
    assert row["lane"] == "待确认" and row["tier"] == "待确认"


def test_missing_holder_is_unknown_and_cannot_create_S_or_A(conn):
    _stock(conn, "300014", circ_mv=80.0)
    _klines(conn, "300014", _confirmed_closes(20))
    _weak_nodes(conn, "300014", ["高管变动", "设立子公司"])
    scanner.scan_factors(conn, today=TODAY)
    row = _result_map(conn)["300014"]
    assert row["tier"] == "B" and row["lane"] == "新起涨"
    assert json.loads(row["missing_fields_json"]) == ["holder_num"]
