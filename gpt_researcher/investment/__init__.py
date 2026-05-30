from .b_classifier import FINANCE_WHITELIST, classify_subquery
from .bootstrap_parsers import parse_company_list, parse_string_list
from .classifier import ClassificationResult, QueryClassifier
from .explicit_research_conductor import (
    ExplicitQueryResearchConductor,
    run_query_batch,
)
from .orchestrator import Orchestrator
from .researcher import InvestmentResearcher
from .retriever import InvestmentTavilySearch
from .schema import (
    CompanyMetrics,
    CompanyTarget,
    FilingDoc,
    MetricField,
    TrustLabel,
)
from .strategies import (
    CompanyComparisonStrategy,
    CompanyProfileStrategy,
    SectorLandscapeStrategy,
    ThemeAnalysisStrategy,
    ValueChainStrategy,
    VanillaStrategy,
)
from .writing_prompts import (
    WRITING_PROMPT_COMPANY_COMPARISON,
    WRITING_PROMPT_COMPANY_PROFILE,
    WRITING_PROMPT_SECTOR_LANDSCAPE,
    WRITING_PROMPT_THEME_ANALYSIS,
    WRITING_PROMPT_VALUE_CHAIN,
)

__all__ = [
    # Slice 1 / 2b
    "InvestmentResearcher",
    "CompanyTarget",
    "FilingDoc",
    "MetricField",
    "CompanyMetrics",
    "TrustLabel",
    # Slice 3.0
    "QueryClassifier",
    "ClassificationResult",
    "classify_subquery",
    "FINANCE_WHITELIST",
    "InvestmentTavilySearch",
    "WRITING_PROMPT_COMPANY_PROFILE",
    # Slice 3.1
    "Orchestrator",
    "CompanyProfileStrategy",
    "VanillaStrategy",
    # Slice 3.2
    "ExplicitQueryResearchConductor",
    "run_query_batch",
    "CompanyComparisonStrategy",
    "SectorLandscapeStrategy",
    "WRITING_PROMPT_COMPANY_COMPARISON",
    "WRITING_PROMPT_SECTOR_LANDSCAPE",
    # Slice 3.3
    "parse_string_list",
    "parse_company_list",
    "ValueChainStrategy",
    "ThemeAnalysisStrategy",
    "WRITING_PROMPT_VALUE_CHAIN",
    "WRITING_PROMPT_THEME_ANALYSIS",
]
