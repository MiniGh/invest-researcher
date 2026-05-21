"""
InvestmentResearcher —— 美股投研助手的顶层 wrapper(Slice 1)。

Slice 1 仅实现 "other 兜底":构造一个 GPTResearcher,预填投研人设
(跳过 choose_agent LLM 调用)+ 财经域名白名单,直通原 gpt-researcher
研究流程。

后续 slice 会在 .run() 内加 L0 intent routing / L1 树编排 / L4 分层组装。
"""
import hashlib
import time
from typing import Any

from gpt_researcher import GPTResearcher


INVESTMENT_ANALYST_AGENT = "equity_analyst"

INVESTMENT_ANALYST_ROLE = """\
You are a meticulous US-equity research analyst. Your job is to produce \
fundamental analysis of public US-listed companies — focused on business \
model, competitive position, recent quarterly performance, catalysts, \
and risks.

Source discipline:
- Anchor factual claims to sources; cite every non-trivial number
- Distinguish company-reported numbers from third-party estimates
- When a figure is unconfirmed or estimated, say so explicitly
- Prefer recent (latest quarter / past 12 months) over historical data

Output discipline:
- Write in clear, analytical, professional prose
- Avoid hype and PR language (e.g., "revolutionary", "game-changing")
- Use figures where available; never fabricate numbers
- If a figure is not disclosed, state so explicitly rather than invent

Boundaries:
- You are an investment-research aide, not a fiduciary
- Reports inform analysis but do not constitute investment advice
- Do not predict price movement; describe fundamentals only
"""


class InvestmentResearcher:
    """美股投研助手的顶层入口(Slice 1: other 兜底分支)。"""

    def __init__(
        self,
        query: str,
        query_domains: list = None,
        report_type: str = "research_report",
        report_source: str = "web",
        source_urls=None,
        document_urls=None,
        tone: Any = None,
        config_path: str = None,
        websocket=None,
        headers=None,
        mcp_configs=None,
        mcp_strategy=None,
        max_search_results=None,
        encoding: str = "utf-8",
    ):
        self.research_id = self._generate_research_id(query)

        gpt_researcher_params = {
            "query": query,
            "query_domains": query_domains if query_domains else None,
            "report_type": report_type,
            "report_source": report_source,
            "source_urls": source_urls,
            "document_urls": document_urls,
            "tone": tone,
            "config_path": config_path,
            "websocket": websocket,
            "headers": headers or {},
            # 预填 agent + role -> 触发 agent.py:400 的 skip 逻辑,
            # 不调用 choose_agent() LLM,直接用固定的投研人设。
            "agent": INVESTMENT_ANALYST_AGENT,
            "role": INVESTMENT_ANALYST_ROLE,
            "encoding": encoding,
        }
        if mcp_configs is not None:
            gpt_researcher_params["mcp_configs"] = mcp_configs
        if mcp_strategy is not None:
            gpt_researcher_params["mcp_strategy"] = mcp_strategy

        self.gpt_researcher = GPTResearcher(**gpt_researcher_params)

        # 用户没传 query_domains -> 从 cfg 取默认白名单(财经域名)。
        # GPTResearcher 本身不会做这个 fallback,在 wrapper 这层处理。
        if not query_domains:
            default_wl = getattr(
                self.gpt_researcher.cfg, "finance_domain_whitelist", None
            )
            if default_wl:
                self.gpt_researcher.query_domains = default_wl

        if max_search_results is not None:
            self.gpt_researcher.cfg.max_search_results_per_query = int(
                max_search_results
            )

    def _generate_research_id(self, query: str) -> str:
        timestamp = str(int(time.time()))
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        return f"investment_research_{timestamp}_{query_hash}"

    async def run(self) -> str:
        await self.gpt_researcher.conduct_research()
        return await self.gpt_researcher.write_report()
