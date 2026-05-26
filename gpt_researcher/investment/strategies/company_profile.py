"""CompanyProfileStrategy —— L0-A label = company_profile 时执行的策略。

逻辑搬自 Slice 3.0 InvestmentResearcher._run_company_profile_path,功能不变:
1. 正常 web research(L0-B per sub-query 决定白名单,investment_tavily 处理)
2. 公司深挖子例程(filing_finder + extractor mode="full")
3. metrics markdown 注入 context
4. write_report 用 WRITING_PROMPT_COMPANY_PROFILE

进度 log(`📑 已取财报` / `🔬 已抽取`)直接通过 stream_output 输出 ——
跟 InvestmentResearcher._log 走同一条 scraper logger,CLI 和 WS 都可见。
"""
import logging

from gpt_researcher.actions import stream_output

from ..classifier import ClassificationResult
from ..extractor import METRIC_FIELDS
from ..schema import CompanyTarget
from ..writing_prompts import WRITING_PROMPT_COMPANY_PROFILE

logger = logging.getLogger(__name__)


class CompanyProfileStrategy:
    def __init__(self, gpt_researcher, filing_finder, extractor):
        self.gpt_researcher = gpt_researcher
        self.filing_finder = filing_finder
        self.extractor = extractor

    async def _log(self, message: str) -> None:
        """向用户输出进度 log;CLI + WS 都可见(stream_output 内部处理 ws=None)。"""
        try:
            ws = getattr(self.gpt_researcher, "websocket", None)
            await stream_output("logs", "company_profile_strategy", message, ws)
        except Exception as e:
            logger.warning(f"_log via stream_output failed: {e}")

    async def run(self, classification: ClassificationResult) -> str:
        # 1. 正常 web research
        # (L0-B 自动决定每条 sub-query 用不用白名单,InvestmentTavilySearch 内部处理)
        await self.gpt_researcher.conduct_research()

        # 2. 公司深挖子例程(Slice 2b 代码复用)
        # 直接用 classifier 出的 name/ticker,跳过原 CompanyDetector 的二次抽取
        target = CompanyTarget(
            name=classification.company_name,
            ticker=classification.ticker,
        )

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
                # Slice 3.1:company_profile 始终用 full mode(6 字段)
                # mini mode 真实逻辑由 Slice 3.2 sector_landscape / value_chain 调用时实现
                mode="full",
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

        # 4. 写报告:用 per-label writing prompt
        # L4 塌缩形态的具体实例 —— 不再走 N 次模板 + assembler,而是
        # 1 次原生 write_report,用 company_profile 专用 prompt 引导 6 段结构。
        return await self.gpt_researcher.write_report(
            custom_prompt=WRITING_PROMPT_COMPANY_PROFILE,
        )
