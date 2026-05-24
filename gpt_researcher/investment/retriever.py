"""InvestmentTavilySearch —— per-sub-query 白名单决策的 Tavily 子类。

通过 L0-B heuristic(`b_classifier.classify_subquery`)在 `__init__` 时自查 query,
决定是否套财经白名单:
- trust-critical(财务/新闻/估值)→ query_domains = FINANCE_WHITELIST
- exploratory(业务/竞争/技术理解)→ query_domains = None(全网)

注册位置:`gpt_researcher/actions/retriever.py` `get_retriever()` 加
`case "investment_tavily"` 返回本类;`InvestmentResearcher` 在 init 时通过
`cfg.retriever = "investment_tavily"` 切入。

Slice 3.0 引入。Slice 3.1+ 加入 L1 编排器后,这里逻辑不变。
"""
from gpt_researcher.retrievers.tavily.tavily_search import TavilySearch

from .b_classifier import FINANCE_WHITELIST, classify_subquery


class InvestmentTavilySearch(TavilySearch):
    """Tavily 子类,自动按 L0-B per sub-query 决定 query_domains。"""

    def __init__(self, query, headers=None, topic="general", query_domains=None):
        # 优先级:
        # 1. 上层显式传 query_domains(非空)→ 完全尊重(escape hatch)
        # 2. 否则按 L0-B heuristic 决定
        if not query_domains:
            label = classify_subquery(query)
            if label == "trust-critical":
                query_domains = FINANCE_WHITELIST
            # exploratory: query_domains 保持 None,父类构造时即全网
        super().__init__(
            query=query,
            headers=headers,
            topic=topic,
            query_domains=query_domains,
        )
