"""L0-A 分类器 —— Slice 3.2 起 4 标签形态。

一次 FAST_LLM 调用,输出 JSON `{label, name?, ticker?, companies?, sector?}`。
按 label 决定哪些 scope 字段填充:
- company_profile        → name + ticker
- company_comparison     → companies: [{name, ticker}, ...](D1 决策,见 prepare/11)
- sector_landscape       → sector
- 其他                   → 无 scope 字段

scope 字段不齐时(LLM 偶尔会漏),降级到 其他(永不拒答)。

替代 Slice 2b 的 `CompanyDetector`(选项 A,见 08 开放问题 1)。
Slice 3.3 将再加 value_chain / theme_analysis 两个标签;同一份 prompt 加分支。
"""
import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

import json_repair

from ..utils.llm import create_chat_completion
from .schema import CompanyTarget

logger = logging.getLogger(__name__)

Label = Literal[
    "company_profile",
    "company_comparison",
    "sector_landscape",
    "其他",
]


@dataclass
class ClassificationResult:
    """L0-A 分类器输出。各 scope 字段按 label 条件填充,其余 None。"""

    label: Label
    # company_profile
    company_name: Optional[str] = None
    ticker: Optional[str] = None
    # company_comparison
    companies: Optional[list[CompanyTarget]] = None
    # sector_landscape
    sector: Optional[str] = None


CLASSIFIER_PROMPT_V1 = """\
You are a router for an investment research assistant. Classify the user query into one of:

- "company_profile": query asks for an in-depth look at ONE specific named US-listed public company
- "company_comparison": query explicitly compares 2-4 specific named companies
- "sector_landscape": query asks to map / overview / landscape / scan ONE specific industry or sector
- "其他": anything else (supply-chain / value-chain / industry-decomposition analysis,
  investment themes / narratives, methodology / education, macro / Fed, stock screening,
  vague or ambiguous queries, etc.)

Output STRICT JSON, no prose, no markdown fence, with this exact schema:
{
  "label": "company_profile" | "company_comparison" | "sector_landscape" | "其他",
  "name": <string|null>,             // only for company_profile: full company name
  "ticker": <string|null>,           // only for company_profile: US ticker symbol
  "companies": <array|null>,         // only for company_comparison: [{"name":..., "ticker":...}, ...]
  "sector": <string|null>            // only for sector_landscape: sector / industry name
}

If a ticker is unknown / not US-listed, set it to null but still include the company.

Examples:
- "Analyze NVIDIA's latest quarterly performance"
  → {"label":"company_profile","name":"NVIDIA Corporation","ticker":"NVDA","companies":null,"sector":null}
- "Tell me about Vertiv Holdings"
  → {"label":"company_profile","name":"Vertiv Holdings","ticker":"VRT","companies":null,"sector":null}
- "Compare NVDA and AMD"
  → {"label":"company_comparison","name":null,"ticker":null,"companies":[{"name":"NVIDIA Corporation","ticker":"NVDA"},{"name":"Advanced Micro Devices","ticker":"AMD"}],"sector":null}
- "Compare NVDA, AMD and INTC in terms of AI chip strategy"
  → {"label":"company_comparison","name":null,"ticker":null,"companies":[{"name":"NVIDIA Corporation","ticker":"NVDA"},{"name":"Advanced Micro Devices","ticker":"AMD"},{"name":"Intel Corporation","ticker":"INTC"}],"sector":null}
- "Give me a landscape of the US EV battery industry"
  → {"label":"sector_landscape","name":null,"ticker":null,"companies":null,"sector":"US EV battery industry"}
- "Overview of the US data center REIT sector"
  → {"label":"sector_landscape","name":null,"ticker":null,"companies":null,"sector":"US data center REIT"}
- "AI 芯片产业链分析"
  → {"label":"其他","name":null,"ticker":null,"companies":null,"sector":null}
- "What is the best approach to learning value investing?"
  → {"label":"其他","name":null,"ticker":null,"companies":null,"sector":null}
- "How is the Fed likely to move rates in 2026?"
  → {"label":"其他","name":null,"ticker":null,"companies":null,"sector":null}
"""


def _parse_companies(raw) -> Optional[list[CompanyTarget]]:
    """从 LLM 输出的 companies 数组里抽 list[CompanyTarget];失败返回 None。"""
    if not isinstance(raw, list) or not raw:
        return None
    out: list[CompanyTarget] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            out.append(
                CompanyTarget(name=str(item["name"]), ticker=item.get("ticker"))
            )
    return out or None


class QueryClassifier:
    """Slice 3.2 L0-A 4 标签分类器。"""

    def __init__(self, cfg):
        self.cfg = cfg

    async def classify(self, query: str) -> ClassificationResult:
        """对原始 query 做 L0-A 分类。

        失败 fallback 永远是 `其他`(永不抛错,永不拒答用户)。
        Scope 字段不齐(LLM 漏填)同样 fallback 到 其他,避免下游 strategy 出错。
        """
        try:
            response = await create_chat_completion(
                messages=[
                    {"role": "system", "content": CLASSIFIER_PROMPT_V1},
                    {"role": "user", "content": query},
                ],
                model=self.cfg.fast_llm_model,
                llm_provider=self.cfg.fast_llm_provider,
                max_tokens=400,
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
            logger.warning(
                f"QueryClassifier non-dict response, fallback to 其他: {response[:200]!r}"
            )
            return ClassificationResult(label="其他")

        label = parsed.get("label")

        if label == "company_profile":
            return ClassificationResult(
                label="company_profile",
                company_name=parsed.get("name"),
                ticker=parsed.get("ticker"),
            )

        if label == "company_comparison":
            companies = _parse_companies(parsed.get("companies"))
            if not companies or len(companies) < 2:
                logger.warning(
                    f"company_comparison without ≥2 companies, fallback to 其他: {parsed!r}"
                )
                return ClassificationResult(label="其他")
            return ClassificationResult(
                label="company_comparison",
                companies=companies,
            )

        if label == "sector_landscape":
            sector = parsed.get("sector")
            if not isinstance(sector, str) or not sector.strip():
                logger.warning(
                    f"sector_landscape without sector, fallback to 其他: {parsed!r}"
                )
                return ClassificationResult(label="其他")
            return ClassificationResult(
                label="sector_landscape",
                sector=sector.strip(),
            )

        # 任何非已知 label 输出都归到 其他(包括 LLM 编出来的奇怪 label)
        return ClassificationResult(label="其他")
