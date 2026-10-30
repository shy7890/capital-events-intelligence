"""M1 全市场当日公告适配层验收测试：纯函数 + 假 ak 模块注入，禁止真实联网。"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from zhuang_monitor import em_throttle  # noqa: E402
from zhuang_monitor.sources import ak_notice  # noqa: E402

NOTICE_DF = pd.DataFrame(
    {
        "代码": ["002348", 1, "600519"],
        "名称": ["高乐股份", "平安银行", "贵州茅台"],
        "公告标题": [" 关于变更证券事务代表的公告 ", "2024年半年度报告", "重大事项公告"],
        "公告类型": ["信息变更", "财务报告", None],
        "公告日期": [
            date(2024, 7, 15),
            "20240715",
            pd.Timestamp("2024-07-15 16:00:00"),
        ],
        "网址": [
            "https://data.eastmoney.com/notices/detail/002348/ABC123.html",
            None,
            "https://data.eastmoney.com/notices/detail/600519/X.html",
        ],
    }
)


def test_normalize_notice_rows_full_mapping() -> None:
    rows = ak_notice._normalize_notice_df(NOTICE_DF)
    assert len(rows) == 3
    assert rows[0] == {
        "code": "002348",
        "pub_date": "2024-07-15",
        "title": "关于变更证券事务代表的公告",
        "pdf_url": "https://data.eastmoney.com/notices/detail/002348/ABC123.html",
        "category": "dc:信息变更",
        "source": "akshare_dc",
    }
    assert rows[1]["code"] == "000001"
    assert rows[1]["pdf_url"] is None
    assert rows[1]["category"] == "dc:财务报告"
    assert rows[2]["category"] is None


def test_normalize_notice_date_forms() -> None:
    df = pd.DataFrame(
        {
            "代码": ["000001"] * 5,
            "公告标题": ["t"] * 5,
            "公告日期": [
                pd.Timestamp("2024-07-15 00:00:00"),
                "2024-07-15 08:30:00",
                "20240715",
                date(2024, 7, 15),
                "2024/7/15",
            ],
        }
    )
    rows = ak_notice._normalize_notice_df(df)
    assert len(rows) == 5
    assert {r["pub_date"] for r in rows} == {"2024-07-15"}


def test_normalize_notice_int_code_padded_to_six() -> None:
    df = pd.DataFrame({"代码": [1, 2348], "公告标题": ["a", "b"], "公告日期": ["20240715", "20240715"]})
    rows = ak_notice._normalize_notice_df(df)
    assert [r["code"] for r in rows] == ["000001", "002348"]


def test_normalize_notice_empty_df_returns_empty_list() -> None:
    assert ak_notice._normalize_notice_df(pd.DataFrame()) == []
    assert (
        ak_notice._normalize_notice_df(
            pd.DataFrame(columns=["代码", "公告标题", "公告日期", "网址"])
        )
        == []
    )


def test_normalize_notice_missing_required_columns_tolerated() -> None:
    df = pd.DataFrame({"代码": ["002348"], "名称": ["高乐股份"]})
    assert ak_notice._normalize_notice_df(df) == []


def test_normalize_notice_incomplete_rows_skipped() -> None:
    df = pd.DataFrame(
        {
            "代码": ["002348", None, "600519"],
            "公告标题": ["a", "b", None],
            "公告日期": ["2024-07-15", "2024-07-15", "2024-07-15"],
        }
    )
    rows = ak_notice._normalize_notice_df(df)
    assert [r["code"] for r in rows] == ["002348"]


def test_normalize_notice_fallback_to_link_column() -> None:
    df = pd.DataFrame(
        {
            "代码": ["002348"],
            "公告标题": ["a"],
            "公告日期": ["2024-07-15"],
            "链接": ["https://x/y.html"],
        }
    )
    rows = ak_notice._normalize_notice_df(df)
    assert rows[0]["pdf_url"] == "https://x/y.html"


class FakeAk:
    """假 akshare 模块：记录调用参数并返回预置 DataFrame / 抛预置异常，绝不联网。"""

    def __init__(self, df: pd.DataFrame | None = None, exc: Exception | None = None) -> None:
        self.df = df
        self.exc = exc
        self.calls: dict[str, Any] = {}

    def stock_notice_report(self, symbol: str = "全部", date: str | None = None) -> pd.DataFrame:
        self.calls["notice"] = (symbol, date)
        if self.exc is not None:
            raise self.exc
        assert self.df is not None
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


def test_notice_day_rows_calls_ak_with_compact_date(paced: list[float]) -> None:
    fake = FakeAk(df=NOTICE_DF)
    rows = ak_notice.notice_day_rows("2024-07-15", ak_module=fake)
    assert fake.calls["notice"] == ("全部", "20240715")
    assert len(rows) == 3
    assert rows[0]["code"] == "002348"
    assert len(paced) == 1


def test_notice_day_rows_goes_through_pace_gate(paced: list[float]) -> None:
    fake = FakeAk(df=NOTICE_DF)
    ak_notice.notice_day_rows("2024-07-15", ak_module=fake)
    ak_notice.notice_day_rows("2024-07-16", ak_module=fake)
    assert len(paced) == 2
    assert paced[1] >= em_throttle.MIN_INTERVAL


def test_notice_day_rows_empty_day_typeerror_returns_empty(paced: list[float]) -> None:
    fake = FakeAk(exc=TypeError("'NoneType' object is not subscriptable"))
    assert ak_notice.notice_day_rows("2026-09-05", ak_module=fake) == []


def test_notice_day_rows_empty_day_keyerror_returns_empty(paced: list[float]) -> None:
    fake = FakeAk(exc=KeyError("代码"))
    assert ak_notice.notice_day_rows("2026-09-05", ak_module=fake) == []


def test_notice_day_rows_other_typeerror_propagates(paced: list[float]) -> None:
    fake = FakeAk(exc=TypeError("真正的问题"))
    with pytest.raises(TypeError, match="真正的问题"):
        ak_notice.notice_day_rows("2024-07-15", ak_module=fake)


def test_notice_day_rows_network_error_propagates(paced: list[float]) -> None:
    fake = FakeAk(exc=ConnectionError("upstream down"))
    with pytest.raises(ConnectionError):
        ak_notice.notice_day_rows("2024-07-15", ak_module=fake)
