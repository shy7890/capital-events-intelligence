"""baostock 日K线主源（PRD §4.2：TCP 直连不封 IP）。

baostock 的 login 会话非线程安全，模块级 Lock 串行化全部调用；
真实联网逻辑一律留 bs 注入点，测试注入 fake 模块。
"""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()

RAW_FIELDS = "date,open,high,low,close,volume,amount,turn"


def to_bs_code(code: str) -> str:
    """六位数字代码转 baostock 带市场前缀代码（6 开头→sh.，0/3 开头→sz.）。"""
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith(("0", "3")):
        return f"sz.{code}"
    raise ValueError(f"无法识别的市场前缀: {code}")


def _to_float(value: Any) -> float | None:
    """baostock 字段转 float，空串/缺失/非法值返回 None。"""
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _check(rsp: Any, action: str) -> Any:
    if rsp.error_code != "0":
        raise RuntimeError(f"baostock {action}失败: {rsp.error_msg}")
    return rsp


def _get_data(rsp: Any) -> Any:
    """等价于 baostock ResultData.get_data，但用 pandas.concat 合并分页。

    上游 0.9.3 在跨页合并时调用已被 pandas>=2.0 移除的 DataFrame.append，
    长区间查询会崩（2026-09-02 实测）；此处按同一翻页协议自行收集。
    """
    import pandas as pd

    rows = list(rsp.data)
    rsp.cur_row_num = len(rows)
    while rsp.next():
        page = list(rsp.data)
        rows.extend(page)
        rsp.cur_row_num = len(page)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=list(rsp.fields))


def kline_rows(code: str, start: str, end: str, *, bs: Any = None) -> list[dict]:
    """拉取单只股票日K线（不复权原始价 + 后复权 close 对齐算复权因子），返回 kline_daily 行列表。"""
    if bs is None:
        import baostock as bs  # noqa: F811
    with _LOCK:
        _check(bs.login(), "登录")
        try:
            bs_code = to_bs_code(code)
            query_kwargs = dict(start_date=start, end_date=end, frequency="d")
            rsp_raw = _check(bs.query_history_k_data_plus(
                bs_code, RAW_FIELDS, adjustflag="3", **query_kwargs), "不复权K线查询")
            rsp_hfq = _check(bs.query_history_k_data_plus(
                bs_code, "date,close", adjustflag="1", **query_kwargs), "后复权K线查询")
            raw_df = _get_data(rsp_raw)
            hfq_df = _get_data(rsp_hfq)
        finally:
            bs.logout()
    hfq_close = {}
    for _, r in hfq_df.iterrows():
        f = _to_float(r["close"])
        if f is not None:
            hfq_close[str(r["date"])] = f
    rows: list[dict] = []
    for _, r in raw_df.iterrows():
        date = str(r["date"])
        close = _to_float(r["close"])
        hfq = hfq_close.get(date)
        adj = round(hfq / close, 8) if hfq is not None and close else None
        rows.append({
            "code": code,
            "trade_date": date,
            "open": _to_float(r["open"]),
            "high": _to_float(r["high"]),
            "low": _to_float(r["low"]),
            "close": close,
            "volume": _to_float(r["volume"]),
            "amount": _to_float(r["amount"]),
            "turnover": _to_float(r["turn"]),
            "adj_factor": adj,
            "source": "baostock",
        })
    return rows


def all_basic(*, bs: Any = None) -> list[dict]:
    """一次性全量上市日期：query_stock_basic 空参返回全部证券，剔除退市/非股票行。"""
    if bs is None:
        import baostock as bs  # noqa: F811
    with _LOCK:
        _check(bs.login(), "登录")
        try:
            rsp = _check(bs.query_stock_basic(), "全量基本资料查询")
            df = _get_data(rsp)
        finally:
            bs.logout()
    return [
        {"code": str(r["code"]).split(".", 1)[-1], "name": str(r["code_name"]),
         "list_date": str(r["ipoDate"]) or None}
        for _, r in df.iterrows()
        if str(r.get("type", "1")) == "1" and str(r.get("status", "1")) == "1"
    ]


def board_for(code: str) -> str | None:
    """按代码前缀判板块；北交所等 v1 排除范围返回 None。"""
    if code.startswith("68"):
        return "科创板"
    if code.startswith("6"):
        return "沪市主板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith("00"):
        return "深市主板"
    return None


def stock_basic(code: str, *, bs: Any = None) -> dict:
    """查单只股票基本资料 → stock 表行 {"code","name","board","list_date","is_st"}。"""
    if bs is None:
        import baostock as bs  # noqa: F811
    with _LOCK:
        _check(bs.login(), "登录")
        try:
            rsp = _check(bs.query_stock_basic(to_bs_code(code)), "基本资料查询")
            df = _get_data(rsp)
        finally:
            bs.logout()
    if df is None or len(df) == 0:
        raise RuntimeError(f"baostock 无 {code} 基本资料")
    r = df.iloc[0]
    name = str(r["code_name"])
    return {
        "code": code,
        "name": name,
        "board": board_for(code),
        "list_date": str(r["ipoDate"]) or None,
        "is_st": 1 if "ST" in name.upper() else 0,
    }


def all_stocks(day: str, *, bs: Any = None) -> list[dict]:
    """查询指定交易日全部上市股票：{"code": 六位代码, "name": 简称, "bs_code": "sh.600000"}。"""
    if bs is None:
        import baostock as bs  # noqa: F811
    with _LOCK:
        _check(bs.login(), "登录")
        try:
            rsp = _check(bs.query_all_stock(day=day), "全部股票查询")
            df = _get_data(rsp)
        finally:
            bs.logout()
    return [
        {"code": str(r["code"]).split(".", 1)[-1], "name": str(r["code_name"]), "bs_code": str(r["code"])}
        for _, r in df.iterrows()
    ]
