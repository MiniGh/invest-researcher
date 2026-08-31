from .base import BaseConfig

DEFAULT_CONFIG: BaseConfig = {
    "RETRIEVER": "tavily",
    # "RETRIEVER": "duckduckgo",
    # "EMBEDDING": "openai:text-embedding-3-small",
    # --- 备选:硅基流动上的 bge-m3(账户有余额时可用)---
    # 注意硅基流动余额为零时,免费档模型也一并返回 402,不是只拦付费模型。
    # "EMBEDDING": "custom:Pro/BAAI/bge-m3",
    "EMBEDDING": "zhipuai:embedding-3",
    # 相似度阈值随 embedding 模型走 —— 不同模型的余弦相似度分布不同,阈值照搬
    # 会让上下文被过度过滤(取不到料)或过度放行(引入噪声)。0.42 原本是照
    # bge-m3 定的,换成 embedding-3 后实测(query = "Micron Technology latest
    # quarterly revenue and HBM market share"):
    #   0.709  同公司同主题的财报段落
    #   0.631  同主题的行业格局段落
    #   0.377  同行业但换了公司(Apple 财报)
    #   0.367  同领域但换了话题(DRAM 合约价)
    #   0.180  完全无关(菜谱)
    # 0.42 落在 0.377 与 0.631 之间的空档里,两侧余量都够,故沿用不改。
    "SIMILARITY_THRESHOLD": 0.42,
    # "FAST_LLM": "openai:gpt-4o-mini",
    # "SMART_LLM": "openai:gpt-4.1",  # Has support for long responses (2k+ words).
    # "STRATEGIC_LLM": "openai:o4-mini",  # Can be used with o1 or o3, please note it will make tasks slower.
    #
    # --- 当前启用:智谱(BigModel)直连 ---
    # 读 ZHIPUAI_API_KEY(base URL 可用 ZHIPUAI_BASE_URL 覆盖,默认
    # https://open.bigmodel.cn/api/paas/v4)。
    #
    # 刻意不复用 OPENAI_API_KEY / OPENAI_BASE_URL:那套变量被 EMBEDDING
    # (下面的 custom:Pro/BAAI/bge-m3)占着指向硅基流动。共用会互相冲掉 ——
    # 换 LLM 厂商会连带把 embedding 打断。所以 LLM 走智谱、embedding 留在
    # 硅基流动,两边各用自己的凭据。
    #
    # 注意:evals/ 下的抽取模型与判定模型硬编码 provider="openai",走的仍是
    # OPENAI_* 那套(硅基流动)—— 这是有意的,判定模型必须与写作模型不同门,
    # 见 evals/investment_eval/judge.py 的 FORBIDDEN_JUDGE_SUBSTR。
    "FAST_LLM": "zhipuai:GLM-5.3-Flash",
    "SMART_LLM": "zhipuai:GLM-5.3-Flash",  # 报告正文由这个角色写
    "STRATEGIC_LLM": "zhipuai:GLM-5.3-Flash",
    #
    # --- 备选 A:经硅基流动调用 DeepSeek(上一版配置)---
    # "FAST_LLM": "openai:deepseek-ai/DeepSeek-V4-Flash",
    # "SMART_LLM": "openai:deepseek-ai/DeepSeek-V4-Flash",
    # "STRATEGIC_LLM": "openai:deepseek-ai/DeepSeek-V4-Pro",
    #
    # --- 备选 B:DeepSeek 官方直连(需要 DEEPSEEK_API_KEY 有余额)---
    # "FAST_LLM": "deepseek:deepseek-v4-flash",
    # "SMART_LLM": "deepseek:deepseek-v4-flash",
    # "STRATEGIC_LLM": "deepseek:deepseek-v4-pro",
    "FAST_TOKEN_LIMIT": 3000,
    "SMART_TOKEN_LIMIT": 6000,
    "STRATEGIC_TOKEN_LIMIT": 4000,
    "BROWSE_CHUNK_MAX_LENGTH": 8192,
    "CURATE_SOURCES": False,
    "SUMMARY_TOKEN_LIMIT": 700,
    "TEMPERATURE": 0.4,
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "MAX_SEARCH_RESULTS_PER_QUERY": 5,
    "MEMORY_BACKEND": "local",
    "TOTAL_WORDS": 1200,
    "REPORT_FORMAT": "APA",
    "MAX_ITERATIONS": 3,
    "AGENT_ROLE": None,
    "SCRAPER": "bs",
    "MAX_SCRAPER_WORKERS": 15,
    "SCRAPER_RATE_LIMIT_DELAY": 0.0,  # Minimum seconds between scraper requests (0 = no limit, useful for API rate limiting)
    "MAX_SUBTOPICS": 3,
    "LANGUAGE": "english",
    "REPORT_SOURCE": "web",
    "DOC_PATH": "./my-docs",
    "PROMPT_FAMILY": "default",
    # 给每次 LLM 调用加超时与重试。
    #
    # 不加的话请求会无限挂起:实测一次 value_chain 研究在写报告阶段卡死 59 分钟,
    # 进程 67 分钟只消耗 6 秒 CPU,始终挂着一条到代理的连接等响应,不会自行恢复。
    # 底层 ChatOpenAI 默认不设 timeout,一次丢包就永久等待。
    #
    # 600 秒的依据:同一次运行里正常调用耗时为 8s / 86s / 271s,最长约 4.5 分钟,
    # 留出充分余量;流式响应下该值是"多久没有新数据"而非总时长,更不会误杀。
    "LLM_KWARGS": {"timeout": 600, "max_retries": 2},
    "EMBEDDING_KWARGS": {"chunk_size": 64},
    "VERBOSE": False,
    # Deep research specific settings
    "DEEP_RESEARCH_BREADTH": 3,
    "DEEP_RESEARCH_DEPTH": 2,
    "DEEP_RESEARCH_CONCURRENCY": 4,
    # MCP retriever specific settings
    "MCP_SERVERS": [],  # List of predefined MCP server configurations
    "MCP_AUTO_TOOL_SELECTION": True,  # Whether to automatically select the best tool for a query
    "MCP_ALLOWED_ROOT_PATHS": [],  # List of allowed root paths for local file access
    "MCP_STRATEGY": "fast",  # MCP execution strategy: "fast", "deep", "disabled"
    "REASONING_EFFORT": "medium",
    # Investment research (Slice 1): default Tavily include_domains whitelist
    "FINANCE_DOMAIN_WHITELIST": [
        # SEC / regulatory
        "sec.gov", "www.sec.gov",
        # Top-tier financial press
        "ft.com", "wsj.com", "bloomberg.com", "reuters.com",
        "cnbc.com", "marketwatch.com", "barrons.com",
        # Data & retail-investor research
        "finance.yahoo.com", "seekingalpha.com", "morningstar.com", "fool.com",
    ],
    # Image generation settings (optional - requires GOOGLE_API_KEY)
    # Free tier models: gemini-2.5-flash-image, gemini-2.0-flash-exp-image-generation
    # Paid tier models: imagen-4.0-generate-001, imagen-4.0-fast-generate-001
    "IMAGE_GENERATION_MODEL": "models/gemini-2.5-flash-image",
    "IMAGE_GENERATION_MAX_IMAGES": 3,  # Maximum number of images to generate per report
    "IMAGE_GENERATION_ENABLED": False,  # Master switch for inline image generation
    "IMAGE_GENERATION_STYLE": "dark",  # Image style: "dark" (matches app theme), "light", or "auto"
}
