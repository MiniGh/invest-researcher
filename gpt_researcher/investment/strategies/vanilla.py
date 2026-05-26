"""VanillaStrategy —— L0-A label = 其他 时的兜底策略。

透传 vanilla gpt-researcher,不进任何特殊管线(无白名单 / 无 detector / 无 extractor)。
投研 persona 仍然保留(已在 InvestmentResearcher.__init__ 预填给 GPTResearcher)。

逻辑搬自 Slice 3.0 InvestmentResearcher._run_other_path,功能不变。
"""
import logging

from gpt_researcher.actions import stream_output

from ..classifier import ClassificationResult

logger = logging.getLogger(__name__)


class VanillaStrategy:
    def __init__(self, gpt_researcher):
        self.gpt_researcher = gpt_researcher

    async def _log(self, message: str) -> None:
        try:
            ws = getattr(self.gpt_researcher, "websocket", None)
            await stream_output("logs", "vanilla_strategy", message, ws)
        except Exception as e:
            logger.warning(f"_log via stream_output failed: {e}")

    async def run(self, classification: ClassificationResult) -> str:
        # 切回原生 tavily,关掉 per-sub-query L0-B 决策
        # InvestmentResearcher.__init__ 默认把 retriever 切成 investment_tavily;
        # 兜底路径下需要回到 vanilla(不套白名单,全网搜)
        self.gpt_researcher.cfg.retriever = "tavily"
        await self._log("ℹ️ 走 其他 兜底:vanilla gpt-researcher,无白名单 / 无子例程")

        await self.gpt_researcher.conduct_research()
        # 不传 custom_prompt:沿用 gpt-researcher 默认报告 prompt
        return await self.gpt_researcher.write_report()
