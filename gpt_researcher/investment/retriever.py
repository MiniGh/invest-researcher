"""InvestmentTavilySearch —— per-sub-query 白名单决策的 Tavily 子类。

通过 L0-B heuristic(`b_classifier.classify_subquery`)在 `__init__` 时自查 query,
决定是否套财经白名单:
- trust-critical(财务/新闻/估值)→ query_domains = FINANCE_WHITELIST
- exploratory(业务/竞争/技术理解)→ query_domains = None(全网)

注册位置:`gpt_researcher/actions/retriever.py` `get_retriever()` 加
`case "investment_tavily"` 返回本类;`InvestmentResearcher` 在 init 时通过
`cfg.retriever = "investment_tavily"` 切入。

Slice 3.0 引入。Slice 3.1+ 加入 L1 编排器后,这里逻辑不变。

Slice 3.3:`_search()` 加 retry+指数退避(D2)。depth-3 一次 query 扇出 ~30 次
Tavily 搜索,TLS 抖动/timeout 会让某次调用失败 → 父类 `search()` 吞成空结果 →
报告对应段落变薄。retry 包在网络层 `_search`(父类此处 `requests.post` 失败会抛),
让抖动在被 `search()` 吞掉前先重试。成功调用零额外开销,只有真失败才退避。
注意:retry 不碰 upstream `TavilySearch`,只在本 fork 私有子类里;"200 但空结果"
(父类 `search` 里的 `No results found`)不在这里重试 —— 那不是抖动,重试无益。
"""
import logging
import time

from gpt_researcher.retrievers.tavily.tavily_search import TavilySearch

from .b_classifier import FINANCE_WHITELIST, classify_subquery

logger = logging.getLogger(__name__)

# retry 参数:3 次尝试,退避 0.5s → 1s(指数);只对真网络失败生效。
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.5


class InvestmentTavilySearch(TavilySearch):
    """Tavily 子类,自动按 L0-B per sub-query 决定 query_domains;_search 带 retry。"""

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

    def _search(self, *args, **kwargs):
        """父类 _search 外包一层 retry+指数退避(D2,治 Tavily TLS 抖动)。

        本方法是 sync(父类用 requests),`time.sleep` 退避安全。末次仍失败则
        re-raise,父类 `search()` 据此 returns [](与无 retry 时同样的兜底,不崩)。
        """
        last_exc = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return super()._search(*args, **kwargs)
            except Exception as e:  # TLS 抖动 / timeout / 5xx 等
                last_exc = e
                if attempt < _MAX_ATTEMPTS - 1:
                    backoff = _BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        f"Tavily _search failed (attempt {attempt + 1}/"
                        f"{_MAX_ATTEMPTS}), retrying in {backoff:.1f}s: {e}"
                    )
                    time.sleep(backoff)
        raise last_exc
