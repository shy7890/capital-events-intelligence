"""M2 流程节点状态机测试：按事件类型推进节点序列，同日多中介文件压缩，终止/完成收敛。"""

from __future__ import annotations

import pytest

from zhuang_monitor.events.state_machine import (
    FLOW_SEQUENCES,
    NodeUpdate,
    compress_duplicate_nodes,
    is_after,
    is_terminal,
    next_expected_node,
    sequence_for,
    terminal_status_for,
)


def test_sequence_for_each_type():
    assert sequence_for("定增").index("方案披露") < sequence_for("定增").index("股东大会通过")
    assert sequence_for("定增").index("股东大会通过") < sequence_for("定增").index("交易所受理")
    assert "终止" in sequence_for("控制权变更")
    assert sequence_for("未知类型") == sequence_for("定增")  # 兜底


def test_terminal_status():
    assert terminal_status_for("定增", "发行完成") == "已发行"
    assert terminal_status_for("重大重组", "过户完成") == "已完成"
    assert terminal_status_for("定增", "终止") == "终止"


def test_next_expected_node():
    seq = sequence_for("定增")
    assert next_expected_node("定增", "方案披露", seq) == "股东大会通过"
    assert next_expected_node("定增", "发行完成", seq) is None


def test_is_after():
    assert is_after("定增", "股东大会通过", "方案披露")
    assert not is_after("定增", "方案披露", "股东大会通过")


def test_is_terminal():
    assert is_terminal("定增", "发行完成")
    assert is_terminal("定增", "终止")
    assert not is_terminal("定增", "交易所受理")


def test_compress_same_day_multi_nodes():
    """同日多份中介文件：同节点类型只保留一条（关键节点压缩口径）。"""
    updates = [
        NodeUpdate("方案披露", "2025-12-02", 1),
        NodeUpdate("方案披露", "2025-12-02", 2),
        NodeUpdate("方案披露", "2025-12-02", 3),
        NodeUpdate("股东大会通知", "2026-04-08", 4),
    ]
    out = compress_duplicate_nodes(updates)
    assert len(out) == 2
    assert out[0].node_type == "方案披露"
    assert out[0].announcement_ids == {1, 2, 3}
    assert out[1].announcement_ids == {4}


def test_compress_empty():
    assert compress_duplicate_nodes([]) == []
