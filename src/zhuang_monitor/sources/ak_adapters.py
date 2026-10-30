"""akshare 列名映射适配层（PRD 规约：锁定 akshare==1.18.81 + 适配层隔离列名漂移）。

导出的取数函数一律经 em_throttle.pace 串行闸门（akshare 内部直连东财不受我们控制），
网络/上游错误直接向上抛出、由调用方 scan_run 层记录；ak_module 注入点仅供测试。
"""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

_EPOCH_NAN_TOKENS = {"", "--", "-", "nan", "NaT", "None", "<NA>"}


def _is_nan(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA


def _to_float(value: Any) -> float | None:
    """宽松转 float：支持千分位逗号与百分号，空值/脏值返回 None。"""
    if _is_nan(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text.lower() in _EPOCH_NAN_TOKENS:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _norm_date(value: Any) -> str | None:
    """日期归一化为 YYYY-MM-DD：支持 YYYYMMDD、YYYY-MM-DD、YYYY/M/D 与 Timestamp。"""
    if _is_nan(value):
        return None
    parts = re.findall(r"\d+", str(value))
    if len(parts) >= 3:
        y, m, d = (int(p) for p in parts[:3])
    elif len(parts) == 1 and len(parts[0]) == 8:
        y, m, d = int(parts[0][:4]), int(parts[0][4:6]), int(parts[0][6:8])
    else:
        return None
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def _norm_code(value: Any) -> str | None:
    """证券代码归一化为 6 位字符串，缺失返回 None。"""
    if _is_nan(value):
        return None
    text = str(value).strip()
    if text.lower() in _EPOCH_NAN_TOKENS:
        return None
    return text.zfill(6) if text.isdigit() and len(text) < 6 else text


def _norm_text(value: Any) -> str | None:
    if _is_nan(value):
        return None
    text = str(value).strip()
    return text if text and text.lower() not in _EPOCH_NAN_TOKENS else None


from ..em_throttle import pace


def _ak() -> Any:
    import akshare

    return akshare


def _compact(date_str: str) -> str:
    return date_str.replace("-", "")


def parse_cn_amount(value: Any) -> float | None:
    """把东财风格的金额（元/万元/亿元混合）统一换算为万元。

    无后缀的裸数值视为元；`x万` 视为万元；`x亿` 视为亿元（×1e4）；
    支持千分位逗号、`元` 后缀与首尾空白；无法解析返回 None。
    """
    if value is None or value is pd.NA:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value) / 10000.0
    text = str(value).strip().replace(",", "").replace("，", "")
    text = text.removesuffix("元").strip()
    if not text or text in {"--", "-", "nan", "NaT", "None", "<NA>"}:
        return None
    multiplier = 0.0001
    if text.endswith("亿"):
        multiplier = 10000.0
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 1.0
        text = text[:-1]
    try:
        return float(text.strip()) * multiplier
    except ValueError:
        return None


def _normalize_holder_df(df: pd.DataFrame) -> list[dict]:
    """东财股东户数明细 DataFrame → PRD holder_num 行（总市值换算为亿元）。"""
    rows: list[dict] = []
    for _, r in df.iterrows():
        total_mv = _to_float(r.get("总市值"))
        rows.append(
            {
                "code": _norm_code(r.get("代码")),
                "disclose_date": _norm_date(r.get("股东户数公告日期"))
                or _norm_date(r.get("股东户数统计截止日")),
                "holder_num": _to_float(r.get("股东户数-本次")),
                "pct_change": _to_float(r.get("股东户数-增减比例")),
                "avg_hold": _to_float(r.get("户均持股市值")),
                "total_mv": None if total_mv is None else total_mv / 1e8,
                "source": "akshare_em",
            }
        )
    return rows


def _normalize_longhu_df(df: pd.DataFrame) -> list[dict]:
    """东财龙虎榜明细 DataFrame → PRD longhu 行（净买额统一为万元）。"""
    rows: list[dict] = []
    for _, r in df.iterrows():
        rows.append(
            {
                "code": _norm_code(r.get("代码")),
                "trade_date": _norm_date(r.get("上榜日")),
                "reason": _norm_text(r.get("上榜原因")),
                "net_buy": parse_cn_amount(r.get("龙虎榜净买额")),
                "source": "akshare_em",
            }
        )
    return rows


def _normalize_kline_df(df: pd.DataFrame, code: str) -> list[dict]:
    """新浪日 K DataFrame → PRD kline_daily 行（turnover 小数比例 ×100 为百分数）。"""
    dates = df["date"].tolist() if "date" in df.columns else list(df.index)
    rows: list[dict] = []
    for i, (_, r) in enumerate(df.iterrows()):
        turnover = _to_float(r.get("turnover"))
        rows.append(
            {
                "code": code,
                "trade_date": _norm_date(dates[i]),
                "open": _to_float(r.get("open")),
                "high": _to_float(r.get("high")),
                "low": _to_float(r.get("low")),
                "close": _to_float(r.get("close")),
                "volume": _to_float(r.get("volume")),
                "amount": _to_float(r.get("amount")),
                "turnover": None if turnover is None else turnover * 100.0,
                "adj_factor": None,
                "source": "sina",
            }
        )
    return rows


@pace
def holder_num_rows(code: str, *, ak_module: Any | None = None) -> list[dict]:
    """单票股东户数历史（akshare 东财数据中心 → PRD holder_num 行）。"""
    ak = ak_module if ak_module is not None else _ak()
    df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
    return _normalize_holder_df(df)


@pace
def longhu_rows(trade_date: str, *, ak_module: Any | None = None) -> list[dict]:
    """指定交易日全市场龙虎榜明细（akshare 东财 → PRD longhu 行）。

    akshare 1.18.81 已知 quirk：当日榜单尚未发布（如当天盘后过早）时东财返回
    null result，其代码直接取下标抛 TypeError（2026-09-02 实测）。适配层把该
    形态按"当日无数据"处理；其余异常照抛，由 pipeline 记红单。
    """
    ak = ak_module if ak_module is not None else _ak()
    day = _compact(trade_date)
    try:
        df = ak.stock_lhb_detail_em(start_date=day, end_date=day)
    except TypeError as exc:
        if "'NoneType' object is not subscriptable" in str(exc):
            return []
        raise
    return _normalize_longhu_df(df)


@pace
def kline_sina_rows(
    code: str,
    start: str,
    end: str,
    *,
    ak_module: Any | None = None,
) -> list[dict]:
    """新浪日 K 兜底源（不封 IP）→ PRD kline_daily 行。"""
    ak = ak_module if ak_module is not None else _ak()
    symbol = ("sh" if code.startswith(("6", "9")) else "sz") + code
    df = ak.stock_zh_a_daily(symbol=symbol, start_date=_compact(start), end_date=_compact(end))
    return _normalize_kline_df(df, code)
