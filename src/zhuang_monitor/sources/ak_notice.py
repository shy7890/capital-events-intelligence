"""akshare 东财数据中心"全市场当日公告"适配层（stock_notice_report → PRD announcement 行）。

取数函数经 em_throttle.pace 串行闸门（akshare 内部直连东财不受我们控制）；
ak_module 注入点仅供测试。列名以 akshare 1.18.81 实测为准（代码/名称/公告标题/
公告类型/公告日期/网址），"链接"列名保留兼容读取。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..em_throttle import pace
from .ak_adapters import _norm_code, _norm_date, _norm_text


def _ak() -> Any:
    import akshare

    return akshare


def _compact(date_str: str) -> str:
    return date_str.replace("-", "")


def _normalize_notice_df(df: pd.DataFrame) -> list[dict]:
    """全市场当日公告 DataFrame → PRD announcement 行（缺必需字段的行直接丢弃）。"""
    rows: list[dict] = []
    if df is None or len(df) == 0:
        return rows
    for _, r in df.iterrows():
        code = _norm_code(r.get("代码"))
        pub_date = _norm_date(r.get("公告日期"))
        title = _norm_text(r.get("公告标题"))
        if code is None or pub_date is None or title is None:
            continue
        notice_type = _norm_text(r.get("公告类型"))
        pdf_url = _norm_text(r.get("网址")) or _norm_text(r.get("链接"))
        rows.append(
            {
                "code": code,
                "pub_date": pub_date,
                "title": title,
                "pdf_url": pdf_url,
                "category": f"dc:{notice_type}" if notice_type else None,
                "source": "akshare_dc",
            }
        )
    return rows


@pace
def notice_day_rows(day: str, *, ak_module: Any = None) -> list[dict]:
    """指定日期全市场公告（东财公告大全，单日约 1500 条）→ PRD announcement 行。

    akshare 已知 quirk（2026-09-02 源码走查）：当日公告数为 0（节假日/未发布）时
    东财返回 data=null 或空列表，其组装代码分别抛 TypeError（'NoneType' object is
    not subscriptable）或 KeyError('代码')。适配层把这两种形态与空 DataFrame 一并
    归一为 []；其余异常照抛，由回填层记红单。
    """
    ak = ak_module if ak_module is not None else _ak()
    try:
        df = ak.stock_notice_report(symbol="全部", date=_compact(day))
    except KeyError as exc:
        if exc.args and exc.args[0] in {"代码", "网址", "公告日期", "公告标题"}:
            return []
        raise
    except TypeError as exc:
        if "'NoneType' object is not subscriptable" in str(exc):
            return []
        raise
    return _normalize_notice_df(df)
