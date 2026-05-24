"""
InvestmentResearcher —— 美股投研助手的顶层路由器。

Slice 1: "其他 兜底"(投研人设 + 财经域名白名单)—— 历史形态
Slice 2b: 加 L0 简化版(单公司检测)+ L2b 财报旁路 + L3 结构化抽取 + trust label —— 历史形态
Slice 3.0: 重构为 L0-A 二分类路由器
  - L0-A 分类:company_profile / 其他
  - L0-B 由 InvestmentTavilySearch 自查每条 sub-query 决定白名单(per-sub-query)
  - company_profile 路径:走 Slice 2b 子例程(filing + extractor)+ per-label writing prompt
  - 其他 路径:透传 vanilla GPTResearcher,不进任何特殊管线

后续 slice 会加 L1 树编排器(Slice 3.1)+ 其余 4 个标签(Slice 3.2 / 3.3)。
"""
import hashlib
import logging
import time
from typing import Any

from gpt_researcher import GPTResearcher

from ..actions import stream_output
from .classifier import ClassificationResult, QueryClassifier
from .extractor import METRIC_FIELDS, StructuredExtractor
from .filing_finder import FilingFinder
from .schema import CompanyTarget
from .writing_prompts import WRITING_PROMPT_COMPANY_PROFILE

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
    """美股投研助手的顶层入口(Slice 3.0:L0-A 路由器)。"""

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
        # "其他" 兜底分支在 run() 内会临时切回 vanilla "tavily"。
        # 用户显式传 query_domains 仍被 InvestmentTavilySearch 尊重(escape hatch)。
        self.gpt_researcher.cfg.retriever = "investment_tavily"

        if max_search_results is not None:
            self.gpt_researcher.cfg.max_search_results_per_query = int(
                max_search_results
            )

        # Slice 3.0:实例化 L0-A 分类器(替换 Slice 2b 的 CompanyDetector)。
        # FilingFinder / StructuredExtractor 在 company_profile 路径仍被复用。
        cfg = self.gpt_researcher.cfg
        self.classifier = QueryClassifier(cfg)
        self.filing_finder = FilingFinder(self.gpt_researcher)
        self.extractor = StructuredExtractor(cfg)

    def _generate_research_id(self, query: str) -> str:
        timestamp = str(int(time.time()))
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        return f"investment_research_{timestamp}_{query_hash}"

    async def _log(self, message: str) -> None:
        """流式日志:无条件走 stream_output,它内部会处理 ws=None(CLI)和 ws 在(WS)两种情况。

        旧实现把 stream_output 调用 gate 在 `if ws is not None`,导致 CLI 下
        L0-A 路由 / company_profile / 财报子例程的标志 log 全部静默,只在 WS 下可见。
        """
        try:
            ws = getattr(self.gpt_researcher, "websocket", None)
            await stream_output("logs", "investment_research", message, ws)
        except Exception as e:
            logger.warning(f"_log via stream_output failed: {e}")

    async def run(self) -> str:
        """顶层路由器(Slice 3.0)。"""
        # 1. L0-A 分类
        try:
            classification = await self.classifier.classify(self.gpt_researcher.query)
        except Exception as e:
            # 分类器内部已有 try/except,这里是双保险
            logger.warning(f"L0-A 分类抛错(意外),fallback 走 其他 兜底:{e}")
            classification = ClassificationResult(label="其他")

        await self._log(f"🎯 L0-A 标签:{classification.label}")

        # 2. 分支路由
        if classification.label == "company_profile":
            return await self._run_company_profile_path(classification)
        # 其他(包括未来未定义的 label,防御性都走兜底)
        return await self._run_other_path()

    async def _run_other_path(self) -> str:
        """其他 兜底:vanilla GPTResearcher,不套白名单,不调子例程。

        投研 persona 仍然保留(已在 __init__ 预填给 GPTResearcher)——
        定位决定我们的报告仍带"投研味",只是不强加 per-label writing prompt
        也不跑 filing/extractor。
        """
        # 切回原生 tavily,关掉 per-sub-query L0-B 决策
        self.gpt_researcher.cfg.retriever = "tavily"
        await self._log("ℹ️ 走 其他 兜底:vanilla gpt-researcher,无白名单 / 无子例程")
        await self.gpt_researcher.conduct_research()
        # 不传 custom_prompt:沿用 gpt-researcher 默认报告 prompt
        return await self.gpt_researcher.write_report()

    async def _run_company_profile_path(self, c: ClassificationResult) -> str:
        """company_profile 主路径。

        - per-sub-query 白名单决策:由 investment_tavily retriever 自动处理(已在 __init__ 切好)
        - Slice 2b 公司深挖子例程:filing_finder + extractor
        - per-label writing prompt:write_report(custom_prompt=WRITING_PROMPT_COMPANY_PROFILE)
        """
        await self._log(f"📌 company_profile:{c.company_name} ({c.ticker or 'no ticker'})")

        # 1. 正常 web research
        # (L0-B 自动决定每条 sub-query 用不用白名单,InvestmentTavilySearch 内部处理)
        await self.gpt_researcher.conduct_research()

        # 2. 公司深挖子例程(Slice 2b 代码复用)
        # 直接用 classifier 出的 name/ticker,跳过原 CompanyDetector 的二次抽取
        target = CompanyTarget(name=c.company_name, ticker=c.ticker)

        filing = None
        try:
            filing = await self.filing_finder.fetch(target)
        except Exception as e:
            logger.warning(f"FilingFinder raised: {e}")

        if filing:
            await self._log(f"📑 已取财报:{filing.url}")
        else:
            await self._log("⚠️ 未取到财报,L3 将只用 web context")

        metrics_md: str | None = None
        try:
            metrics = await self.extractor.extract(
                filing=filing,
                web_context=self.gpt_researcher.context,
                target=target,
            )
            populated = sum(
                1
                for f in METRIC_FIELDS
                if getattr(metrics, f).value is not None
            )
            await self._log(
                f"🔬 已抽取 {populated}/{len(METRIC_FIELDS)} 个指标字段 → 注入 context"
            )
            metrics_md = self.extractor.render_as_markdown(metrics)
        except Exception as e:
            logger.warning(f"StructuredExtractor raised: {e}")
            await self._log("⚠️ 结构化抽取失败,只写原始研究报告")

        # 3. 注入 metrics markdown 到 context(让 LLM 在写报告时能看到结构化指标)
        if metrics_md:
            ctx = self.gpt_researcher.context
            if isinstance(ctx, list):
                ctx.append(metrics_md)
            else:
                self.gpt_researcher.context = f"{ctx}\n\n{metrics_md}"

        # 4. 写报告:用 per-label writing prompt(Slice 3.0 引入)
        # 这是 L4 塌缩形态的具体实例 —— 不再走 N 次模板 + assembler,而是
        # 1 次原生 write_report,用 company_profile 专用 prompt 引导 6 段结构。
        return await self.gpt_researcher.write_report(
            custom_prompt=WRITING_PROMPT_COMPANY_PROFILE,
        )
