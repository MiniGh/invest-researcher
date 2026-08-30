"""写作模板的结构性测试(Slice E)。

模板由共享片段拼接而成,拼接容易出两类错:某套模板漏掉一个片段,或者原有
的约束在重构中被丢掉。这里对五套模板逐条断言,让这两类错在提交前就暴露。

新增的四条跨模板规则来自「五类报告 vs 券商研报」的信息完整性对照:
  A 第三方评级与目标价的转述纪律(此前三份报告重复越界)
  B 开篇摘要 + 结论式章节标题(五份报告全部缺失)
  E 非美股主要厂商写入正文并标注不可投资(否则行业图景失真)
  F 社交媒体不得作为数字来源 + 独立来源数披露

断言前统一折叠空白:模板正文按 80 列硬换行,句子会被断开,直接用原文子串
匹配会因为换行位置变化而误报。
"""
import re

import pytest

from gpt_researcher.investment import writing_prompts as wp

ALL_LABELS = {
    "company_profile": wp.WRITING_PROMPT_COMPANY_PROFILE,
    "company_comparison": wp.WRITING_PROMPT_COMPANY_COMPARISON,
    "sector_landscape": wp.WRITING_PROMPT_SECTOR_LANDSCAPE,
    "value_chain": wp.WRITING_PROMPT_VALUE_CHAIN,
    "theme_analysis": wp.WRITING_PROMPT_THEME_ANALYSIS,
}

# E 只适用于会触及"行业里还有谁"的四套模板。company_comparison 的对比集合
# 由用户 query 指定,不在本轮范围内(属于"模板刚性"这个尚未决定的议题)。
NON_US_LABELS = {"company_profile", "sector_landscape", "value_chain", "theme_analysis"}

LABEL_IDS = list(ALL_LABELS)


def _norm(text: str) -> str:
    """折叠所有空白,消除硬换行对子串匹配的影响。"""
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(params=LABEL_IDS)
def label_and_prompt(request):
    return request.param, _norm(ALL_LABELS[request.param])


def test_summary_section_is_required(label_and_prompt):
    """B:每套模板都必须要求开篇摘要,且摘要里要有一条反面判断。"""
    _, prompt = label_and_prompt
    assert 'Before section 1, add a section titled "Summary"' in prompt
    assert "cuts against the positive case" in prompt
    # T1 实测把摘要写成了 **Summary** 而非 ## Summary —— 模板此前没规定层级
    assert "as an H2 heading (`## Summary`)" in prompt
    assert "Bold text is not a heading" in prompt


def test_headings_must_state_a_finding_with_fallback(label_and_prompt):
    """B:结论式标题,且证据不足时要能退回描述性标题。"""
    _, prompt = label_and_prompt
    assert "state a finding rather than name a topic" in prompt
    assert "use a plain descriptive heading" in prompt


def test_social_media_is_banned_as_a_source_of_figures(label_and_prompt):
    """F:社交媒体不得作为数字来源。"""
    _, prompt = label_and_prompt
    assert "Do not use social media posts" in prompt
    for domain in ("x.com", "instagram.com", "youtube.com", "reddit.com"):
        assert domain in prompt


def test_independent_source_count_must_be_disclosed(label_and_prompt):
    """F:报告开头披露独立来源数,并在单一来源过半时点名。"""
    _, prompt = label_and_prompt
    assert "Sources: N independent domains." in prompt
    assert "more than half of your cited figures" in prompt


def test_third_party_targets_allowed_with_hygiene_rules(label_and_prompt):
    """A:转述允许,但必须标机构与日期、与现价矛盾要点明、不得作为自身结论前提。"""
    _, prompt = label_and_prompt
    assert "may be reported as facts about market expectations" in prompt
    # 实测 4 次触发全部漏掉 as-of 日期 —— 原先规则写在嵌套的 (a)(b)(c) 里,
    # 模型只执行了最前面部分。改为一个必须照抄的固定格式。
    assert "<Institution> (<as-of date>): <rating or verdict>, target <value>" in prompt
    assert "date not stated in source" in prompt
    assert "never drop the slot" in prompt
    assert "market price already exceeds the quoted target" in prompt
    assert "Never use a third-party rating or target as a premise" in prompt


def test_model_may_not_produce_its_own_rating(label_and_prompt):
    """A 的另一半:自己产出目标价或评级仍然禁止。"""
    _, prompt = label_and_prompt
    assert "Never produce a price target, rating, or buy/sell call of your own." in prompt


def test_non_us_players_rule_applies_only_where_intended(label_and_prompt):
    """E:四套涉及行业格局的模板要求写入非美股厂商;company_comparison 不适用。"""
    label, prompt = label_and_prompt
    has_rule = "not US-listed — outside the investable scope of this report" in prompt
    assert has_rule is (label in NON_US_LABELS)
    if has_rule:
        assert "snapshot cards remain US-listed only" in prompt


def test_pre_existing_citation_discipline_survived_refactor(label_and_prompt):
    """重构不得丢掉原有的引用纪律。"""
    _, prompt = label_and_prompt
    assert "Use figures ONLY when they appear in the provided context. Never fabricate." in prompt
    assert "For every cited number, include the source URL inline (markdown link)." in prompt
    assert "Hard constraints:" in prompt
    assert "Total length target:" in prompt


def test_vague_price_predictions_wording_is_gone(label_and_prompt):
    """改动前的 "Avoid hype, PR language, and price predictions." 有漏洞:模型把它
    读成"不要自己预测",于是转述他人目标价不算违规。该措辞必须已被替换。"""
    _, prompt = label_and_prompt
    assert "Avoid hype, PR language, and price predictions." not in prompt
    assert "Avoid hype and PR language." in prompt


def test_template_specific_rules_are_preserved():
    """各模板自己的规则不能在共享化过程中丢失。"""
    assert "Never extrapolate to numbers not explicitly stated." in _norm(wp.WRITING_PROMPT_COMPANY_PROFILE)
    assert '"N/A" is mandatory for missing cells' in _norm(wp.WRITING_PROMPT_COMPANY_COMPARISON)
    assert "Keep section numbering exactly as 1, 2, 3, 4, 5, 6 above." in _norm(wp.WRITING_PROMPT_SECTOR_LANDSCAPE)
    assert "Cover EVERY segment from the Layer 1 list" in _norm(wp.WRITING_PROMPT_VALUE_CHAIN)
    assert "Cover EVERY category from the Layer 2 list" in _norm(wp.WRITING_PROMPT_THEME_ANALYSIS)


def test_literal_braces_survive_concatenation():
    """模板正文含字面花括号,拼接不得把它们当成格式占位符处理。"""
    assert "{growth | profitability | valuation | scale | optionality}" in wp.WRITING_PROMPT_COMPANY_COMPARISON
    assert "### {{segment}} — representative companies" in wp.WRITING_PROMPT_VALUE_CHAIN
    assert "### {{category}} — most leveraged stocks" in wp.WRITING_PROMPT_THEME_ANALYSIS


def test_every_template_still_resolves_by_label():
    """五个标签各自都能取到一份非空模板。"""
    for label, prompt in ALL_LABELS.items():
        assert prompt.strip(), f"{label} 模板为空"
        assert prompt.count("Hard constraints:") == 1, f"{label} 的约束段落重复或缺失"
