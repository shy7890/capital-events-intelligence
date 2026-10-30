"""M2 事件规则表（PRD §3.4 分级 + F3 节点口径）。

规则按序匹配、先命中先得：终止类 → 控制权 → 定增 → 重组 → 股东大会/权益变动 → B 级。
behavior：open=可开新事件；attach=只挂到进行中事件；close=收尾并关闭事件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

OPEN = "open"
ATTACH = "attach"
CLOSE = "close"

CAPITAL_TYPES = ("定增", "重大重组", "控制权变更", "协议转让", "现金购买", "吸收合并")
WEAK_SIGNAL_NODE_TYPES = ("高管变动", "设立子公司", "经营范围变更", "激励计划", "质押变动")


@dataclass(frozen=True)
class RuleHit:
    event_type: str
    grade: str
    node_type: str
    behavior: str
    targets: tuple[str, ...] = field(default=())

    @property
    def closes_event(self) -> bool:
        return self.behavior == CLOSE


_RULES: list[tuple[str, RuleHit]] = [
    # ---- 终止类（最先，防止被"重组/发行"前缀抢走） ----
    (r"终止.*(前次)?(非公开发行|向特定对象发行)", RuleHit("定增", "T", "终止", CLOSE)),
    (r"(终止|暂缓|取消).*(重大资产重组|资产重组)", RuleHit("重大重组", "T", "终止", CLOSE)),
    (r"控制权变更.*(失败|终止|未完成)|(终止|未完成).*控制权", RuleHit("控制权变更", "T", "终止", CLOSE)),
    (r"(撤回|终止).*发行(股份|申请)", RuleHit("定增", "T", "终止", CLOSE)),
    # ---- 控制权变更（S） ----
    (r"过户完成|协议转让过户|控制权发生变更",
     RuleHit("控制权变更", "S", "过户完成", CLOSE)),
    (r"控制权.*(停牌)|(停牌).*控制权", RuleHit("控制权变更", "S", "筹划停牌", OPEN)),
    (r"(控制权变更|控制权).*复牌|复牌.*(控制权|筹划)", RuleHit("控制权变更", "S", "复牌", ATTACH)),
    (r"(实际控制人|控股股东|控制权).*(拟发生变更|发生变更的提示|签署.*股份转让协议|《股份转让协议》)",
     RuleHit("控制权变更", "S", "方案披露", OPEN)),
    (r"控制权变更.*进展|筹划控制权变更", RuleHit("控制权变更", "S", "推进进展", ATTACH)),
    # ---- 定增（S）：先节点后开事件 ----
    (r"(向特定对象发行|非公开发行).*(申请.*受理|受理)", RuleHit("定增", "A", "交易所受理", ATTACH)),
    (r"(向特定对象发行|非公开发行|发行.*股票).*(审核通过|上市委.*通过|过会)", RuleHit("定增", "A", "过会", ATTACH)),
    (r"(向特定对象发行|非公开发行).*(问询|落实函|审核中心意见)", RuleHit("定增", "A", "问询回复", ATTACH)),
    (r"(向特定对象发行|非公开发行).*(同意注册|注册.*(批准|同意)|证监会.*注册)",
     RuleHit("定增", "A", "注册批复", ATTACH)),
    (r"(向特定对象发行|非公开发行).*(发行情况报告书|缴款|发行完成|募集资金到账)",
     RuleHit("定增", "S", "发行完成", CLOSE)),
    (r"(向特定对象发行|非公开发行|发行.*股票).*(预案|发行方案)", RuleHit("定增", "S", "方案披露", OPEN)),
    # ---- 重大重组 / 吸收合并（S） ----
    (r"(重大资产重组|购买资产|吸收合并).*(停牌)", RuleHit("重大重组", "S", "筹划停牌", OPEN)),
    (r"重大资产重组.*(报告书|预案|草案)|发行股份.*购买资产|吸收合并",
     RuleHit("重大重组", "S", "方案披露", OPEN)),
    (r"(重大资产重组|购买资产).*(股东大会.*通过|受理|审核通过|过会|核准|注册)",
     RuleHit("重大重组", "A", "推进进展", ATTACH)),
    # ---- 股东大会（挂到进行中的资本运作事件） ----
    (r"(股东大会|股东会).*(决议公告|决议)", RuleHit("股东大会", "A", "股东大会通过", ATTACH, CAPITAL_TYPES)),
    (r"关于召开.*(股东大会|股东会)|(股东大会|股东会).*召开.*通知",
     RuleHit("股东大会", "A", "股东大会通知", ATTACH, CAPITAL_TYPES)),
    # ---- 权益变动报告书 / 一致行动：优先并入控制权事件 ----
    (r"权益变动报告书", RuleHit("权益变动", "A", "权益变动披露", ATTACH, ("控制权变更", "协议转让", "定增", "重大重组"))),
    (r"一致行动(协议|人)", RuleHit("一致行动", "A", "一致行动协议", ATTACH, ("控制权变更", "协议转让"))),
    # ---- B 级（只挂时间线，不开事件） ----
    (r"(董事|监事|高管|总经理|副总经理|财务总监|董事会秘书|董秘|独立董事|董事长).*(辞职|聘任|辞任|离任|补选|选举|变动)",
     RuleHit("高管变动", "B", "高管变动", ATTACH, CAPITAL_TYPES)),
    (r"(?:设立|成立|新设|投资设立|增资设立).*(子公司|分公司)",
     RuleHit("设子公司", "B", "设立子公司", ATTACH, CAPITAL_TYPES)),
    (r"经营范围变更|变更.*经营范围", RuleHit("经营范围变更", "B", "经营范围变更", ATTACH, CAPITAL_TYPES)),
    (r"(?:股权激励.*(?:计划|草案)|限制性股票.*(?:激励|计划)|员工持股计划|股票期权.*激励)",
     RuleHit("股权激励", "B", "激励计划", ATTACH, CAPITAL_TYPES)),
    (r"(解除质押|质押.*(股份|股权))", RuleHit("质押", "B", "质押变动", ATTACH, CAPITAL_TYPES)),
    (r"减持.*(计划|进展|公告)", RuleHit("减持", "B", "减持动向", ATTACH, CAPITAL_TYPES)),
    (r"(以集中竞价|回购).*(回购|股份)", RuleHit("回购", "B", "回购动作", ATTACH, CAPITAL_TYPES)),
    (r"问询函", RuleHit("问询函", "B", "财务问询", ATTACH, CAPITAL_TYPES)),
]

_COMPILED = [(re.compile(p), hit) for p, hit in _RULES]
_PREFIX_RE = re.compile(r"^[^:：]{1,12}[:：](.+)$")
_WEAK_EXCLUSIONS = {
    "设立子公司": re.compile(r"担保|贷款|分红|利润分配|租赁|注销|清算|转让"),
    "激励计划": re.compile(r"自查|回购注销|注销.*(?:未解锁|限制性股票)|未解锁.*注销"),
}


def strip_company_prefix(title: str) -> str:
    """去掉东财标题里的公司前缀（如 "示例公司:" / "示例公司："），≤12 字才视为前缀。"""
    m = _PREFIX_RE.match(title or "")
    return m.group(1).strip() if m else (title or "").strip()


def match_title(title: str) -> RuleHit | None:
    """公告标题 → 规则命中（先序先得）。"""
    cleaned = strip_company_prefix(title)
    for pattern, hit in _COMPILED:
        exclusion = _WEAK_EXCLUSIONS.get(hit.node_type)
        if exclusion is not None and exclusion.search(cleaned):
            continue
        if pattern.search(cleaned):
            return hit
    return None
