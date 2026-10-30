"""M1 限流封装验收测试：全部使用假 session/fake sleeper，禁止真实联网。"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from zhuang_monitor import em_throttle  # noqa: E402
from zhuang_monitor.em_throttle import EastmoneyBlocked, em_get  # noqa: E402

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


class FakeResp:
    """假响应：只带状态码与 payload，不联网。"""

    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeSession:
    """按预置队列逐次返回响应，并记录每次 get 调用。"""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, Any, Any]] = []

    def get(self, url: str, params: Any = None, timeout: Any = None) -> FakeResp:
        self.calls.append((url, params, timeout))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _deterministic(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """重置串行时间戳与共享 session，并把抖动固定为 0.5，返回 fake sleeper 记录表。"""
    monkeypatch.setattr(em_throttle, "_last_request_ts", time.monotonic() - 100.0)
    monkeypatch.setattr(em_throttle, "_shared_session", None)
    monkeypatch.setattr(em_throttle.random, "uniform", lambda a, b: 0.5)
    sleeps: list[float] = []
    return sleeps


def _sleeper(box: list[float]):
    def _s(x: float) -> None:
        box.append(x)
    return _s


def test_blocked_is_runtimeerror() -> None:
    assert issubclass(EastmoneyBlocked, RuntimeError)


def test_em_get_200_returns_json_and_paces(_deterministic: list[float]) -> None:
    sleeps = _deterministic
    session = FakeSession([FakeResp(200, {"ok": 1})])
    result = em_get(URL, {"a": "1"}, session=session, sleeper=_sleeper(sleeps))
    assert result == {"ok": 1}
    assert session.calls == [(URL, {"a": "1"}, 15.0)]
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.5)


def test_em_get_two_calls_interval_at_least_min(_deterministic: list[float]) -> None:
    sleeps = _deterministic
    session = FakeSession([FakeResp(200, {"n": 1}), FakeResp(200, {"n": 2})])
    assert em_get(URL, session=session, sleeper=_sleeper(sleeps)) == {"n": 1}
    assert em_get(URL, session=session, sleeper=_sleeper(sleeps)) == {"n": 2}
    assert len(sleeps) == 2
    assert sleeps[1] >= em_throttle.MIN_INTERVAL


def test_em_get_403_raises_blocked_immediately_without_retry(
    _deterministic: list[float],
) -> None:
    sleeps = _deterministic
    session = FakeSession([FakeResp(403), FakeResp(200, {"x": 1})])
    with pytest.raises(EastmoneyBlocked):
        em_get(URL, session=session, sleeper=_sleeper(sleeps))
    assert len(session.calls) == 1
    assert all(s < 2.0 for s in sleeps)


def test_em_get_retryable_status_backs_off_exponentially(
    _deterministic: list[float],
) -> None:
    sleeps = _deterministic
    session = FakeSession(
        [FakeResp(429), FakeResp(503), FakeResp(200, {"ok": True})]
    )
    assert em_get(URL, session=session, sleeper=_sleeper(sleeps)) == {"ok": True}
    assert len(session.calls) == 3
    assert [s for s in sleeps if s in (2.5, 4.5)] == [2.5, 4.5]


def test_em_get_retry_exhausted_then_raise_for_status(
    _deterministic: list[float],
) -> None:
    sleeps = _deterministic
    session = FakeSession([FakeResp(429) for _ in range(5)])
    with pytest.raises(RuntimeError, match="http 429"):
        em_get(URL, session=session, sleeper=_sleeper(sleeps))
    assert len(session.calls) == em_throttle.MAX_RETRIES + 1
    assert [s for s in sleeps if s in (2.5, 4.5, 8.5)] == [2.5, 4.5, 8.5]


def test_em_get_other_status_no_retry(_deterministic: list[float]) -> None:
    session = FakeSession([FakeResp(404), FakeResp(200, {"x": 1})])
    with pytest.raises(RuntimeError, match="http 404"):
        em_get(URL, session=session, sleeper=_sleeper(_deterministic))
    assert len(session.calls) == 1


def test_em_get_network_error_propagates(_deterministic: list[float]) -> None:
    session = FakeSession([ConnectionError("net down")])
    with pytest.raises(ConnectionError):
        em_get(URL, session=session, sleeper=_sleeper(_deterministic))


def test_default_session_is_reused(_deterministic: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[Any] = []

    class SessionFactory:
        def __call__(self) -> FakeSession:
            s = FakeSession([FakeResp(200, {"i": len(created)}), FakeResp(200, {"i": 9})])
            created.append(s)
            return s

    monkeypatch.setattr(em_throttle.requests, "Session", SessionFactory())
    assert em_get(URL, params=None, sleeper=_sleeper(_deterministic)) == {"i": 0}
    assert em_get(URL, params=None, sleeper=_sleeper(_deterministic)) == {"i": 9}
    assert len(created) == 1


def test_threads_share_single_gate(_deterministic: list[float]) -> None:
    sleeps = _deterministic
    session = FakeSession([FakeResp(200, {"t": i}) for i in range(3)])
    results: list[Any] = []
    lock = threading.Lock()

    def _worker() -> None:
        r = em_get(URL, session=session, sleeper=_sleeper(sleeps))
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(results) == 3
    assert len(sleeps) == 3


def test_gate_context_manager_paces(_deterministic: list[float]) -> None:
    sleeps = _deterministic
    with em_throttle.gate(_sleeper(sleeps)):
        pass
    with em_throttle.gate(_sleeper(sleeps)):
        pass
    assert len(sleeps) == 2
    assert sleeps[1] >= em_throttle.MIN_INTERVAL


def test_pace_bare_decorator_preserves_behavior(_deterministic: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _deterministic
    monkeypatch.setattr(em_throttle, "_default_sleeper", _sleeper(sleeps))

    @em_throttle.pace
    def add(a: int, b: int = 2) -> int:
        return a + b

    assert add(1) == 3
    assert add.__name__ == "add"


def test_pace_same_call_serialized_and_paced(_deterministic: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _deterministic
    monkeypatch.setattr(em_throttle, "_default_sleeper", _sleeper(sleeps))

    @em_throttle.pace
    def noop(x: int) -> int:
        return x * 2

    assert [noop(i) for i in range(3)] == [0, 2, 4]
    assert len(sleeps) == 3
    assert all(s >= em_throttle.MIN_INTERVAL for s in sleeps[1:])


def test_pace_with_explicit_sleeper_kwarg(_deterministic: list[float]) -> None:
    sleeps = _deterministic

    @em_throttle.pace(sleeper=_sleeper(sleeps))
    def twice(x: int) -> int:
        return x * 2

    assert twice(5) == 10
    assert twice(6) == 12
    assert len(sleeps) == 2
    assert sleeps[1] >= em_throttle.MIN_INTERVAL
