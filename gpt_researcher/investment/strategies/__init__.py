"""Investment research strategies(per L0-A label)。

Slice 3.1 起步:company_profile + vanilla(兜底)。
Slice 3.2 加 sector_landscape + company_comparison(depth-2)。
Slice 3.3 计划再加:value_chain / theme_analysis(depth-3)。
"""
from .company_comparison import CompanyComparisonStrategy
from .company_profile import CompanyProfileStrategy
from .sector_landscape import SectorLandscapeStrategy
from .vanilla import VanillaStrategy

__all__ = [
    "CompanyProfileStrategy",
    "CompanyComparisonStrategy",
    "SectorLandscapeStrategy",
    "VanillaStrategy",
]
