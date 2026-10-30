"""M1 akshare 适配层验收测试：纯函数 + 假 ak 模块注入，禁止真实联网。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from zhuang_monitor.sources import ak_adapters  # noqa: E402
from zhuang_monitor.sources.ak_adapters import parse_cn_amount  # noqa: E402


def test_parse_cn_amount_none_and_empty() -> None:
    for value in (None, "", "--", "-", float("nan"), pd.NA, "nan"):
        assert parse_cn_amount(value) is None


def test_parse_cn_amount_yi_suffix_to_wan() -> None:
    assert parse_cn_amount("1.2亿") == pytest.approx(12000.0)
    assert parse_cn_amount("1.5亿元") == pytest.approx(15000.0)


def test_parse_cn_amount_wan_suffix() -> None:
    assert parse_cn_amount("-5000.00万") == pytest.approx(-5000.0)
    assert parse_cn_amount(" 3.5万 ") == pytest.approx(3.5)


def test_parse_cn_amount_thousand_separator_bare_is_yuan() -> None:
    assert parse_cn_amount("1,234.56") == pytest.approx(0.123456)


def test_parse_cn_amount_plain_numbers_treated_as_yuan() -> None:
    assert parse_cn_amount(12345678.0) == pytest.approx(1234.5678)
    assert parse_cn_amount(50000) == pytest.approx(5.0)
    assert parse_cn_amount(-25000.0) == pytest.approx(-2.5)


def test_parse_cn_amount_negative_yi() -> None:
    assert parse_cn_amount("-0.88亿") == pytest.approx(-8800.0)


def test_parse_cn_amount_garbage_returns_none() -> None:
    assert parse_cn_amount("abc") is None
    assert parse_cn_amount("1.2.3亿") is None


HOLDER_DF = pd.DataFrame(
    {
        "股东户数统计截止日": ["20240331", "20231229", "20230930"],
        "区间涨跌幅": [1.23, -2.34, 0.0],
        "股东户数-本次": [35000.0, 36923.0, float("nan")],
        "股东户数-上次": [36923.0, 35000.0, 35000.0],
        "股东户数-增减": [-1923.0, 1923.0, 0.0],
        "股东户数-增减比例": ["-5.23%", 5.5, None],
        "户均持股市值": [88888.0, float("nan"), 77777.0],
        "户均持股数量": [2857.0, 2700.0, 2700.0],
        "总市值": [3111080000.0, 3.0e9, None],
        "流通市值": [2.5e9, 2.4e9, 2.3e9],
        "股本变动": [0, 0, 0],
        "股本变动原因": [None, None, None],
        "股东户数公告日期": ["2024/4/10", "", "2024-01-18"],
        "代码": ["002157", "002157", "002157"],
        "名称": ["乐金健康", "乐金健康", "乐金健康"],
    }
)


def test_normalize_holder_rows_full_columns() -> None:
    rows = ak_adapters._normalize_holder_df(HOLDER_DF)
    assert rows[0] == {
        "code": "002157",
        "disclose_date": "2024-04-10",
        "holder_num": 35000.0,
        "pct_change": -5.23,
        "avg_hold": 88888.0,
        "total_mv": pytest.approx(31.1108),
        "source": "akshare_em",
    }
    assert rows[1]["disclose_date"] == "2023-12-29"
    assert rows[1]["pct_change"] == 5.5
    assert rows[1]["avg_hold"] is None
    assert rows[1]["total_mv"] == pytest.approx(30.0)
    assert rows[2]["holder_num"] is None
    assert rows[2]["pct_change"] is None
    assert rows[2]["total_mv"] is None
    assert rows[2]["disclose_date"] == "2024-01-18"
    assert len(rows) == 3


def test_normalize_holder_missing_columns_tolerated() -> None:
    df = pd.DataFrame({"股东户数统计截止日": ["20240630"], "股东户数-本次": [12345]})
    assert ak_adapters._normalize_holder_df(df) == [
        {
            "code": None,
            "disclose_date": "2024-06-30",
            "holder_num": 12345.0,
            "pct_change": None,
            "avg_hold": None,
            "total_mv": None,
            "source": "akshare_em",
        }
    ]


LHB_DF = pd.DataFrame(
    {
        "代码": ["002157", "600519", 1],
        "名称": ["乐金健康", "贵州茅台", "平安银行"],
        "上榜日": ["2024/3/15", "2024-03-15", "2024/03/15"],
        "涨跌幅": [10.05, -3.2, 5.5],
        "龙虎榜净买额": ["1.2亿", 34567890.0, "-5000.00万"],
        "龙虎榜买入额": [1.0e8, 2.0e8, 3.0e8],
        "上榜原因": ["日涨幅偏离值达7%", "机构专用", "连续三个交易日涨幅偏离累计达20%"],
    }
)


def test_normalize_longhu_rows() -> None:
    rows = ak_adapters._normalize_longhu_df(LHB_DF)
    assert rows[0] == {
        "code": "002157",
        "trade_date": "2024-03-15",
        "reason": "日涨幅偏离值达7%",
        "net_buy": pytest.approx(12000.0),
        "source": "akshare_em",
    }
    assert rows[1]["net_buy"] == pytest.approx(3456.789)
    assert rows[2]["code"] == "000001"
    assert rows[2]["net_buy"] == pytest.approx(-5000.0)
    assert rows[2]["trade_date"] == "2024-03-15"


def test_normalize_longhu_missing_optional_fields() -> None:
    df = pd.DataFrame({"代码": ["600000"], "上榜日": ["2024/3/15"]})
    rows = ak_adapters._normalize_longhu_df(df)
    assert rows == [
        {
            "code": "600000",
            "trade_date": "2024-03-15",
            "reason": None,
            "net_buy": None,
            "source": "akshare_em",
        }
    ]


KLINE_DF = pd.DataFrame(
    {
        "date": ["2024-03-15", pd.Timestamp("2024-03-14")],
        "open": [10.0, 9.8],
        "high": [10.5, 9.9],
        "low": [9.7, 9.5],
        "close": [10.2, 9.8],
        "volume": [12345678.0, 9876543.0],
        "amount": [1.25e8, 9.7e7],
        "outstanding_share": [3.0e9, 3.0e9],
        "turnover": [0.0254, 0.0199],
    }
)


def test_normalize_kline_date_as_column() -> None:
    rows = ak_adapters._normalize_kline_df(KLINE_DF, "600519")
    r0 = rows[0]
    assert r0["code"] == "600519"
    assert r0["trade_date"] == "2024-03-15"
    assert (r0["open"], r0["high"], r0["low"], r0["close"]) == (10.0, 10.5, 9.7, 10.2)
    assert r0["volume"] == 12345678.0
    assert r0["amount"] == pytest.approx(1.25e8)
    assert r0["turnover"] == pytest.approx(2.54)
    assert r0["adj_factor"] is None
    assert r0["source"] == "sina"


def test_normalize_kline_date_as_index() -> None:
    rows = ak_adapters._normalize_kline_df(KLINE_DF.set_index("date"), "000001")
    assert [r["trade_date"] for r in rows] == ["2024-03-15", "2024-03-14"]


def test_normalize_kline_missing_turnover() -> None:
    df = KLINE_DF.drop(columns=["turnover"])
    rows = ak_adapters._normalize_kline_df(df, "300750")
    assert rows[0]["turnover"] is None
    assert rows[0]["code"] == "300750"


import time  # noqa: E402

from zhuang_monitor import em_throttle  # noqa: E402


class FakeAk:
    """假 akshare 模块：记录调用参数并返回预置 DataFrame，绝不联网。"""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self.calls: dict[str, Any] = {}

    def stock_zh_a_gdhs_detail_em(self, symbol: str) -> pd.DataFrame:
        self.calls["gdhs"] = symbol
        return self.df

    def stock_lhb_detail_em(self, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls["lhb"] = (start_date, end_date)
        return self.df

    def stock_zh_a_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls["daily"] = (symbol, start_date, end_date)
        return self.df


@pytest.fixture
def paced(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """把 em_throttle 闸门切换为记录模式：返回 fake sleeper 记录表。"""
    monkeypatch.setattr(em_throttle, "_last_request_ts", time.monotonic() - 100.0)
    monkeypatch.setattr(em_throttle, "_shared_session", None)
    monkeypatch.setattr(em_throttle.random, "uniform", lambda a, b: 0.5)
    sleeps: list[float] = []
    monkeypatch.setattr(em_throttle, "_default_sleeper", sleeps.append)
    return sleeps


def test_holder_num_rows_passes_symbol_and_goes_through_gate(paced: list[float]) -> None:
    fake = FakeAk(HOLDER_DF)
    rows = ak_adapters.holder_num_rows("002157", ak_module=fake)
    assert fake.calls["gdhs"] == "002157"
    assert len(rows) == 3
    assert rows[0]["code"] == "002157"
    assert len(paced) == 1


def test_holder_num_rows_two_calls_serialized(paced: list[float]) -> None:
    fake = FakeAk(HOLDER_DF)
    ak_adapters.holder_num_rows("002157", ak_module=fake)
    ak_adapters.holder_num_rows("002158", ak_module=fake)
    assert len(paced) == 2
    assert paced[1] >= em_throttle.MIN_INTERVAL


def test_longhu_rows_converts_trade_date(paced: list[float]) -> None:
    fake = FakeAk(LHB_DF)
    rows = ak_adapters.longhu_rows("2024-03-15", ak_module=fake)
    assert fake.calls["lhb"] == ("20240315", "20240315")
    assert rows[2]["code"] == "000001"


def test_kline_sina_rows_sh_symbol(paced: list[float]) -> None:
    fake = FakeAk(KLINE_DF)
    rows = ak_adapters.kline_sina_rows("600519", "2024-03-14", "2024-03-15", ak_module=fake)
    assert fake.calls["daily"] == ("sh600519", "20240314", "20240315")
    assert rows[0]["source"] == "sina"
    assert rows[0]["code"] == "600519"


def test_kline_sina_rows_sz_and_bj_symbols(paced: list[float]) -> None:
    fake_sz = FakeAk(KLINE_DF)
    ak_adapters.kline_sina_rows("000001", "20240314", "20240315", ak_module=fake_sz)
    assert fake_sz.calls["daily"][0] == "sz000001"
    fake_bj = FakeAk(KLINE_DF)
    ak_adapters.kline_sina_rows("900901", "20240314", "20240315", ak_module=fake_bj)
    assert fake_bj.calls["daily"][0] == "sh900901"


def test_upstream_error_propagates(paced: list[float]) -> None:
    class BoomAk:
        def stock_zh_a_gdhs_detail_em(self, symbol: str) -> pd.DataFrame:
            raise ConnectionError("upstream down")

    with pytest.raises(ConnectionError):
        ak_adapters.holder_num_rows("002157", ak_module=BoomAk())


# ---- 龙虎榜"数据未发布日"quirk（akshare 对空数据抛 TypeError，2026-09-02 实测） ----

class _LhbRaiseAk:
    def __init__(self, exc):
        self.exc = exc
        self.calls = []

    def stock_lhb_detail_em(self, start_date=None, end_date=None):
        self.calls.append((start_date, end_date))
        raise self.exc


def test_longhu_rows_treats_empty_day_as_no_rows():
    ak = _LhbRaiseAk(TypeError("'NoneType' object is not subscriptable"))
    assert ak_adapters.longhu_rows("2026-09-02", ak_module=ak) == []


def test_longhu_rows_re_raises_other_type_error():
    ak = _LhbRaiseAk(TypeError("真正的问题"))
    with pytest.raises(TypeError, match="真正的问题"):
        ak_adapters.longhu_rows("2026-01-02", ak_module=ak)
