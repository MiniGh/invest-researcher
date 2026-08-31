"""投研 Tavily 子类的回归测试(Slice E5)。

锁定一个功能性缺陷的修复:上游 `search()` 只取摘要(400-1300 字符),而下游
`skills/researcher.py` 见到长度 >100 的内容就当成"正文已就绪"并跳过抓取 ——
于是每一条结果都跳过抓取,整个 scraper 包从未执行,卷宗单薄,幻觉率评估里的
"无据率"被抬高。修法是让那个判断的前提成立:请求 Tavily 返回真正的网页正文。
"""
import pytest

from gpt_researcher.investment.retriever import (
    MAX_RAW_CONTENT_CHARS,
    InvestmentTavilySearch,
)

SNIPPET = "A short snippet about Micron HBM share. " * 8      # ~320 字符
FULLTEXT = "Full page text with much more detail about HBM. " * 400  # ~19k 字符


def _fake_results(items):
    return {"results": items}


def _mk(monkeypatch, items):
    r = InvestmentTavilySearch("Micron HBM market share")
    monkeypatch.setattr(r, "_search", lambda *a, **k: _fake_results(items))
    return r


def test_requests_raw_content(monkeypatch):
    """必须显式请求网页正文,否则 Tavily 只回摘要。"""
    seen = {}

    r = InvestmentTavilySearch("Micron HBM market share")

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return _fake_results([{"url": "https://a.com", "content": SNIPPET, "raw_content": FULLTEXT}])

    monkeypatch.setattr(r, "_search", spy)
    r.search(max_results=5)
    assert seen.get("include_raw_content") is True


def test_returns_full_text_as_raw_content(monkeypatch):
    r = _mk(monkeypatch, [{"url": "https://a.com", "content": SNIPPET, "raw_content": FULLTEXT}])
    out = r.search()

    assert out[0]["href"] == "https://a.com"
    assert out[0]["body"] == SNIPPET, "摘要字段要保留,上游其他消费方还在用"
    assert len(out[0]["raw_content"]) > len(SNIPPET) * 5


def test_raw_content_is_truncated(monkeypatch):
    """单页可达两万字符,一次研究扇出约 30×5 篇,不截断下游压缩开销会失控。"""
    r = _mk(monkeypatch, [{"url": "https://a.com", "content": SNIPPET, "raw_content": "x" * 50000}])
    assert len(r.search()[0]["raw_content"]) == MAX_RAW_CONTENT_CHARS


def test_shorter_raw_content_is_dropped(monkeypatch):
    """正文比摘要还短时不能带上 —— 下游会拿它当"全文",反而不如摘要。"""
    r = _mk(monkeypatch, [{"url": "https://a.com", "content": SNIPPET, "raw_content": "tiny"}])
    out = r.search()
    assert "raw_content" not in out[0]
    assert out[0]["body"] == SNIPPET


def test_missing_raw_content_falls_back_to_snippet(monkeypatch):
    r = _mk(monkeypatch, [{"url": "https://a.com", "content": SNIPPET}])
    out = r.search()
    assert "raw_content" not in out[0]
    assert out[0]["body"] == SNIPPET


def test_mixed_results_are_all_returned(monkeypatch):
    r = _mk(monkeypatch, [
        {"url": "https://a.com", "content": SNIPPET, "raw_content": FULLTEXT},
        {"url": "https://b.com", "content": SNIPPET},
    ])
    out = r.search()
    assert len(out) == 2
    assert "raw_content" in out[0] and "raw_content" not in out[1]


def test_empty_results_return_empty_list(monkeypatch):
    r = _mk(monkeypatch, [])
    assert r.search() == []


def test_search_failure_returns_empty_list_not_crash(monkeypatch):
    r = InvestmentTavilySearch("Micron HBM market share")

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(r, "_search", boom)
    assert r.search() == []


def test_whitelist_decision_still_applies():
    """L0-B 的白名单判断不能因为本次改动失效。"""
    trust = InvestmentTavilySearch("Microsoft latest quarterly revenue and margins")
    explore = InvestmentTavilySearch("Microsoft core business and product portfolio")
    assert trust.query_domains, "trust-critical 应套财经白名单"
    assert not explore.query_domains, "exploratory 应全网检索"


def test_explicit_query_domains_win():
    r = InvestmentTavilySearch("Microsoft latest quarterly revenue", query_domains=["sec.gov"])
    assert r.query_domains == ["sec.gov"]
