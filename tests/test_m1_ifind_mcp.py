"""M1 iFinD MCP 客户端测试：MCP-over-HTTP 握手、tools/call 解析、限流、密钥不落日志。全部离线。"""

from __future__ import annotations

import json
import time

import pytest

from zhuang_monitor.sources.ifind_mcp import (
    MIN_TOOL_INTERVAL,
    McpError,
    IfindMcpClient,
    parse_sse_messages,
    resp_text,
)

FAKE_KEY = "sk-TESTKEY-not-a-real-secret"


class FakeResp:
    def __init__(self, status_code=200, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


def _tool_result(payload: dict):
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
            },
        },
        ensure_ascii=False,
    )


@pytest.fixture
def key_file(tmp_path):
    p = tmp_path / "mcp.key"
    p.write_text(FAKE_KEY + "\n")
    return p


@pytest.fixture
def poster():
    """可编程 fake HTTP：按序消费响应，记录每次请求。"""

    class Poster:
        def __init__(self):
            self.calls = []
            self.responses = []

        def add(self, resp):
            self.responses.append(resp)

        def __call__(self, url, headers, payload, timeout):
            self.calls.append({"url": url, "headers": dict(headers), "payload": payload})
            if not self.responses:
                raise AssertionError("没有预置响应却发起了请求")
            return self.responses.pop(0)

    return Poster()


def _client(key_file, poster, sleeps):
    return IfindMcpClient(
        url="http://mcp.test/mcp",
        key_path=str(key_file),
        poster=poster,
        sleeper=sleeps.append,
    )


def _handshake_ok(poster, session_id="sess-42"):
    poster.add(
        FakeResp(
            200,
            {"mcp-session-id": session_id},
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26","capabilities":{},"serverInfo":{"name":"ifind"}}}\n\n',
        )
    )
    poster.add(FakeResp(202, {}, ""))  # notifications/initialized


def test_parse_sse_joins_multiline_data_and_keeps_frames():
    body = (
        'event: message\ndata: {"jsonrpc":"2.0","id":1,\ndata: "data": {"x": 1}}\n\n'
        'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n\n'
    )
    msgs = parse_sse_messages(body)
    assert msgs == [{"jsonrpc": "2.0", "id": 1, "data": {"x": 1}},
                    {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}]


def test_parse_plain_json():
    assert parse_sse_messages('{"ok": true}') == [{"ok": True}]


def test_parse_sse_embedded_raw_newlines_in_string():
    # 真实上游 quirk（2026-09-02 实测）：payload 字符串里带裸换行、续行无 data: 前缀
    body = 'event: message\ndata: {"a": "中文第一行\n中文第二行"}\n\n'
    msgs = parse_sse_messages(body)
    assert msgs == [{"a": "中文第一行\n中文第二行"}]


def test_parse_garbage_raises():
    with pytest.raises(McpError):
        parse_sse_messages("<html>502 Bad Gateway</html>")


def test_resp_text_redecodes_default_latin1_as_utf8():
    class Resp:
        content = '{"a": "高乐股份"}'.encode("utf-8")
        encoding = "ISO-8859-1"
        text = content.decode("ISO-8859-1")  # requests 对 text/event-stream 的默认劣化解码

    assert resp_text(Resp()) == '{"a": "高乐股份"}'


def test_resp_text_trusts_declared_utf8():
    class Resp:
        content = b"irrelevant"
        encoding = "utf-8"
        text = "已正确解码"

    assert resp_text(Resp()) == "已正确解码"


def test_resp_text_without_content_falls_back():
    class Resp:
        encoding = ""
        text = "仅有 text"

    assert resp_text(Resp()) == "仅有 text"


def test_initialize_then_call_tool_flow(key_file, poster):
    sleeps = []
    _handshake_ok(poster)
    poster.add(FakeResp(200, {}, _tool_result({"行情": "002348"})))
    client = _client(key_file, poster, sleeps)
    out = client.call_tool("get_x", {"代码": "002348"})
    assert out == {"行情": "002348"}
    methods = [c["payload"].get("method") for c in poster.calls]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    sess_headers = [c["headers"].get("mcp-session-id") for c in poster.calls]
    assert sess_headers == [None, "sess-42", "sess-42"]
    assert poster.calls[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert poster.calls[2]["payload"]["params"]["name"] == "get_x"


def test_handshake_only_once(key_file, poster):
    sleeps = []
    _handshake_ok(poster)
    poster.add(FakeResp(200, {}, _tool_result({"x": 1})))
    poster.add(FakeResp(200, {}, _tool_result({"x": 2})))
    client = _client(key_file, poster, sleeps)
    assert client.call_tool("t", {}) == {"x": 1}
    assert client.call_tool("t", {}) == {"x": 2}
    assert len(poster.calls) == 4


def test_response_frame_selected_by_request_id(key_file, poster):
    _handshake_ok(poster)
    frame_match = '{"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\\"m\\":3}"}]}}'
    frame_noise = '{"jsonrpc":"2.0","id":7,"result":{"content":[{"type":"text","text":"{\\"m\\":7}"}]}}'
    poster.add(FakeResp(200, {}, f"event: message\ndata: {frame_match}\n\nevent: message\ndata: {frame_noise}\n\n"))
    client = _client(key_file, poster, [])
    assert client.call_tool("t", {}) == {"m": 3}


def test_tools_call_between_throttled(key_file, poster):
    sleeps = []
    _handshake_ok(poster)
    poster.add(FakeResp(200, {}, _tool_result({"x": 1})))
    poster.add(FakeResp(200, {}, _tool_result({"x": 2})))
    client = _client(key_file, poster, sleeps)
    client.call_tool("t", {})
    client.call_tool("t", {})
    tool_sleeps = sleeps[1:]  # 首次 tools/call 前有 initialize 间隔
    assert tool_sleeps and tool_sleeps[0] >= MIN_TOOL_INTERVAL


def test_non_json_text_returned_raw(key_file, poster):
    _handshake_ok(poster)
    poster.add(
        FakeResp(
            200,
            {},
            json.dumps({"jsonrpc": "2.0", "id": 9, "result": {"content": [{"type": "text", "text": "纯文本说明"}]}}),
        )
    )
    client = _client(key_file, poster, [])
    assert client.call_tool("t", {}) == {"text": "纯文本说明"}


def test_jsonrpc_error_raises(key_file, poster):
    _handshake_ok(poster)
    poster.add(
        FakeResp(
            200,
            {},
            json.dumps({"jsonrpc": "2.0", "id": 9, "error": {"code": -32603, "message": "上游403"}}),
        )
    )
    client = _client(key_file, poster, [])
    with pytest.raises(McpError, match="上游403"):
        client.call_tool("t", {})


def test_content_is_error_raises(key_file, poster):
    _handshake_ok(poster)
    poster.add(
        FakeResp(
            200,
            {},
            json.dumps({"jsonrpc": "2.0", "id": 9, "result": {"isError": True, "content": [{"type": "text", "text": "配额耗尽"}]}}),
        )
    )
    client = _client(key_file, poster, [])
    with pytest.raises(McpError, match="配额耗尽"):
        client.call_tool("t", {})


def test_http_401_raises_without_leaking_key(key_file, poster):
    poster.add(FakeResp(401, {}, "Unauthorized"))
    client = _client(key_file, poster, [])
    with pytest.raises(McpError) as exc:
        client.call_tool("t", {})
    assert FAKE_KEY not in str(exc.value)
    assert "401" in str(exc.value)


def test_missing_key_file_raises(key_file):
    client = IfindMcpClient(url="http://mcp.test/mcp", key_path=str(key_file.parent / "nope.key"), poster=None)
    with pytest.raises(McpError, match="密钥"):
        client._bearer()


def test_search_notice_arguments(key_file, poster):
    _handshake_ok(poster)
    poster.add(FakeResp(200, {}, _tool_result({"公告": []})))
    client = _client(key_file, poster, [])
    out = client.search_notice("定增", "2024-01-01", time_end="2024-01-31", size=20)
    assert out == {"公告": []}
    call = poster.calls[-1]["payload"]["params"]
    assert call["name"] == "ifind_search_notice"
    assert call["arguments"] == {"query": "定增", "time_start": "2024-01-01", "time_end": "2024-01-31", "size": 20}
