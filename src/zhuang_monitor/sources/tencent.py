"""腾讯行情（PRD §4.2：qt.gtimg.cn / web.ifzq.gtimg.cn，不封 IP）。

- 实时快照：qt.gtimg.cn/q=（市值快照主源）
- 历史日 K：web.ifzq.gtimg.cn/appstock/app/fqkline/get=（K 线第三兜底源）
响应为 GBK 编码（历史接口为 JSON）；真实联网走 fetch_text 注入点，测试注入。
"""

from __future__ import annotations

import json
import re
from typing import Callable

from .baostock_k import to_bs_code

_LINE_RE = re.compile(r'^v_(?:sh|sz)(\d{6})="([^"]*)"')

_MIN_FIELDS = 46
_BATCH_SIZE = 60
_KLINE_BATCH = 400  # 单次请求最多约 400 根（腾讯上限），长区间自动分页
_URL_BASE = "http://qt.gtimg.cn/q="
_KLINE_URL_BASE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _to_float(text: str) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value


def parse_quote_line(line: str) -> dict | None:
    """解析一行 v_sz000000="51~名称~代码~现价~...~流通市值~总市值~..." 报价；非法行返回 None。"""
    match = _LINE_RE.match(line.strip())
    if not match:
        return None
    fields = match.group(2).split("~")
    if len(fields) < _MIN_FIELDS:
        return None
    return {
        "code": match.group(1),
        "name": fields[1],
        "price": _to_float(fields[3]),
        "circ_mv": _to_float(fields[44]),
        "total_mv": _to_float(fields[45]),
    }


def _default_fetch(url: str) -> str:
    """默认拉取：腾讯响应为 GBK 编码，须显式解码。"""
    import requests

    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.content.decode("gbk", errors="replace")


def _default_fetch_json(url: str) -> bytes:
    import requests

    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.content


def snapshot(codes: list[str], *, fetch_text: Callable[[str], str] | None = None) -> dict[str, dict]:
    """批量拉取实时行情快照，返回 {六位代码: {name, price, circ_mv, total_mv}}（市值单位亿元）。"""
    fetch = fetch_text or _default_fetch
    result: dict[str, dict] = {}
    for i in range(0, len(codes), _BATCH_SIZE):
        batch = codes[i:i + _BATCH_SIZE]
        symbols = [to_bs_code(c).replace(".", "") for c in batch]
        text = fetch(_URL_BASE + ",".join(symbols))
        for line in text.splitlines():
            quote = parse_quote_line(line)
            if quote is not None:
                result[quote.pop("code")] = quote
    return result


def kline_rows(
    code: str,
    start: str,
    end: str,
    *,
    fetch_bytes: Callable[[str], bytes] | None = None,
) -> list[dict]:
    """腾讯前复权日 K（第三兜底源，2026-09-03 验证不封 IP、响应 <1s）→ PRD kline_daily 行。

    字段格式：'[date, open, close, high, low, volume]'（无 amount/turnover/adj_factor，
    置 None）；区间按 _KLINE_BATCH 分页，任一页空/失败整体返回空列表（由上层决定是否继续降级）。
    """
    fetch = fetch_bytes or _default_fetch_json
    market = "sh" if code.startswith(("6", "9")) else "sz"
    symbol = f"{market}{code}"
    rows: list[dict] = []
    cursor = start
    while cursor <= end:
        # 腾讯要求日期带横杠：20260831 会返回 {"code":0,"msg":"param error","data":[]}
        url = f"{_KLINE_URL_BASE}?param={symbol},day,{cursor},{end},{_KLINE_BATCH},qfq"
        payload = fetch(url)
        data = json.loads(payload.decode("utf-8", errors="replace"))
        item = (data.get("data") or {}).get(symbol) or {}
        day = item.get("qfqday") or item.get("day") or []
        for line in day:
            if len(line) < 6:
                continue
            rows.append({
                "code": code,
                "trade_date": line[0],
                "open": _to_float(line[1]),
                "close": _to_float(line[2]),
                "high": _to_float(line[3]),
                "low": _to_float(line[4]),
                "volume": _to_float(line[5]),
                "amount": None,
                "turnover": None,
                "adj_factor": None,
                "source": "tencent",
            })
        if len(day) < _KLINE_BATCH:
            break
        last = day[-1][0]
        if last >= end or last == cursor:
            break
        cursor = last
    return [r for r in rows if start <= r["trade_date"] <= end]
