"""ValueChainStrategy —— L0-A label = value_chain 时的策略(depth-3,两层 bootstrap)。

骨架(对应 prepare/08 § L0-A.4):
  Level 1:1 条 bootstrap —— 拆 {industry} 价值链 → 解析环节 [N1..Nk](k≤4)
    └ 解析失败 → 降级:只用 Level 1 出纵切概览(无环节骨架)
  Level 2:每环节 3 条(经济性 / 卡点 / 龙头)→ 每环节解析 leaders [Lj1..](≤2)
    └ 某环节 0 美股龙头 → 保留该环节定性内容,跳小卡(D4)
  Level 3:每 leader 2 条(角色 / 财务 snapshot)→ 每 leader mini 抽取
  → 拼 (L1 + L2 + 环节骨架行 + 按环节分组的 mini 卡片)
  → write_report(custom_prompt=WRITING_PROMPT_VALUE_CHAIN)

扇出上限 Balanced(D1):环节 ≤4,每环节公司 ≤2。
跨 batch context 累加由本 strategy 自己拼(run_query_batch 返回的 context 会被
下一次 conduct_research overwrite)。
"""
import logging
from typing import Optional

from gpt_researcher.actions import stream_output

from ..bootstrap_parsers import parse_company_list, parse_string_list
from ..classifier import ClassificationResult
from ..explicit_research_conductor import (
    ExplicitQueryResearchConductor,
    run_query_batch,
)
from ..schema import CompanyTarget
from ..writing_prompts import WRITING_PROMPT_VALUE_CHAIN

logger = logging.getLogger(__name__)

MAX_SEGMENTS = 4          # D1 Balanced:环节数上限
MAX_LEADERS_PER_SEG = 2   # D1 Balanced:每环节公司上限


class ValueChainStrategy:
    def __init__(self, gpt_researcher, extractor):
        self.gpt_researcher = gpt_researcher
        self.extractor = extractor

    async def _log(self, message: str) -> None:
        try:
            ws = getattr(self.gpt_researcher, "websocket", None)
            await stream_output("logs", "value_chain_strategy", message, ws)
        except Exception as e:
            logger.warning(f"_log via stream_output failed: {e}")

    @staticmethod
    def _level1_queries(industry: str) -> list[str]:
        return [
            f"Decompose the value chain of {industry}: upstream, midstream, "
            f"downstream — list the segments and their core activities"
        ]

    @staticmethod
    def _level2_queries(segments: list[str], industry: str) -> list[str]:
        queries: list[str] = []
        for seg in segments:
            queries.append(
                f"{seg} segment economics in {industry}: value capture, "
                f"margin profile, capital intensity"
            )
            queries.append(
                f"{seg} segment key bottlenecks, chokepoints, supply constraints "
                f"in {industry}"
            )
            queries.append(
                f"Top public companies dominating the {seg} segment of {industry}"
            )
        return queries

    @staticmethod
    def _level3_queries(
        seg_leaders: dict[str, list[CompanyTarget]], industry: str
    ) -> list[str]:
        queries: list[str] = []
        for seg, leaders in seg_leaders.items():
            for leader in leaders:
                queries.append(
                    f"{leader.name} role within the {seg} segment of {industry}, "
                    f"revenue share from this segment"
                )
                queries.append(
                    f"{leader.name} latest revenue, year-over-year growth rate, "
                    f"and gross margin"
                )
        return queries

    async def run(self, classification: ClassificationResult) -> str:
        industry: Optional[str] = classification.industry
        if not industry:
            logger.warning(
                "value_chain called with no industry in classification; "
                "using raw query as industry name"
            )
            industry = self.gpt_researcher.query

        # 切 conductor(同 sector_landscape;实例只跑一次 run,不需 restore)
        self.gpt_researcher.research_conductor = ExplicitQueryResearchConductor(
            self.gpt_researcher
        )

        # ---------- Level 1:拆环节 ----------
        await self._log(f"🔍 Level 1:拆解 {industry} 的产业链环节(1 条检索)")
        level1_ctx = await run_query_batch(
            self.gpt_researcher, self._level1_queries(industry)
        )

        segments = await parse_string_list(
            self.gpt_researcher.cfg,
            text=level1_ctx,
            instruction=f"list the distinct value-chain segments of {industry} "
            f"(upstream to downstream)",
            max_n=MAX_SEGMENTS,
        )

        if not segments:
            await self._log(
                "⚠️ 未能识别出产业链环节,改为只输出产业链概览,不做逐环节展开"
            )
            self.gpt_researcher.context = level1_ctx
            return await self.gpt_researcher.write_report(
                custom_prompt=WRITING_PROMPT_VALUE_CHAIN,
            )

        await self._log(
            f"📌 识别出 {len(segments)} 个产业链环节:" + ", ".join(segments)
        )

        # ---------- Level 2:每环节 3 条 ----------
        await self._log(
            f"🔍 Level 2:每个环节 3 条,共 {3 * len(segments)} 条检索"
        )
        level2_ctx = await run_query_batch(
            self.gpt_researcher, self._level2_queries(segments, industry)
        )

        # ---------- 每环节解析龙头(第二层 bootstrap)----------
        seg_leaders: dict[str, list[CompanyTarget]] = {}
        for seg in segments:
            leaders = await parse_company_list(
                self.gpt_researcher.cfg,
                text=level2_ctx,
                scope_label=f"the {seg} segment of {industry}",
                max_n=MAX_LEADERS_PER_SEG,
            )
            seg_leaders[seg] = leaders
        total_leaders = sum(len(v) for v in seg_leaders.values())
        await self._log(
            f"📌 各环节龙头:"
            + "; ".join(
                f"{seg}[" + ", ".join(l.ticker or l.name for l in leaders) + "]"
                if leaders
                else f"{seg}[无美股]"
                for seg, leaders in seg_leaders.items()
            )
        )

        if total_leaders == 0:
            # 所有环节都没美股龙头:跳 Level 3,用 L1+L2 出报告(带环节骨架)
            await self._log("⚠️ 各环节均未找到美股上市公司,跳过 Level 3,仅分析环节经济性")
            merged = (
                level1_ctx
                + "\n\n"
                + level2_ctx
                + "\n\n## Value-chain segments identified: "
                + ", ".join(segments)
            )
            self.gpt_researcher.context = merged
            return await self.gpt_researcher.write_report(
                custom_prompt=WRITING_PROMPT_VALUE_CHAIN,
            )

        # ---------- Level 3:每 leader 2 条 ----------
        await self._log(
            f"🔍 Level 3:每家公司 2 条,共 {2 * total_leaders} 条检索"
        )
        level3_ctx = await run_query_batch(
            self.gpt_researcher, self._level3_queries(seg_leaders, industry)
        )

        # ---------- 每 leader mini 抽取,按环节分组渲染 ----------
        grouped_cards: list[str] = []
        cards_done = 0
        for seg, leaders in seg_leaders.items():
            if not leaders:
                continue
            seg_blocks: list[str] = []
            for leader in leaders:
                try:
                    metrics = await self.extractor.extract(
                        filing=None,
                        web_context=[level3_ctx],
                        target=leader,
                        mode="mini",
                    )
                    seg_blocks.append(self.extractor.render_as_markdown(metrics))
                    cards_done += 1
                    # 逐家播报,供前端「研究计划」面板显示第 3 层的覆盖情况。
                    # 只报代码与成败,不报具体数字 —— 数字在报告里已经有了,
                    # 这里重复一遍没有增量;而「哪一家没取到」才是读者会困惑的点。
                    await self._log(f"🔬 {leader.ticker or leader.name} 指标已获取")
                except Exception as e:
                    logger.warning(f"mini extract failed for {leader.name}: {e}")
                    await self._log(f"🔬 {leader.ticker or leader.name} 指标未取到")
            if seg_blocks:
                grouped_cards.append(
                    f"### {seg} — representative companies\n\n"
                    + "\n\n".join(seg_blocks)
                )
        await self._log(
            f"🔬 已获取 {cards_done}/{total_leaders} 家公司的财务指标"
        )

        # ---------- 拼总 context(D5:显式环节骨架行 + 分组卡片)----------
        merged = (
            level1_ctx
            + "\n\n"
            + level2_ctx
            + "\n\n## Value-chain segments identified: "
            + ", ".join(segments)
        )
        if grouped_cards:
            merged += "\n\n" + "\n\n".join(grouped_cards)
        self.gpt_researcher.context = merged

        return await self.gpt_researcher.write_report(
            custom_prompt=WRITING_PROMPT_VALUE_CHAIN,
        )
