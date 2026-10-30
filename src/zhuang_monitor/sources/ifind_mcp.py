"""iFinD MCP 客户端：直连自建 MCP-over-HTTP 服务（PRD §4.2 主源通道）。

密钥只从本机 0600 文件读取，绝不出现在日志与异常文本（AGENTS.md 密钥规约）。
上游共享配额：所有请求经同一把锁串行并限速。
"""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

from ..config import settings

PROTOCOL_VERSION = "2025-03-26"
MIN_TOOL_INTERVAL = 1.0
JITTER_MAX = 0.5


class McpError(RuntimeError):
    pass


def resp_text(resp: Any) -> str:
    """取响应正文。requests 对 text/event-stream 默认按 ISO-8859-1 解码导致中文乱码，
    未声明或声明为 latin-1 时用原始字节按 UTF-8 重解码。"""
    encoding = (getattr(resp, "encoding", None) or "").lower()
    text = getattr(resp, "text", "") or ""
    if encoding in ("", "iso-8859-1", "latin-1", "latin_1", "latin1"):
        raw = getattr(resp, "content", None)
        if raw:
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return text
    return text


def parse_sse_messages(body: str) -> list[dict]:
    """解析 streamable-HTTP 响应，兼容两种上游：

    - 规范 SSE：`event:`/`data:` 行组成帧，空行分隔，多 data 行按规范以换行拼接；
    - 自建 ifind-mcp-server 的 quirk（实测）：payload 字符串内含裸换行、续行无
      `data:` 前缀 → 续行并入缓冲，`json.loads(strict=False)` 容忍字符串内控制字符。

    缓冲只在能解析出 JSON 对象时结算为消息，否则继续累积（跨空行的未结束帧）。
    非 SSE 响应（整体以 `{` 开头）按纯 JSON 解析。
    """
    stripped = body.lstrip()
    if stripped.startswith("{") and "event:" not in body and "\ndata:" not in body:
        try:
            return [json.loads(body, strict=False)]
        except ValueError as exc:
            raise McpError(f"无法解析 MCP 响应：{exc}") from exc
    messages: list[dict] = []
    chunks: list[str] = []

    def try_flush() -> None:
        if not chunks:
            return
        text = "\n".join(chunks)
        try:
            obj = json.loads(text, strict=False)
        except ValueError:
            if text.strip().startswith(("{", "[")):
                return  # 未结束帧：保留缓冲，等待后续行
            chunks.clear()  # ping 等噪声
            return
        if isinstance(obj, dict):
            messages.append(obj)
        chunks.clear()

    for line in body.splitlines():
        if line.startswith("event:") or not line.strip():
            try_flush()
        elif line.startswith("data:"):
            value = line[len("data:"):]
            chunks.append(value[1:] if value.startswith(" ") else value)
        elif chunks:
            chunks.append(line)
    try_flush()
    if not messages:
        raise McpError("SSE 响应中没有可解析的 JSON 帧")
    return messages


class IfindMcpClient:
    def __init__(
        self,
        url: str | None = None,
        key_path: str | Path | None = None,
        *,
        poster: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        client_name: str = "capital-events-intelligence",
    ) -> None:
        self.url = url or settings.ifind_mcp_url
        self.key_path = Path(key_path or settings.ifind_key_path)
        self._poster = poster
        self._sleeper = sleeper
        self._client_name = client_name
        self._session_id: str | None = None
        self._initialized = False
        self._lock = threading.RLock()
        self._last_request_at = 0.0
        self._id = 1

    def _bearer(self) -> str:
        try:
            key = self.key_path.read_text("utf-8").strip()
        except OSError as exc:
            raise McpError(f"无法读取 iFinD 密钥文件：{self.key_path.name}") from exc
        if not key:
            raise McpError("iFinD 密钥文件为空")
        return key

    def _wait_slot(self) -> None:
        now = time.monotonic()
        wait = MIN_TOOL_INTERVAL - (now - self._last_request_at)
        if wait > 0:
            self._sleeper(wait + random.uniform(0.0, JITTER_MAX))
        self._last_request_at = time.monotonic()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post(self, payload: dict, *, expect_response: bool = True) -> dict | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._bearer()}",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        with self._lock:
            self._wait_slot()
            if self._poster is not None:
                resp = self._poster(self.url, headers, payload, 60)
            else:
                resp = requests.post(self.url, headers=headers, json=payload, timeout=60)
        if resp.status_code not in (200, 202):
            raise McpError(f"MCP HTTP {resp.status_code}")
        if self._session_id is None:
            for k, v in dict(resp.headers).items():
                if k.lower() == "mcp-session-id":
                    self._session_id = v
                    break
        text = resp_text(resp)
        if not expect_response or resp.status_code == 202 or not text.strip():
            return None
        msgs = parse_sse_messages(text)
        want = payload.get("id")
        if want is not None:
            matched = [m for m in msgs if m.get("id") == want]
            msg = matched[0] if matched else (msgs[-1] if len(msgs) == 1 else msgs[0])
        else:
            msg = msgs[-1]
        if msg.get("error"):
            err = msg["error"]
            raise McpError(f"MCP error {err.get('code')}: {err.get('message')}")
        return msg.get("result", msg)

    def initialize(self) -> None:
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": self._client_name, "version": "0.1.0"},
                },
            }
        )
        if result is None:
            raise McpError("initialize 无响应")
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_response=False,
        )
        self._initialized = True

    @staticmethod
    def _first_text(result: dict) -> str:
        for item in result.get("content", []):
            if item.get("type") == "text":
                return str(item.get("text", ""))
        return ""

    def call_tool(self, name: str, arguments: dict) -> Any:
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self.initialize()
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        result = result or {}
        if result.get("isError"):
            raise McpError(f"工具返回错误：{self._first_text(result)}")
        if "content" in result:
            text = self._first_text(result)
            try:
                parsed = json.loads(text)
            except ValueError:
                return {"text": text}
            return parsed if isinstance(parsed, dict) else {"data": parsed}
        return result

    def search_notice(
        self,
        query: str,
        time_start: str,
        time_end: str | None = None,
        size: int = 50,
    ) -> Any:
        arguments: dict[str, Any] = {"query": query, "time_start": time_start, "size": size}
        if time_end:
            arguments["time_end"] = time_end
        return self.call_tool("ifind_search_notice", arguments)
