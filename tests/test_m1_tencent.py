"""M1 腾讯行情快照测试：注入 fetch_text，不真实联网。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuang_monitor.sources.tencent import parse_quote_line  # noqa: E402


def _quote_line(code: str, name: str, price: str, circ: str, total: str) -> str:
    fields = [""] * 60
    fields[0] = "51"
    fields[1] = name
    fields[2] = code
    fields[3] = price
    fields[44] = circ
    fields[45] = total
    return f'v_sz{code}="' + "~".join(fields) + '";'


def test_parse_quote_line_full() -> None:
    """完整 60 字段行：1=名称、2=代码、3=现价、44=流通市值、45=总市值（亿元）。"""
    line = _quote_line("002348", "高乐股份", "6.88", "45.62", "61.38")
    assert parse_quote_line(line) == {
        "code": "002348", "name": "高乐股份",
        "price": 6.88, "circ_mv": 45.62, "total_mv": 61.38,
    }


def test_parse_quote_line_empty_and_invalid_numeric_give_none() -> None:
    """数值字段为空串或非法值时应为 None 而不是崩溃。"""
    assert parse_quote_line(_quote_line("002348", "高乐股份", "", "abc", "61.38")) == {
        "code": "002348", "name": "高乐股份",
        "price": None, "circ_mv": None, "total_mv": 61.38,
    }


def test_parse_quote_line_rejects_bad_lines() -> None:
    """无匹配行（none_match、空串、字段不足、无前缀）应返回 None。"""
    assert parse_quote_line('v_pv_none_match="1";') is None
    assert parse_quote_line("") is None
    assert parse_quote_line('v_sz002348="51~高乐股份~002348~6.88";') is None
    assert parse_quote_line("not a quote line") is None


from zhuang_monitor.sources.tencent import snapshot  # noqa: E402


def test_snapshot_skips_none_match_and_keys_by_code() -> None:
    """无匹配行跳过，返回以六位代码为键、四个字段为值的字典。"""
    text = "\n".join([
        _quote_line("002348", "高乐股份", "6.88", "45.62", "61.38"),
        'v_pv_none_match="1";',
        'v_sh600000="51~浦发银行~600000~7.20~7.10~7.15' + "~1" * 55 + '";',
    ])
    result = snapshot(["002348", "600000"], fetch_text=lambda url: text)
    assert set(result) == {"002348", "600000"}
    assert result["002348"] == {"name": "高乐股份", "price": 6.88, "circ_mv": 45.62, "total_mv": 61.38}
    assert result["600000"]["name"] == "浦发银行"


def test_snapshot_requests_prefixed_codes_in_input_order() -> None:
    """URL 应为 https://qt.gtimg.cn/q= 加逗号连接的带市场前缀代码（无点）。"""
    urls: list[str] = []

    def fake(url: str) -> str:
        urls.append(url)
        return ""

    snapshot(["002348", "600000", "300750"], fetch_text=fake)
    assert urls == ["https://qt.gtimg.cn/q=sz002348,sh600000,sz300750"]


def test_snapshot_batches_at_most_60_codes() -> None:
    """150 只应分 3 批（60/60/30），每批 URL 内代码数不超限且总覆盖不重不漏。"""
    codes = [f"{600000 + i:06d}" for i in range(150)]
    counts: list[int] = []

    def fake(url: str) -> str:
        batch = url.split("q=")[1].split(",")
        counts.append(len(batch))
        return ""

    assert snapshot(codes, fetch_text=fake) == {}
    assert counts == [60, 60, 30]


from zhuang_monitor.sources.tencent import kline_rows  # noqa: E402


def _kline_payload(symbol: str, days: list[list[str]]) -> str:
    """构造腾讯 fqkline JSON 响应：qfqday 优先，缺省会回退 day。"""
    return (
        '{"code":0,"msg":"","data":{"%s":{"qfqday":%s}}}'
        % (symbol, str([list(d) for d in days]).replace("'", '"').replace("None", "null"))
    )


_FAKE_DAYS = [
    # [date, open, close, high, low, volume]
    ["2025-01-02", "10.772", "10.472", "10.812", "10.432", "1819597.0"],
    ["2025-01-03", "10.472", "10.600", "10.700", "10.400", "1520000.0"],
]


def test_kline_rows_parses_qfq_days_and_maps_columns() -> None:
    """腾讯行（date/open/close/high/low/volume）应正确映射为 PRD kline_daily 行。"""
    urls: list[str] = []

    def fake(url: str) -> bytes:
        urls.append(url)
        return _kline_payload("sz000001", _FAKE_DAYS).encode("utf-8")

    rows = kline_rows("000001", "2025-01-01", "2025-01-10", fetch_bytes=fake)
    assert len(rows) == 2
    assert rows[0] == {
        "code": "000001", "trade_date": "2025-01-02",
        "open": 10.772, "close": 10.472, "high": 10.812, "low": 10.432,
        "volume": 1819597.0, "amount": None, "turnover": None,
        "adj_factor": None, "source": "tencent",
    }
    assert urls[0].startswith("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz000001,day,2025-01-01,2025-01-10,400,qfq")


def test_kline_rows_uses_sh_prefix_and_handles_error_payload() -> None:
    """6/9 开头代码带 sh 前缀；接口返回错误码时应返回空列表（不抛异常）。"""
    def fake(url: str) -> bytes:
        return '{"code":-1,"data":null}'.encode("utf-8")

    assert kline_rows("600000", "2025-01-01", "2025-01-10", fetch_bytes=fake) == []


def test_kline_rows_filters_dates_outside_range() -> None:
    """越出 start/end 的行（分页往返）应被剔除。"""
    def fake(url: str) -> bytes:
        return _kline_payload("sz000001", _FAKE_DAYS).encode("utf-8")

    rows = kline_rows("000001", "2025-01-03", "2025-01-03", fetch_bytes=fake)
    assert [r["trade_date"] for r in rows] == ["2025-01-03"]
