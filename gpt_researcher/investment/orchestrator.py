"""L1 树编排器 —— Slice 3.2 起 depth-2 形态。

按 L0-A classification.label dispatch 到对应 Strategy 实例。
Slice 3.3 起 strategies dict 注册 6 个 strategy:
- company_profile      (Slice 2b/3.1,depth-1 平面)
- company_comparison   (Slice 3.2,classifier 直接给 companies,depth-2 单批)
- sector_landscape     (Slice 3.2,depth-2 两步搜索:Level 1 → 玩家解析 → Level 2)
- value_chain          (Slice 3.3,depth-3 两层 bootstrap:拆环节 → 每环节龙头 → mini)
- theme_analysis       (Slice 3.3,depth-3 两层 bootstrap:拆类别 → 每类代表股 → mini)
- 其他                  (Slice 3.0 vanilla 兜底)

depth-2/3 strategies 用 ExplicitQueryResearchConductor 跳过 gpt-researcher 默认的
LLM sub-query 拆解,保留下游 retrieve/scrape/summarize 全套机制。
"""
import logging

from .classifier import ClassificationResult
from .strategies import (
    CompanyComparisonStrategy,
    CompanyProfileStrategy,
    SectorLandscapeStrategy,
    ThemeAnalysisStrategy,
    ValueChainStrategy,
    VanillaStrategy,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """L0-A label → Strategy dispatcher。"""

    def __init__(self, gpt_researcher, filing_finder, extractor):
        """收 InvestmentResearcher 装配好的依赖,实例化各 strategy。

        各 strategy 只接它自己需要的依赖:
        - CompanyProfileStrategy:gpt_researcher + filing_finder + extractor
        - CompanyComparisonStrategy:gpt_researcher + extractor(不调 filing_finder,见 prepare/11 已知限制)
        - SectorLandscapeStrategy:gpt_researcher + extractor(mini mode,跳 filing)
        - ValueChainStrategy / ThemeAnalysisStrategy:gpt_researcher + extractor(mini mode,depth-3)
        - VanillaStrategy:gpt_researcher only
        """
        self.strategies = {
            "company_profile": CompanyProfileStrategy(
                gpt_researcher=gpt_researcher,
                filing_finder=filing_finder,
                extractor=extractor,
            ),
            "company_comparison": CompanyComparisonStrategy(
                gpt_researcher=gpt_researcher,
                extractor=extractor,
            ),
            "sector_landscape": SectorLandscapeStrategy(
                gpt_researcher=gpt_researcher,
                extractor=extractor,
            ),
            "value_chain": ValueChainStrategy(
                gpt_researcher=gpt_researcher,
                extractor=extractor,
            ),
            "theme_analysis": ThemeAnalysisStrategy(
                gpt_researcher=gpt_researcher,
                extractor=extractor,
            ),
            "其他": VanillaStrategy(gpt_researcher=gpt_researcher),
        }

    async def execute(self, classification: ClassificationResult) -> str:
        """按 label dispatch 到对应 strategy。未知 label fallback 到 vanilla。"""
        strategy = self.strategies.get(classification.label)
        if strategy is None:
            # 防御性:未来 label 扩展时,旧 Orchestrator 兼容到 vanilla
            logger.warning(
                f"Orchestrator: unknown label {classification.label!r}, "
                f"fallback to VanillaStrategy"
            )
            strategy = self.strategies["其他"]
        return await strategy.run(classification)
