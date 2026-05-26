"""
InvestmentResearcher —— 美股投研助手的顶层入口。

Slice 1: "其他 兜底"(投研人设 + 财经域名白名单)—— 历史形态
Slice 2b: 加 L0 简化版(单公司检测)+ L2b 财报旁路 + L3 结构化抽取 + trust label —— 历史形态
Slice 3.0: 重构为 L0-A 二分类路由器(company_profile / 其他)+ L0-B per-sub-query 白名单
Slice 3.1: 把 routing 逻辑搬进正式的 L1 Orchestrator + Strategy 类(几乎纯重构)
  - L0-A 分类:company_profile / 其他
  - L1 Orchestrator dispatch:按 label 调对应 strategy
  - CompanyProfileStrategy:Slice 2b 子例程 + per-label writing prompt
  - VanillaStrategy:透传 vanilla GPTResearcher,不进任何特殊管线

后续 slice 加 depth ≥ 2 标签(Slice 3.2 / 3.3:sector_landscape / value_chain / ...)。
"""
import hashlib
import logging
import time
from typing import Any

from gpt_researcher import GPTResearcher

from ..actions import stream_output
from .classifier import ClassificationResult, QueryClassifier
from .extractor import StructuredExtractor
from .filing_finder import FilingFinder
from .orchestrator import Orchestrator

logger = logging.getLogger(__name__)


INVESTMENT_ANALYST_AGENT = "equity_analyst"

INVESTMENT_ANALYST_ROLE = """\
You are a meticulous US-equity research analyst. Your job is to produce \
fundamental analysis of public US-listed companies — focused on business \
model, competitive position, recent quarterly performance, catalysts, \
and risks.

Source discipline:
- Anchor factual claims to sources; cite every non-trivial number
- Distinguish company-reported numbers from third-party estimates
- When a figure is unconfirmed or estimated, say so explicitly
- Prefer recent (latest quarter / past 12 months) over historical data

Output discipline:
- Write in clear, analytical, professional prose
- Avoid hype and PR language (e.g., "revolutionary", "game-changing")
- Use figures where available; never fabricate numbers
- If a figure is not disclosed, state so explicitly rather than invent

Boundaries:
- You are an investment-research aide, not a fiduciary
- Reports inform analysis but do not constitute investment advice
- Do not predict price movement; describe fundamentals only
"""


class InvestmentResearcher:
    """美股投研助手的顶层入口。

    Slice 3.1 后形态:**composition root + thin routing**。
    - 装配 classifier / filing_finder / extractor / orchestrator 依赖
    - run() 只做"分类 + 高层 log + 把控制权交给 orchestrator"
    - 各路径的实际研究逻辑都在 strategies/ 下
    """

    def __init__(
        self,
        query: str,
        query_domains: list = None,
        report_type: str = "research_report",
        report_source: str = "web",
        source_urls=None,
        document_urls=None,
        tone: Any = None,
        config_path: str = None,
        websocket=None,
        headers=None,
        mcp_configs=None,
        mcp_strategy=None,
        max_search_results=None,
        encoding: str = "utf-8",
    ):
        self.research_id = self._generate_research_id(query)

        gpt_researcher_params = {
            "query": query,
            "query_domains": query_domains if query_domains else None,
            "report_type": report_type,
            "report_source": report_source,
            "source_urls": source_urls,
            "document_urls": document_urls,
            "tone": tone,
            "config_path": config_path,
            "websocket": websocket,
            "headers": headers or {},
            # 预填 agent + role -> 触发 agent.py:400 的 skip 逻辑,
            # 不调用 choose_agent() LLM,直接用固定的投研人设。
            "agent": INVESTMENT_ANALYST_AGENT,
            "role": INVESTMENT_ANALYST_ROLE,
            "encoding": encoding,
        }
        if mcp_configs is not None:
            gpt_researcher_params["mcp_configs"] = mcp_configs
        if mcp_strategy is not None:
            gpt_researcher_params["mcp_strategy"] = mcp_strategy

        self.gpt_researcher = GPTResearcher(**gpt_researcher_params)

        # Slice 3.0:不再无脑全局套白名单(Slice 2b 那段已删除)。
        # 白名单决策下放给 L0-B + InvestmentTavilySearch per sub-query 处理。
        # 切 retriever:整个 wrapper 默认走 investment_tavily;
        # VanillaStrategy 在执行时会临时切回 vanilla "tavily"。
        # 用户显式传 query_domains 仍被 InvestmentTavilySearch 尊重(escape hatch)。
        self.gpt_researcher.cfg.retriever = "investment_tavily"

        if max_search_results is not None:
            self.gpt_researcher.cfg.max_search_results_per_query = int(
                max_search_results
            )

        # 装配各 layer 的依赖:
        #   - classifier(L0-A)
        #   - filing_finder + extractor(公司深挖子例程,被 CompanyProfileStrategy 复用)
        #   - orchestrator(L1)持有各 strategy 实例
        cfg = self.gpt_researcher.cfg
        self.classifier = QueryClassifier(cfg)
        self.filing_finder = FilingFinder(self.gpt_researcher)
        self.extractor = StructuredExtractor(cfg)
        self.orchestrator = Orchestrator(
            gpt_researcher=self.gpt_researcher,
            filing_finder=self.filing_finder,
            extractor=self.extractor,
        )

    def _generate_research_id(self, query: str) -> str:
        timestamp = str(int(time.time()))
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        return f"investment_research_{timestamp}_{query_hash}"

    async def _log(self, message: str) -> None:
        """流式日志:无条件走 stream_output,它内部会处理 ws=None(CLI)和 ws 在(WS)两种情况。"""
        try:
            ws = getattr(self.gpt_researcher, "websocket", None)
            await stream_output("logs", "investment_research", message, ws)
        except Exception as e:
            logger.warning(f"_log via stream_output failed: {e}")

    async def run(self) -> str:
        """顶层路由器(Slice 3.1):分类 → log → orchestrator dispatch。"""
        # 1. L0-A 分类
        try:
            classification = await self.classifier.classify(self.gpt_researcher.query)
        except Exception as e:
            # 分类器内部已有 try/except,这里是双保险
            logger.warning(f"L0-A 分类抛错(意外),fallback 走 其他 兜底:{e}")
            classification = ClassificationResult(label="其他")

        # 2. 高层路由 log
        await self._log(f"🎯 L0-A 标签:{classification.label}")
        if classification.label == "company_profile":
            await self._log(
                f"📌 company_profile:{classification.company_name} "
                f"({classification.ticker or 'no ticker'})"
            )

        # 3. L1 dispatch 到对应 strategy
        return await self.orchestrator.execute(classification)
