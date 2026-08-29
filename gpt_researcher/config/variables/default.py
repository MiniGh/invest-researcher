from .base import BaseConfig

DEFAULT_CONFIG: BaseConfig = {
    "RETRIEVER": "tavily",
    # "RETRIEVER": "duckduckgo",
    # "EMBEDDING": "openai:text-embedding-3-small",
    "EMBEDDING": "custom:Pro/BAAI/bge-m3",
    "SIMILARITY_THRESHOLD": 0.42,
    # "FAST_LLM": "openai:gpt-4o-mini",
    # "SMART_LLM": "openai:gpt-4.1",  # Has support for long responses (2k+ words).
    # "STRATEGIC_LLM": "openai:o4-mini",  # Can be used with o1 or o3, please note it will make tasks slower.
    #
    # --- 备选 A:DeepSeek 官方直连(需要 DEEPSEEK_API_KEY 有余额)---
    # 与下面启用中的配置是同一批模型,切回来只需把这三行取消注释、注释掉下面三行。
    # "FAST_LLM": "deepseek:deepseek-v4-flash",
    # "SMART_LLM": "deepseek:deepseek-v4-flash",
    # "STRATEGIC_LLM": "deepseek:deepseek-v4-pro",
    #
    # --- 当前启用:经硅基流动调用同一批 DeepSeek 模型 ---
    # 走 openai 兼容接口,读 OPENAI_API_KEY + OPENAI_BASE_URL
    # (base.py:105 会把 OPENAI_BASE_URL 注入 ChatOpenAI),与 EMBEDDING 共用同一套凭据。
    # 模型本体与备选 A 相同,因此切换不改变输出行为。
    "FAST_LLM": "openai:deepseek-ai/DeepSeek-V4-Flash",
    "SMART_LLM": "openai:deepseek-ai/DeepSeek-V4-Flash",  # Has support for long responses (2k+ words).
    "STRATEGIC_LLM": "openai:deepseek-ai/DeepSeek-V4-Pro",  # Can be used with o1 or o3, please note it will make tasks slower.
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
    "LLM_KWARGS": {},
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
