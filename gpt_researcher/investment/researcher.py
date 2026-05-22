"""
InvestmentResearcher —— 美股投研助手的顶层 wrapper。

Slice 1: "other 兜底"(投研人设 + 财经域名白名单)
Slice 2b: 加 L0 简化版(单公司检测)+ L2b 财报旁路 + L3 结构化抽取 + trust label

后续 slice 会加 L1 树编排 / L4 分层模板写作。
"""
import hashlib
import logging
import time
from typing import Any

from gpt_researcher import GPTResearcher

from ..actions import stream_output
from .company_detector import CompanyDetector
from .extractor import METRIC_FIELDS, StructuredExtractor
from .filing_finder import FilingFinder

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
    """美股投研助手的顶层入口。"""

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

        # 用户没传 query_domains -> 从 cfg 取默认白名单(财经域名)。
        # GPTResearcher 本身不会做这个 fallback,在 wrapper 这层处理。
        if not query_domains:
            default_wl = getattr(
                self.gpt_researcher.cfg, "finance_domain_whitelist", None
            )
            if default_wl:
                self.gpt_researcher.query_domains = default_wl

        if max_search_results is not None:
            self.gpt_researcher.cfg.max_search_results_per_query = int(
                max_search_results
            )

        # Slice 2b: instantiate the three new components
        cfg = self.gpt_researcher.cfg
        self.company_detector = CompanyDetector(cfg)
        self.filing_finder = FilingFinder(self.gpt_researcher)
        self.extractor = StructuredExtractor(cfg)

    def _generate_research_id(self, query: str) -> str:
        timestamp = str(int(time.time()))
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        return f"investment_research_{timestamp}_{query_hash}"

    async def _log(self, message: str) -> None:
        """流式日志(Python logger + WebSocket,如果 WS 在)。失败不影响主流程。"""
        logger.info(message)
        ws = getattr(self.gpt_researcher, "websocket", None)
        if ws is not None:
            try:
                await stream_output("logs", "investment_research", message, ws)
            except Exception as e:
                logger.warning(f"stream_output failed: {e}")

    async def run(self) -> str:
        # 1. 正常 L2 web research(Slice 1 已有)
        await self.gpt_researcher.conduct_research()

        # Slice 2b 入口 ----------------------------------------------------
        metrics_md: str | None = None

        # 2. 公司检测(失败时降级到 Slice 1 行为)
        target = None
        try:
            target = await self.company_detector.detect(self.gpt_researcher.query)
        except Exception as e:
            logger.warning(f"Company detection raised: {e}")

        if target is None:
            await self._log("ℹ️ 未识别为单一目标公司,跳过 Slice 2b 财报旁路")
        else:
            await self._log(
                f"🎯 目标公司:{target.name} ({target.ticker or 'no ticker'})"
            )

            # 3. L2b 财报抓取(失败 → filing=None,L3 改 web-only)
            filing = None
            try:
                filing = await self.filing_finder.fetch(target)
            except Exception as e:
                logger.warning(f"FilingFinder raised: {e}")

            if filing:
                await self._log(f"📑 已取财报:{filing.url}")
            else:
                await self._log("⚠️ 未取到财报,L3 将只用 web context")

            # 4. L3 结构化抽取(失败 → 直接跳过 metrics,不影响主报告)
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
                    f"🔬 已抽取 {populated}/{len(METRIC_FIELDS)} 个指标字段 → 附加到报告末尾"
                )
                metrics_md = self.extractor.render_as_markdown(metrics)
            except Exception as e:
                logger.warning(f"StructuredExtractor raised: {e}")
                await self._log("⚠️ 结构化抽取失败,只写原始研究报告")

        # Slice 2b UX 修复:也注入到 context,让 LLM 在写报告时看到结构化数据
        # → UI 流式渲染就会有 metrics(LLM 大概率会复述这个 markdown 表)。
        # Post-append 仍保留作 safety net,保证文件输出完整。
        #
        # 注意:conduct_research() 跑完后 self.context 是 str(由 list 拼接而成),
        # 不是 list,所以不能直接 .append();这里 type-aware 处理。
        if metrics_md:
            ctx = self.gpt_researcher.context
            if isinstance(ctx, list):
                ctx.append(metrics_md)
            else:
                self.gpt_researcher.context = f"{ctx}\n\n{metrics_md}"

        # 5. 写报告(Slice 1)
        # Slice 2b 注:metrics 已通过 context 注入(上面),LLM 会在报告体里
        # 引用这些精确数字 + 来源 URL,自己生成表格。post-append 那份 canonical
        # 表已根据使用反馈移除(冗余 + 数据可能比报告体更旧 + UI 不渲染)。
        # 想找回显式 trust badge 的话,未来可做 post-LLM badge 注入。
        return await self.gpt_researcher.write_report()
