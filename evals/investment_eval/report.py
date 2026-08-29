"""溯源率汇总报表(Slice E2)。

用法:
    # 给 results/ 下所有证据快照打分
    venv/bin/python -m evals.investment_eval.report

    # 给没有快照的历史报告打分(命中率不可计算,显示为 —)
    venv/bin/python -m evals.investment_eval.report -m outputs/task_xxx.md ...

    # 两者一起,做修改前后对照
    venv/bin/python -m evals.investment_eval.report -m outputs/*.md
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

from .artifacts import RESULTS_DIR, ResearchArtifact
from .traceability import TraceabilityScore, score_artifact, score_report


def _pct(v: float | None) -> str:
    return "  —  " if v is None else f"{v:>5.0%}"


def _frac(num: int, den: int) -> str:
    return f"{num}/{den}" if den else "0/0"


def print_board(scores: list[TraceabilityScore]) -> None:
    if not scores:
        print("没有可打分的报告。")
        return

    print()
    print("溯源率")
    print("=" * 104)
    print(
        f"{'报告':<26}{'类别':<20}"
        f"{'链接有效率':>11}{'链接命中率':>11}{'数字覆盖率':>11}"
        f"{'死锚':>6}{'仅首页':>7}{'来源数':>7}"
    )
    print("-" * 104)
    for s in scores:
        print(
            f"{s.research_id[:25]:<26}{s.label[:19]:<20}"
            f"{_pct(s.link_validity):>11}{_pct(s.link_hit_rate):>11}{_pct(s.numeric_coverage):>11}"
            f"{s.dead_anchors:>6}{s.bare_domain_links:>7}{s.distinct_domains:>7}"
        )
    print("-" * 104)

    print("\n明细")
    for s in scores:
        print(f"\n  {s.research_id}  [{s.label}]")
        print(
            f"    链接      共 {s.links_total} 条,其中真 URL {s.links_http} 条"
            f"(死锚 {s.dead_anchors},仅指向域名首页 {s.bare_domain_links})"
        )
        if s.source_urls_known:
            print(f"    命中      {_frac(s.links_matched, s.links_http)} 条 URL 出现在本次资料清单中")
            if s.unmatched_examples:
                print("    未命中示例:")
                for u in s.unmatched_examples:
                    print(f"                {u}")
        else:
            print("    命中      无资料清单,不可计算(不等于 0)")
        print(
            f"    数字      正文含数字的句子 {s.prose_sentences_with_numbers} 句,"
            f"其中 {s.prose_sentences_cited} 句带引用;另有表格行 {s.table_lines} 行未计入"
        )
        if s.top_domain_share is not None:
            flag = "  ⚠️ 来源过度集中" if s.top_domain_share >= 0.5 and s.links_http >= 10 else ""
            print(
                f"    来源      独立域名 {s.distinct_domains} 个,"
                f"最大占比 {s.top_domain} {s.top_domain_share:.0%}{flag}"
            )

    print("\n提示:只有「链接命中率」能识破指向发布商首页的假溯源 —— 那类链接在「链接有效率」上是满分。")


def main() -> None:
    p = argparse.ArgumentParser(description="溯源率汇总报表")
    p.add_argument("-r", "--results-dir", default=str(RESULTS_DIR), help="证据快照目录")
    p.add_argument("-m", "--markdown", nargs="*", default=[], help="额外给这些 .md 报告打分(无资料清单)")
    args = p.parse_args()

    scores: list[TraceabilityScore] = []
    for path in sorted(glob.glob(str(Path(args.results_dir) / "*.json"))):
        scores.append(score_artifact(ResearchArtifact.load(path)))
    for path in args.markdown:
        md = Path(path).read_text(encoding="utf-8")
        scores.append(score_report(md, source_urls=None, research_id=Path(path).stem, label="(无快照)"))

    print_board(scores)


if __name__ == "__main__":
    main()
