"""L0-A 分类器 —— Slice 3.3 起 6 标签形态(5+1)。

一次 FAST_LLM 调用,输出 JSON `{label, name?, ticker?, companies?, sector?, industry?, theme?}`。
按 label 决定哪些 scope 字段填充:
- company_profile        → name + ticker
- company_comparison     → companies: [{name, ticker}, ...](D1 决策,见 prepare/11)
- sector_landscape       → sector     (行业横切扫描)
- value_chain            → industry   (产业链纵切,Slice 3.3)
- theme_analysis         → theme      (主题/赛道受益,Slice 3.3)
- 其他                   → 无 scope 字段

scope 字段不齐时(LLM 偶尔会漏),降级到 其他(永不拒答)。

替代 Slice 2b 的 `CompanyDetector`(选项 A,见 08 开放问题 1)。
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
    "value_chain",
    "theme_analysis",
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
    # value_chain
    industry: Optional[str] = None
    # theme_analysis
    theme: Optional[str] = None


CLASSIFIER_PROMPT_V1 = """\
You are a router for an investment research assistant. Classify the user query into one of:

- "company_profile": query asks for an in-depth look at ONE specific named US-listed public company
- "company_comparison": query explicitly compares 2-4 specific named companies
- "sector_landscape": HORIZONTAL scan of ONE industry/sector — map / overview / landscape /
  who are the players. Output is "the field and its leading companies side by side".
- "value_chain": VERTICAL decomposition of an industry's value/supply chain — upstream →
  midstream → downstream, segment economics, chokepoints, who dominates each link.
  Triggers: "value chain", "supply chain", "产业链", "链路", "upstream/downstream", "环节".
- "theme_analysis": an investment THEME / narrative and WHO BENEFITS from it — the thesis,
  the categories of beneficiaries by mechanism of exposure, the leveraged stocks.
  Triggers: "theme", "narrative", "赛道", "受益", "beneficiaries", "winners of ...", "谁受益".
- "其他": anything else (methodology / education, macro / Fed, stock screening,
  vague or ambiguous queries, etc.)

Discriminating the three industry-level labels:
- "landscape of the EV battery industry"      → sector_landscape (横切:扫这个行业)
- "value chain of the EV battery industry"    → value_chain      (纵切:拆上中下游)
- "who benefits from the EV adoption theme"   → theme_analysis   (主题:找受益方)

Output STRICT JSON, no prose, no markdown fence, with this exact schema:
{
  "label": "company_profile" | "company_comparison" | "sector_landscape" | "value_chain" | "theme_analysis" | "其他",
  "name": <string|null>,             // only for company_profile: full company name
  "ticker": <string|null>,           // only for company_profile: US ticker symbol
  "companies": <array|null>,         // only for company_comparison: [{"name":..., "ticker":...}, ...]
  "sector": <string|null>,           // only for sector_landscape: sector / industry name
  "industry": <string|null>,         // only for value_chain: the industry whose value chain to decompose
  "theme": <string|null>             // only for theme_analysis: the investment theme / narrative
}

If a ticker is unknown / not US-listed, set it to null but still include the company.

Examples:
- "Analyze NVIDIA's latest quarterly performance"
  → {"label":"company_profile","name":"NVIDIA Corporation","ticker":"NVDA","companies":null,"sector":null,"industry":null,"theme":null}
- "Tell me about Vertiv Holdings"
  → {"label":"company_profile","name":"Vertiv Holdings","ticker":"VRT","companies":null,"sector":null,"industry":null,"theme":null}
- "Compare NVDA and AMD"
  → {"label":"company_comparison","name":null,"ticker":null,"companies":[{"name":"NVIDIA Corporation","ticker":"NVDA"},{"name":"Advanced Micro Devices","ticker":"AMD"}],"sector":null,"industry":null,"theme":null}
- "Give me a landscape of the US EV battery industry"
  → {"label":"sector_landscape","name":null,"ticker":null,"companies":null,"sector":"US EV battery industry","industry":null,"theme":null}
- "Overview of the US data center REIT sector"
  → {"label":"sector_landscape","name":null,"ticker":null,"companies":null,"sector":"US data center REIT","industry":null,"theme":null}
- "Analyze the value chain of the US semiconductor industry from upstream to downstream"
  → {"label":"value_chain","name":null,"ticker":null,"companies":null,"sector":null,"industry":"US semiconductor industry","theme":null}
- "AI 芯片产业链分析"
  → {"label":"value_chain","name":null,"ticker":null,"companies":null,"sector":null,"industry":"AI 芯片产业链","theme":null}
- "Which US-listed stocks are the biggest beneficiaries of the AI infrastructure investment theme?"
  → {"label":"theme_analysis","name":null,"ticker":null,"companies":null,"sector":null,"industry":null,"theme":"AI infrastructure"}
- "Who wins from the GLP-1 weight-loss drug boom?"
  → {"label":"theme_analysis","name":null,"ticker":null,"companies":null,"sector":null,"industry":null,"theme":"GLP-1 weight-loss drugs"}
- "What is the best approach to learning value investing?"
  → {"label":"其他","name":null,"ticker":null,"companies":null,"sector":null,"industry":null,"theme":null}
- "How is the Fed likely to move rates in 2026?"
  → {"label":"其他","name":null,"ticker":null,"companies":null,"sector":null,"industry":null,"theme":null}
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

        if label == "value_chain":
            industry = parsed.get("industry")
            if not isinstance(industry, str) or not industry.strip():
                logger.warning(
                    f"value_chain without industry, fallback to 其他: {parsed!r}"
                )
                return ClassificationResult(label="其他")
            return ClassificationResult(
                label="value_chain",
                industry=industry.strip(),
            )

        if label == "theme_analysis":
            theme = parsed.get("theme")
            if not isinstance(theme, str) or not theme.strip():
                logger.warning(
                    f"theme_analysis without theme, fallback to 其他: {parsed!r}"
                )
                return ClassificationResult(label="其他")
            return ClassificationResult(
                label="theme_analysis",
                theme=theme.strip(),
            )

        # 任何非已知 label 输出都归到 其他(包括 LLM 编出来的奇怪 label)
        return ClassificationResult(label="其他")
