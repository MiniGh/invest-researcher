# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GPT Researcher is an autonomous LLM research agent: given a query it plans sub-questions, searches the web (or local docs), scrapes and summarizes sources, then writes a cited long-form report. Despite the `.cursorrules` framing this as a Next.js project, the substance lives in **Python** — the Next.js app is just one of two optional frontends.

Requires Python >= 3.11.

## Commands

Dependencies can be installed three ways: `pip install -r requirements.txt`, Poetry (`pyproject.toml`), or uv (workspace defined under `[tool.uv.sources]`).

- **Run the server (web UI + API):** `python -m uvicorn main:app --reload` (or `python main.py`) — serves FastAPI on port 8000, static frontend at `/`.
- **CLI research:** `python cli.py "<query>" --report_type research_report --tone objective --report_source web`
- **Run all tests:** `python -m pytest` — pytest config (`pyproject.toml`) sets `testpaths=["tests"]`, `asyncio_mode=strict`, and only collects files matching `test_*.py`.
- **Run a single test:** `python -m pytest tests/test_mcp.py::test_name`
- **Manual test scripts:** Many files in `tests/` use other naming (e.g. `report-types.py`, `vector-store.py`, `research_test.py`, `test-your-llm.py`). These are NOT collected by `pytest` automatically — run them explicitly: `python -m pytest tests/report-types.py`.
- **Docker:** `docker-compose up` (app on 8000, Next.js on 3000). Tests in container: `docker-compose --profile test run --rm gpt-researcher-tests`.
- **Multi-agent run:** `python multi_agents/main.py` — reads `multi_agents/task.json`.
- **LangGraph:** `langgraph.json` exposes the multi-agent graph at `multi_agents/agent.py:graph` (use with the `langgraph` CLI / LangGraph Studio).
- **Next.js frontend:** in `frontend/nextjs/`: `npm run dev`, `npm run build`, `npm run lint`.

## Architecture

There are **three independent ways to run research**, sharing the `gpt_researcher` core:

1. **Programmatic / core** — `from gpt_researcher import GPTResearcher`; the `GPTResearcher` class in `gpt_researcher/agent.py` orchestrates everything. Primary flow: `await researcher.conduct_research()` then `await researcher.write_report()`.
2. **FastAPI server** — `backend/server/app.py`. The WebSocket `/ws` endpoint is the main research channel (streams progress); `websocket_manager.py` drives it, `report_store.py` persists reports, `multi_agent_runner.py` runs the multi-agent flow. REST endpoints under `/api/*` handle reports CRUD and chat.
3. **Multi-agent (LangGraph)** — `multi_agents/`: `ChiefEditorAgent.init_research_team()` builds a LangGraph `StateGraph` of role agents (researcher, editor, reviewer, reviser, writer, publisher, human). `multi_agents_ag2/` is a parallel AutoGen/AG2 variant of the same idea.

### `gpt_researcher/` core package

- `agent.py` — `GPTResearcher` orchestrator (entry point for all research; also handles deep-research and MCP config resolution).
- `skills/` — the pipeline stages: `researcher.py` (`ResearchConductor`), `writer.py` (`ReportGenerator`), `browser.py` (`BrowserManager`), `context_manager.py`, `curator.py` (`SourceCurator`), `deep_research.py` (`DeepResearchSkill`, recursive tree exploration), `image_generator.py`.
- `actions/` — stateless steps: `query_processing.py`, `web_scraping.py`, `retriever.py`, `report_generation.py`, `agent_creator.py`.
- `retrievers/` — pluggable search backends (tavily, duckduckgo, google, bing, arxiv, pubmed_central, exa, searx, serper, serpapi, semantic_scholar, xquik, `mcp/`, `custom/`). Selected by the `RETRIEVER` config.
- `scraper/` — content scrapers, selected by `SCRAPER` config (default `bs` = BeautifulSoup).
- `llm_provider/generic/` — provider-agnostic LLM wrapper (litellm-backed); `image/` for image generation.
- `context/`, `memory/`, `vector_store/`, `document/`, `mcp/`, `prompts.py` (prompt families, selected by `PROMPT_FAMILY`).

### Configuration model

`gpt_researcher/config/config.py` (`Config` class) layers settings with this precedence (highest wins):

1. **Environment variables** (and `.env`)
2. **JSON config file** — path via constructor arg or `CONFIG_PATH` env var
3. **`gpt_researcher/config/variables/default.py`** (`DEFAULT_CONFIG`) — base defaults; `base.py` is the typed schema.

Key knobs: `RETRIEVER` (comma-separate for hybrid, e.g. `tavily,mcp`), and the three LLM roles `FAST_LLM` / `SMART_LLM` / `STRATEGIC_LLM` plus `EMBEDDING`, all using `provider:model` format (e.g. `openai:gpt-4o-mini`). Each LLM role has its own token limit. This fork edits the defaults directly in `default.py` rather than via env vars — when changing model/retriever defaults, edit that file.

### Frontends

- **Static** (`frontend/index.html`, `scripts.js`, `styles.css`) — served directly by the FastAPI app at `/`. Zero build step.
- **Next.js** (`frontend/nextjs/`) — production frontend, talks to the API/WebSocket.

### Report types & evals

- Report types live in `backend/report_type/`: `basic_report/`, `detailed_report/`, `deep_research/`; valid values come from the `ReportType` enum in `gpt_researcher/utils/enum.py`.
- `evals/` — `simple_evals/` (factuality, adapted from OpenAI simple-evals) and `hallucination_eval/`.
