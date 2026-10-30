"""M2 流程节点状态机（PRD F3）：按事件类型维护节点序列与终止判定。

无开源实现，口径参照 v6 手工流水线：同日期多份中介文件压缩为一个关键节点。
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_EVENT_TYPE = "定增"

TERMINAL_NODES = frozenset({"发行完成", "过户完成", "终止", "撤回"})

# 事件终态 → pool 处置（PRD §3.4：终止类触发降级/离场提醒）
_TERMINAL_STATUS = {
    ("定增", "发行完成"): "已发行",
    ("重大重组", "过户完成"): "已完成",
    ("控制权变更", "过户完成"): "已完成",
    ("协议转让", "过户完成"): "已完成",
}

# 主推进里程碑序列，末尾附"终止"分支（不作为预期下一节点）
FLOW_SEQUENCES: dict[str, tuple[str, ...]] = {
    "定增": (
        "方案披露", "股东大会通过", "交易所受理", "问询回复",
        "过会", "注册批复", "发行完成", "终止",
    ),
    "重大重组": (
        "筹划停牌", "方案披露", "股东大会通过", "交易所受理", "问询回复",
        "过会", "注册批复", "过户完成", "终止",
    ),
    "控制权变更": (
        "筹划停牌", "复牌", "方案披露", "权益变动披露", "一致行动协议",
        "推进进展", "过户完成", "终止",
    ),
    "协议转让": ("方案披露", "权益变动披露", "推进进展", "过户完成", "终止"),
    "权益变动": ("权益变动披露", "过户完成", "终止"),
    "一致行动": ("一致行动协议", "推进进展", "过户完成", "终止"),
    "股东大会": ("股东大会通知", "股东大会通过", "终止"),
}


@dataclass
class NodeUpdate:
    node_type: str
    node_date: str
    announcement_id: int
    announcement_ids: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.announcement_ids.add(self.announcement_id)


def sequence_for(event_type: str) -> tuple[str, ...]:
    return FLOW_SEQUENCES.get(event_type, FLOW_SEQUENCES[DEFAULT_EVENT_TYPE])


def _index(seq: tuple[str, ...], node_type: str) -> int:
    try:
        return seq.index(node_type)
    except ValueError:
        return -1


def terminal_status_for(event_type: str, node_type: str) -> str | None:
    if node_type in ("终止", "撤回"):
        return "终止"
    return _TERMINAL_STATUS.get((event_type, node_type))


def next_expected_node(event_type: str, node_type: str, seq: tuple[str, ...] | None = None) -> str | None:
    s = seq or sequence_for(event_type)
    idx = _index(s, node_type)
    if idx < 0 or idx + 1 >= len(s):
        return None
    nxt = s[idx + 1]
    return None if nxt in ("终止", "撤回") else nxt


def is_after(event_type: str, a: str, b: str) -> bool:
    s = sequence_for(event_type)
    ia, ib = _index(s, a), _index(s, b)
    return ia >= 0 and ib >= 0 and ia > ib


def is_terminal(event_type: str, node_type: str) -> bool:
    return node_type in TERMINAL_NODES


def compress_duplicate_nodes(updates: list[NodeUpdate]) -> list[NodeUpdate]:
    """同 (node_type, node_date) 合并为一条关键节点，announcement_ids 取并集；保持首次出现顺序。"""
    merged: dict[tuple[str, str], NodeUpdate] = {}
    order: list[tuple[str, str]] = []
    for u in updates:
        key = (u.node_type, u.node_date)
        if key not in merged:
            merged[key] = NodeUpdate(u.node_type, u.node_date, u.announcement_id)
            order.append(key)
        else:
            merged[key].announcement_ids.add(u.announcement_id)
    return [merged[k] for k in order]
