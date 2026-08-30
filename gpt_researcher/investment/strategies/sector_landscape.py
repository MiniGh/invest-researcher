"""SectorLandscapeStrategy —— L0-A label = sector_landscape 时的策略。

骨架(对应 prepare/08 § L0-A.3):
  Level 1:5 条静态 sub-query(行业市场规模 / 驱动 / 阻力 / 竞争格局 / 主要玩家)
  → 解析"主要玩家"列表(bootstrap_parsers.parse_company_list,3 层防御)
    L1: prompt 强约束"只输 JSON" / L2: json_repair 容错
    L3: 解析失败 → 跳 Level 2,降级出"无玩家卡片"的行业概览
  Level 2:每家 P_j 2 条(角色 + 财务 snapshot)
  → 每家 mini 抽取(3 字段:revenue / yoy_growth / gross_margin)
  → 拼 (level1_ctx + level2_ctx + mini_blocks)
  → write_report(custom_prompt=WRITING_PROMPT_SECTOR_LANDSCAPE)

跨 batch context 累加由本 strategy 自己管(conduct_research 会 overwrite
researcher.context,必须 strategy 层保存上一轮字符串再拼)。

Slice 3.3:玩家解析逻辑提取到共享 bootstrap_parsers.parse_company_list
(value_chain / theme_analysis 也用);本文件改为调用它,行为不变。
"""
import logging
from typing import Optional

from gpt_researcher.actions import stream_output

from ..bootstrap_parsers import parse_company_list
from ..classifier import ClassificationResult
from ..explicit_research_conductor import (
    ExplicitQueryResearchConductor,
    run_query_batch,
)
from ..schema import CompanyTarget
from ..writing_prompts import WRITING_PROMPT_SECTOR_LANDSCAPE

logger = logging.getLogger(__name__)


class SectorLandscapeStrategy:
    def __init__(self, gpt_researcher, extractor):
        self.gpt_researcher = gpt_researcher
        self.extractor = extractor

    async def _log(self, message: str) -> None:
        try:
            ws = getattr(self.gpt_researcher, "websocket", None)
            await stream_output("logs", "sector_landscape_strategy", message, ws)
        except Exception as e:
            logger.warning(f"_log via stream_output failed: {e}")

    @staticmethod
    def _level1_queries(sector: str) -> list[str]:
        return [
            f"{sector} market size and growth trajectory",
            f"{sector} key demand drivers and tailwinds",
            f"{sector} key headwinds, risks, regulatory factors",
            f"{sector} competitive dynamics and consolidation trends",
            f"Top 3-5 leading public companies in {sector} by revenue or market share",
        ]

    @staticmethod
    def _level2_queries(players: list[CompanyTarget], sector: str) -> list[str]:
        queries: list[str] = []
        for p in players:
            queries.append(
                f"{p.name} role within {sector}, revenue share from {sector}"
            )
            queries.append(
                f"{p.name} latest revenue, year-over-year growth rate, and gross margin"
            )
        return queries

    async def run(self, classification: ClassificationResult) -> str:
        sector: Optional[str] = classification.sector
        if not sector:
            # classifier 已经在 sector 为空时降级到 其他;真到这里说明 orchestrator
            # 调用方向错了。仍兜底,用原始 query 当 sector 名字。
            logger.warning(
                "sector_landscape called with no sector in classification; "
                "using raw query as sector name"
            )
            sector = self.gpt_researcher.query

        # 切 conductor。
        # 注意:这一行会替换 InvestmentResearcher.__init__ 装好的 ResearchConductor。
        # 同一个 InvestmentResearcher 实例理论上只跑一次 run(),所以不需要 restore。
        self.gpt_researcher.research_conductor = ExplicitQueryResearchConductor(
            self.gpt_researcher
        )

        # ---------- Level 1 ----------
        await self._log(f"🔍 Level 1:调研 {sector} 行业基本面(5 条检索)")
        level1_ctx = await run_query_batch(
            self.gpt_researcher, self._level1_queries(sector)
        )

        # ---------- 玩家解析(三层防御,共享 parser)----------
        players = await parse_company_list(
            self.gpt_researcher.cfg,
            text=level1_ctx,
            scope_label=f"the {sector} industry",
            max_n=5,
        )

        if not players:
            # 第 3 层:降级,只用 Level 1 出报告
            await self._log(
                "⚠️ 未能识别出代表公司,改为只输出行业概览,不含公司卡片"
            )
            self.gpt_researcher.context = level1_ctx
            return await self.gpt_researcher.write_report(
                custom_prompt=WRITING_PROMPT_SECTOR_LANDSCAPE,
            )

        await self._log(
            f"📌 识别出 {len(players)} 家代表公司:"
            + ", ".join(f"{p.name}({p.ticker or '-'})" for p in players)
        )

        # ---------- Level 2 ----------
        await self._log(
            f"🔍 Level 2:每家公司 2 条,共 {2 * len(players)} 条检索"
        )
        level2_ctx = await run_query_batch(
            self.gpt_researcher, self._level2_queries(players, sector)
        )

        # ---------- Per-player mini 抽取 ----------
        mini_blocks: list[str] = []
        for p in players:
            try:
                metrics = await self.extractor.extract(
                    filing=None,
                    web_context=[level2_ctx],
                    target=p,
                    mode="mini",
                )
                mini_blocks.append(self.extractor.render_as_markdown(metrics))
            except Exception as e:
                logger.warning(f"mini extract failed for {p.name}: {e}")
        await self._log(
            f"🔬 已获取 {len(mini_blocks)}/{len(players)} 家公司的财务指标"
        )

        # ---------- 拼总 context ----------
        merged = level1_ctx + "\n\n" + level2_ctx
        if mini_blocks:
            merged += "\n\n" + "\n\n".join(mini_blocks)
        self.gpt_researcher.context = merged

        return await self.gpt_researcher.write_report(
            custom_prompt=WRITING_PROMPT_SECTOR_LANDSCAPE,
        )
