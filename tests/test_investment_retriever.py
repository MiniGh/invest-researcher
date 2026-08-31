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


# --- set_retriever:检索器是否真的装到了实例上 -----------------------------
#
# 上面那批测试全部只测 InvestmentTavilySearch 类本身,所以完全没发现它从未被
# 装载:InvestmentResearcher 只写了 cfg.retriever,而 GPTResearcher 早在构造
# 时就把 get_retrievers() 的结果冻结在 self.retrievers 上,且 get_retrievers()
# 里 cfg.retrievers(复数)优先于 cfg.retriever(单数)。运行日志一直是
# `Active retrievers: ['TavilySearch']`。以下测试锁的就是这条链。

class _FakeCfg:
    def __init__(self):
        self.retriever = "tavily"
        self.retrievers = ["tavily"]


class _FakeResearcher:
    def __init__(self):
        self.cfg = _FakeCfg()
        self.headers = {}
        self.retrievers = []


def test_set_retriever_replaces_live_retriever_list():
    """光改 cfg 不够 —— 必须覆盖实例上已冻结的 retrievers。"""
    from gpt_researcher.investment.retriever import set_retriever

    gr = _FakeResearcher()
    set_retriever(gr, "investment_tavily")
    assert [c.__name__ for c in gr.retrievers] == ["InvestmentTavilySearch"]


def test_set_retriever_syncs_both_cfg_fields():
    """cfg.retrievers(复数)不同步的话,任何重新解析都会把改动冲掉。"""
    from gpt_researcher.investment.retriever import set_retriever

    gr = _FakeResearcher()
    set_retriever(gr, "investment_tavily")
    assert gr.cfg.retriever == "investment_tavily"
    assert gr.cfg.retrievers == ["investment_tavily"]


def test_set_retriever_can_switch_back_to_vanilla():
    """VanillaStrategy 的兜底路径要能真的切回原生 tavily。"""
    from gpt_researcher.investment.retriever import set_retriever

    gr = _FakeResearcher()
    set_retriever(gr, "investment_tavily")
    set_retriever(gr, "tavily")
    assert [c.__name__ for c in gr.retrievers] == ["TavilySearch"]
    assert gr.cfg.retrievers == ["tavily"]


def test_set_retriever_rejects_unknown_name():
    from gpt_researcher.investment.retriever import set_retriever

    gr = _FakeResearcher()
    with pytest.raises(ValueError, match="unknown retriever"):
        set_retriever(gr, "no_such_retriever")
    # 失败时不应留下半改状态
    assert [c.__name__ for c in gr.retrievers] == []
    assert gr.cfg.retriever == "tavily"
