"""Investment research strategies(per L0-A label)。

Slice 3.1 起步:company_profile + vanilla(兜底)。
Slice 3.2 / 3.3 会加入:sector_landscape / company_comparison / value_chain / theme_analysis。
"""
from .company_profile import CompanyProfileStrategy
from .vanilla import VanillaStrategy

__all__ = [
    "CompanyProfileStrategy",
    "VanillaStrategy",
]
