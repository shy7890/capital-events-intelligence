"""M1 baostock 日K线主源测试：全程注入 fake bs 模块，不真实联网。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from zhuang_monitor.sources.baostock_k import all_stocks, kline_rows, to_bs_code  # noqa: E402


def test_to_bs_code_sh_prefix() -> None:
    """6 开头（含 688 科创板）应加 sh. 前缀。"""
    assert to_bs_code("600000") == "sh.600000"
    assert to_bs_code("688981") == "sh.688981"


def test_to_bs_code_sz_prefix() -> None:
    """0/3 开头应加 sz. 前缀。"""
    assert to_bs_code("000001") == "sz.000001"
    assert to_bs_code("300750") == "sz.300750"


def test_to_bs_code_unknown_prefix_raises() -> None:
    """北交所等非沪深代码应抛 ValueError。"""
    with pytest.raises(ValueError):
        to_bs_code("830799")


class FakeRsp:
    """fake baostock 响应：按 ResultData 真实协议暴露 fields/data/next。"""

    def __init__(self, error_code: str = "0", error_msg: str = "", data: pd.DataFrame | None = None) -> None:
        self.error_code = error_code
        self.error_msg = error_msg
        df = data if data is not None else pd.DataFrame()
        self.fields = list(df.columns)
        self.data = df.values.tolist()
        self.cur_row_num = 0

    def next(self) -> bool:
        return False


class FakeBS:
    """fake baostock 模块：记录 login/logout/query 调用序，按队列吐出响应。"""

    def __init__(self, responses: list[FakeRsp], login_error: FakeRsp | None = None) -> None:
        self.calls: list[Any] = []
        self.responses = list(responses)
        self.login_error = login_error

    def login(self) -> FakeRsp:
        self.calls.append("login")
        return self.login_error if self.login_error else FakeRsp()

    def logout(self) -> FakeRsp:
        self.calls.append("logout")
        return FakeRsp()

    def query_history_k_data_plus(self, code: str, columns: str, **kwargs: Any) -> FakeRsp:
        self.calls.append(("query", code, columns, kwargs))
        return self.responses.pop(0)


def _raw_df(rows: list[dict[str, str]]) -> pd.DataFrame:
    cols = ["date", "open", "high", "low", "close", "volume", "amount", "turn"]
    return pd.DataFrame(rows, columns=cols)


def _hfq_df(rows: list[dict[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "close"])


def test_kline_rows_call_sequence() -> None:
    """流程应为 login → 不复权查询(adjustflag=3) → 后复权查询(adjustflag=1) → logout。"""
    raw = _raw_df([{"date": "2024-01-02", "open": "9.8", "high": "10.2", "low": "9.7",
                    "close": "10.0", "volume": "123456", "amount": "1234567.8", "turn": "1.23"}])
    hfq = _hfq_df([{"date": "2024-01-02", "close": "12.5"}])
    fake = FakeBS([FakeRsp(data=raw), FakeRsp(data=hfq)])
    rows = kline_rows("600000", "2024-01-01", "2024-01-03", bs=fake)
    assert rows != []
    assert fake.calls[0] == "login"
    assert fake.calls[-1] == "logout"
    q_raw, q_hfq = fake.calls[1], fake.calls[2]
    assert q_raw[1] == "sh.600000"
    assert q_raw[2] == "date,open,high,low,close,volume,amount,turn"
    assert q_raw[3]["adjustflag"] == "3"
    assert q_raw[3]["frequency"] == "d"
    assert q_raw[3]["start_date"] == "2024-01-01" and q_raw[3]["end_date"] == "2024-01-03"
    assert q_hfq[2] == "date,close"
    assert q_hfq[3]["adjustflag"] == "1"


def test_kline_rows_row_fields_and_adj_factor() -> None:
    """输出行键集合须精确匹配 kline_daily schema，adj_factor=后复权close/不复权close。"""
    raw = _raw_df([{"date": "2024-01-02", "open": "9.8", "high": "10.2", "low": "9.7",
                    "close": "10.0", "volume": "123456", "amount": "1234567.8", "turn": "1.23"}])
    hfq = _hfq_df([{"date": "2024-01-02", "close": "12.5"}])
    fake = FakeBS([FakeRsp(data=raw), FakeRsp(data=hfq)])
    rows = kline_rows("600000", "2024-01-01", "2024-01-03", bs=fake)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"code", "trade_date", "open", "high", "low", "close",
                        "volume", "amount", "turnover", "adj_factor", "source"}
    assert row["code"] == "600000"
    assert row["trade_date"] == "2024-01-02"
    assert row["open"] == 9.8 and row["high"] == 10.2 and row["low"] == 9.7 and row["close"] == 10.0
    assert row["volume"] == 123456.0
    assert row["amount"] == 1234567.8
    assert row["turnover"] == 1.23
    assert row["adj_factor"] == 1.25
    assert row["source"] == "baostock"


def test_kline_rows_missing_hfq_date_and_zero_close_give_none_factor() -> None:
    """后复权缺对齐日或原始收盘为 0 时 adj_factor 应为 None。"""
    raw = _raw_df([
        {"date": "2024-01-02", "open": "9.8", "high": "10.2", "low": "9.7",
         "close": "10.0", "volume": "1", "amount": "2", "turn": ""},
        {"date": "2024-01-03", "open": "10.0", "high": "10.5", "low": "10.0",
         "close": "0", "volume": "3", "amount": "4", "turn": "0.5"},
    ])
    hfq = _hfq_df([{"date": "2024-01-04", "close": "12.5"}])
    fake = FakeBS([FakeRsp(data=raw), FakeRsp(data=hfq)])
    rows = kline_rows("600000", "2024-01-01", "2024-01-05", bs=fake)
    assert rows[0]["adj_factor"] is None
    assert rows[0]["turnover"] is None
    assert rows[1]["adj_factor"] is None


def test_kline_rows_query_error_raises_and_logout_still_called() -> None:
    """查询 error_code≠0 应抛 RuntimeError，且 finally 中 logout 仍被调用；锁不得残留。"""
    fake = FakeBS([FakeRsp(error_code="10002", error_msg="不正确的查询参数")])
    with pytest.raises(RuntimeError, match="不正确的查询参数"):
        kline_rows("600000", "2024-01-01", "2024-01-03", bs=fake)
    assert fake.calls == ["login", ("query", "sh.600000", "date,open,high,low,close,volume,amount,turn",
                                    mock.ANY), "logout"]
    raw = _raw_df([{"date": "2024-01-02", "open": "1", "high": "1", "low": "1",
                    "close": "1", "volume": "1", "amount": "1", "turn": "1"}])
    fake2 = FakeBS([FakeRsp(data=raw), FakeRsp(data=_hfq_df([]))])
    assert kline_rows("600000", "2024-01-01", "2024-01-03", bs=fake2) != []


def test_kline_rows_login_error_raises() -> None:
    """登录失败应抛含 error_msg 的 RuntimeError。"""
    fake = FakeBS([], login_error=FakeRsp(error_code="10001", error_msg="网络接收错误"))
    with pytest.raises(RuntimeError, match="网络接收错误"):
        kline_rows("600000", "2024-01-01", "2024-01-03", bs=fake)


def test_kline_rows_empty_returns_empty_list() -> None:
    """无任何数据时应返回空列表。"""
    fake = FakeBS([FakeRsp(data=_raw_df([])), FakeRsp(data=_hfq_df([]))])
    assert kline_rows("600000", "2024-01-01", "2024-01-03", bs=fake) == []


class FakeBSAll(FakeBS):
    """增加 query_all_stock 注入能力的 fake 模块。"""

    def query_all_stock(self, day: str) -> FakeRsp:
        self.calls.append(("all_stock", day))
        return self.responses.pop(0)


def test_all_stocks_strips_prefix_and_follows_session_rule() -> None:
    """应 login→query_all_stock(day)→logout，剥掉市场前缀并保留 bs_code。"""
    df = pd.DataFrame(
        [("sh.600000", "浦发银行"), ("sz.000001", "平安银行"), ("sh.688981", "品高股份")],
        columns=["code", "code_name"],
    )
    fake = FakeBSAll([FakeRsp(data=df)])
    rows = all_stocks("2024-01-02", bs=fake)
    assert fake.calls == ["login", ("all_stock", "2024-01-02"), "logout"]
    assert rows == [
        {"code": "600000", "name": "浦发银行", "bs_code": "sh.600000"},
        {"code": "000001", "name": "平安银行", "bs_code": "sz.000001"},
        {"code": "688981", "name": "品高股份", "bs_code": "sh.688981"},
    ]


def test_all_stocks_error_raises_and_logout_in_finally() -> None:
    """error_code≠0 应抛 RuntimeError，finally 中 logout 仍被调用。"""
    fake = FakeBSAll([FakeRsp(error_code="10004", error_msg="非法日期")])
    with pytest.raises(RuntimeError, match="非法日期"):
        all_stocks("bad-day", bs=fake)
    assert fake.calls == ["login", ("all_stock", "bad-day"), "logout"]


# ---- stock_basic / board_for（主代理扩展：PRD F7 案例回填需要上市日与板块） ----

def test_board_for_prefixes():
    from zhuang_monitor.sources.baostock_k import board_for
    assert board_for("688981") == "科创板"
    assert board_for("600519") == "沪市主板"
    assert board_for("300750") == "创业板"
    assert board_for("002348") == "深市主板"
    assert board_for("832000") is None


def test_stock_basic_normalizes(tmp_path):
    import pandas as pd
    from zhuang_monitor.sources.baostock_k import stock_basic

    class FakeRsp:
        error_code = "0"
        error_msg = ""
        def __init__(self):
            df = pd.DataFrame([{
                "code": "sz.002348", "code_name": "高乐股份", "ipoDate": "2010-01-28",
                "outDate": "1", "type": "1", "status": "1",
            }])
            self.fields = list(df.columns)
            self.data = df.values.tolist()
            self.cur_row_num = 0
        def next(self): return False

    class FakeBs:
        def login(self): return FakeRsp()
        def logout(self): return FakeRsp()
        def query_stock_basic(self, code):
            assert code == "sz.002348"
            return FakeRsp()

    row = stock_basic("002348", bs=FakeBs())
    assert row == {"code": "002348", "name": "高乐股份", "board": "深市主板",
                   "list_date": "2010-01-28", "is_st": 0}


def test_stock_basic_st_flag():
    import pandas as pd
    from zhuang_monitor.sources.baostock_k import stock_basic

    class FakeRsp:
        error_code = "0"
        error_msg = ""
        def __init__(self):
            df = pd.DataFrame([{
                "code": "sh.600xxx", "code_name": "*ST某某", "ipoDate": "2000-01-01",
                "outDate": "1", "type": "1", "status": "1",
            }])
            self.fields = list(df.columns)
            self.data = df.values.tolist()
            self.cur_row_num = 0
        def next(self): return False

    class FakeBs:
        def login(self): return FakeRsp()
        def logout(self): return FakeRsp()
        def query_stock_basic(self, code): return FakeRsp()

    assert stock_basic("600000", bs=FakeBs())["is_st"] == 1


# ---- _get_data 安全分页（修复 baostock 0.9.3 在 pandas>=2 下跨页崩溃，2026-09-02 实测） ----

class _PagedRsp:
    def __init__(self, pages):
        self.fields = ["date", "close"]
        self.error_code = "0"
        self._pages = list(pages)
        self.data = self._pages.pop(0)
        self.cur_row_num = 0

    def next(self):
        if not self._pages:
            return False
        if len(self.data) < 2000:
            return False
        self.data = self._pages.pop(0)
        return True


def test_get_data_single_page():
    from zhuang_monitor.sources.baostock_k import _get_data
    rsp = _PagedRsp([[["2024-01-02", 6.9]]])
    df = _get_data(rsp)
    assert list(df["date"]) == ["2024-01-02"]


def test_get_data_merges_pages_in_order():
    import pandas as pd
    from zhuang_monitor.sources.baostock_k import _get_data
    page1 = [[f"d{i}", 1.0] for i in range(2000)]
    page2 = [["x1", 2.0], ["x2", 3.0]]
    df = _get_data(_PagedRsp([page1, page2]))
    assert len(df) == 2002
    assert df.iloc[0]["date"] == "d0" and df.iloc[-1]["date"] == "x2"


def test_get_data_empty():
    from zhuang_monitor.sources.baostock_k import _get_data
    rsp = _PagedRsp([[]])
    assert len(_get_data(rsp)) == 0


def test_all_basic_single_call():
    import pandas as pd
    from zhuang_monitor.sources.baostock_k import all_basic

    class FakeRsp:
        error_code = "0"; error_msg = ""
        def __init__(self):
            df = pd.DataFrame([
                {"code": "sz.002348", "code_name": "高乐股份", "ipoDate": "2010-01-28", "status": "1"},
                {"code": "sh.600000", "code_name": "浦发银行", "ipoDate": "1999-11-10", "status": "1"},
                {"code": "sz.000999", "code_name": "已退市", "ipoDate": "1995-01-01", "status": "0"},
            ])
            self.fields = list(df.columns)
            self.data = df.values.tolist()
            self.cur_row_num = 0
        def next(self): return False

    class FakeBs:
        def __init__(self): self.login_ok = 0
        def login(self): self.login_ok += 1; return FakeRsp()
        def logout(self): pass
        def query_stock_basic(self, code=""):
            assert code == ""
            return FakeRsp()

    fake = FakeBs()
    rows = all_basic(bs=fake)
    assert fake.login_ok == 1  # 单次登录全量
    assert rows == [
        {"code": "002348", "name": "高乐股份", "list_date": "2010-01-28"},
        {"code": "600000", "name": "浦发银行", "list_date": "1999-11-10"},
    ]  # status≠1 的退市行剔除
