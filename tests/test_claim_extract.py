"""断言抽取的回归测试(Slice E3)。

抽取环节的错误会一路传导:抽出不自足的断言("$23.86B" 没有主语和期间)
后面根本无从核对;抽出没有数字的句子则会稀释幻觉率的分母。
"""
import asyncio
import json

import pytest

from evals.investment_eval import claim_extract as CE


class FakeCfg:
    fast_llm_model = "x"
    fast_llm_provider = "openai"
    fast_token_limit = 3000
    llm_kwargs = {}


def _run(coro):
    return asyncio.run(coro)


def _fake(payload):
    async def fake(**kwargs):
        return json.dumps(payload)
    return fake


REPORT = """## Summary

Micron reported revenue of $13.64 billion, up 57% year over year.

## Outlook

The moat is durable and the team executes well.
"""


# ---------------- 分块与分节 ----------------

def test_sections_split_by_heading():
    secs = CE._sections("## A\n\ntext a\n\n## B\n\ntext b")
    assert [s[0] for s in secs] == ["A", "B"]


def test_chunks_do_not_split_mid_paragraph():
    text = "\n\n".join(["p" * 1500 for _ in range(4)])
    for c in CE._chunks(text, size=2000):
        assert not c.startswith("p" * 1500 + "p")


def test_table_rows_are_excluded(monkeypatch):
    """表格里的数字没有上下文,抽出来的断言不自足。"""
    seen = []

    async def spy(**kwargs):
        seen.append(kwargs["messages"][0]["content"])
        return "[]"

    monkeypatch.setattr(CE, "create_chat_completion", spy)
    _run(CE.extract_claims(FakeCfg(), "| Revenue | $13.64B |\n| Margin | 56.8% |"))
    assert all("| Revenue |" not in s for s in seen)


def test_chunks_without_numbers_never_reach_the_model(monkeypatch):
    async def boom(**kwargs):
        raise AssertionError("整块没有数字,不该调用模型")

    monkeypatch.setattr(CE, "create_chat_completion", boom)
    out = _run(CE.extract_claims(FakeCfg(), "## A\n\nThe moat is durable and the team executes."))
    assert out == []


# ---------------- 抽取结果 ----------------

def test_extracts_and_attaches_numbers(monkeypatch):
    monkeypatch.setattr(CE, "create_chat_completion", _fake([
        {"claim": "Micron revenue was $13.64 billion in the quarter.",
         "subject": "Micron", "metric": "revenue"},
    ]))
    out = _run(CE.extract_claims(FakeCfg(), REPORT))
    assert len(out) >= 1
    c = out[0]
    assert c.subject == "Micron"
    assert any("13.64" in n for n in c.numbers)


def test_claims_without_numbers_are_dropped(monkeypatch):
    """模型偶尔会抽出定性句子 —— 这类判不准,不能进分母。"""
    monkeypatch.setattr(CE, "create_chat_completion", _fake([
        {"claim": "The moat is durable.", "subject": "Micron", "metric": "moat"},
    ]))
    assert _run(CE.extract_claims(FakeCfg(), REPORT)) == []


def test_section_is_recorded(monkeypatch):
    monkeypatch.setattr(CE, "create_chat_completion", _fake([
        {"claim": "Revenue was $13.64 billion.", "subject": "Micron", "metric": "revenue"},
    ]))
    out = _run(CE.extract_claims(FakeCfg(), REPORT))
    assert out and out[0].section in ("Summary", "Outlook")


def test_duplicate_claims_are_deduped(monkeypatch):
    monkeypatch.setattr(CE, "create_chat_completion", _fake([
        {"claim": "Revenue was $13.64 billion.", "subject": "M", "metric": "r"},
        {"claim": "revenue   WAS $13.64 billion.", "subject": "M", "metric": "r"},
    ]))
    out = _run(CE.extract_claims(FakeCfg(), REPORT))
    assert len(out) == 1, "大小写与空白不同的同一条断言没有去重"


def test_cited_urls_are_captured(monkeypatch):
    monkeypatch.setattr(CE, "create_chat_completion", _fake([
        {"claim": "Revenue was $13.64 billion ([src](https://example.com/a)).",
         "subject": "M", "metric": "r"},
    ]))
    out = _run(CE.extract_claims(FakeCfg(), REPORT))
    assert out[0].cited_urls == ["https://example.com/a"]


# ---------------- 异常处理 ----------------

def test_malformed_json_skips_the_chunk(monkeypatch):
    async def junk(**kwargs):
        return "not json at all"
    monkeypatch.setattr(CE, "create_chat_completion", junk)
    assert _run(CE.extract_claims(FakeCfg(), REPORT)) == []


def test_timeout_is_retried_then_skipped(monkeypatch):
    """超时来自网络抖动,重试通常就过;耗尽后跳过该块而不是卡死整轮。"""
    calls = []

    async def slow(**kwargs):
        calls.append(1)
        await asyncio.sleep(3600)

    monkeypatch.setattr(CE, "create_chat_completion", slow)
    monkeypatch.setattr(CE, "CALL_TIMEOUT", 0.05)
    out = _run(CE.extract_claims(FakeCfg(), REPORT))
    assert out == []
    assert len(calls) >= 2, f"没有重试,只调用了 {len(calls)} 次"


def test_one_failing_chunk_does_not_kill_the_rest(monkeypatch):
    state = {"n": 0}

    async def flaky(**kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")
        return json.dumps([{"claim": "Revenue was $13.64 billion.", "subject": "M", "metric": "r"}])

    monkeypatch.setattr(CE, "create_chat_completion", flaky)
    long_report = REPORT + "\n\n## More\n\n" + "Capex was $10.5 billion. " * 200
    out = _run(CE.extract_claims(FakeCfg(), long_report))
    assert out, "一块失败就把整份报告的抽取结果清空了"
