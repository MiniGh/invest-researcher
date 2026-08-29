"""溯源率指标的回归测试(Slice E2)。

用例取自五篇真实报告里实际出现过的三种失败形态:
  - 死锚点 `](#)`：上下文里来源为 "Source: None" 时模型的退化输出
  - 发布商首页链接：形式上是 http、实际不可核对的"假溯源"
  - 来源高度集中：约九成数字回溯到同一个 URL,而逐数字挂链接把它盖住了
"""
import pytest

from evals.investment_eval.artifacts import ResearchArtifact, SourceDoc
from evals.investment_eval.traceability import score_artifact, score_report

REAL = "https://example.com/mu-fq3-results"
OTHER = "https://other.com/hbm-share-2026"


def test_dead_anchors_score_zero_validity():
    md = "Revenue was $13.64B ([MU FQ3](#)), up 57% ([MU PR](#sina))."
    s = score_report(md, source_urls={REAL})

    assert s.links_total == 2
    assert s.dead_anchors == 2
    assert s.link_validity == 0.0
    assert s.link_hit_rate is None or s.links_http == 0


def test_publisher_homepage_passes_validity_but_fails_hit_rate():
    """核心用例:只看指标 1 会给满分,指标 2 才能识破。"""
    md = "HBM was worth $2.95B in 2025 ([Fortune](https://www.fortunebusinessinsights.com))."
    s = score_report(md, source_urls={REAL})

    assert s.link_validity == 1.0          # 形式上完全合格
    assert s.link_hit_rate == 0.0          # 实际一条都追不回去
    assert s.bare_domain_links == 1        # 诊断项直接点名


def test_hit_rate_counts_only_urls_in_the_source_list():
    md = f"A ([x]({REAL})) and B ([y]({OTHER})) and C ([z](https://made-up.com/article))."
    s = score_report(md, source_urls={REAL, OTHER})

    assert s.links_http == 3
    assert s.links_matched == 2
    assert s.link_hit_rate == pytest.approx(2 / 3)
    assert s.unmatched_examples == ["https://made-up.com/article"]


def test_trailing_slash_and_fragment_do_not_break_matching():
    md = f"A ([x]({REAL}/#section))."
    s = score_report(md, source_urls={REAL})

    assert s.link_hit_rate == 1.0


def test_unknown_source_list_returns_none_not_zero():
    """给没有快照的历史报告打分时,「算不出来」不能显示成「一条都没命中」。"""
    s = score_report(f"A ([x]({REAL})).", source_urls=None)

    assert s.link_validity == 1.0
    assert s.link_hit_rate is None
    assert s.source_urls_known is False


def test_numeric_coverage_counts_only_sentences_containing_numbers():
    md = (
        "This sentence has no figures at all. "
        f"Revenue was $13.64 billion ([MU]({REAL})). "
        "Gross margin was 56.8% with no citation."
    )
    s = score_report(md, source_urls={REAL})

    assert s.prose_sentences_with_numbers == 2
    assert s.prose_sentences_cited == 1
    assert s.numeric_coverage == 0.5


def test_table_rows_are_excluded_from_prose_coverage():
    """表格里的数字几乎从不带行内引用,混进正文会把覆盖率压低成假信号。"""
    md = (
        f"Revenue was $13.64 billion ([MU]({REAL})).\n"
        "\n"
        "| Metric | Value |\n"
        "|---|---|\n"
        "| Revenue | $13.64B |\n"
        "| Margin | 56.8% |\n"
    )
    s = score_report(md, source_urls={REAL})

    assert s.prose_sentences_with_numbers == 1
    assert s.numeric_coverage == 1.0
    assert s.table_lines == 4


def test_source_concentration_is_reported():
    md = " ".join(f"Fact {i} is 10% ([s]({REAL}))." for i in range(9)) + f" One more ([o]({OTHER}))."
    s = score_report(md, source_urls={REAL, OTHER})

    assert s.distinct_domains == 2
    assert s.top_domain == "example.com"
    assert s.top_domain_share == pytest.approx(0.9)


def test_empty_report_yields_none_not_crash():
    s = score_report("", source_urls=set())

    assert s.link_validity is None
    assert s.numeric_coverage is None


def test_score_artifact_uses_the_snapshot_source_list():
    art = ResearchArtifact(
        research_id="rid_1", query="q", label="company_profile", created_at="t",
        report_md=f"Revenue was $13.64B ([MU]({REAL})).",
        sources=[SourceDoc(url=REAL, title="T", raw_content="Revenue was $13.64B.")],
    )
    s = score_artifact(art)

    assert (s.research_id, s.label) == ("rid_1", "company_profile")
    assert s.link_hit_rate == 1.0
    assert s.numeric_coverage == 1.0
