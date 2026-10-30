"""东方财富直连强制限流封装（AGENTS.md 数据源规约）。

限流模式改编自 simonlin1212/a-stock-data（Apache-2.0），本实现有修改：
- 原版仅做请求间 sleep，本实现引入全局串行锁 gate，使 akshare 等不受控的
  直连东财调用也纳入同一节奏；
- sleeper 为注入式参数，测试可断言间隔而无需真实等待。
"""

from __future__ import annotations

import random
import threading
import time
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterator

import requests

MIN_INTERVAL = 1.5
JITTER_MAX = 0.6
RETRY_STATUSES = frozenset({429, 500, 502, 503})
MAX_RETRIES = 3


class EastmoneyBlocked(RuntimeError):
    """东财 403 封禁信号：绝不重试，由调用方切换兜底源。"""


_lock = threading.Lock()
_last_request_ts: float = 0.0
_shared_session: Any = None
_default_sleeper: Callable[[float], None] = time.sleep


def _ensure_session() -> Any:
    global _shared_session
    if _shared_session is None:
        _shared_session = requests.Session()
    return _shared_session


@contextmanager
def gate(sleeper: Callable[[float], None] | None = None) -> Iterator[None]:
    """串行闸门：全局锁 + 距上次请求补足 MIN_INTERVAL 并叠加 0~JITTER_MAX 抖动。

    with 块内的整个请求过程被串行化，供 em_get 与 pace 共用。
    """
    global _last_request_ts
    s = sleeper if sleeper is not None else _default_sleeper
    with _lock:
        elapsed = time.monotonic() - _last_request_ts
        s(max(0.0, MIN_INTERVAL - elapsed) + random.uniform(0.0, JITTER_MAX))
        _last_request_ts = time.monotonic()
        yield


def pace(
    func: Callable | None = None,
    *,
    sleeper: Callable[[float], None] | None = None,
) -> Callable:
    """把 akshare 内部直连东财的调用也纳入同一串行闸门的装饰器（支持裸用与带参用）。"""

    def deco(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with gate(sleeper):
                return f(*args, **kwargs)

        return wrapper

    if func is not None:
        return deco(func)
    return deco


def em_get(
    url: str,
    params: dict | None = None,
    *,
    session: Any = None,
    sleeper: Callable[[float], None] = time.sleep,
    timeout: float = 15.0,
) -> dict:
    """直连东财 GET（强制串行限流）。

    200 → resp.json()；403 → 立即抛 EastmoneyBlocked（绝不重试）；
    429/500/502/503 → 最多重试 MAX_RETRIES 次，指数退避 2*(2**n)+抖动；
    其他状态码 → raise_for_status；网络异常直接上抛。
    """
    sess = session if session is not None else _ensure_session()
    attempt = 0
    while True:
        with gate(sleeper):
            resp = sess.get(url, params=params, timeout=timeout)
        status = resp.status_code
        if status == 200:
            return resp.json()
        if status == 403:
            raise EastmoneyBlocked(f"东财 403 封禁：{url}")
        if status in RETRY_STATUSES and attempt < MAX_RETRIES:
            sleeper(2 * (2**attempt) + random.uniform(0.0, JITTER_MAX))
            attempt += 1
            continue
        resp.raise_for_status()
