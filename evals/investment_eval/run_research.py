"""跑投研查询并落证据快照(Slice E1)。

用法:
    # 跑内置的 5 类基准用例
    venv/bin/python -m evals.investment_eval.run_research

    # 跑指定用例文件里的前 2 条
    venv/bin/python -m evals.investment_eval.run_research -c evals/investment_eval/inputs/cases.jsonl -n 2

    # 跑单个查询
    venv/bin/python -m evals.investment_eval.run_research -q "Analyze Micron Technology (MU)"

产出:evals/investment_eval/results/<research_id>.json —— 报告 + 全部原文 +
带来源的上下文块。后续 traceability / judge 都只读这个文件,不再重跑研究。

骨架沿用上游 evals/hallucination_eval/run_eval.py 的形状(jsonl 用例 → 逐条跑
→ 落盘),区别是我们把证据存下来而不是就地评完即弃。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# .env 必须按绝对路径加载:load_dotenv() 默认从调用者所在目录往上找,
# 从别处调用本脚本时会静默找不到 key,表现为"模型/检索全挂"。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

from gpt_researcher.investment.researcher import InvestmentResearcher  # noqa: E402

from .artifacts import (  # noqa: E402
    RESULTS_DIR,
    ResearchArtifact,
    SourceDoc,
    parse_context_blocks,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_CASES = Path(__file__).parent / "inputs" / "cases.jsonl"


def load_cases(path: Path, limit: int | None = None) -> list[dict]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    ]
    return cases[:limit] if limit else cases


def _snapshot_config(cfg) -> dict:
    """记下这次是哪套配置产出的 —— 前后对比时必须知道变量控制住了没有。"""
    keys = (
        "fast_llm", "smart_llm", "strategic_llm", "embedding",
        "retriever", "max_search_results_per_query", "report_source",
    )
    out = {}
    for k in keys:
        v = getattr(cfg, k, None)
        if v is not None:
            out[k] = str(v)
    return out


def _active_retrievers(gr) -> list[str]:
    """实际装到实例上的检索器类名。

    只记 cfg.retriever 会说谎:那是**意图**,不是**事实**。曾经因此白跑一轮完整
    评估 —— 快照里写着 retriever=investment_tavily,而运行时用的是
    TavilySearch(cfg 改晚了,且 cfg.retrievers 复数优先级更高),数字纹丝不动,
    只能靠翻日志才发现。之后一律记类名。
    """
    return [c.__name__ for c in getattr(gr, "retrievers", [])]


async def run_one(case: dict, results_dir: Path) -> ResearchArtifact | None:
    query = case["query"]
    expected = case.get("expected_label")
    logger.info("▶ %s", query)
    t0 = time.time()

    researcher = InvestmentResearcher(
        query=query,
        report_type=case.get("report_type", "research_report"),
        report_source=case.get("report_source", "web"),
        tone=case.get("tone"),
    )

    active = _active_retrievers(researcher.gpt_researcher)
    logger.info("生效检索器:%s", active or "(空)")
    if not active:
        raise RuntimeError("没有任何检索器生效,跑下去只会产出空报告")

    try:
        report_md = await researcher.run()
    except Exception:
        logger.exception("研究失败,跳过:%s", query)
        return None

    gr = researcher.gpt_researcher
    label = getattr(
        getattr(researcher, "classification", None), "label", "unknown"
    )
    chunks, unattributed = parse_context_blocks(gr.context)

    artifact = ResearchArtifact(
        research_id=researcher.research_id,
        query=query,
        label=label,
        created_at=datetime.now().isoformat(timespec="seconds"),
        report_md=report_md or "",
        sources=[
            SourceDoc(
                url=s.get("url", ""),
                title=s.get("title", "") or "",
                raw_content=s.get("raw_content") or "",
            )
            for s in gr.get_research_sources()
        ],
        context_chunks=chunks,
        unattributed_context=unattributed,
        run_config={
            **_snapshot_config(gr.cfg),
            "active_retrievers": _active_retrievers(gr),
            "elapsed_sec": round(time.time() - t0, 1),
            "expected_label": expected,
            "case_id": case.get("id"),
        },
    )
    # 写报告失败时 report_md 会是空串,但研究阶段的证据仍然值钱(一次 value_chain
    # 采集了 150 篇资料、18 万字,耗时 38 分钟)。所以照常落盘、标记 report_ok=False,
    # 但不计入成功 —— 空报告无法评估,混进看板会污染统计。
    artifact.run_config["report_ok"] = bool(artifact.report_md.strip())
    path = artifact.save(results_dir)

    traceable = sum(1 for c in chunks if c.has_real_source)
    flag = "" if expected in (None, label) else f"  ⚠️ 期望 {expected}"
    detail = (
        "L0-A=%s%s | 报告 %d 字符 | 卷宗 %d 篇 | 上下文块 %d(带真 URL %d)| %.0fs → %s"
        % (label, flag, len(artifact.report_md), len(artifact.sources),
           len(chunks), traceable, time.time() - t0, path.name)
    )
    if not artifact.run_config["report_ok"]:
        logger.error(
            "✘ %s | 写报告失败,产出为空(研究阶段的证据已保留,可据此重写)| %s",
            artifact.research_id, detail,
        )
        return None

    logger.info("✔ %s | %s", artifact.research_id, detail)
    return artifact


async def main_async(args) -> None:
    results_dir = Path(args.output_dir)
    if args.query:
        cases = [{"id": "adhoc", "query": args.query}]
    else:
        cases = load_cases(Path(args.cases), args.num)

    logger.info("共 %d 条用例 → %s", len(cases), results_dir)
    done = []
    for case in cases:
        art = await run_one(case, results_dir)
        if art:
            done.append(art)

    print("\n" + "=" * 78)
    print(f"{'case':<8}{'L0-A':<20}{'块':>5}{'带URL':>7}{'卷宗':>6}{'报告字符':>9}")
    print("-" * 78)
    for a in done:
        tr = sum(1 for c in a.context_chunks if c.has_real_source)
        print(
            f"{str(a.run_config.get('case_id')):<8}{a.label:<20}"
            f"{len(a.context_chunks):>5}{tr:>7}{len(a.sources):>6}{len(a.report_md):>9}"
        )
    print("=" * 78)
    print(f"{len(done)}/{len(cases)} 成功,快照写入 {results_dir}")


def main() -> None:
    p = argparse.ArgumentParser(description="跑投研查询并落证据快照")
    p.add_argument("-c", "--cases", default=str(DEFAULT_CASES), help="用例 jsonl 路径")
    p.add_argument("-n", "--num", type=int, default=None, help="只跑前 N 条")
    p.add_argument("-q", "--query", default=None, help="跑单个查询(忽略 --cases)")
    p.add_argument("-o", "--output-dir", default=str(RESULTS_DIR), help="快照输出目录")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
