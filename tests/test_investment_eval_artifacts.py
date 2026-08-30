"""证据快照与上下文解析的回归测试(Slice E1)。

关键用例是**往返测试**:用真实的 `PromptFamily.pretty_print_docs` 渲染上下文,
再用 `parse_context_blocks` 反解,断言 URL/标题/正文原样还原。这样一旦上游改了
上下文格式,这里会立刻红,而不是等到评估数字悄悄失真。
"""
import json

try:
    import fitz  # noqa: F401  (仅确认 venv 可用,与本测试无关)
except Exception:
    pass

from langchain_core.documents import Document

from evals.investment_eval.artifacts import (
    ContextChunk,
    ResearchArtifact,
    SourceDoc,
    parse_context_blocks,
)
from gpt_researcher.prompts import PromptFamily

PAGES = [
    {
        "url": "https://example.com/mu-q3",
        "title": "Micron FQ3 2026 Results",
        "raw_content": "Micron reported revenue of $13.64B, up 57% YoY.\nGross margin was 56.8%.",
    },
    {
        "url": "https://example.com/hbm-share",
        "title": "HBM Market Share 2026",
        "raw_content": "SK hynix held 58.3% of the HBM market; Micron and Samsung ~20% each.",
    },
]


def _render(pages):
    """用生产代码渲染上下文,避免测试里手写格式导致的假通过。"""
    docs = [
        Document(
            page_content=p["raw_content"],
            metadata={"source": p["url"], "title": p["title"]},
        )
        for p in pages
    ]
    return PromptFamily.pretty_print_docs(docs, len(docs))


def test_roundtrip_preserves_source_title_content():
    chunks, leftover = parse_context_blocks(_render(PAGES))

    assert leftover == ""
    assert len(chunks) == len(PAGES)
    for chunk, page in zip(chunks, PAGES):
        assert chunk.source_url == page["url"]
        assert chunk.title == page["title"]
        assert chunk.content == page["raw_content"].strip()
        assert chunk.has_real_source


def test_multiline_content_is_not_truncated():
    """正文跨多行时不能只截到第一行 —— 块尾要切到下一个块头。"""
    chunks, _ = parse_context_blocks(_render(PAGES))

    assert "Gross margin was 56.8%." in chunks[0].content


def test_source_none_is_detected_as_untraceable():
    """修复前的产物里 Source 是字面量 'None',必须被判为不可溯源。"""
    ctx = "Source: None\nTitle: Some Doc\nContent: Revenue was $81.6B.\n"
    chunks, _ = parse_context_blocks(ctx)

    assert len(chunks) == 1
    assert chunks[0].source_url == "None"
    assert not chunks[0].has_real_source


def test_list_context_is_joined():
    """researcher.context 原生是 list[str],多个 batch 要全部解出来。"""
    chunks, _ = parse_context_blocks([_render(PAGES[:1]), _render(PAGES[1:])])

    assert [c.source_url for c in chunks] == [p["url"] for p in PAGES]


def test_strategy_injected_content_lands_in_unattributed():
    """投研 strategy 拼进来的骨架行/extractor 卡片没有 Source 头,要单独留存。"""
    injected = "## Value-chain segments identified: upstream, midstream, downstream\n\n"
    chunks, leftover = parse_context_blocks(injected + _render(PAGES))

    assert len(chunks) == len(PAGES)
    assert "Value-chain segments identified" in leftover


def test_context_with_no_blocks_all_goes_to_unattributed():
    chunks, leftover = parse_context_blocks("### Company card\n| Metric | Value |\n")

    assert chunks == []
    assert "Company card" in leftover


def test_artifact_save_load_roundtrip(tmp_path):
    art = ResearchArtifact(
        research_id="test_001",
        query="Analyze Micron Technology (MU)",
        label="company_profile",
        created_at="2026-08-24T00:00:00",
        report_md="# Report\nRevenue was $13.64B ([MU FQ3](https://example.com/mu-q3)).",
        sources=[SourceDoc(**{k: p[k] for k in ("url", "title", "raw_content")}) for p in PAGES],
        context_chunks=[ContextChunk("https://example.com/mu-q3", "T", "C")],
        unattributed_context="## skeleton",
        run_config={"SMART_LLM": "deepseek:deepseek-v4-flash"},
    )
    path = art.save(tmp_path)
    loaded = ResearchArtifact.load(path)

    assert loaded == art
    assert loaded.source_urls == {p["url"] for p in PAGES}
    assert loaded.sources_by_url()["https://example.com/hbm-share"].title == "HBM Market Share 2026"
    # 落盘必须是可读 JSON(人要能直接打开看原文)
    assert json.loads(path.read_text(encoding="utf-8"))["label"] == "company_profile"


def test_source_urls_excludes_placeholders():
    art = ResearchArtifact(
        research_id="x", query="q", label="其他", created_at="t", report_md="",
        sources=[SourceDoc(url="None"), SourceDoc(url=""), SourceDoc(url="https://ok.com")],
    )
    assert art.source_urls == {"https://ok.com"}


def test_empty_report_snapshot_is_still_loadable_and_flagged():
    """写报告失败时仍要保留研究证据,但必须能被识别出来。

    实测一次 value_chain 运行在写报告阶段因网络中断失败,产出 0 字符,而研究
    阶段已采集 150 篇资料、18 万字、耗时 38 分钟。这份证据值得保留(可据此重写),
    但绝不能当成成功计入统计 —— 空报告的溯源率分母为 0,会把看板算歪。
    """
    art = ResearchArtifact(
        research_id="rid_empty", query="q", label="value_chain", created_at="t",
        report_md="",
        sources=[SourceDoc(url="https://example.com/a", raw_content="x" * 500)],
        run_config={"report_ok": False},
    )

    assert not art.report_md.strip()
    assert art.run_config["report_ok"] is False
    assert art.sources, "证据必须保留下来"
