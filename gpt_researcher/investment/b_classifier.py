"""L0-B heuristic 分类器 —— sub-query → trust-critical / exploratory(无 LLM)。

唯一作用:决定该 sub-query 走 Tavily 时要不要套 FINANCE_DOMAIN_WHITELIST。
- trust-critical(财务数字 / 近期新闻 / 估值 / 业绩指引等)→ 套白名单(权威金融媒体)
- exploratory(业务模式 / 行业格局 / 技术理解 / 叙事等)→ 不套,全网搜

设计原则:纯关键词正则,无 LLM 调用。理由:每条 sub-query 一次 LLM 太贵 + 延迟,
而关键词分布很集中,heuristic 准确率够用。
"""
import re
from typing import Literal

from ..config.variables.default import DEFAULT_CONFIG

# 直接 import 默认 whitelist;v1 不支持用户运行时 override 白名单内容。
# 若以后需要按 cfg.finance_domain_whitelist override,再改为 InvestmentResearcher
# 在 init 时把这个 list 设进来。
FINANCE_WHITELIST = list(DEFAULT_CONFIG["FINANCE_DOMAIN_WHITELIST"])

Label = Literal["trust-critical", "exploratory"]

# 命中任一关键词即归 trust-critical
# 关键词分组(英文为主,因为 sub-query 实际语言以英文为主):
#   - 财务数字:revenue / earnings / EPS / margin / profit / net income /
#               market cap / valuation / P/E / P/S / P/B
#   - 季度业绩:quarterly / Q1-Q4 / fiscal year / FY\d+
#   - 业绩指引:guidance / outlook / forecast / consensus
#   - 市场份额:market share / share of
#   - 近期事件:latest news / announcement / earnings call / press release
#   - 公司事件:acquisition / merger / M&A / IPO / catalyst / spin-off
TRUST_CRITICAL_PATTERN = re.compile(
    r"\b("
    r"revenue|earnings|EPS|margin|profit|net\s+income|market\s+cap|valuation"
    r"|P/E|P/S|P/B"
    r"|quarterly|Q[1-4]|fiscal\s+year|FY\d+"
    r"|guidance|outlook|forecast|consensus"
    r"|market\s+share|share\s+of\s+"
    r"|latest\s+news|announcement|earnings\s+call|press\s+release"
    r"|acquisition|merger|M&A|IPO|catalyst|spin-off"
    r")\b",
    re.IGNORECASE,
)


def classify_subquery(text: str) -> Label:
    """Slice 3.0 L0-B 决策。

    Args:
        text: 一条 sub-query 文本(英文为主)。

    Returns:
        "trust-critical" → InvestmentTavilySearch 会套 FINANCE_WHITELIST。
        "exploratory" → 不套,全网搜。
    """
    if not text:
        return "exploratory"
    if TRUST_CRITICAL_PATTERN.search(text):
        return "trust-critical"
    return "exploratory"
