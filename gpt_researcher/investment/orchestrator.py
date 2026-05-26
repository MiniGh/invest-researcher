"""L1 树编排器(骨架) —— Slice 3.1 depth-1 起步形态。

按 L0-A classification.label dispatch 到对应 Strategy 实例。
Slice 3.1 只支持 company_profile 和 其他 两个 strategy;Slice 3.2 / 3.3
加多 strategy 时只需在 strategies dict 里注册(以及在 strategies/ 下新建文件)。

Slice 3.1 阶段 L1 只是"strategy 选择器 + 调用器"。Slice 3.2+ 引入
bootstrap-then-expand 时,L1 会承担更多职责(管 sub-query 树展开 +
bottom-up summarization),strategies 则提供拆解策略和 leaf 逻辑。
"""
import logging

from .classifier import ClassificationResult
from .strategies import CompanyProfileStrategy, VanillaStrategy

logger = logging.getLogger(__name__)


class Orchestrator:
    """L0-A label → Strategy dispatcher。"""

    def __init__(self, gpt_researcher, filing_finder, extractor):
        """收 InvestmentResearcher 装配好的依赖,实例化各 strategy。

        各 strategy 只接它自己需要的依赖:
        - CompanyProfileStrategy 需要 gpt_researcher + filing_finder + extractor
        - VanillaStrategy 只需要 gpt_researcher
        """
        self.strategies = {
            "company_profile": CompanyProfileStrategy(
                gpt_researcher=gpt_researcher,
                filing_finder=filing_finder,
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
