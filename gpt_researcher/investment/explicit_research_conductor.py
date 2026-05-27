"""跑预先写死的 sub-query 列表,跳过 gpt-researcher 默认的 LLM 拆解步骤。

L1 树展开机制核心(Slice 3.2+)。

用法:
  - Strategy 在每个 batch 之前往 conductor.explicit_queries 塞列表
  - 调 gpt_researcher.conduct_research() 跑这批
  - 取 gpt_researcher.context 字符串,自己做跨 batch 累加

ResearchConductor.plan_research 原本会发一次 Tavily 拿初始 search results + 调
plan_research_outline 让 LLM 拆 sub-query。这里只 override plan_research,跳过
那两步,直接返回写死的列表;下游 _get_context_by_web_search → _process_sub_query
照原样跑(retrieve + scrape + summarize 全部保留)。
"""
from gpt_researcher.skills.researcher import ResearchConductor


class ExplicitQueryResearchConductor(ResearchConductor):
    """ResearchConductor 子类:plan_research 改成读 self.explicit_queries。

    若 explicit_queries 为 None,fallback 走原版 LLM 拆解(safety net)。
    """

    def __init__(self, researcher):
        super().__init__(researcher)
        self.explicit_queries: list[str] | None = None

    async def plan_research(self, query, query_domains=None):
        if self.explicit_queries is not None:
            return list(self.explicit_queries)  # defensive copy
        return await super().plan_research(query, query_domains)


async def run_query_batch(gpt_researcher, queries: list[str]) -> str:
    """跑一批 explicit sub-queries,返回此 batch 完成后的 researcher.context 字符串。

    Caller responsibility:
      - 提前把 gpt_researcher.research_conductor 换成 ExplicitQueryResearchConductor
      - 多 batch 累加 context 自己拼(本函数返回的 context 会被下次 conduct_research
        overwrite 掉 gpt_researcher.context;Strategy 需要保存上一轮的字符串)
    """
    conductor = gpt_researcher.research_conductor
    if not isinstance(conductor, ExplicitQueryResearchConductor):
        raise TypeError(
            "research_conductor must be ExplicitQueryResearchConductor; "
            "got " + type(conductor).__name__
        )
    conductor.explicit_queries = list(queries)
    try:
        await gpt_researcher.conduct_research()
    finally:
        conductor.explicit_queries = None
    return gpt_researcher.context
