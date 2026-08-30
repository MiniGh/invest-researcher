"""Per-label writing prompt 变体 —— L4 塌缩形态的具体实现。

Slice 3.0 只做 `company_profile` 一份;Slice 3.2 / 3.3 逐步补足
`sector_landscape` / `company_comparison` / `value_chain` / `theme_analysis`。

通过 `write_report(custom_prompt=...)` 的原生接口注入,不需要改 gpt-researcher 核心。

设计原则(为什么 prompt 是这样写的):
- 英文:LLM 处理英文 token 更高效,且报告本身就是英文(LANGUAGE=english)
- 固定骨架:跟 Slice 2b extractor 的指标 schema 对齐(business/financials/competitive/
  catalysts/risks/outlook),context 注入的 metrics markdown 自然落到 financials 段
- 引用纪律:强约束"数字必须来自 context + 内联 source URL",防止 LLM 编数字
- 拒答策略:"Data not available" 强于 LLM 凭直觉补充,跟项目 honesty boundary 一致

Slice E 依据「五类报告 vs 券商研报」的信息完整性对照结果,新增了四条跨模板
规则(见下方共享片段):开篇摘要与结论式标题、非美股主要厂商的写入与标注、
社交媒体不得作为数字来源与独立来源数披露、第三方评级与目标价的转述纪律。
"""

# ---------------------------------------------------------------------------
# 五套模板共用的片段。抽出来集中维护,避免五份拷贝各自漂移。
#
# 注意:本文件的模板正文里含有字面花括号(例如 "{growth | profitability | ...}"
# 与 "### {{segment}}"),因此模板一律用字符串拼接组合,不能改写成 f-string。
# ---------------------------------------------------------------------------

# 开篇摘要 + 结论式章节标题。
#
# 对照发现:五份报告全部没有开篇摘要,读者需要读完全文才知道结论;而参考研报
# 无一例外把结论放在第一屏。章节标题方面,现有模板产出的是通用名词槽位
# ("Business overview" / "Competitive position"),换成任何一家公司都成立;
# 参考研报的标题本身就是判断("DRAM 及 HBM 呈三足鼎立之势,NAND 格局更为分散"),
# 因此它的目录即是一份摘要。
#
# 结论式标题的风险是证据不足时模型硬凑判断,所以附了退回名词标题的兜底。
_SUMMARY_AND_HEADINGS = """
Before section 1, add a section titled "Summary" as an H2 heading (`## Summary`).
Bold text is not a heading — use `##`:
- 4-6 sentences stating your actual findings — not a description of what the
  report will cover.
- Include at least one point that cuts against the positive case.
- Every claim here must be supported by material in the body. Do not introduce
  a fact that appears nowhere else in the report.

Write section headings that state a finding rather than name a topic — for
example "Memory pricing is the single swing factor in FY2026 earnings" rather
than "Pricing". A heading of this kind is a claim, so it must be one the
section's own cited data supports. Where the context does not support a
defensible claim for a section, use a plain descriptive heading instead of
asserting something you cannot evidence.
"""

# 非美股主要厂商的处理。
#
# 对照发现:HBM 行业报告里,占供给约 79% 的 SK 海力士与三星因非美股被挡在正文
# 之外,只出现在一句脚注中,结果整份"行业图景"实际只写了采购方。产业链报告同理,
# 被漏掉的非美股供应商恰恰是美股公司的直接竞争者与替代者。
#
# 结论:行业分析层面写全,可投资性单独标注。
_NON_US_PLAYERS = """
Name the leading players in this market even when they are not US-listed —
Samsung Electronics, SK hynix, and companies listed only in Asia or Europe are
frequently the largest suppliers in their industry. Excluding them distorts the
competitive picture: a sector whose biggest suppliers are missing reads as if
its buyers were the whole industry. On first mention, mark such a company
"not US-listed — outside the investable scope of this report". Keep it out of
any list presented as investable, and do not give it a financial snapshot card;
snapshot cards remain US-listed only.
"""

# 全部五套模板共用的硬约束。
#
# 其中三条与改动前逐字相同(数字必须来自 context / 内联 source URL / 避免炒作
# 语言);另外三条是 Slice E 新增:
#
#   - 社交媒体不得作为数字来源:实测有报告用 instagram.com 的短视频支撑
#     "中东局势推高能耗成本" 这一论断。
#   - 独立来源数披露:实测有报告 91% 的引用回溯到同一个域名,而"逐数字挂链接"
#     这个正确做法反而把来源单一这件事盖住了。
#   - 第三方评级与目标价:改动前写的是 "Avoid ... price predictions",模型将其
#     理解为"不要自己预测",于是转述他人共识不算违规,目标价照样进了报告
#     (在三份报告中重复出现)。现在把"自己产出"与"转述他人"分开写:前者禁止,
#     后者允许但必须标注机构与日期、与现价矛盾时须点明、且不得作为自身结论的
#     前提。实测案例:引用的共识目标价上限 $536,而当时市价已达 $963。
_SHARED_CONSTRAINTS = """\
- Use figures ONLY when they appear in the provided context. Never fabricate.
- For every cited number, include the source URL inline (markdown link).
- Do not use social media posts, video descriptions, or user-generated forum
  content as the source for any figure — this includes x.com, instagram.com,
  youtube.com, reddit.com, and personal blog or newsletter posts. Where a
  figure is available only from such a source, either omit it or state plainly
  that it is unverified, and do not build an argument on it.
- Directly beneath the report title, add a single line of the form
  "Sources: N independent domains." N is the number of distinct domains you
  actually cite. If one domain supplies more than half of your cited figures,
  name that domain on the same line.
- Third-party analyst ratings, price targets, and valuation verdicts
  ("undervalued", "outperform", sector rankings) may be reported as facts about
  market expectations, but ONLY written in this exact form:
      <Institution> (<as-of date>): <rating or verdict>, target <value>
  Worked examples:
      Tickeron (2026-08-14): Strong Buy consensus, average 12-month target $1,250
      Morningstar (2026-05-01): utilities sector 5% undervalued
  Where the context gives no date for the figure, fill the date slot with
  "date not stated in source" — never drop the slot. Where the context names no
  institution, do not report the figure at all.
- Immediately after any such figure, state whether other data in the context
  contradicts it — for instance where the market price already exceeds the
  quoted target. Write either "consistent with current price" or the specific
  conflict. Do not leave this unstated.
- Never use a third-party rating or target as a premise for a conclusion of your
  own. Report what others expect; do not adopt their view as the report's view.
- Never produce a price target, rating, or buy/sell call of your own.
- Avoid hype and PR language.
"""

_HARD_CONSTRAINTS_HEADER = "\nHard constraints:\n"


WRITING_PROMPT_COMPANY_PROFILE = (
    """\
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
"""
    + _SUMMARY_AND_HEADINGS
    + _NON_US_PLAYERS
    + _HARD_CONSTRAINTS_HEADER
    + _SHARED_CONSTRAINTS
    + """\
- Never extrapolate to numbers not explicitly stated.
- If a section has insufficient data in the provided context, write
  "Data not available in current sources" rather than guessing or filling
  with generic prose.
- Total length target: 1000-1500 words.
- Write in clear, analytical, professional prose. Section headings as H2.
"""
)


WRITING_PROMPT_COMPANY_COMPARISON = (
    """\
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
"""
    + _SUMMARY_AND_HEADINGS
    + _HARD_CONSTRAINTS_HEADER
    + _SHARED_CONSTRAINTS
    + """\
- "N/A" is mandatory for missing cells — do not silently drop dimensions.
- Total length target: 1200-1800 words.
- Section headings as H2; table headers as required by markdown.
"""
)


WRITING_PROMPT_SECTOR_LANDSCAPE = (
    """\
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
"""
    + _SUMMARY_AND_HEADINGS
    + _NON_US_PLAYERS
    + _HARD_CONSTRAINTS_HEADER
    + _SHARED_CONSTRAINTS
    + """\
- Keep section numbering exactly as 1, 2, 3, 4, 5, 6 above. Do not renumber
  Forward Outlook as Section 5 even if you choose to skip player snapshots
  — keep its number 6 in the output.
- Total length target: 1500-2200 words.
- Section headings as H2; player cards as H3 inside section 5.
"""
)


WRITING_PROMPT_VALUE_CHAIN = (
    """\
You are writing a value-chain (vertical) analysis of a single US industry. The
provided context is gathered in three layers:
  - Layer 1: a decomposition of the industry value chain into segments
    (upstream / midstream / downstream). Look for a line
    "## Value-chain segments identified: ..." listing the segments.
  - Layer 2: per-segment content — segment economics (value capture, margin
    profile, capital intensity), bottlenecks / chokepoints / supply constraints.
  - Layer 3: per-segment representative US-listed companies, each with a
    structured mini-snapshot (revenue / yoy growth / gross margin) rendered as
    a markdown table under "### {{segment}} — representative companies".

Structure the report by walking DOWN the value chain, segment by segment:

1. **Value-chain map** — lay out the segments in order (upstream → midstream →
   downstream) and what each does. Use the segment list from Layer 1 as the
   skeleton. One paragraph or a compact table.

2. **Segment-by-segment analysis** — for EACH identified segment, an H2/H3
   sub-section that MUST cover:
   - **Economics**: value capture, margin profile, capital intensity — cite
     numbers with inline source URLs.
   - **Bottlenecks / chokepoints**: where supply is constrained or where one
     player holds pricing power.
   - **Representative companies**: for each US-listed leader surfaced in Layer 3,
     write the company's role in this segment plus its financial snapshot
     (revenue / yoy growth / gross margin, inline source URLs). If a specific
     number is missing, write "Data not available in current sources" for that
     field only; do not skip the company. If a segment has NO US-listed leader
     in the context (common for raw-material / foreign-dominated links), say so
     in one line and keep the qualitative economics/bottleneck text — do not
     drop the segment.

3. **Where value concentrates** — across the whole chain, which segment(s)
   capture the most economic profit and why (chokepoints, IP, scale, switching
   costs). This is the analytical payoff of a value-chain study.

4. **Forward outlook** — structural shifts, reshoring / vertical-integration
   moves, regulatory or technological inflection points that could re-route
   value between segments.
"""
    + _SUMMARY_AND_HEADINGS
    + _NON_US_PLAYERS
    + _HARD_CONSTRAINTS_HEADER
    + _SHARED_CONSTRAINTS
    + """\
- Keep section numbering exactly as 1, 2, 3, 4 above.
- Cover EVERY segment from the Layer 1 list; do not silently drop a segment
  just because its company cards are sparse.
- Total length target: 1600-2400 words.
- Section headings as H2; per-segment blocks and company cards as H3.
"""
)


WRITING_PROMPT_THEME_ANALYSIS = (
    """\
You are writing a thematic (investment-narrative) analysis of a single theme.
The provided context is gathered in three layers:
  - Layer 1: the driving narrative + catalysts, time horizon + milestones, and
    thesis-invalidating risks of the theme.
  - Layer 2: benefit categories — the TYPES of companies that benefit, grouped
    by mechanism of exposure. Look for a line
    "## Benefit categories identified: ..." listing the categories.
  - Layer 3: per-category representative US-listed stocks, each with a
    structured mini-snapshot (revenue / yoy growth / gross margin) rendered as
    a markdown table under "### {{category}} — most leveraged stocks".

Structure:

1. **Thesis and catalysts** — what is the theme, why now, what concrete
   catalysts are driving it. Cite numbers with inline source URLs.

2. **How the value flows — benefit categories** — for EACH category from
   Layer 2, an H3 sub-section that MUST cover:
   - **Mechanism of exposure**: WHY this category benefits — how value is
     transmitted from the theme to these companies (direct demand, pricing
     power, volume, picks-and-shovels, etc.).
   - **Representative stocks**: for each US-listed stock surfaced in Layer 3,
     its exposure to the theme (revenue share / strategic positioning) plus its
     financial snapshot (revenue / yoy growth / gross margin, inline source
     URLs). If a number is missing, write "Data not available in current
     sources" for that field only; do not skip the stock. If a category has NO
     US-listed stock in the context, say so in one line and keep the mechanism
     paragraph — do not drop the category.

3. **Time horizon and milestones** — over what timeframe the theme plays out;
   the concrete milestones a reader should watch.

4. **Risks that invalidate the thesis** — be specific about what would break
   the theme (substitution, regulation, demand air-pocket, over-supply).

5. **Forward outlook** — net read: where the theme is most/least de-risked, and
   which category offers the cleanest exposure (without a buy/sell call).
"""
    + _SUMMARY_AND_HEADINGS
    + _NON_US_PLAYERS
    + _HARD_CONSTRAINTS_HEADER
    + _SHARED_CONSTRAINTS
    + """\
- Keep section numbering exactly as 1, 2, 3, 4, 5 above.
- Cover EVERY category from the Layer 2 list; do not silently drop a category.
- Stock cards must be US-listed (the upstream pipeline already filters for
  this). Non-US-listed companies may appear in the narrative under the rule
  above, but never as a stock card.
- Total length target: 1600-2400 words.
- Section headings as H2; per-category blocks and stock cards as H3.
"""
)
