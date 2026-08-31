"""在已知答案的验证集上测判定准确率,并横向对比候选模型(Slice E4)。

用法:
    venv/bin/python -m evals.investment_eval.validation.run
    venv/bin/python -m evals.investment_eval.validation.run -m Qwen/Qwen3.6-35B-A3B

输出每个模型的总准确率与分类别准确率。**分类别看比总分重要** —— 一个把
所有题都答成 NOT_FOUND 的模型总分能有三分之一,但它对幻觉检测毫无用处。
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

from gpt_researcher.config import Config  # noqa: E402

from ..artifacts import ResearchArtifact  # noqa: E402
from ..judge import judge_claim  # noqa: E402
from .build import load  # noqa: E402

# 候选必须与写作模型不同门(见 judge.py 的 FORBIDDEN_JUDGE_SUBSTR)。写手是
# 智谱 GLM,所以 GLM 系出局;硅基流动上的 Qwen 因账户余额为零不可用。剩下
# DeepSeek 官方直连的两个型号。
CANDIDATES = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
]

PASS_THRESHOLD = 0.85


def _all_sources():
    """验证集的题目取自这些原文,判定时也要在同一批原文里找证据。"""
    out, seen = [], set()
    for p in sorted(glob.glob(str(PROJECT_ROOT / "evals/investment_eval/results/*.json"))):
        for s in ResearchArtifact.load(p).sources:
            if s.raw_content and s.url not in seen:
                seen.add(s.url)
                out.append(s)
    return out


class _Wrapped:
    """judge_claim 期望有 .claim 属性。"""

    def __init__(self, text):
        self.claim = text


async def evaluate(model: str, cases, sources, cfg, concurrency: int = 8) -> dict:
    sem = asyncio.Semaphore(concurrency)

    async def one(case):
        async with sem:
            v = await judge_claim(_Wrapped(case.claim), sources, model=model,
                                  llm_kwargs=cfg.llm_kwargs)
            return case, v

    t0 = time.time()
    pairs = await asyncio.gather(*[one(c) for c in cases])
    elapsed = time.time() - t0

    correct = sum(1 for c, v in pairs if v.verdict == c.expected)
    by_kind = Counter()
    tot_kind = Counter()
    for c, v in pairs:
        tot_kind[c.kind] += 1
        if v.verdict == c.expected:
            by_kind[c.kind] += 1

    return {
        "model": model,
        "accuracy": correct / len(cases) if cases else 0.0,
        "correct": correct,
        "total": len(cases),
        "by_kind": {k: (by_kind[k], tot_kind[k]) for k in tot_kind},
        "downgraded": sum(1 for _, v in pairs if v.downgraded),
        "elapsed": elapsed,
        "pairs": pairs,
    }


async def main_async(args):
    cfg = Config()
    cases = load()
    sources = _all_sources()
    print(f"验证集 {len(cases)} 题 | 卷宗 {len(sources)} 篇 | 及格线 {PASS_THRESHOLD:.0%}\n")

    models = [args.model] if args.model else CANDIDATES
    results = []
    for m in models:
        try:
            r = await evaluate(m, cases, sources, cfg)
        except Exception as e:
            print(f"  {m:<38} ✗ {type(e).__name__}: {str(e)[:70]}")
            continue
        results.append(r)
        kinds = "  ".join(
            f"{k}={a}/{b}" for k, (a, b) in sorted(r["by_kind"].items())
        )
        flag = "✅" if r["accuracy"] >= PASS_THRESHOLD else "⚠️"
        print(f"  {flag} {m:<38} 准确率 {r['accuracy']:>5.0%}  ({r['correct']}/{r['total']})"
              f"  {kinds}  降级 {r['downgraded']}  {r['elapsed']:.0f}s")

    if not results:
        return
    best = max(results, key=lambda r: r["accuracy"])
    print(f"\n最佳:{best['model']}  准确率 {best['accuracy']:.0%}")
    if best["accuracy"] < PASS_THRESHOLD:
        print("⚠️ 全部候选均未达及格线 —— 幻觉率数字不可信,需要先解决判定准确率")

    if args.show_errors:
        print("\n判错的题:")
        for c, v in best["pairs"]:
            if v.verdict != c.expected:
                print(f"  期望 {c.expected:<13} 实得 {v.verdict:<13} [{c.kind}]")
                print(f"     断言: {c.claim[:110]}")
                print(f"     理由: {v.reason[:110]}")


def main():
    p = argparse.ArgumentParser(description="判定准确率验证与选型")
    p.add_argument("-m", "--model", default=None, help="只测这一个模型")
    p.add_argument("-e", "--show-errors", action="store_true", help="打印判错的题")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
