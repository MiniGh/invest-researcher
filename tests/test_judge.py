"""判定环节的回归测试(Slice E4)。

重点在两条防护上,它们决定了幻觉率数字可不可信:

1. **抄写核验** —— judge 必须把依据的原文原样抄出来,本模块用字符匹配去核对。
   模型可以"觉得"原文支持,但没法凭空抄出一句原文里不存在的话而不被发现。
   核验不过就降级成 NOT_FOUND:宁可少认一条,也不让它蒙混过去。

2. **同源禁用** —— 不能用写报告的模型判自己写的报告。同源模型对自己的输出
   有系统性偏袒,它编错的地方正是它认为对的地方。
"""
import asyncio
import json

import pytest

from evals.investment_eval.artifacts import SourceDoc
from evals.investment_eval.claim_extract import Claim
from evals.investment_eval import judge as J

SENTENCE = "Micron reported quarterly revenue of 13.64 billion dollars, up 57 percent year over year."
SOURCES = [SourceDoc(url="https://example.com/mu", title="MU", raw_content=SENTENCE)]
CLAIM = Claim(claim="Micron quarterly revenue was $13.64 billion.", numbers=["$13.64 billion"])


def _run(coro):
    return asyncio.run(coro)


def _fake_llm(payload):
    """替换 create_chat_completion,返回预设的判定结果。"""
    async def fake(**kwargs):
        return json.dumps(payload)
    return fake


# ---------------- 抄写核验 ----------------

def test_quote_must_actually_appear_in_the_excerpt():
    assert J.verify_quote(SENTENCE, [SENTENCE])
    assert not J.verify_quote("Micron reported revenue of 99 billion dollars this quarter.", [SENTENCE])


def test_quote_verification_tolerates_whitespace_and_quote_style():
    """换行、多空格、中英文引号不该被算成抄错。"""
    messy = "Micron   reported quarterly revenue\nof 13.64 billion dollars, up 57 percent year over year."
    assert J.verify_quote(messy, [SENTENCE])


def test_very_short_quote_is_rejected():
    """只抄一个数字谁都能抄对,不构成证据。"""
    assert not J.verify_quote("13.64", [SENTENCE])
    assert not J.verify_quote("revenue", [SENTENCE])


def test_supported_is_downgraded_when_quote_cannot_be_verified(monkeypatch):
    """核心用例:模型声称 SUPPORTED,但抄的原文不存在 → 必须降级。"""
    monkeypatch.setattr(J, "create_chat_completion", _fake_llm({
        "verdict": "SUPPORTED",
        "evidence_quote": "Micron confirmed revenue of 13.64 billion in an interview last week.",
        "reason": "matches",
    }))
    v = _run(J.judge_claim(CLAIM, SOURCES))

    assert v.verdict == "NOT_FOUND"
    assert v.downgraded is True
    assert v.quote_verified is False
    assert "降级" in v.reason


def test_supported_survives_when_quote_checks_out(monkeypatch):
    monkeypatch.setattr(J, "create_chat_completion", _fake_llm({
        "verdict": "SUPPORTED", "evidence_quote": SENTENCE, "reason": "matches",
    }))
    v = _run(J.judge_claim(CLAIM, SOURCES))

    assert v.verdict == "SUPPORTED"
    assert v.quote_verified is True
    assert v.downgraded is False


def test_contradicted_also_requires_a_verifiable_quote(monkeypatch):
    monkeypatch.setattr(J, "create_chat_completion", _fake_llm({
        "verdict": "CONTRADICTED", "evidence_quote": "totally made up sentence about revenue figures",
        "reason": "mismatch",
    }))
    v = _run(J.judge_claim(CLAIM, SOURCES))

    assert v.verdict == "NOT_FOUND"
    assert v.downgraded is True


# ---------------- 不调用模型的短路 ----------------

def test_unrelated_claim_short_circuits_without_calling_the_model(monkeypatch):
    """既无该数字、主题也对不上 → 直接判 NOT_FOUND,省掉模型调用。"""
    async def boom(**kwargs):
        raise AssertionError("不该调用模型")

    monkeypatch.setattr(J, "create_chat_completion", boom)
    unrelated = Claim(claim="Reykjavik summer temperature averaged 14.2 degrees.",
                      numbers=["14.2"])
    v = _run(J.judge_claim(unrelated, SOURCES))

    assert v.verdict == "NOT_FOUND"
    assert v.candidates == 0


def test_topic_match_without_number_still_goes_to_the_model(monkeypatch):
    """数字对不上、但原文在讲同一件事 —— 这正是 CONTRADICTED 的形态,
    必须交给模型比较,不能在代码层短路掉,否则这一类永远判不出来。"""
    monkeypatch.setattr(J, "create_chat_completion", _fake_llm({
        "verdict": "CONTRADICTED", "evidence_quote": SENTENCE, "reason": "number differs",
    }))
    altered = Claim(claim="Micron quarterly revenue was $16.34 billion.",
                    numbers=["$16.34 billion"])
    v = _run(J.judge_claim(altered, SOURCES))

    assert v.candidates > 0, "主题段落没被取到,模型无从比较"
    assert v.verdict == "CONTRADICTED"


# ---------------- 同源禁用 ----------------

@pytest.mark.parametrize("model", [
    "zai-org/GLM-5.2",
    "GLM-5.3-Flash",
    "glm-4-plus",
])
def test_same_family_judge_is_rejected(model):
    """写手换成智谱 GLM 之后,judge 就不能也是 GLM。"""
    with pytest.raises(ValueError, match="同源"):
        _run(J.judge_claim(CLAIM, SOURCES, model=model))


def test_default_judge_is_not_same_family():
    """换写作模型时最容易漏掉的一处:默认判定模型跟着变成了同门。"""
    assert not any(
        s in J.DEFAULT_JUDGE_MODEL.lower() for s in J.FORBIDDEN_JUDGE_SUBSTR
    )


# ---------------- 异常与汇总 ----------------

def test_malformed_json_falls_back_to_not_found(monkeypatch):
    async def junk(**kwargs):
        return "这不是 JSON"
    monkeypatch.setattr(J, "create_chat_completion", junk)
    v = _run(J.judge_claim(CLAIM, SOURCES))
    assert v.verdict == "NOT_FOUND"


def test_unknown_verdict_falls_back_to_not_found(monkeypatch):
    monkeypatch.setattr(J, "create_chat_completion", _fake_llm({
        "verdict": "PROBABLY_FINE", "evidence_quote": SENTENCE,
    }))
    v = _run(J.judge_claim(CLAIM, SOURCES))
    assert v.verdict == "NOT_FOUND"


def test_llm_failure_does_not_crash_the_batch(monkeypatch):
    async def fail(**kwargs):
        raise RuntimeError("network down")
    monkeypatch.setattr(J, "create_chat_completion", fail)
    v = _run(J.judge_claim(CLAIM, SOURCES))
    assert v.verdict == "NOT_FOUND"
    assert "失败" in v.reason


def test_summarize_computes_both_rates():
    vs = [
        J.Verdict(claim="a", verdict="SUPPORTED"),
        J.Verdict(claim="b", verdict="SUPPORTED"),
        J.Verdict(claim="c", verdict="CONTRADICTED"),
        J.Verdict(claim="d", verdict="NOT_FOUND"),
    ]
    s = J.summarize(vs)
    assert s["total"] == 4
    assert s["hallucination_rate"] == 0.25
    assert s["unsupported_rate"] == 0.25


def test_checkpoint_resumes_without_recalling_the_model(tmp_path, monkeypatch):
    """几百条跑到一半断网,不该从头再来。"""
    cp = tmp_path / "cp.jsonl"
    cp.write_text(json.dumps({
        "claim": CLAIM.claim, "verdict": "SUPPORTED", "evidence_quote": SENTENCE,
        "reason": "", "source_url": "", "quote_verified": True, "downgraded": False,
        "candidates": 1, "model": "x",
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    async def boom(**kwargs):
        raise AssertionError("已判定过的断言不该再调用模型")
    monkeypatch.setattr(J, "create_chat_completion", boom)

    out = _run(J.judge_all([CLAIM], SOURCES, checkpoint=cp))
    assert len(out) == 1
    assert out[0].verdict == "SUPPORTED"


# ---------------- 判定调用失败必须与"原文无据"区分 ----------------
#
# 两者都返回 NOT_FOUND,但含义完全不同。实测一次 DeepSeek 余额耗尽让 71 条里
# 62 条判定失败,全部作为 NOT_FOUND 写进了 checkpoint,看板显示"无据率 90.1%"
# —— 而那份快照本身毫无问题。更糟的是续跑会把这些假 NOT_FOUND 当成已判定结果
# 沿用,从此再也看不出这一批没判过。

def _v(**kw):
    base = dict(claim="x", verdict="NOT_FOUND")
    base.update(kw)
    return J.Verdict(**base)


def test_summarize_excludes_call_failures_from_rates():
    verdicts = [
        _v(claim="a", verdict="SUPPORTED"),
        _v(claim="b", verdict="CONTRADICTED"),
        _v(claim="c", verdict="NOT_FOUND"),
        _v(claim="d", call_failed=True),
        _v(claim="e", call_failed=True),
    ]
    s = J.summarize(verdicts)
    assert s["total"] == 3, "调用失败的条目不该进分母"
    assert s["call_failed"] == 2
    assert s["unsupported_rate"] == 1 / 3
    assert s["hallucination_rate"] == 1 / 3


def test_summarize_reports_none_when_everything_failed():
    """全军覆没时给 None 而不是 0% —— 0% 会被读成"表现完美"。"""
    s = J.summarize([_v(claim="a", call_failed=True), _v(claim="b", call_failed=True)])
    assert s["total"] == 0
    assert s["call_failed"] == 2
    assert s["hallucination_rate"] is None
    assert s["unsupported_rate"] is None


def test_call_failures_are_not_checkpointed(tmp_path, monkeypatch):
    """失败结果落盘会污染续跑。"""
    ckpt = tmp_path / "v.jsonl"

    async def fake_judge(claim, sources, **kw):
        text = getattr(claim, "claim", str(claim))
        if text == "boom":
            return _v(claim=text, call_failed=True, reason="判定调用失败:402")
        return _v(claim=text, verdict="SUPPORTED")

    monkeypatch.setattr(J, "judge_claim", fake_judge)
    claims = [J.Claim(claim="ok1"), J.Claim(claim="boom"), J.Claim(claim="ok2")] \
        if hasattr(J, "Claim") else ["ok1", "boom", "ok2"]
    _run(J.judge_all(claims, [], checkpoint=ckpt))

    written = [l for l in ckpt.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(written) == 2, f"失败结果被写进了 checkpoint:{written}"
    assert all("boom" not in l for l in written)
