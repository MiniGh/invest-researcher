"""Per-label writing prompt 变体 —— L4 塌缩形态的具体实现。

Slice 3.0 只做 `company_profile` 一份;Slice 3.2 / 3.3 逐步补足
`sector_landscape` / `company_comparison` / `value_chain` / `theme_analysis`。

通过 `write_report(custom_prompt=...)` 的原生接口注入,不需要改 gpt-researcher 核心。

设计原则(为什么 prompt 是这样写的):
- 英文:LLM 处理英文 token 更高效,且报告本身就是英文(LANGUAGE=english)
- 6 段固定骨架:跟 Slice 2b extractor 的指标 schema 对齐(business/financials/competitive/
  catalysts/risks/outlook),context 注入的 metrics markdown 自然落到 financials 段
- 引用纪律:强约束"数字必须来自 context + 内联 source URL",防止 LLM 编数字
- 拒答策略:"Data not available" 强于 LLM 凭直觉补充,跟项目 honesty boundary 一致
- 长度 1000-1500 词:给 SMART_LLM 充分空间但不至于跑偏
"""

WRITING_PROMPT_COMPANY_PROFILE = """\
You are writing a fundamental analysis report on a single US-listed public company.
Structure the report into the following six sections, in this exact order:

1. **Business overview** — what the company does, core product lines and customer
   segments, business model essentials.

2. **Recent financial performance** — latest quarterly results. Cite exact numbers
   with their sources from the provided context using inline markdown links
   (e.g., "revenue of $X [10-Q](https://...)"). If filing-source numbers conflict
   with web-source numbers for the same metric, surface both with attribution.

3. **Competitive position** — key competitors (name them), market share if
   available, moats / differentiators / structural advantages.

4. **Catalysts and recent developments** — product launches, M&A, guidance
   changes, earnings calls, regulatory events. For each, briefly state the
   potential investment thesis impact.

5. **Risks and headwinds** — execution / competitive / regulatory / macro.
   Be specific rather than generic.

6. **Forward outlook** — company guidance, analyst consensus (if found in
   context), key upcoming milestones or catalysts.

Hard constraints:
- Use figures ONLY when they appear in the provided context. Never fabricate
  numbers. Never extrapolate to numbers not explicitly stated.
- When citing a number, include the source URL inline (markdown link).
- If a section has insufficient data in the provided context, write
  "Data not available in current sources" rather than guessing or filling
  with generic prose.
- Avoid hype, PR language, and price predictions.
- Total length target: 1000-1500 words.
- Write in clear, analytical, professional prose. Section headings as H2.
"""

WRITING_PROMPT_COMPANY_COMPARISON = """\
You are writing a comparative analysis report on 2-4 US-listed public companies.
Build the report around an aligned comparison matrix; each company contributes
one column (or one row block, your choice), and every dimension is compared
side-by-side. If a number is missing for one company, write "N/A" in that
cell — do NOT skip the row.

Structure:

1. **Comparison snapshot** — a markdown table comparing the companies on:
   - Core business / product portfolio (one-line each)
   - Latest quarterly revenue, gross margin, operating margin, net income, EPS
   - Year-over-year revenue growth
   - Market cap (or P/E, P/S, where available)
   - Market share within their shared industry (if applicable)
   - Forward guidance / analyst consensus
   When a value is missing in the provided context, put "N/A" (not "—" and not
   a fabricated number). When numbers come from different reporting periods,
   note the as-of date in the cell.

2. **Cross-dimension reading** — analytical paragraphs (NOT just rephrased table).
   For each major dimension, what does the comparison reveal? Who is leading
   on revenue scale, who on margins, who on growth, who on valuation? Where
   do the businesses actually differ in strategy / customer mix / moat?

3. **Catalysts diverging the companies** — 2-4 paragraphs on near-term events
   (launches, M&A, guidance changes) that could move them differently.

4. **Risks unique to each company** — bullet list per company; only company-
   specific risks (skip generic industry risks).

5. **Verdict by lens** — short paragraphs answering "who looks better if you
   prioritize {growth | profitability | valuation | scale | optionality}".
   Do NOT give a single buy/sell recommendation.

Hard constraints:
- Use figures ONLY when they appear in the provided context. Never fabricate.
- For every cited number, include the source URL inline (markdown link).
- "N/A" is mandatory for missing cells — do not silently drop dimensions.
- Avoid hype, PR language, and price predictions.
- Total length target: 1200-1800 words.
- Section headings as H2; table headers as required by markdown.
"""


WRITING_PROMPT_SECTOR_LANDSCAPE = """\
You are writing a landscape report on a single US industry / sector. The
provided context is gathered in two layers:
  - Layer 1: macro view of the sector (market size, drivers, headwinds,
    competitive dynamics, mention of top public companies)
  - Layer 2: per-player content — for each leading US-listed company, a
    paragraph on its role within the sector plus a structured mini-snapshot
    (revenue / yoy growth / gross margin) rendered as a markdown table
    (look for "## 📊 ... — 关键财务指标" sub-headers in the input)

Use ALL 6 sections by default. Skip section 5 ONLY when there is literally
not a single named US-listed company anywhere in the context (that should
be rare; the upstream pipeline already filters for US-listed players).

Structure:

1. **Market size and growth trajectory** — current TAM/SAM if available,
   historical growth rate, forecast trajectory. Cite numbers with inline
   source URLs.

2. **Key demand drivers and tailwinds** — what's pulling the sector forward.

3. **Key headwinds and risks** — supply / regulatory / macro / technological
   substitution risks. Be specific.

4. **Competitive dynamics** — fragmentation vs consolidation, M&A activity,
   pricing power, switching costs at industry level.

5. **Representative player snapshots** — for each top player surfaced in
   Layer 2 (typically 3-5 US-listed companies), write a sub-section. Use H3
   heading "### {{Company name}} ({{ticker}})". Each sub-section MUST cover:
   - **Role within the sector**: where in the value chain, what they sell,
     how their exposure compares to the rest of the sector. Write this
     paragraph from the Layer 2 context — it is almost always present.
   - **Financial snapshot**: revenue / yoy growth / gross margin, citing
     inline source URLs. If a specific number is missing, write
     "Data not available in current sources" for that field only; do not
     skip the snapshot section.
   This section is REQUIRED whenever any Layer 2 player content exists.
   Do not summarize the players in the table and then omit per-player
   paragraphs — readers want the qualitative paragraph for each.

6. **Forward outlook** — sector-level milestones, regulatory calendar,
   structural inflection points.

Hard constraints:
- Use figures ONLY when they appear in the provided context. Never fabricate.
- For every cited number, include the source URL inline (markdown link).
- Keep section numbering exactly as 1, 2, 3, 4, 5, 6 above. Do not renumber
  Forward Outlook as Section 5 even if you choose to skip player snapshots
  — keep its number 6 in the output.
- Avoid hype, PR language, and price predictions.
- Total length target: 1500-2200 words.
- Section headings as H2; player cards as H3 inside section 5.
"""


# 未来扩展点(Slice 3.3 上完 5 全标签时填):
# WRITING_PROMPT_VALUE_CHAIN = """..."""           # 产业链纵切
# WRITING_PROMPT_THEME_ANALYSIS = """..."""        # 主题/赛道
