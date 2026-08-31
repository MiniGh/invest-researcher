"""构造已知答案的判定验证集(Slice E4)。

**为什么需要它**

幻觉率这个数字本身也要有可信度。如果判定模型自己就判不准,算出来的幻觉率
就是噪声。要衡量判定准不准,得有一批"标准答案已知"的题目。

**怎么造出标准答案已知的题**

从真实卷宗原文出发,三种造法各自对应一个必然的答案:

    原样抄出原文里的数字        → 答案必然是 SUPPORTED
    把原文里的数字改掉          → 答案必然是 CONTRADICTED
    编一个原文完全没提的指标    → 答案必然是 NOT_FOUND

这样不需要人工标注就能得到准确率,而且可以重复跑、用来横向对比不同模型。

**它的局限必须说清楚**

构造出来的题可能比真实情况干净:数字改动明显、编造的指标离谱。真实报告里
的难题是单位换算、口径不同、时间错配,这套题测不到。所以得到的准确率是
**上限**,不是保证 —— 人工抽样标注仍然值得做,只是不必作为前置条件。
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path

VALIDATION_FILE = Path(__file__).parent / "cases.jsonl"

# 先切句再筛,不能直接在全文上正则匹配 —— 那样会从数字中间起手,
# 造出 "9B, with an operating margin of..." 这种残句,题目本身就是坏的。
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# 句子里必须有"带单位的数字":纯数字容易撞上年份与编号。
_NUM_UNIT = re.compile(
    r"(?P<num>[\$€£]?\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<unit>%|billion|million|bn\b|GW|MW|TWh|percent)",
    re.IGNORECASE,
)

# 表格行、图表数据、导航碎片 —— 这些不是可核对的陈述句
_JUNK = re.compile(r"[|]|Intraday chart|\.{3}|^\W")


def _fact_sentences(text: str, limit: int):
    """挑出可以当题目的完整陈述句。

    四道筛子缺一不可:必须是完整句(不从中间起手)、要有带单位的数字、
    不能是表格或图表碎片、长度适中。任何一条不满足,造出来的题目本身
    就是坏的,拿它测出来的准确率没有意义。
    """
    out = []
    for raw in _SENT_SPLIT.split(text):
        sent = " ".join(raw.split())
        if not (40 <= len(sent) <= 260):
            continue
        if _JUNK.search(sent):
            continue
        if len(sent.split()) < 7:
            continue
        # 必须像一句话的开头。抓取来的正文常常没有规范的句号,按 [.!?] 切会
        # 在缩写或换行处断开,得到 "into around 58% of global HBM..." 这种
        # 从中间起手的残句 —— 拿它当题目,判定模型答什么都不算数。
        if not (sent[0].isupper() or sent[0] in "$€£"):
            continue
        m = _NUM_UNIT.search(sent)
        if not m:
            continue
        out.append((sent, m))
        if len(out) >= limit:
            break
    return out

# 编造用的指标名:与投研语境相关但具体,避免"离谱到一眼假"
_FAKE_METRICS = [
    ("employee headcount", "48,300"),
    ("R&D spending", "$7.42 billion"),
    ("number of manufacturing sites", "23"),
    ("average selling price per unit", "$412.60"),
    ("dividend payout ratio", "38.5%"),
    ("inventory turnover", "4.7"),
    ("customer retention rate", "91.2%"),
    ("patent portfolio size", "12,400"),
]


@dataclass
class ValidationCase:
    claim: str
    expected: str          # SUPPORTED / CONTRADICTED / NOT_FOUND
    kind: str              # verbatim / altered / fabricated
    source_url: str
    origin: str = ""       # 原文出处,便于人工复核这道题出得对不对

    def to_dict(self) -> dict:
        return asdict(self)


def _perturb(num: str) -> str:
    """把数字改成一个明显不同、但量级相同的值。

    只动有效数字不动量级 —— 改成 10 倍会让题目变得过于简单,
    测不出判定模型对"数值细微不符"的敏感度。
    """
    digits = re.sub(r"[^\d.]", "", num)
    if not digits:
        return num
    try:
        val = float(digits)
    except ValueError:
        return num
    new = val * 1.37 if val < 1000 else val * 0.61
    txt = f"{new:.2f}".rstrip("0").rstrip(".")
    return num.replace(digits, txt)


def build(sources, per_source: int = 2, seed: int = 20260831) -> list[ValidationCase]:
    """从卷宗构造验证集。

    Args:
        sources: SourceDoc 列表。
        per_source: 每篇原文最多取几个事实句。
    """
    rng = random.Random(seed)
    cases: list[ValidationCase] = []

    for src in sources:
        text = getattr(src, "raw_content", "") or ""
        url = getattr(src, "url", "")
        if len(text) < 200:
            continue
        for sent, m in _fact_sentences(text, per_source):
            # 1) 原样 → 必然有支撑
            cases.append(ValidationCase(
                claim=sent, expected="SUPPORTED", kind="verbatim",
                source_url=url, origin=sent))

            # 2) 改数字 → 必然矛盾
            altered = sent.replace(m.group("num"), _perturb(m.group("num")), 1)
            if altered != sent:
                cases.append(ValidationCase(
                    claim=altered, expected="CONTRADICTED", kind="altered",
                    source_url=url, origin=sent))

    # 3) 编造 → 必然查无此据。数量与前两类大致均衡。
    n_fab = max(1, len(cases) // 2)
    subjects = [getattr(s, "title", "") or "the company" for s in sources if getattr(s, "raw_content", "")]
    for i in range(n_fab):
        metric, value = _FAKE_METRICS[i % len(_FAKE_METRICS)]
        subj = rng.choice(subjects) if subjects else "the company"
        name = (subj[:40].strip() or "The company")
        name = name[0].upper() + name[1:]
        cases.append(ValidationCase(
            claim=f"{name} reported {metric} of {value} for the period.",
            expected="NOT_FOUND", kind="fabricated", source_url=""))

    rng.shuffle(cases)
    return cases


def save(cases: list[ValidationCase], path: Path = VALIDATION_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(c.to_dict(), ensure_ascii=False) for c in cases),
        encoding="utf-8",
    )
    return path


def load(path: Path = VALIDATION_FILE) -> list[ValidationCase]:
    return [
        ValidationCase(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
