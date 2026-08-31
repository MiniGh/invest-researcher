"""数字定位的回归测试(Slice E3)。

这一层决定了 judge 的输入质量,而且它的两类错误后果完全不同:
  - 漏搜(原文里有、没搜到)→ 会把真实数据误判成「查无此据」,虚高无据率
  - 滥搜(搜出一堆无关段落)→ 稀释候选,judge 反而更容易看错

所以用例集中在"同一个数值的不同写法"和"什么不该被当成数据点"。
"""
import pytest

from evals.investment_eval.artifacts import SourceDoc
from evals.investment_eval.locate import extract_numbers, locate


def src(text, url="https://example.com/a"):
    return SourceDoc(url=url, title="t", raw_content=text)


# ---------------- 抽数字 ----------------

def test_extracts_currency_and_percent():
    nums = extract_numbers("Revenue was $13.64 billion, up 57% year over year.")
    assert any("13.64" in n for n in nums)
    assert any("57" in n for n in nums)


def test_ignores_bare_years():
    """2026 是年份不是数据点,拿去搜会命中一大片无关段落。"""
    assert extract_numbers("In 2026 the company grew.") == []


def test_ignores_small_counting_integers():
    """「3 家公司」「第 2 层」这类计数不是可核对的数据点。"""
    assert extract_numbers("We compare 3 companies across 6 dimensions.") == []


def test_keeps_small_number_when_it_carries_a_unit():
    """带单位就是数据点,哪怕数值很小。"""
    nums = extract_numbers("Gross margin reached 5%.")
    assert nums and "5" in nums[0]


# ---------------- 找候选 ----------------

def test_finds_exact_match():
    r = locate("Revenue was $13.64 billion.", [src("Micron reported revenue of 13.64 billion USD.")])
    assert r.found
    assert "13.64" in r.candidates[0].excerpt


def test_finds_across_magnitude_forms():
    """报告写 billion,原文写 million —— 同一个数值,必须搜到。"""
    r = locate("Revenue was $13.64 billion.", [src("Quarterly revenue came in at 13,640 million dollars.")])
    assert r.found, "跨量级写法没搜到,会把真实数据误判成查无此据"


def test_finds_comma_separated_form():
    r = locate("Backlog reached 678000 million.", [src("Commercial backlog of 678,000 million was disclosed.")])
    assert r.found


def test_does_not_match_a_longer_number():
    """搜 13.64 不能命中 213.647 —— 那是另一个数。

    主题词路径可能仍会返回这段(都在讲 revenue/billion),但数字必须没命中,
    否则会把两个不同的数算成对得上。"""
    r = locate("Revenue was $13.64 billion.", [src("Total assets were 213.647 billion.")])
    assert not r.number_found


def test_absent_number_is_flagged_even_when_topic_matches():
    """数字不在原文里,但原文在讲同一件事 —— 这正是 CONTRADICTED 的形态。

    只按数字检索时这种情况会落到"查无此据",于是"改错了"和"凭空写"两种
    性质完全不同的问题被混成一类。"""
    r = locate("Revenue was $99.99 billion.", [src("Micron reported revenue of 13.64 billion USD.")])
    assert r.numbers
    assert not r.number_found, "不该声称数字命中"


def test_unrelated_source_yields_nothing():
    """既没有这个数、主题也对不上 —— 才是真正的查无此据。"""
    r = locate("Micron revenue was $99.99 billion.",
               [src("The weather in Reykjavik is mild for the season.")])
    assert not r.found


def test_claim_without_numbers_yields_nothing():
    r = locate("The company has a strong competitive position.", [src("anything")])
    assert r.numbers == []
    assert not r.found


def test_excerpt_carries_context_window():
    text = "x" * 500 + "revenue of 13.64 billion" + "y" * 500
    r = locate("Revenue was $13.64 billion.", [src(text)])
    assert r.found
    e = r.candidates[0].excerpt
    assert 200 < len(e) < 500, f"上下文窗口不对:{len(e)}"


def test_candidate_records_source_and_matched_form():
    r = locate("Revenue was $13.64 billion.", [src("revenue of 13.64 billion", url="https://x.com/a")])
    c = r.candidates[0]
    assert c.source_url == "https://x.com/a"
    assert "13.64" in c.matched_form


def test_adjacent_hits_in_one_source_are_deduped():
    """同一句话里同一个数出现两次,不应产生两条几乎相同的候选。"""
    r = locate("Revenue was $13.64 billion.", [src("13.64 billion, i.e. 13.64 billion again")])
    assert len(r.candidates) == 1


def test_scans_all_sources():
    r = locate(
        "Revenue was $13.64 billion.",
        [src("nothing here", url="https://a.com"), src("revenue 13.64 billion", url="https://b.com")],
    )
    assert r.found
    assert r.candidates[0].source_url == "https://b.com"


# ---------------- 链接噪声 ----------------

def test_numbers_inside_link_urls_are_ignored():
    """URL 里的编号不是数据点。实测 tickeron 的文章 ID 14361 被误抽,
    而这个数原文里当然搜不到,于是好端端的句子被算成「查无此据」。"""
    claim = "Analyst consensus is Strong Buy ([tickeron](https://tickeron.com/blogs/micron-mu-14361))."
    assert extract_numbers(claim) == []


def test_link_text_is_kept_for_extraction():
    """只剥地址,不能把链接文字里的数字也丢掉。"""
    claim = "Revenue was [$13.64 billion](https://example.com/a-99887)."
    nums = extract_numbers(claim)
    assert any("13.64" in n for n in nums)
    assert not any("99887" in n for n in nums)


def test_bare_urls_are_stripped():
    claim = "See https://example.com/report-20260316 for details on the 57% growth."
    nums = extract_numbers(claim)
    assert any("57" in n for n in nums)
    assert not any("20260316" in n for n in nums)


# ---------------- 主题词定位 ----------------

def test_keyword_path_finds_the_passage_when_the_number_differs():
    """核心用例:报告把 13.64 写成了 16.34,原文讲的还是同一件事。
    必须能找回那段原文,否则判定模型无从比较,只能判成查无此据。"""
    r = locate(
        "Micron quarterly revenue was $16.34 billion.",
        [src("Micron reported quarterly revenue of 13.64 billion dollars for the period.")],
    )
    assert r.found, "主题词路径没找回同主题段落"
    assert not r.number_found
    assert "13.64" in r.candidates[0].excerpt


def test_does_not_pull_in_a_different_company():
    """一条关于 Microsoft 的断言,不该拿 Micron 的原文来核对。
    这是只按数字检索时最常见的错配 —— '20%' 在几百篇文档里到处都是。"""
    r = locate(
        "Microsoft Azure revenue grew 43% in the quarter.",
        [src("Micron reported that HBM accounted for 43% of DRAM revenue.")],
    )
    assert not any("Micron" in c.excerpt and c.by_number is False for c in r.candidates), \
        "把别家公司的原文当成了同主题证据"


def test_number_hits_rank_above_keyword_hits():
    """数字命中的证据力更强,应排在前面。"""
    r = locate(
        "Micron revenue was $13.64 billion.",
        [
            src("Micron discussed revenue trends and billion-dollar investments.", url="https://a.com"),
            src("Micron reported revenue of 13.64 billion dollars.", url="https://b.com"),
        ],
    )
    assert r.candidates[0].by_number is True


def test_keywords_drop_stopwords_and_keep_proper_nouns():
    from evals.investment_eval.locate import keywords
    kw = [k.lower() for k in keywords("Micron reported revenue of $13.64 billion in the quarter.")]
    assert "micron" in kw
    assert "revenue" in kw
    assert "the" not in kw and "of" not in kw
