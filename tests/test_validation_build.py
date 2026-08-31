"""合成验证集构造的回归测试(Slice E4)。

这套题的作用是衡量判定模型准不准。**题目本身出错了,测出来的准确率就没有
意义** —— 实测第一版就造出过 "9B, with an operating margin of 17.95%..."
这类从数字中间起手的残句,以及表格碎片。所以这里逐条锁定题目质量。

局限(必须写明):构造出来的题可能比真实情况干净 —— 数字改动明显、编造的
指标离谱。真实报告里的难题是单位换算、口径不同、时间错配,这套题测不到。
因此得到的准确率是上限,不是保证。
"""
import pytest

from evals.investment_eval.artifacts import SourceDoc
from evals.investment_eval.validation.build import ValidationCase, build, load, save

GOOD = (
    "Micron reported quarterly revenue of 13.64 billion dollars for the period. "
    "Gross margin expanded to 56.8 percent on stronger HBM pricing. "
    "Management guided to continued tightness through calendar 2026 and beyond."
)


def src(text=GOOD, url="https://example.com/a", title="Micron"):
    return SourceDoc(url=url, title=title, raw_content=text * 3)


def test_builds_all_three_kinds():
    cases = build([src()], per_source=2)
    kinds = {c.kind for c in cases}
    assert kinds == {"verbatim", "altered", "fabricated"}


def test_expected_labels_match_their_kind():
    for c in build([src()], per_source=2):
        assert c.expected == {
            "verbatim": "SUPPORTED",
            "altered": "CONTRADICTED",
            "fabricated": "NOT_FOUND",
        }[c.kind]


def test_verbatim_claims_appear_in_the_source():
    """原样题必须真能在原文里找回,否则它的标准答案就是错的。"""
    s = src()
    for c in build([s], per_source=2):
        if c.kind == "verbatim":
            assert c.claim in " ".join(s.raw_content.split())


def test_altered_claims_actually_differ_from_the_original():
    for c in build([src()], per_source=2):
        if c.kind == "altered":
            assert c.claim != c.origin, "改写后和原句一样,等于没改"


def test_altered_keeps_the_same_order_of_magnitude():
    """改成 10 倍会让题目过于简单,测不出对数值细微不符的敏感度。"""
    import re
    for c in build([src()], per_source=2):
        if c.kind != "altered":
            continue
        a = [float(x) for x in re.findall(r"\d+\.?\d*", c.origin)]
        b = [float(x) for x in re.findall(r"\d+\.?\d*", c.claim)]
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        for x, y in diff:
            assert 0.1 < (y / x if x else 1) < 10, f"量级变了:{x} → {y}"


def test_no_sentence_fragments():
    """题目必须是完整句 —— 第一版造出过从数字中间起手的残句。"""
    for c in build([src()], per_source=2):
        assert c.claim[0].isupper() or c.claim[0] in "$€£", f"从中间起手:{c.claim[:60]}"


def test_table_rows_are_excluded():
    """表格行不是可核对的陈述句。"""
    table = "| Metric | Value |\n| Revenue | 13.64 billion |\n" * 20
    cases = build([src(table)], per_source=3)
    assert not any("|" in c.claim for c in cases)


def test_short_or_wordless_lines_are_excluded():
    cases = build([src("Up 5 percent. " * 40)], per_source=3)
    assert all(len(c.claim.split()) >= 7 for c in cases if c.kind != "fabricated")


def test_source_without_numbers_yields_only_fabricated():
    cases = build([src("The company continued to execute against its long-term strategy. " * 8)],
                  per_source=2)
    assert all(c.kind == "fabricated" for c in cases)


def test_build_is_deterministic():
    """同样的输入要出同样的题,否则前后两次选型结果不可比。"""
    a = build([src()], per_source=2)
    b = build([src()], per_source=2)
    assert [c.claim for c in a] == [c.claim for c in b]


def test_save_and_load_roundtrip(tmp_path):
    cases = build([src()], per_source=2)
    path = save(cases, tmp_path / "cases.jsonl")
    assert [c.to_dict() for c in load(path)] == [c.to_dict() for c in cases]


def test_empty_sources_do_not_crash():
    assert build([]) == [] or all(isinstance(c, ValidationCase) for c in build([]))
