"""上下文来源 URL 回归测试(Slice E0)。

背景 —— 报告里引用链接全是死锚 `](#)` 的根因:

  ContextCompressor.async_get_context 有两条路径:
    · standard path: 经 SearchAPIRetriever 把 page["url"] 转录到 metadata["source"]
    · fast path    : 内容 <COMPRESSION_THRESHOLD 时跳过 embedding 压缩,
                     曾经直接 `metadata=doc` —— 而 scraped page 的键叫 "url",
                     PromptFamily.pretty_print_docs 读的是 metadata["source"],
                     取不到 → 上下文里写 "Source: None" → 写作模型无 URL 可引。

实测:一次 value_chain 研究 25 个抓取批次全部命中 fast path
(Tavily 自带摘要 → 跳过抓网页 → 总量远小于阈值),报告 87 个引用全是 `](#)`。

本测试锁定 fast path 必须产出真实 URL,且与 standard path 一致。
"""
import pytest

from gpt_researcher.context.compression import ContextCompressor
from gpt_researcher.context.retriever import SearchAPIRetriever
from gpt_researcher.prompts import PromptFamily

# 一份真实形状的 scraped page(scraper.py 产出的就是这三个键)
# raw_content 故意很短,以确保命中 fast path(total_chars < 8000)
SHORT_PAGES = [
    {
        "url": "https://example.com/nvda-q2",
        "title": "NVIDIA Q2 FY2026 Results",
        "raw_content": "NVIDIA reported quarterly revenue of $81.6B, up 85% year over year.",
    },
    {
        "url": "https://example.com/amd-q2",
        "title": "AMD Q2 2026 Results",
        "raw_content": "AMD data center revenue doubled year over year.",
    },
]


def _fast_path_context(pages):
    """直接触发 fast path 并返回上下文字符串(不碰 embedding / 网络)。"""
    compressor = ContextCompressor(documents=pages, embeddings=None)
    import asyncio

    return asyncio.run(compressor.async_get_context(query="revenue", max_results=10))


def _standard_path_context(pages):
    """standard path 的确定性等价物:走 SearchAPIRetriever 的 metadata 转录。

    跳过 embedding 相似度过滤(需要网络),只验证 metadata 映射这一环 ——
    这正是 fast path 出错的那一环。
    """
    docs = SearchAPIRetriever(pages=pages)._get_relevant_documents(
        "revenue", run_manager=None
    )
    return PromptFamily.pretty_print_docs(docs, len(pages))


def test_fast_path_is_actually_taken():
    """前提校验:这批文档确实走 fast path,否则本测试没有意义。"""
    total_chars = sum(len(str(p.get("raw_content", ""))) for p in SHORT_PAGES)
    assert total_chars < 8000, "样本太大,不会命中 fast path"
    assert len(SHORT_PAGES) <= 10


def test_fast_path_context_carries_real_source_urls():
    """fast path 的上下文必须带真实 URL,不能是 'Source: None'。"""
    ctx = _fast_path_context(SHORT_PAGES)

    assert "Source: None" not in ctx, f"fast path 丢了来源 URL:\n{ctx}"
    for page in SHORT_PAGES:
        assert f"Source: {page['url']}" in ctx, f"缺少 {page['url']}:\n{ctx}"


def test_fast_path_context_carries_titles():
    """标题同样不能丢(pretty_print_docs 也读 metadata['title'])。"""
    ctx = _fast_path_context(SHORT_PAGES)

    assert "Title: None" not in ctx, f"fast path 丢了标题:\n{ctx}"
    for page in SHORT_PAGES:
        assert f"Title: {page['title']}" in ctx


def test_both_paths_agree():
    """两条路径对同一份输入必须产出相同的上下文,否则报告质量取决于抓取量。"""
    assert _fast_path_context(SHORT_PAGES) == _standard_path_context(SHORT_PAGES)


def test_missing_url_degrades_to_empty_not_none():
    """缺 url 的边界情况:应为空串而非字面量 'None'(避免污染上下文)。"""
    ctx = _fast_path_context([{"raw_content": "Some text with no source."}])

    assert "Source: None" not in ctx
    assert "Title: None" not in ctx
