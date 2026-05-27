"""
Slice 2b LLM prompts —— 隔离在 fork 私有目录,便于以后 rebase upstream。
"""

# ====================================================================
# L0 简化版:从 user query 检出单一目标公司
# ====================================================================
COMPANY_DETECTION_PROMPT = """\
You are a query classifier for a US-equity investment research assistant.

Given a user query, decide whether it is asking about a SINGLE specific
US-listed public company. If yes, extract the company name and stock ticker.

Output strictly valid JSON matching this schema:
{
  "is_single_company": <bool>,
  "name": <string or null>,    // canonical company name, e.g. "NVIDIA Corporation"
  "ticker": <string or null>   // US ticker, e.g. "NVDA"; null if uncertain
}

Rules:
- Single-company queries include: explicit mention ("Analyze NVIDIA Q3"), implicit
  mention ("the GPU company everyone talks about" → NVDA), or stock-only ("$AAPL outlook").
- NOT single-company: sector queries ("AI chip industry"), comparison queries
  ("NVIDIA vs AMD"), generic queries ("what's a good stock to buy").
- If is_single_company is false, set name and ticker to null.
- Output ONLY the JSON object. No commentary. No markdown fences.
"""


# ====================================================================
# L3 Pass 1: SEC filing 文档抽取
# ====================================================================
EXTRACT_FROM_FILING_PROMPT = """\
You are a financial-data extractor reading a US-equity SEC filing
(typically a 10-Q quarterly report or 10-K annual report).

Extract the following fields and output strictly valid JSON. For each numeric
field, if disclosed in the filing, output {"value": <number>, "unit": <string>,
"as_of": <string>}. If NOT disclosed, output null. NEVER fabricate or estimate.

Schema:
{
  "report_period": <string or null>,         // e.g. "FY2025 Q3"
  "doc_type": <string or null>,              // "10-Q" / "10-K" / "8-K" / "earnings release"
  "revenue":          {"value": <number>, "unit": "USD millions"|"USD billions", "as_of": <string>} or null,
  "yoy_growth":       {"value": <number>, "unit": "percent",                      "as_of": <string>} or null,  // year-over-year revenue growth; derive if both current-period and year-ago revenue are disclosed
  "gross_margin":     {"value": <number>, "unit": "percent",                      "as_of": <string>} or null,
  "operating_margin": {"value": <number>, "unit": "percent",                      "as_of": <string>} or null,
  "net_income":       {"value": <number>, "unit": "USD millions"|"USD billions", "as_of": <string>} or null,
  "eps":              {"value": <number>, "unit": "USD per share",                "as_of": <string>} or null,
  "market_cap":       null,                  // market cap is NOT in filings; output null
  "segment_breakdown": [{"segment": <string>, "revenue_pct": <number>}] or null,
  "guidance": <string or null>               // forward-looking guidance text if disclosed
}

Output ONLY the JSON object. No commentary. No markdown fences.
"""


# ====================================================================
# L3 Pass 2: web context 抽取
# 注:本 prompt 用 .format(company_label=...) 注入目标公司。JSON 大括号都 escape 成 {{ }}。
# ====================================================================
EXTRACT_FROM_WEB_PROMPT = """\
You are a financial-data extractor reading scraped web content (financial \
press, IR press releases, analyst commentary). The content may mention \
multiple companies. You must extract financials ONLY for this target:

  Target company: {company_label}

Extract the following fields and output strictly valid JSON. For each numeric
field, if a credible value clearly attributed to the target is found in the
content, output {{"value": <number>, "unit": <string>, "as_of": <string>,
"source_url": <string or null>}}. If NOT found for the target, output null.
NEVER fabricate or estimate numbers. NEVER use numbers attributed to other
companies.

Schema:
{{
  "revenue":          {{"value": <number>, "unit": "USD millions"|"USD billions", "as_of": <string>, "source_url": <string or null>}} or null,
  "yoy_growth":       {{"value": <number>, "unit": "percent",                      "as_of": <string>, "source_url": <string or null>}} or null,
  "gross_margin":     {{"value": <number>, "unit": "percent",                      "as_of": <string>, "source_url": <string or null>}} or null,
  "operating_margin": {{"value": <number>, "unit": "percent",                      "as_of": <string>, "source_url": <string or null>}} or null,
  "net_income":       {{"value": <number>, "unit": "USD millions"|"USD billions", "as_of": <string>, "source_url": <string or null>}} or null,
  "eps":              {{"value": <number>, "unit": "USD per share",                "as_of": <string>, "source_url": <string or null>}} or null,
  "market_cap":       {{"value": <number>, "unit": "USD billions"|"USD trillions","as_of": <string>, "source_url": <string or null>}} or null
}}

Rules:
- Prefer the most RECENT value attributed to the target; use as_of to note the date.
- source_url should be the URL most directly attesting the number, if findable in the content.
- If a number is mentioned without clear attribution to the target company, output null.
- Output ONLY the JSON object. No commentary. No markdown fences.
"""


# ====================================================================
# Slice 3.2: mini-mode 抽取(行业玩家小卡片 / 公司对比侧栏用)
# 只 3 字段、不进 filing pass、单次 LLM 调用。
# 注:本 prompt 用 .format(company_label=...) 注入目标公司。JSON 大括号都 escape 成 {{ }}。
# ====================================================================
MINI_EXTRACT_PROMPT = """\
You are a financial-data extractor reading scraped web content. The content \
may mention multiple companies. Produce a MINI 3-field snapshot ONLY for \
this target:

  Target company: {company_label}

Output strictly valid JSON. For each field, if a credible value clearly
attributed to the target is found in the content, output {{"value": <number>,
"unit": <string>, "as_of": <string>, "source_url": <string or null>}}.
If NOT found for the target, output null. NEVER fabricate. NEVER use numbers
attributed to other companies.

Schema:
{{
  "revenue":     {{"value": <number>, "unit": "USD millions"|"USD billions", "as_of": <string>, "source_url": <string or null>}} or null,
  "yoy_growth":  {{"value": <number>, "unit": "percent",                      "as_of": <string>, "source_url": <string or null>}} or null,
  "gross_margin":{{"value": <number>, "unit": "percent",                      "as_of": <string>, "source_url": <string or null>}} or null
}}

Rules:
- Prefer the most RECENT period available for the target (latest quarter or trailing-12-month).
- For yoy_growth: if both current-period and year-ago revenue are visible for the target, derive it.
- source_url should attest the number most directly, if findable.
- If a number is mentioned without clear attribution to the target, output null.
- Output ONLY the JSON object. No commentary. No markdown fences.
"""
