"""幻觉率评估:抽断言 → 定位 → 判定 → 汇总(Slice E4)。

用法:
    # 对 results/ 下全部快照跑
    venv/bin/python -m evals.investment_eval.hallucination_report

    # 只跑一份,并换判定模型(候选须与写作模型不同门,见 judge.py)
    venv/bin/python -m evals.investment_eval.hallucination_report \
        -f evals/investment_eval/results/xxx.json -m deepseek-v4-pro

判定结果逐条写入 results/<research_id>.verdicts.jsonl —— 中断可续跑,
换模型重评时删掉对应文件即可。
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

from gpt_researcher.config import Config  # noqa: E402

from .artifacts import RESULTS_DIR, ResearchArtifact  # noqa: E402
from .claim_extract import extract_claims  # noqa: E402
from .judge import DEFAULT_JUDGE_MODEL, judge_all, summarize  # noqa: E402


async def evaluate_artifact(cfg, artifact: ResearchArtifact, model: str,
                            results_dir: Path) -> dict | None:
    if not artifact.report_md.strip():
        return None

    t0 = time.time()
    claims = await extract_claims(cfg, artifact.report_md)
    if not claims:
        print(f"  {artifact.research_id[:34]:<36} 未抽出任何数字型断言,跳过")
        return None

    checkpoint = results_dir / f"{artifact.research_id}.verdicts.jsonl"
    verdicts = await judge_all(claims, artifact.sources, model=model,
                               llm_kwargs=cfg.llm_kwargs, checkpoint=checkpoint)
    s = summarize(verdicts)
    s.update({
        "research_id": artifact.research_id,
        "label": artifact.label,
        "case_id": artifact.run_config.get("case_id"),
        "elapsed": time.time() - t0,
        "verdicts": verdicts,
    })
    return s


def print_board(rows: list[dict]) -> None:
    print()
    print("幻觉率")
    print("=" * 96)
    print(f"{'用例':<6}{'类别':<20}{'断言':>6}{'有支撑':>8}{'矛盾':>7}{'无据':>7}"
          f"{'幻觉率':>9}{'无据率':>9}{'降级':>6}{'调用失败':>9}")
    print("-" * 96)
    for r in rows:
        hr = r["hallucination_rate"]
        ur = r["unsupported_rate"]
        print(f"{str(r.get('case_id') or '—'):<6}{r['label'][:19]:<20}"
              f"{r['total']:>6}{r['SUPPORTED']:>8}{r['CONTRADICTED']:>7}{r['NOT_FOUND']:>7}"
              f"{('  —  ' if hr is None else f'{hr:.1%}'):>8}"
              f"{('  —  ' if ur is None else f'{ur:.1%}'):>9}"
              f"{r['downgraded']:>6}{r.get('call_failed', 0):>9}")
    print("-" * 96)
    tot = sum(r["total"] for r in rows)
    con = sum(r["CONTRADICTED"] for r in rows)
    nf = sum(r["NOT_FOUND"] for r in rows)
    failed = sum(r.get("call_failed", 0) for r in rows)
    if tot:
        print(f"{'合计':<26}{tot:>6}{tot - con - nf:>8}{con:>7}{nf:>7}"
              f"{con / tot:>8.1%}{nf / tot:>9.1%}{'':>6}{failed:>9}")
    print()
    print("幻觉率 = 原文写的与报告写的不一致(改错了)")
    print("无据率 = 原文里根本没有这个数(凭空写,或引错了源)")
    if failed:
        # 一次 DeepSeek 余额耗尽曾让 71 条里 62 条判定失败;若把它们算进无据,
        # 看板会显示"无据率 90%",而快照本身毫无问题。所以必须显式喊出来。
        print()
        print(f"⚠️  有 {failed} 条判定调用失败(超时 / 余额 / 网络),已从统计中剔除。")
        print("    本轮数字建立在不完整的样本上,补齐凭据后重跑再作结论。")


def print_contradictions(rows: list[dict], limit: int = 20) -> None:
    items = [(r, v) for r in rows for v in r["verdicts"] if v.verdict == "CONTRADICTED"]
    if not items:
        print("\n没有被判为矛盾的断言。")
        return
    print(f"\n被判为矛盾的断言(共 {len(items)} 条,列出前 {min(limit, len(items))} 条供人工核对)")
    print("=" * 96)
    for r, v in items[:limit]:
        print(f"\n[{r.get('case_id') or '—'} · {r['label']}]")
        print(f"  报告写的 : {v.claim[:150]}")
        print(f"  原文写的 : {v.evidence_quote[:150]}")
        print(f"  判定理由 : {v.reason[:150]}")
        if v.source_url:
            print(f"  来源     : {v.source_url}")


async def main_async(args):
    cfg = Config()
    results_dir = Path(args.results_dir)
    files = [args.file] if args.file else sorted(glob.glob(str(results_dir / "*.json")))

    print(f"判定模型:{args.model}\n")
    rows = []
    for path in files:
        art = ResearchArtifact.load(path)
        if not art.report_md.strip():
            continue
        r = await evaluate_artifact(cfg, art, args.model, results_dir)
        if not r:
            continue
        rows.append(r)
        print(f"  ✔ {str(r.get('case_id') or '—'):<4} {r['label']:<20} "
              f"断言 {r['total']:>3} | 矛盾 {r['CONTRADICTED']:>2} | 无据 {r['NOT_FOUND']:>3} "
              f"| {r['elapsed']:.0f}s")

    print_board(rows)
    if args.show_contradictions:
        print_contradictions(rows)


def main():
    p = argparse.ArgumentParser(description="幻觉率评估")
    p.add_argument("-r", "--results-dir", default=str(RESULTS_DIR))
    p.add_argument("-f", "--file", default=None, help="只评这一份快照")
    p.add_argument("-m", "--model", default=DEFAULT_JUDGE_MODEL)
    p.add_argument("-c", "--show-contradictions", action="store_true", default=True)
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
