from .b_classifier import FINANCE_WHITELIST, classify_subquery
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
    VanillaStrategy,
)
from .writing_prompts import (
    WRITING_PROMPT_COMPANY_COMPARISON,
    WRITING_PROMPT_COMPANY_PROFILE,
    WRITING_PROMPT_SECTOR_LANDSCAPE,
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
]
