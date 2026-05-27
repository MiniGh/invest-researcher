"""CompanyComparisonStrategy —— L0-A label = company_comparison 时的策略。

骨架(对应 prepare/08 § L0-A.2):classifier 已经在 scope.companies 里给了
要对比的公司列表(D1 决策,见 prepare/11)—— 不需要再发 bootstrap 提取。
直接进 Level 2:每家 × 6 维度 sub-query → 每家 full mode 抽取 →
write_report(custom_prompt=WRITING_PROMPT_COMPANY_COMPARISON)

注意 v1 限制(prepare/11 已知限制):每家不调 filing_finder,只用 web context +
full mode 抽 6 字段。per-company filing 抓取成本太高,精度要求高的话用户应该
单跑 company_profile。
"""
import logging
from typing import Optional

from gpt_researcher.actions import stream_output

from ..classifier import ClassificationResult
from ..explicit_research_conductor import (
    ExplicitQueryResearchConductor,
    run_query_batch,
)
from ..writing_prompts import WRITING_PROMPT_COMPANY_COMPARISON

logger = logging.getLogger(__name__)


class CompanyComparisonStrategy:
    def __init__(self, gpt_researcher, extractor):
        self.gpt_researcher = gpt_researcher
        self.extractor = extractor

    async def _log(self, message: str) -> None:
        try:
            ws = getattr(self.gpt_researcher, "websocket", None)
            await stream_output("logs", "company_comparison_strategy", message, ws)
        except Exception as e:
            logger.warning(f"_log via stream_output failed: {e}")

    @staticmethod
    def _build_queries(companies, shared_industry: Optional[str]) -> list[str]:
        queries: list[str] = []
        ind_hint = f" within {shared_industry}" if shared_industry else ""
        for c in companies:
            queries.extend([
                f"{c.name} core business and product portfolio",
                f"{c.name} latest quarterly revenue, margins, EPS",
                f"{c.name} market share{ind_hint}",
                f"{c.name} current P/E, P/S, market cap",
                f"{c.name} forward guidance and analyst consensus",
                f"{c.name} key risks and headwinds",
            ])
        return queries

    async def run(self, classification: ClassificationResult) -> str:
        companies = classification.companies or []
        if not companies:
            # classifier 已经在 companies 不齐时降级到 其他;真到这里说明 orchestrator
            # 调用方向错了。兜底走 vanilla 模式,不让用户白等。
            logger.warning(
                "company_comparison called with no companies in classification; "
                "vanilla fallback"
            )
            self.gpt_researcher.cfg.retriever = "tavily"
            await self.gpt_researcher.conduct_research()
            return await self.gpt_researcher.write_report()

        await self._log(
            f"📌 对比 {len(companies)} 家:"
            + ", ".join(f"{c.name}({c.ticker or '-'})" for c in companies)
        )

        self.gpt_researcher.research_conductor = ExplicitQueryResearchConductor(
            self.gpt_researcher
        )

        queries = self._build_queries(companies, shared_industry=None)
        await self._log(
            f"🔍 共 {len(queries)} 条 sub-query({len(companies)} 家 × 6 维度)"
        )
        web_ctx = await run_query_batch(self.gpt_researcher, queries)

        # 每家 full 抽取(filing 不抓 —— v1 限制,见 prepare/11 已知限制)
        metric_blocks: list[str] = []
        for c in companies:
            try:
                metrics = await self.extractor.extract(
                    filing=None,
                    web_context=[web_ctx],
                    target=c,
                    mode="full",
                )
                metric_blocks.append(self.extractor.render_as_markdown(metrics))
            except Exception as e:
                logger.warning(f"full extract failed for {c.name}: {e}")
        await self._log(
            f"🔬 已为 {len(metric_blocks)}/{len(companies)} 家产出对比指标"
        )

        merged = web_ctx
        if metric_blocks:
            merged += "\n\n" + "\n\n".join(metric_blocks)
        self.gpt_researcher.context = merged

        return await self.gpt_researcher.write_report(
            custom_prompt=WRITING_PROMPT_COMPANY_COMPARISON,
        )
