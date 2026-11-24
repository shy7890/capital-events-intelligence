"""巨潮资讯网（cninfo）公告客户端（PRD §4.2）：零鉴权公开接口，域内全局限流。

- orgId 映射表一次性内存缓存（orgId 一律取文件实际值，绝不自己拼）
- 公告分页查询 pageSize 硬上限 30
"""

from __future__ import annotations

import html
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

ORGID_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_PREFIX = "https://static.cninfo.com.cn/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www.cninfo.com.cn/new/index",
}

MIN_INTERVAL = 1.5
JITTER_RANGE = 0.5
PAGE_SIZE = 30

_orgid_cache: dict[str, str] | None = None
_lock = threading.Lock()
_last_request_at: float | None = None


def _clear_cache() -> None:
    """重置模块级 orgId 缓存与限流时钟（供测试与长任务分轮回置）。"""
    global _orgid_cache, _last_request_at
    with _lock:
        _orgid_cache = None
        _last_request_at = None


def _throttle(sleeper: Callable[[float], None] = time.sleep) -> None:
    """cninfo 域内串行限流：任意两次请求间隔 ≥1.5 秒并叠加 0~0.5 秒随机抖动。"""
    global _last_request_at
    with _lock:
        now = time.monotonic()
        wait = MIN_INTERVAL + random.uniform(0.0, JITTER_RANGE)
        if _last_request_at is not None:
            wait = max(MIN_INTERVAL, wait - (now - _last_request_at))
        _last_request_at = now
    sleeper(wait)


def _default_fetch_json(url: str) -> dict:
    _throttle()
    resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=False)
    resp.raise_for_status()
    return resp.json()


def resolve_org_id(code: str, *, fetch_json: Callable[[str], dict] | None = None) -> str | None:
    """按 6 位代码查全市场 orgId；首次调用拉取映射表并缓存，未命中返回 None。"""
    global _orgid_cache
    with _lock:
        cached = _orgid_cache
    if cached is None:
        fetch = fetch_json or _default_fetch_json
        payload = fetch(ORGID_URL)
        cached = {
            str(item.get("code", "")): str(item["orgId"])
            for item in payload.get("stockList", [])
            if item.get("code") and item.get("orgId")
        }
        with _lock:
            _orgid_cache = cached
    return cached.get(code)


def column_for(code: str) -> str:
    """代码 6 开头走上交所 column=sse，否则（0/3 开头）走深交所 column=szse。"""
    return "sse" if code.startswith("6") else "szse"


def _clean_title(title: str) -> str:
    """公告标题清洗：去 HTML 标签、还原实体、压缩空白为纯文本。"""
    text = re.sub(r"<[^>]+>", "", title or "")
    text = html.unescape(text)
    return " ".join(text.split())


def _ms_to_date(ms: int) -> str:
    """毫秒 epoch 时间戳转 YYYY-MM-DD（固定东八区，避免受本机时区影响）。"""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone(timedelta(hours=8)))
    return dt.strftime("%Y-%m-%d")


def fetch_announcements(
    code: str,
    start: str,
    end: str,
    *,
    category: str | None = None,
    max_pages: int | None = None,
    session: Any = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """分页拉取某股区间公告，返回 PRD §6 announcement 行；翻页至 hasMore 为假或 max_pages。"""
    org_id = resolve_org_id(code)
    sess = session if session is not None else requests.Session()
    rows: list[dict] = []
    page = 1
    while True:
        data = {
            "pageNum": page,
            "pageSize": PAGE_SIZE,
            "column": column_for(code),
            "tabName": "fulltext",
            "stock": f"{code},{org_id}",
            "seDate": f"{start}~{end}",
            "sortField": "announcementTime",
            "sortAsc": "false",
        }
        if category:
            data["category"] = category
        _throttle(sleeper)
        resp = sess.post(QUERY_URL, data=data, headers=HEADERS, timeout=30, allow_redirects=False)
        resp.raise_for_status()
        payload = resp.json()
        for item in payload.get("announcements") or []:
            adjunct = item.get("adjunctUrl")
            rows.append(
                {
                    "code": item.get("secCode") or code,
                    "pub_date": _ms_to_date(int(item["announcementTime"])),
                    "title": _clean_title(item.get("announcementTitle", "")),
                    "pdf_url": (STATIC_PREFIX + adjunct) if adjunct else None,
                    "category": category,
                    "source": "cninfo",
                }
            )
        has_more = bool(payload.get("hasMore"))
        if not has_more or (max_pages is not None and page >= max_pages):
            break
        page += 1
    return rows
