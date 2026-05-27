"""
Slice 2b schema —— dataclasses。

每个数字字段 (MetricField) 都带 trust_label,代码层面打,不靠 LLM 自报。
"""
from dataclasses import dataclass, field
from typing import Literal, Optional, Union

TrustLabel = Literal["filing", "web", "null"]


@dataclass
class CompanyTarget:
    """简化版 L0 输出:从 query 检出的单一目标公司。"""
    name: str
    ticker: Optional[str] = None


@dataclass
class FilingDoc:
    """L2b 抓到的财报文档。"""
    url: str
    raw_content: str
    report_period: Optional[str] = None  # e.g. "FY2025 Q3"
    doc_type: Optional[str] = None       # "10-Q" / "10-K" / "8-K"


@dataclass
class MetricField:
    """单个数字字段 + 来源标签 + 冲突标记。"""
    value: Optional[Union[float, str]] = None
    unit: Optional[str] = None
    trust_label: TrustLabel = "null"
    source_url: Optional[str] = None
    as_of: Optional[str] = None
    # filing 和 web 同字段值不同时填:{"web_value": ..., "web_source": ...}
    conflict: Optional[dict] = None


@dataclass
class CompanyMetrics:
    """L3 抽取产出 —— 整个公司指标包。"""
    company: CompanyTarget
    filing_retrieved: bool = False
    report_period: Optional[str] = None
    revenue: MetricField = field(default_factory=MetricField)
    yoy_growth: MetricField = field(default_factory=MetricField)  # Slice 3.2: 同比增长率(%);mini 必填字段之一
    gross_margin: MetricField = field(default_factory=MetricField)
    operating_margin: MetricField = field(default_factory=MetricField)
    net_income: MetricField = field(default_factory=MetricField)
    eps: MetricField = field(default_factory=MetricField)
    market_cap: MetricField = field(default_factory=MetricField)
    segment_breakdown: Optional[list] = None
    guidance: Optional[str] = None
