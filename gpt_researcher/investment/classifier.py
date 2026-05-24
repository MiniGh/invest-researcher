"""L0-A 分类器 —— Slice 3.0 binary 起步(`company_profile` / `其他`)。

一次 FAST_LLM 调用,输出 JSON `{label, name?, ticker?}`。
- company_profile:用户在问一家具体的美股上市公司 → 走 Slice 2b 完整子例程
- 其他:其余一切(多公司对比 / 行业 / 产业链 / 主题 / 学习方法论 / 宏观 / ...)
        → 透传 vanilla GPTResearcher,不进任何特殊管线

Slice 3.2 / 3.3 扩 5 全标签时,本文件的 prompt 加分支 + ClassificationResult 加字段,
其他模块基本不动。

替代 Slice 2b 的 `CompanyDetector`(选项 A,见 08 开放问题 1)。
"""
import logging
from dataclasses import dataclass
from typing import Literal, Optional

import json_repair

from ..utils.llm import create_chat_completion

logger = logging.getLogger(__name__)

Label = Literal["company_profile", "其他"]


@dataclass
class ClassificationResult:
    """L0-A 分类器输出。"""

    label: Label
    company_name: Optional[str] = None  # 仅 label=company_profile 时填
    ticker: Optional[str] = None         # 仅 label=company_profile 时填


# Few-shot 样例特意覆盖:
# - "多公司对比" → 其他(Slice 3.0 暂不分流,等 3.2 上 company_comparison)
# - "产业链 / 行业" → 其他(同上,等 3.3)
# 这样 LLM 不会误把这些 query 归到 company_profile(避免对它们错跑 detector/filing/extractor)。
CLASSIFIER_PROMPT_V1 = """\
You are a router for an investment research assistant. Classify the user query into one of:
- "company_profile": query is specifically about a single named US-listed public company
- "其他": anything else (industry / supply chain analysis, methodology / education,
  macro / Fed, multi-company comparison, stock screening, etc.)

If "company_profile", extract the company's name and ticker symbol (if mentioned or inferable).

Return STRICT JSON, no prose, no markdown fence:
{"label": "company_profile" | "其他", "name": <string|null>, "ticker": <string|null>}

Examples:
- "Analyze NVIDIA's latest quarterly performance"
  → {"label":"company_profile","name":"NVIDIA Corporation","ticker":"NVDA"}
- "What is the best approach to learning value investing?"
  → {"label":"其他","name":null,"ticker":null}
- "Compare NVDA and AMD"
  → {"label":"其他","name":null,"ticker":null}
- "AI 芯片产业链分析"
  → {"label":"其他","name":null,"ticker":null}
- "How is the EV market looking in 2026?"
  → {"label":"其他","name":null,"ticker":null}
- "Tell me about Vertiv Holdings"
  → {"label":"company_profile","name":"Vertiv Holdings","ticker":"VRT"}
"""


class QueryClassifier:
    """Slice 3.0 L0-A 二分类器。"""

    def __init__(self, cfg):
        self.cfg = cfg

    async def classify(self, query: str) -> ClassificationResult:
        """对原始 query 做 L0-A 分类。

        失败 fallback 永远是 `其他`(永不抛错,永不拒答用户)。
        """
        try:
            response = await create_chat_completion(
                messages=[
                    {"role": "system", "content": CLASSIFIER_PROMPT_V1},
                    {"role": "user", "content": query},
                ],
                model=self.cfg.fast_llm_model,
                llm_provider=self.cfg.fast_llm_provider,
                max_tokens=200,
                llm_kwargs=self.cfg.llm_kwargs,
            )
        except Exception as e:
            logger.warning(f"QueryClassifier LLM call failed, fallback to 其他: {e}")
            return ClassificationResult(label="其他")

        try:
            parsed = json_repair.loads(response)
        except Exception as e:
            logger.warning(
                f"QueryClassifier JSON parse failed, fallback to 其他: {e}; raw={response[:200]!r}"
            )
            return ClassificationResult(label="其他")

        if not isinstance(parsed, dict):
            logger.warning(f"QueryClassifier non-dict response, fallback to 其他: {response[:200]!r}")
            return ClassificationResult(label="其他")

        label = parsed.get("label")
        if label == "company_profile":
            return ClassificationResult(
                label="company_profile",
                company_name=parsed.get("name"),
                ticker=parsed.get("ticker"),
            )
        # 任何非 company_profile 输出都归到 其他(包括 LLM 编出来的奇怪 label)
        return ClassificationResult(label="其他")
