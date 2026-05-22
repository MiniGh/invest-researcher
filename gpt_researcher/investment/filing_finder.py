"""
FilingFinder —— L2b 财报旁路。

定向 Tavily query 锁定 sec.gov,取首条命中 → 用 GPTResearcher 的 scraper_manager
抓全文(PDF/HTML 自动检测,scraper_manager 已经在 Slice 1 的 GPTResearcher 实例上)。

诚实局限:
- Tavily 命中"那一份最新 + 正确类型"的财报不稳(~70-80% 拿得到)
- PDF 解析也脏(PyMuPDF 偶尔抽错)
- 非美公司经常拿不到

任何失败都返回 None,上游 InvestmentResearcher 会切到 web-only 抽取路径。
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from ..retrievers.tavily.tavily_search import TavilySearch
from ..scraper.scraper import Scraper
from ..utils.workers import WorkerPool
from .schema import CompanyTarget, FilingDoc

logger = logging.getLogger(__name__)

SEC_DOMAINS = ["sec.gov", "www.sec.gov"]

# SEC EDGAR 的 fair-access 政策:User-Agent 必须带联系邮箱,否则返回
# "Undeclared Automated Tool" 拦截页(2-3 KB 的 HTML 警告)。
# TODO: 换成你自己/项目的联系邮箱 —— SEC 用它来识别大流量来源。
SEC_USER_AGENT = "InvestmentResearcher Fork (demo) noreply@example.com"


class FilingFinder:
    def __init__(self, gpt_researcher):
        """gpt_researcher 用来共享 scraper_manager(抓 PDF/HTML 自动检测)。"""
        self.gpt_researcher = gpt_researcher

    async def fetch(self, target: CompanyTarget) -> Optional[FilingDoc]:
        if not target or not target.name:
            return None

        ticker_part = target.ticker or ""
        # 加 year hint 偏向最近一年的 filing(缓解 Tavily 按 relevance 排序
        # 偶尔返回老 filing 的问题;不能根治,要真稳需直接打 EDGAR API)。
        current_year = datetime.utcnow().year
        query = (
            f"{target.name} {ticker_part} {current_year} "
            f"most recent 10-Q OR 10-K OR quarterly earnings release"
        )

        try:
            retriever = TavilySearch(query=query, query_domains=SEC_DOMAINS)
            # TavilySearch.search 是同步函数,包到 to_thread 里
            results = await asyncio.to_thread(retriever.search, 5)
        except Exception as e:
            logger.warning(f"FilingFinder: Tavily search failed for {target.name}: {e}")
            return None

        if not results:
            logger.info(f"FilingFinder: no SEC results for {target.name}")
            return None

        url = results[0].get("href")
        if not url:
            logger.info(f"FilingFinder: first result has no href: {results[0]!r}")
            return None

        # SEC 反爬虫要求带邮箱 UA,scraper_manager 默认 UA 会被拦截。
        # 自己起一个 Scraper 实例用 SEC_USER_AGENT。
        # PDF/HTML 由 Scraper 内部按 URL 扩展名自动检测(SEC 文档基本是 HTML)。
        worker_pool = WorkerPool(max_workers=1)
        scraper_instance = Scraper(
            urls=[url],
            user_agent=SEC_USER_AGENT,
            scraper="bs",
            worker_pool=worker_pool,
        )
        try:
            scraped = await scraper_instance.run()
        except Exception as e:
            logger.warning(f"FilingFinder: SEC scrape failed for {url}: {e}")
            return None

        if not scraped:
            logger.info(f"FilingFinder: scrape returned empty for {url}")
            return None

        raw_content = scraped[0].get("raw_content") if isinstance(scraped[0], dict) else None
        if not raw_content:
            logger.info(f"FilingFinder: no raw_content from {url}")
            return None

        # DEBUG (Slice 2b 调试):看 Tavily 命中的是什么文档 + scraper 抓到了啥
        logger.info(
            f"FilingFinder hit: url={url}, raw_content_len={len(raw_content)}, "
            f"head={raw_content[:300]!r}"
        )
        return FilingDoc(url=url, raw_content=raw_content)
