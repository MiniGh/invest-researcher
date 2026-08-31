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

Slice E5:`search()` 改为请求网页正文(`include_raw_content=True`)。

原因是一个功能性缺陷:上游 `search()` 只取 `obj["content"]`(400-1300 字符的
摘要),而下游 `skills/researcher.py` 的判断是

    raw_content = result.get("raw_content") or result.get("body")
    if url and raw_content and len(raw_content) > 100:
        prefetched_content.append(...)   # 当成"正文已就绪",跳过抓取

100 字符这个门槛意味着**每一条 Tavily 结果都跳过抓取** —— 实测 5/5 全部命中,
整个 `scraper/` 包在本流水线里从未执行过(所有运行日志里的 "Scraped 0 pages"
不是抓取失败,是压根没去抓)。后果是卷宗单薄(七份快照平均每篇 1090 字符),
直接抬高了幻觉率评估里的"无据率"(11.4%)—— 那多半不是模型在编,是它没看到
足够的资料。

这里不去改上游那个判断,而是让它的前提成立:那个分支本是为 PubMed Central
这类真正返回全文的检索器写的,开启 `include_raw_content` 后 Tavily 也真正返回
全文,分支行为就正确了。实测同一批查询:摘要合计 5,269 字符 → 正文合计
66,700 字符,**资料量 12.6 倍**。

附带好处:内容超过 8KB 会走 `ContextCompressor` 的标准路径,而那条路径本来
就正确保留来源 URL —— 与 commit 5b094a2c 的修复相互加强。
"""
import logging
import time

from gpt_researcher.retrievers.tavily.tavily_search import TavilySearch

from .b_classifier import FINANCE_WHITELIST, classify_subquery

logger = logging.getLogger(__name__)

# retry 参数:3 次尝试,退避 0.5s → 1s(指数);只对真网络失败生效。
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.5

# 单篇网页正文的截断长度。实测单页 6,685-21,082 字符;一次 depth-3 研究扇出
# 约 30 条 sub-query × 每条 5 篇,不截断下游 embedding 压缩开销会失控。
MAX_RAW_CONTENT_CHARS = 12000


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

    def search(self, max_results=10):
        """请求并返回网页正文,而不只是摘要。

        与父类的差异只有两处:请求时带 `include_raw_content=True`;返回的字典里
        多一个 `raw_content` 字段。`body` 仍是摘要,保持与上游其他消费方兼容。

        正文按 MAX_RAW_CONTENT_CHARS 截断:单页可达两万字符,一次 depth-3 研究
        扇出约 30 条 sub-query × 每条 5 篇,不截断会让下游 embedding 压缩的开销
        失控。截断后仍是摘要的 6-8 倍。
        """
        try:
            results = self._search(
                self.query,
                search_depth="basic",
                max_results=max_results,
                topic=self.topic,
                include_domains=self.query_domains,
                include_raw_content=True,
            )
            sources = results.get("results", [])
            if not sources:
                raise Exception("No results found with Tavily API search.")

            out, with_raw = [], 0
            for obj in sources:
                snippet = obj.get("content") or ""
                raw = (obj.get("raw_content") or "").strip()
                item = {"href": obj["url"], "body": snippet}
                # 只在正文确实比摘要长时才带上 —— 否则下游会拿一段更短的文本
                # 当成"全文",反而不如摘要。
                if len(raw) > len(snippet):
                    item["raw_content"] = raw[:MAX_RAW_CONTENT_CHARS]
                    with_raw += 1
                out.append(item)

            if with_raw < len(sources):
                logger.info(
                    f"Tavily 返回 {len(sources)} 条,其中 {with_raw} 条带网页正文;"
                    f"其余仅有摘要"
                )
            return out
        except Exception as e:
            print(f"Error: {e}. Failed fetching sources. Resulting in empty response.")
            return []

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


def set_retriever(gpt_researcher, name: str) -> None:
    """真正把 GPTResearcher 实例的检索器换成 `name`。

    只写 `cfg.retriever` 是无效的,两层原因叠加:

    1. `GPTResearcher.__init__`(agent.py:179)已经把 `get_retrievers()` 的结果
       冻结在实例属性 `self.retrievers` 上。构造之后再改 cfg 不会触发重新解析,
       而 InvestmentResearcher 恰好是先构造、后改 cfg。
    2. 即便改得足够早也没用:`get_retrievers()` 里 `cfg.retrievers`(复数,由
       `Config.__init__` 从 RETRIEVER 解析而来,默认 `["tavily"]`)的判断在
       `cfg.retriever`(单数)之前,复数分支先命中就返回了。

    后果是整个 InvestmentTavilySearch —— 包括 L0-B per-sub-query 白名单决策和
    网页正文抓取 —— 从未执行过,运行日志里一直是 `Active retrievers:
    ['TavilySearch']`。

    这里直接解析类并覆盖 `retrievers`,不经 `get_retrievers()`,以绕开
    headers 的优先级;同时把 cfg 的两个字段同步成一致值,让任何读 cfg 的地方
    (例如证据快照的 run_config)看到的都是真相。
    """
    from gpt_researcher.actions.retriever import get_retriever

    retriever_class = get_retriever(name)
    if retriever_class is None:
        raise ValueError(f"unknown retriever: {name!r}")

    cfg = gpt_researcher.cfg
    cfg.retriever = name
    cfg.retrievers = [name]
    gpt_researcher.retrievers = [retriever_class]
    logger.info(f"retriever switched to {retriever_class.__name__} ({name})")
