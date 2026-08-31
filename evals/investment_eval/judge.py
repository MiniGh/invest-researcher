"""逐条判定断言是否有原文支撑(Slice E4)。

**三态而不是二态**

    SUPPORTED     原文里有,数字对得上
    CONTRADICTED  原文里有,但数字对不上  ← 这才是真幻觉
    NOT_FOUND     原文里根本没有          ← 可能是编造,也可能是引错源

上游 evals/hallucination_eval 给的是"整篇是否合格"的二元结论,说不出哪个
数字有问题;分成三态之后,「改错了」和「凭空写」这两种性质完全不同的问题
才能分开统计、分开修。

**强制抄写原文,并用代码校验**

judge 必须把它依据的那句原文原样抄进 evidence_quote。抄完之后本模块用字符
匹配去原文里核对 —— 抄不出来就不许判 SUPPORTED。这一条是防止判定模型自己
幻觉的关键:模型可以"觉得"原文支持,但它没法凭空抄出一句原文里不存在的话
而不被发现。

**判定模型必须换一家**

不能用写报告的同一个模型来判自己写的报告:同源模型对自己的输出有系统性偏袒,
它编错的地方正是它认为对的地方。本项目写手是 DeepSeek,故 judge 一律用别家。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import json_repair

from gpt_researcher.utils.llm import create_chat_completion

from .locate import locate

logger = logging.getLogger(__name__)

# 与写手(DeepSeek)不同源即可。默认取非思考型 —— 思考型会调用自身世界知识
# 去补全"我记得这个数就是对的",恰恰是这里要避免的。
#
# 选型依据(76 题合成验证集,见 validation/):
#   Qwen/Qwen3-30B-A3B-Instruct-2507   76%   31s
#   Qwen/Qwen3.6-35B-A3B               96%   82s   ← 默认
#   zai-org/GLM-5.2                    99%  629s
# 96% 与 99% 的差距是 76 题里的 2 题,统计上不显著;而 GLM-5.2 慢 7.7 倍
# (425 条断言 59 分钟 vs 8 分钟),会让"改模板→重评→对比"这个循环跑不动。
# 需要更高置信度时显式指定 GLM-5.2。
DEFAULT_JUDGE_MODEL = "Qwen/Qwen3.6-35B-A3B"
HIGH_CONFIDENCE_JUDGE_MODEL = "zai-org/GLM-5.2"
FORBIDDEN_JUDGE_SUBSTR = ("deepseek",)

VERDICTS = ("SUPPORTED", "CONTRADICTED", "NOT_FOUND")

# 单次判定的墙钟上限(秒)。实测正常判定 1.5-2s。
CALL_TIMEOUT = 90

JUDGE_PROMPT = """\
You are fact-checking ONE claim against source excerpts. Be strict and literal.

Decide exactly one verdict:
- SUPPORTED: an excerpt states this figure for this subject and period, and the
  number matches (allowing for unit conversion, e.g. $13.64 billion = 13,640 million).
- CONTRADICTED: an excerpt states this figure for this subject and period, but
  the number is different.
- NOT_FOUND: no excerpt states this figure. Use this when the excerpts are about
  something else, cover a different period, or simply do not contain the number.

Critical rules:
- Judge ONLY from the excerpts below. Do NOT use anything you know about these
  companies. If the excerpts do not contain the figure, the answer is NOT_FOUND
  even if you believe the claim is true.
- For SUPPORTED or CONTRADICTED you MUST copy the exact sentence from an excerpt
  into evidence_quote, character for character. If you cannot copy such a
  sentence, the verdict is NOT_FOUND.
- A figure for a different period or a different subject is NOT support.
- Some excerpts are retrieved by topic, not by the number — they may discuss the
  same metric with a DIFFERENT figure. That is exactly what CONTRADICTED means:
  the excerpt covers this subject, metric and period, but states another number.

CLAIM:
{CLAIM}

SOURCE EXCERPTS:
{EXCERPTS}

Return ONLY this JSON, no prose:
{"verdict": "SUPPORTED|CONTRADICTED|NOT_FOUND", "evidence_quote": "<exact sentence from an excerpt, or empty>", "reason": "<one short sentence>"}
"""


@dataclass
class Verdict:
    """一条断言的判定结果。"""

    claim: str
    verdict: str = "NOT_FOUND"
    evidence_quote: str = ""
    reason: str = ""
    source_url: str = ""
    quote_verified: bool = False   # evidence_quote 是否真能在原文中找到
    downgraded: bool = False       # 因抄写核验失败而被降级
    candidates: int = 0            # 代码定位找到的候选段数
    model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize(text: str) -> str:
    """比对抄写时的归一化:折叠空白、统一引号。不做更激进的处理,
    否则会把"抄错了"洗成"抄对了"。"""
    t = re.sub(r"\s+", " ", text or "").strip().lower()
    for a, b in (("“", '"'), ("”", '"'), ("’", "'"), ("‘", "'"), ("—", "-"), ("–", "-")):
        t = t.replace(a, b)
    return t


def verify_quote(quote: str, excerpts: list[str]) -> bool:
    """judge 抄的那句话是否真的出现在原文里。

    这是整套防护的落点 —— 模型可以声称原文支持,但没法凭空抄出一句
    原文里不存在的话而不被发现。太短的片段不算数(单个数字谁都能"抄对")。
    """
    q = _normalize(quote)
    if len(q) < 25:
        return False
    return any(q in _normalize(e) for e in excerpts)


def _build_excerpts(candidates, limit: int = 6) -> tuple[str, list[str]]:
    picked = candidates[:limit]
    texts = [c.excerpt for c in picked]
    block = "\n\n".join(
        f"[{i + 1}] (source: {c.source_url})\n{c.excerpt}" for i, c in enumerate(picked)
    )
    return block, texts


async def judge_claim(claim, sources, model: str = DEFAULT_JUDGE_MODEL,
                      provider: str = "openai", llm_kwargs: dict | None = None) -> Verdict:
    """判定单条断言。定位不到候选时直接返回 NOT_FOUND,不调用模型。"""
    text = getattr(claim, "claim", str(claim))
    if any(s in model.lower() for s in FORBIDDEN_JUDGE_SUBSTR):
        raise ValueError(
            f"judge 不能用与写手同源的模型({model}):同源模型对自己的输出有系统性偏袒"
        )

    loc = locate(text, sources)
    if not loc.found:
        # 既搜不到这个数字,也找不到讲同一件事的段落 —— 不必调用模型。
        # 注意这与"数字没命中但主题命中"不同:后者恰恰是 CONTRADICTED 的形态,
        # 必须交给模型比较,不能在这里短路掉。
        return Verdict(claim=text, verdict="NOT_FOUND", candidates=0, model=model,
                       reason="卷宗中既无该数字、也无相关主题段落(代码定位,未调用模型)")

    block, texts = _build_excerpts(loc.candidates)
    prompt = JUDGE_PROMPT.replace("{CLAIM}", text).replace("{EXCERPTS}", block)

    try:
        # 硬性墙钟上限,理由同 claim_extract:底层重试会把超时放大到一个多小时,
        # 几百条断言里只要有一条挂住,整轮就停在那里。
        raw = await asyncio.wait_for(
            create_chat_completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                llm_provider=provider,
                max_tokens=600,
                llm_kwargs={**(llm_kwargs or {}), "timeout": CALL_TIMEOUT},
            ),
            timeout=CALL_TIMEOUT * 2,
        )
    except asyncio.TimeoutError:
        logger.warning("判定超时(%ds)", CALL_TIMEOUT * 2)
        return Verdict(claim=text, verdict="NOT_FOUND", candidates=len(loc.candidates),
                       model=model, reason="判定超时")
    except Exception as e:
        logger.warning("判定调用失败:%s", e)
        return Verdict(claim=text, verdict="NOT_FOUND", candidates=len(loc.candidates),
                       model=model, reason=f"判定调用失败:{e}")

    try:
        parsed = json_repair.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("返回的不是对象")
    except Exception:
        return Verdict(claim=text, verdict="NOT_FOUND", candidates=len(loc.candidates),
                       model=model, reason="判定返回的不是合法 JSON")

    verdict = str(parsed.get("verdict", "")).strip().upper()
    if verdict not in VERDICTS:
        verdict = "NOT_FOUND"
    quote = str(parsed.get("evidence_quote", "") or "").strip()
    ok = verify_quote(quote, texts)

    v = Verdict(
        claim=text,
        verdict=verdict,
        evidence_quote=quote,
        reason=str(parsed.get("reason", "") or "").strip(),
        source_url=loc.candidates[0].source_url if loc.candidates else "",
        quote_verified=ok,
        candidates=len(loc.candidates),
        model=model,
    )

    # 抄写核验不过 → 降级。宁可少认一条 SUPPORTED,也不让判定模型
    # 靠"我觉得原文支持"蒙混过去。
    if verdict in ("SUPPORTED", "CONTRADICTED") and not ok:
        v.verdict = "NOT_FOUND"
        v.downgraded = True
        v.reason = (v.reason + " | 抄写核验未通过,已降级").strip(" |")
    return v


async def judge_all(claims, sources, model: str = DEFAULT_JUDGE_MODEL,
                    provider: str = "openai", llm_kwargs: dict | None = None,
                    concurrency: int = 8, checkpoint: Path | None = None) -> list[Verdict]:
    """并发判定一批断言,逐条增量落盘。

    checkpoint 存在时会跳过已判定的断言 —— 几百条跑到一半断网不必从头再来。
    """
    done: dict[str, dict] = {}
    if checkpoint and checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    d = json.loads(line)
                    done[d["claim"]] = d
                except Exception:
                    continue
        logger.info("从检查点恢复 %d 条已判定结果", len(done))

    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    async def run(c):
        text = getattr(c, "claim", str(c))
        if text in done:
            return Verdict(**done[text])
        async with sem:
            v = await judge_claim(c, sources, model=model, provider=provider,
                                  llm_kwargs=llm_kwargs)
        if checkpoint:
            async with lock:
                with checkpoint.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(v.to_dict(), ensure_ascii=False) + "\n")
        return v

    return list(await asyncio.gather(*[run(c) for c in claims]))


def summarize(verdicts: list[Verdict]) -> dict:
    """汇总成幻觉率与无据率。"""
    n = len(verdicts)
    if not n:
        return {"total": 0}
    cnt = {v: sum(1 for x in verdicts if x.verdict == v) for v in VERDICTS}
    return {
        "total": n,
        **cnt,
        "hallucination_rate": cnt["CONTRADICTED"] / n,
        "unsupported_rate": cnt["NOT_FOUND"] / n,
        "downgraded": sum(1 for x in verdicts if x.downgraded),
        "no_candidate": sum(1 for x in verdicts if x.candidates == 0),
    }
