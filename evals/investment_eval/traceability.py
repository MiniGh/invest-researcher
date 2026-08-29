"""溯源率:报告里的数字能不能追回到它引用的原文(Slice E2)。

全部是正则与集合运算,不调用任何模型 —— 确定性、零成本、可以每篇全量跑。

**三个指标是逐级严格的,必须一起看**

  1. 链接有效率  引用里有多少是真 URL(而不是 `](#)` 这类空锚点)
  2. 链接命中率  这些 URL 里有多少确实出现在本次研究的资料清单中
  3. 数字覆盖率  正文里含数字的句子有多少带了引用

只看第 1 项会被"假溯源"骗过去:实测有一篇报告的 24 个链接全部是
`https://www.goldmansachs.com` 这类**发布商首页**,报告自己也承认
"citations link to that publisher's public homepage"。它在第 1 项上是
满分,在第 2 项上是零分 —— 所以第 2 项才是真正的溯源指标。

**诊断项**(不计入三个指标,但会一起打印)

  - 死锚点数量:`](#)` / `](#sina)` 这类
  - 仅指向域名首页的链接数:上面那种假溯源的直接信号
  - 独立来源域名数与最大集中度:实测有一篇报告约九成数字回溯到同一个
    URL,而"逐数字挂链接"这个正确做法反而把这件事盖住了
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# markdown 行内链接。非贪婪,且不跨行。
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]*)[^)]*\)")
# 表格行:整行以 | 开头结尾
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
# 句子切分:句末标点 + 空白
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
# 一个"数字":整数/小数/百分比/带货币符号,排除纯粹的章节序号
_NUMBER = re.compile(r"[\$€£]?\d[\d,]*\.?\d*\s*(?:%|bn|billion|million|trillion|B\b|M\b|K\b|GW|MW|TWh)?")


def _is_http(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _normalize(url: str) -> str:
    """比对前的最小归一化:去掉尾部斜杠与锚点。不做更激进的处理,
    以免把两个不同的文章 URL 归并成一个,虚高命中率。"""
    u = url.split("#", 1)[0]
    return u.rstrip("/")


def _is_bare_domain(url: str) -> bool:
    """只到域名首页、没有具体文章路径 —— 假溯源的信号。"""
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return _is_http(url) and p.path.strip("/") == "" and not p.query


def _split_prose_and_tables(md: str) -> tuple[str, list[str]]:
    """把表格行分出来单独统计。表格里的数字几乎不带行内引用,
    混进正文会把数字覆盖率压低,读起来像写作问题,实际是体裁差异。"""
    prose, tables = [], []
    for line in md.splitlines():
        (tables if _TABLE_LINE.match(line) else prose).append(line)
    return "\n".join(prose), tables


@dataclass
class TraceabilityScore:
    """一篇报告的溯源打分。比率为 None 表示分母为 0 或数据不足以计算。"""

    research_id: str = ""
    label: str = ""

    # 指标 1
    links_total: int = 0
    links_http: int = 0
    # 指标 2
    links_matched: int = 0
    source_urls_known: bool = True  # 没有资料清单时(如历史报告)无法计算命中率
    # 指标 3
    prose_sentences_with_numbers: int = 0
    prose_sentences_cited: int = 0

    # 诊断
    dead_anchors: int = 0
    bare_domain_links: int = 0
    table_lines: int = 0
    distinct_domains: int = 0
    top_domain_share: float | None = None
    top_domain: str = ""
    unmatched_examples: list[str] = field(default_factory=list)

    @staticmethod
    def _ratio(num: int, den: int) -> float | None:
        return num / den if den else None

    @property
    def link_validity(self) -> float | None:
        """指标 1:引用中真 URL 的占比。"""
        return self._ratio(self.links_http, self.links_total)

    @property
    def link_hit_rate(self) -> float | None:
        """指标 2:真 URL 中确实出自本次资料清单的占比。"""
        if not self.source_urls_known:
            return None
        return self._ratio(self.links_matched, self.links_http)

    @property
    def numeric_coverage(self) -> float | None:
        """指标 3:正文中含数字的句子里带引用的占比。"""
        return self._ratio(self.prose_sentences_cited, self.prose_sentences_with_numbers)


def score_report(
    report_md: str,
    source_urls: set[str] | None = None,
    research_id: str = "",
    label: str = "",
) -> TraceabilityScore:
    """给一篇报告打分。

    Args:
        report_md: 报告 markdown 全文。
        source_urls: 本次研究的资料清单 URL 集合。传 None 表示不可知
            (例如给没有快照的历史报告打分),此时命中率返回 None 而不是 0
            —— 「算不出来」和「一条都没命中」是两回事,不能混为一谈。
    """
    s = TraceabilityScore(research_id=research_id, label=label)

    targets = _LINK.findall(report_md or "")
    s.links_total = len(targets)
    http_links = [t for t in targets if _is_http(t)]
    s.links_http = len(http_links)
    s.dead_anchors = sum(1 for t in targets if t.startswith("#"))
    s.bare_domain_links = sum(1 for t in http_links if _is_bare_domain(t))

    # 指标 2
    if source_urls is None:
        s.source_urls_known = False
    else:
        known = {_normalize(u) for u in source_urls}
        unmatched = []
        for t in http_links:
            if _normalize(t) in known:
                s.links_matched += 1
            else:
                unmatched.append(t)
        s.unmatched_examples = list(dict.fromkeys(unmatched))[:5]

    # 来源集中度
    domains = [urlparse(t).netloc for t in http_links if urlparse(t).netloc]
    if domains:
        s.distinct_domains = len(set(domains))
        top = max(set(domains), key=domains.count)
        s.top_domain, s.top_domain_share = top, domains.count(top) / len(domains)

    # 指标 3
    prose, tables = _split_prose_and_tables(report_md or "")
    s.table_lines = len(tables)
    for sent in _SENT_SPLIT.split(prose):
        if not _NUMBER.search(sent):
            continue
        s.prose_sentences_with_numbers += 1
        if _LINK.search(sent):
            s.prose_sentences_cited += 1
    return s


def score_artifact(artifact) -> TraceabilityScore:
    """给一份证据快照打分(命中率可算)。"""
    return score_report(
        artifact.report_md,
        source_urls=artifact.source_urls,
        research_id=artifact.research_id,
        label=artifact.label,
    )
