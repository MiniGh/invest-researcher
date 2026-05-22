"""
CompanyDetector —— 简化版 L0,只识别"this is a single-company query"。

非单公司 query 返回 None,InvestmentResearcher.run() 会跳过 L2b+L3。
失败时(LLM 报错 / JSON 解析失败)也返回 None,fallback 到 Slice 1 行为。
"""
import logging
from typing import Optional

import json_repair

from ..utils.llm import create_chat_completion
from .prompts import COMPANY_DETECTION_PROMPT
from .schema import CompanyTarget

logger = logging.getLogger(__name__)


class CompanyDetector:
    def __init__(self, cfg):
        self.cfg = cfg

    async def detect(self, query: str) -> Optional[CompanyTarget]:
        """从 user query 检出单一目标公司。None = 非单公司 query / 检测失败。"""
        if not query or not query.strip():
            return None

        try:
            response = await create_chat_completion(
                messages=[
                    {"role": "system", "content": COMPANY_DETECTION_PROMPT},
                    {"role": "user", "content": query},
                ],
                model=self.cfg.fast_llm_model,
                llm_provider=self.cfg.fast_llm_provider,
                max_tokens=200,
                llm_kwargs=self.cfg.llm_kwargs,
            )
        except Exception as e:
            logger.warning(f"CompanyDetector: LLM call failed: {e}")
            return None

        try:
            parsed = json_repair.loads(response)
        except Exception as e:
            logger.warning(f"CompanyDetector: JSON parse failed: {e}; raw={response[:200]!r}")
            return None

        if not isinstance(parsed, dict):
            logger.warning(f"CompanyDetector: non-dict response: {response[:200]!r}")
            return None

        if not parsed.get("is_single_company"):
            return None

        name = parsed.get("name")
        if not name or not isinstance(name, str):
            return None

        ticker = parsed.get("ticker")
        if ticker is not None and not isinstance(ticker, str):
            ticker = None

        return CompanyTarget(name=name.strip(), ticker=ticker.strip() if ticker else None)
