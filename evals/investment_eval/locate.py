"""在卷宗原文里定位断言中的数字(Slice E3)。

judge 面对的如果是六十万字的全量卷宗,就成了大海捞针 —— 又贵又容易看漏。
这里先用纯代码把断言里的数字在原文中搜出候选段落,把 judge 的任务从
"在全部资料里找"降级成"看这几段对不对得上"。

**一个数字有很多种写法**,必须都搜到,否则会把"原文里其实有"误判成"查无此据":

    报告里写 $13.64 billion
    原文可能写成  $13.64B / 13.64 billion / 13,640 million / USD 13.64bn / 136.4亿

**搜不到任何候选时直接判定为「查无此据」,连模型都不必调用** —— 这是本模块
最大的价值:实测报告里相当一部分数字来自模型的换算或推断,原文中根本不存在
这个数,纯代码即可判定,省掉全部 judge 调用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 断言里的数字。允许千分位、小数、百分号、货币符号与常见单位后缀。
_NUMBER = re.compile(
    r"""
    (?P<cur>[\$€£¥])?\s*
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    \s*(?P<unit>%|bn\b|billion\b|million\b|trillion\b|[BMKT]\b|亿|万)?
    """,
    re.VERBOSE | re.IGNORECASE,
)

# 纯序号/年份等噪声:这些不构成"可核对的数据点"
_YEAR = re.compile(r"^(19|20)\d\d$")

_SCALE = {
    "bn": 1e9, "billion": 1e9, "b": 1e9,
    "million": 1e6, "m": 1e6,
    "trillion": 1e12, "t": 1e12,
    "k": 1e3,
    "亿": 1e8, "万": 1e4,
}


@dataclass
class Candidate:
    """原文里的一段候选证据。"""

    source_url: str
    excerpt: str
    matched_form: str   # 实际在原文里命中的内容,便于排查
    by_number: bool = True  # True=数字命中,False=主题词命中(数字可能不同)


@dataclass
class LocationResult:
    numbers: list[str] = field(default_factory=list)      # 断言里抽出的数字(原样)
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.candidates)

    @property
    def number_found(self) -> bool:
        """断言里的数字是否原样出现在原文里。

        与 found 的区别很重要:found 为真只说明"找到了讲同一件事的段落",
        number_found 为真才说明"这个数字确实在原文里"。判 CONTRADICTED 时
        恰恰是 found=True 而 number_found=False。"""
        return any(c.by_number for c in self.candidates)


# 行内链接与裸 URL。必须先剥掉再抽数字,否则 URL 里的编号会被当成数据点 ——
# 实测 tickeron.com/blogs/...-14361 里的 14361、日期串 03162026 都会被误抽,
# 这些数原文里当然搜不到,于是把好端端的句子算成「查无此据」。
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+|www\.\S+")


def strip_links(text: str) -> str:
    """去掉 markdown 链接的地址部分与裸 URL,只保留可读正文。"""
    return _BARE_URL.sub(" ", _MD_LINK.sub(r"\1", text or ""))


def extract_numbers(claim: str) -> list[str]:
    """从断言里抽出值得核对的数字。

    排除三类噪声:链接地址里的编号、年份、孤立的小整数(章节号、"3 家"
    这类计数)。它们都不是数据点,拿去搜要么搜不到、要么命中一大片无关
    段落,两种结果都会让判定失真。
    """
    out: list[str] = []
    for m in _NUMBER.finditer(strip_links(claim)):
        raw = m.group("num")
        unit = (m.group("unit") or "").lower()
        if _YEAR.match(raw.replace(",", "")) and not unit:
            continue
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        # 无单位、无货币符号的个位小整数基本是计数,不是数据点
        if not unit and not m.group("cur") and val < 10 and val == int(val):
            continue
        token = m.group(0).strip()
        if token not in out:
            out.append(token)
    return out


def _variants(token: str) -> list[str]:
    """把一个数字扩展成它在原文里可能出现的多种写法(正则片段)。"""
    m = _NUMBER.fullmatch(token.strip()) or _NUMBER.search(token)
    if not m:
        return [re.escape(token.strip())]

    raw = m.group("num")
    unit = (m.group("unit") or "").lower()
    plain = raw.replace(",", "")
    try:
        val = float(plain)
    except ValueError:
        return [re.escape(token.strip())]

    forms: set[str] = set()

    def add_number(text: str) -> None:
        """同一个数值的带/不带千分位两种写法。"""
        forms.add(text)
        if "." not in text and len(text) > 3:
            with_commas = f"{int(text):,}"
            forms.add(with_commas)

    add_number(plain)
    if plain.endswith(".0"):
        add_number(plain[:-2])

    # 换算成其他量级:13.64 billion ↔ 13,640 million ↔ 136.4 亿
    if unit in _SCALE:
        base = val * _SCALE[unit]
        for other, scale in _SCALE.items():
            if other in ("b", "m", "t", "k"):   # 单字母缩写与全称同值,不重复展开
                continue
            conv = base / scale
            if conv < 0.01 or conv > 1e6:
                continue
            s = f"{conv:.10f}".rstrip("0").rstrip(".")
            if len(s.replace(".", "")) <= 8:
                add_number(s)

    return [re.escape(f) for f in forms if f]


# 关键词定位所需。停用词表刻意保留投研语境里有区分度的词(revenue / margin),
# 只去掉通用虚词。
_STOP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "has", "have",
    "had", "it", "its", "this", "that", "these", "those", "which", "will",
    "would", "up", "down", "about", "over", "than", "per", "into", "their",
    "quarter", "year", "period", "reported", "company", "said", "according",
}
_WORD = re.compile(r"[A-Za-z][A-Za-z.&'-]{1,}")


def keywords(claim: str) -> list[str]:
    """抽出断言的主题词,用来判断一段原文是不是在讲同一件事。

    专有名词(大写开头、全大写代码)权重最高 —— 一条关于 Microsoft 的断言
    绝不该拿 Micron 的原文去核对,而这正是只按数字检索时最常见的错配。
    """
    text = strip_links(claim)
    proper, common = [], []
    for m in _WORD.finditer(text):
        w = m.group(0)
        lw = w.lower().strip(".")
        if lw in _STOP or len(lw) < 3:
            continue
        (proper if (w[0].isupper() or w.isupper()) else common).append(w)
    # 去重保序
    seen, out = set(), []
    for w in proper + common:
        k = w.lower().strip(".")
        if k not in seen:
            seen.add(k)
            out.append(w)
    return out


def _relevance(claim_kws: list[str], text: str) -> float:
    """一段文本与断言的主题重合度(0-1)。"""
    if not claim_kws:
        return 0.0
    low = text.lower()
    hit = sum(1 for w in claim_kws if w.lower().strip(".") in low)
    return hit / len(claim_kws)


def locate(claim: str, sources, window: int = 200, max_candidates: int = 6,
           min_relevance: float = 0.25) -> LocationResult:
    """在卷宗里为一条断言找候选证据段落。

    两条检索路径缺一不可:

      按数字找   报告写的数字原样出现在原文里 → 能判 SUPPORTED
      按主题词找 原文在讲同一件事,但数字不同 → 能判 CONTRADICTED

    只做第一条时 CONTRADICTED 实际上不可达:数字被改过的断言在原文里当然
    搜不到那个数,于是一律落到 NOT_FOUND,"改错了"和"凭空写"两种性质完全
    不同的问题被混成一类。实测只按数字检索时,改数字类的识别率只有 5/25。

    候选按相关度排序:主题重合度高的段落排在前面,避免在几百篇原文里
    捞到"数字撞上了但讲的是别的公司"的段落。
    """
    res = LocationResult(numbers=extract_numbers(claim))
    kws = keywords(claim)

    patterns = []
    for token in res.numbers:
        alts = _variants(token)
        if alts:
            patterns.append(re.compile(r"(?<![\d.])(?:" + "|".join(alts) + r")(?![\d])"))

    scored: list[tuple[float, bool, Candidate]] = []
    seen: set[tuple[str, int]] = set()

    for src in sources:
        text = getattr(src, "raw_content", "") or ""
        url = getattr(src, "url", "")
        if not text:
            continue

        src_rel = _relevance(kws, text)

        # --- 路径一:数字命中 ---
        for pat in patterns:
            for m in pat.finditer(text):
                key = (url, m.start() // window)
                if key in seen:
                    continue
                seen.add(key)
                lo, hi = max(0, m.start() - window), min(len(text), m.end() + window)
                exc = text[lo:hi].strip()
                scored.append((_relevance(kws, exc), True,
                               Candidate(url, exc, m.group(0), by_number=True)))

        # --- 路径二:主题词命中(数字可以不同)---
        # 只在整篇原文本身就与断言相关时才展开,否则会把几百篇文档全扫一遍。
        if src_rel >= min_relevance and kws:
            anchor_words = [w for w in kws[:4]]
            for w in anchor_words:
                for m in re.finditer(re.escape(w), text, re.IGNORECASE):
                    key = (url, m.start() // window)
                    if key in seen:
                        continue
                    seen.add(key)
                    lo, hi = max(0, m.start() - window), min(len(text), m.end() + window)
                    exc = text[lo:hi].strip()
                    rel = _relevance(kws, exc)
                    if rel < min_relevance:
                        continue
                    scored.append((rel, False, Candidate(url, exc, w, by_number=False)))

    # 数字命中优先,同类里相关度高的优先
    scored.sort(key=lambda x: (x[1], x[0]), reverse=True)
    res.candidates = [c for _, _, c in scored[:max_candidates]]
    return res
