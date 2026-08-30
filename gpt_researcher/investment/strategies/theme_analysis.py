"""ThemeAnalysisStrategy —— L0-A label = theme_analysis 时的策略(depth-3,两层 bootstrap)。

骨架(对应 prepare/08 § L0-A.5):
  Level 1:4 条(叙事+催化 / 受益分类[bootstrap] / 时间窗 / 风险)
    → 解析受益类别 [Cat1..Catm](m≤4)
    └ 解析失败 → 降级:只用 Level 1 出主题概览(无类别骨架)
  Level 2:每类 2 条(传导机制 / 代表股[bootstrap])→ 每类解析代表股 [Sj1..](≤2)
    └ 某类 0 美股代表股 → 保留该类传导机制文字,跳小卡(D4)
  Level 3:每股 2 条(暴露 / 财务 snapshot)→ 每股 mini 抽取
  → 拼 (L1 + L2 + 类别骨架行 + 按类别分组的 mini 卡片)
  → write_report(custom_prompt=WRITING_PROMPT_THEME_ANALYSIS)

扇出上限 Balanced(D1):类别 ≤4,每类公司 ≤2。形态与 ValueChainStrategy 同构。
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
from ..writing_prompts import WRITING_PROMPT_THEME_ANALYSIS

logger = logging.getLogger(__name__)

MAX_CATEGORIES = 4         # D1 Balanced:受益类别上限
MAX_STOCKS_PER_CAT = 2     # D1 Balanced:每类公司上限


class ThemeAnalysisStrategy:
    def __init__(self, gpt_researcher, extractor):
        self.gpt_researcher = gpt_researcher
        self.extractor = extractor

    async def _log(self, message: str) -> None:
        try:
            ws = getattr(self.gpt_researcher, "websocket", None)
            await stream_output("logs", "theme_analysis_strategy", message, ws)
        except Exception as e:
            logger.warning(f"_log via stream_output failed: {e}")

    @staticmethod
    def _level1_queries(theme: str) -> list[str]:
        return [
            f"Driving narrative and key catalysts of the {theme} investment theme",
            f"Categorize the types of companies that benefit from {theme}, "
            f"grouped by mechanism of exposure",
            f"Time horizon and milestones for the {theme} theme to play out",
            f"Key risks that could invalidate the {theme} investment thesis",
        ]

    @staticmethod
    def _level2_queries(categories: list[str], theme: str) -> list[str]:
        queries: list[str] = []
        for cat in categories:
            queries.append(
                f"Why does {cat} benefit from {theme}? Mechanism of value transmission"
            )
            queries.append(
                f"Top US-listed public stocks in {cat} most leveraged to {theme}"
            )
        return queries

    @staticmethod
    def _level3_queries(
        cat_stocks: dict[str, list[CompanyTarget]], theme: str
    ) -> list[str]:
        queries: list[str] = []
        for cat, stocks in cat_stocks.items():
            for stock in stocks:
                queries.append(
                    f"{stock.name} exposure to {theme}: revenue share, "
                    f"strategic positioning"
                )
                queries.append(
                    f"{stock.name} latest revenue, year-over-year growth rate, "
                    f"and gross margin"
                )
        return queries

    async def run(self, classification: ClassificationResult) -> str:
        theme: Optional[str] = classification.theme
        if not theme:
            logger.warning(
                "theme_analysis called with no theme in classification; "
                "using raw query as theme name"
            )
            theme = self.gpt_researcher.query

        self.gpt_researcher.research_conductor = ExplicitQueryResearchConductor(
            self.gpt_researcher
        )

        # ---------- Level 1:叙事 + 拆类别 ----------
        await self._log(f"🔍 Level 1:调研 {theme} 主题的驱动逻辑(4 条检索)")
        level1_ctx = await run_query_batch(
            self.gpt_researcher, self._level1_queries(theme)
        )

        categories = await parse_string_list(
            self.gpt_researcher.cfg,
            text=level1_ctx,
            instruction=f"list the benefit categories for the {theme} theme "
            f"(types of companies grouped by mechanism of exposure)",
            max_n=MAX_CATEGORIES,
        )

        if not categories:
            await self._log(
                "⚠️ 未能识别出受益类别,改为只输出主题概览,不做分类展开"
            )
            self.gpt_researcher.context = level1_ctx
            return await self.gpt_researcher.write_report(
                custom_prompt=WRITING_PROMPT_THEME_ANALYSIS,
            )

        await self._log(
            f"📌 识别出 {len(categories)} 个受益类别:" + ", ".join(categories)
        )

        # ---------- Level 2:每类 2 条 ----------
        await self._log(
            f"🔍 Level 2:每个类别 2 条,共 {2 * len(categories)} 条检索"
        )
        level2_ctx = await run_query_batch(
            self.gpt_researcher, self._level2_queries(categories, theme)
        )

        # ---------- 每类解析代表股(第二层 bootstrap)----------
        cat_stocks: dict[str, list[CompanyTarget]] = {}
        for cat in categories:
            stocks = await parse_company_list(
                self.gpt_researcher.cfg,
                text=level2_ctx,
                scope_label=f"{cat} exposure to {theme}",
                max_n=MAX_STOCKS_PER_CAT,
            )
            cat_stocks[cat] = stocks
        total_stocks = sum(len(v) for v in cat_stocks.values())
        await self._log(
            f"📌 各类别代表股:"
            + "; ".join(
                f"{cat}[" + ", ".join(s.ticker or s.name for s in stocks) + "]"
                if stocks
                else f"{cat}[无美股]"
                for cat, stocks in cat_stocks.items()
            )
        )

        if total_stocks == 0:
            await self._log("⚠️ 各类别均未找到美股上市公司,跳过 Level 3,仅分析受益传导机制")
            merged = (
                level1_ctx
                + "\n\n"
                + level2_ctx
                + "\n\n## Benefit categories identified: "
                + ", ".join(categories)
            )
            self.gpt_researcher.context = merged
            return await self.gpt_researcher.write_report(
                custom_prompt=WRITING_PROMPT_THEME_ANALYSIS,
            )

        # ---------- Level 3:每股 2 条 ----------
        await self._log(
            f"🔍 Level 3:每家公司 2 条,共 {2 * total_stocks} 条检索"
        )
        level3_ctx = await run_query_batch(
            self.gpt_researcher, self._level3_queries(cat_stocks, theme)
        )

        # ---------- 每股 mini 抽取,按类别分组渲染 ----------
        grouped_cards: list[str] = []
        cards_done = 0
        for cat, stocks in cat_stocks.items():
            if not stocks:
                continue
            cat_blocks: list[str] = []
            for stock in stocks:
                try:
                    metrics = await self.extractor.extract(
                        filing=None,
                        web_context=[level3_ctx],
                        target=stock,
                        mode="mini",
                    )
                    cat_blocks.append(self.extractor.render_as_markdown(metrics))
                    cards_done += 1
                    # 逐家播报,供前端「研究计划」面板显示第 3 层的覆盖情况。
                    # 只报代码与成败,不报具体数字 —— 数字在报告里已经有了,
                    # 这里重复一遍没有增量;而「哪一家没取到」才是读者会困惑的点。
                    await self._log(f"🔬 {stock.ticker or stock.name} 指标已获取")
                except Exception as e:
                    logger.warning(f"mini extract failed for {stock.name}: {e}")
                    await self._log(f"🔬 {stock.ticker or stock.name} 指标未取到")
            if cat_blocks:
                grouped_cards.append(
                    f"### {cat} — most leveraged stocks\n\n"
                    + "\n\n".join(cat_blocks)
                )
        await self._log(
            f"🔬 已获取 {cards_done}/{total_stocks} 家公司的财务指标"
        )

        # ---------- 拼总 context(D5:显式类别骨架行 + 分组卡片)----------
        merged = (
            level1_ctx
            + "\n\n"
            + level2_ctx
            + "\n\n## Benefit categories identified: "
            + ", ".join(categories)
        )
        if grouped_cards:
            merged += "\n\n" + "\n\n".join(grouped_cards)
        self.gpt_researcher.context = merged

        return await self.gpt_researcher.write_report(
            custom_prompt=WRITING_PROMPT_THEME_ANALYSIS,
        )
