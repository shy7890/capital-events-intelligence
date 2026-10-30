"""M2 事件规则表测试：用真实公告标题（002348 样本）驱动规则命中。"""

from __future__ import annotations

import pytest

from zhuang_monitor.events.rules import match_title, strip_company_prefix


def test_strip_company_prefix():
    assert strip_company_prefix("高乐股份:2025年度向特定对象发行A股股票预案") == "2025年度向特定对象发行A股股票预案"
    assert strip_company_prefix("无冒号标题") == "无冒号标题"
    assert strip_company_prefix("高乐股份：带全角冒号的标题") == "带全角冒号的标题"
    # 正文里出现冒号不误删（前缀长度限制）
    assert strip_company_prefix("关于召开2026年第一次临时股东会的通知") == "关于召开2026年第一次临时股东会的通知"


def match(title: str):
    return match_title(strip_company_prefix(title))


# ---- S 级：控制权变更 ----

def test_control_change_suspend():
    hit = match("关于公司第一大股东筹划控制权变更的停牌公告")
    assert hit is not None
    assert hit.event_type == "控制权变更" and hit.grade == "S" and hit.node_type == "筹划停牌"


def test_control_change_resume():
    hit = match("关于筹划公司控制权变更事项进展暨股票复牌公告")
    assert hit.node_type == "复牌"


def test_control_change_plan_with_agreement():
    hit = match("关于控股股东签署《股份转让协议》及公司签署《附条件生效的股份认购协议》暨公司控股股东、实际控制人拟发生变更的提示性公告")
    assert hit.event_type == "控制权变更" and hit.node_type == "方案披露"


def test_control_change_progress_and_transfer_done():
    assert match("关于筹划控制权变更的进展公告").node_type == "推进进展"
    hit = match("关于股东协议转让过户完成暨公司控制权发生变更的提示性公告")
    assert hit.event_type == "控制权变更" and hit.node_type == "过户完成" and hit.closes_event


def test_control_change_equity_report():
    hit = match("广东高乐股份有限公司详式权益变动报告书")
    assert hit.event_type == "权益变动" and hit.grade == "A"


# ---- S 级：定增 ----

def test_private_placement_plan():
    hit = match("2025年度向特定对象发行A股股票预案")
    assert hit.event_type == "定增" and hit.grade == "S" and hit.node_type == "方案披露"


def test_private_placement_shareholder_meeting():
    hit = match("2026年第一次临时股东会决议公告")
    assert hit.event_type == "股东大会" and hit.node_type == "股东大会通过"
    hit2 = match("关于召开2026年第一次临时股东会的通知")
    assert hit2.node_type == "股东大会通知"


def test_private_placement_acceptance_and_pass():
    assert match("关于向特定对象发行股票申请获得深交所受理的公告").node_type == "交易所受理"
    assert match("2026年度向特定对象发行股票审核通过的公告").node_type == "过会"


# ---- S 级：重组 ----

def test_restructuring():
    hit = match("关于筹划重大资产重组的停牌公告")
    assert hit.event_type == "重大重组" and hit.node_type == "筹划停牌"
    assert match("发行股份及支付现金购买资产并募集配套资金暨关联交易报告书（草案）").event_type == "重大重组"


# ---- 终止类 ----

def test_termination_closes():
    hit = match("关于终止前次非公开发行股票事项的公告")
    assert hit.grade == "T" and hit.event_type == "定增" and hit.closes_event
    hit2 = match("关于终止筹划重大资产重组事项的公告")
    assert hit2.grade == "T" and hit2.event_type == "重大重组"


def test_control_change_termination():
    hit = match("关于公司控股股东协议转让终止暨控制权变更未完成的公告")
    assert hit.grade == "T" and hit.event_type == "控制权变更"


# ---- B 级 ----

@pytest.mark.parametrize("title", [
    "关于董事辞职的公告",
    "关于设立全资子公司的公告",
    "关于变更公司经营范围的公告",
    "2026年限制性股票股权激励计划（草案）",
    "关于控股股东股份解除质押的公告",
    "关于公司持股5%以上股东质押部分股份的公告",
    "关于持股5%以上股东减持股份计划的公告",
    "关于以集中竞价交易方式回购公司股份的方案",
])
def test_b_grade(title):
    hit = match(title)
    assert hit is not None and hit.grade == "B"


# ---- 无关公告不命中 ----

@pytest.mark.parametrize("title", [
    "2025年半年度财务报告",
    "关于收到深圳证券交易所年报问询函的公告",  # 财务类问询属 B，单独测试
    "投资者关系活动记录表",
    "关于股票交易异常波动的公告",
    "公司章程（2024年1月）",
])
def test_non_events_do_not_match_or_are_b(title):
    hit = match(title)
    assert hit is None or hit.grade == "B"


def test_financial_inquiry_is_b():
    hit = match("关于收到深圳证券交易所年报问询函的公告")
    assert hit.grade == "B"


@pytest.mark.parametrize("title", [
    "关于为全资子公司提供担保的公告",
    "关于全资子公司向银行贷款的公告",
    "关于全资子公司利润分配的公告",
    "关于全资子公司房屋租赁的公告",
    "关于注销全资子公司的公告",
    "关于清算全资子公司的公告",
    "关于转让全资子公司股权的公告",
])
def test_weak_subsidiary_false_positives_are_ignored(title):
    hit = match(title)
    assert hit is None or hit.node_type != "设立子公司"


@pytest.mark.parametrize("title", [
    "关于投资设立全资子公司的公告",
    "关于成立分公司的公告",
    "关于增资设立子公司的公告",
])
def test_weak_subsidiary_creation_is_positive(title):
    hit = match(title)
    assert hit is not None and hit.node_type == "设立子公司"


@pytest.mark.parametrize("title", [
    "2026年限制性股票激励计划（草案）",
    "关于审议员工持股计划的公告",
    "关于推出股票期权激励计划的公告",
])
def test_formal_incentive_plan_is_positive(title):
    hit = match(title)
    assert hit is not None and hit.node_type == "激励计划"


@pytest.mark.parametrize("title", [
    "关于股权激励计划自查表的公告",
    "关于回购注销未解锁限制性股票的公告",
    "关于注销部分激励对象已获授但尚未解锁股票的公告",
])
def test_incentive_self_check_and_cancellation_are_ignored(title):
    hit = match(title)
    assert hit is None or hit.node_type != "激励计划"


def test_uncertain_weak_title_is_not_positive():
    assert match("关于全资子公司相关事项的说明") is None
