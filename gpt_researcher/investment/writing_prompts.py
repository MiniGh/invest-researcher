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

# 未来扩展点(Slice 3.2 / 3.3 上 5 全标签时填):
# WRITING_PROMPT_COMPANY_COMPARISON = """..."""    # 横向矩阵
# WRITING_PROMPT_SECTOR_LANDSCAPE = """..."""      # 行业横切
# WRITING_PROMPT_VALUE_CHAIN = """..."""           # 产业链纵切
# WRITING_PROMPT_THEME_ANALYSIS = """..."""        # 主题/赛道
