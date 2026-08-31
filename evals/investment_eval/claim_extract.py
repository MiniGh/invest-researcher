"""从报告里抽出可核对的断言(Slice E3)。

**为什么只抽带数字的**

定性判断("竞争力较强""护城河稳固")的核对一致性很差 —— 同一句话让模型判
两次会给出不同结论,拿这种数据算出来的幻觉率没有意义。而投研里真正害人的
是数字错,所以这里刻意收窄到数字型断言:金额、比例、增速、日期、排名。

**一句话可能包含多条断言**

"Q2 revenue was $23.86 billion, up 196% year over year" 里有两个独立可核对的
事实(金额、增速),其中一个对一个错是常见情况,合成一条会丢掉这个区分。

**抽取用 FAST_LLM,判定用另一家模型**

抽取只是把句子拆开、不做判断,用便宜模型即可;判定必须换厂商(见 judge.py)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field

import json_repair

from gpt_researcher.utils.llm import create_chat_completion

from .locate import extract_numbers

logger = logging.getLogger(__name__)

MAX_CLAIMS_PER_CHUNK = 20
# 单次调用的墙钟上限(秒)。实测正常调用 2.7-5.5s,120s 已是极宽裕。
CALL_TIMEOUT = 120
_CHUNK_CHARS = 4000

EXTRACT_PROMPT = """\
You are preparing an investment report for fact-checking.

From the text below, extract every VERIFIABLE NUMERIC CLAIM — a statement that
asserts a specific figure and could be checked against a source document.

Extract a claim when it states any of:
- a monetary amount (revenue, capex, market size, backlog, price)
- a percentage (growth rate, margin, market share, yield)
- a count, capacity, or physical quantity (units, GW, wafers per month)
- a rank or ordinal position ("third-largest supplier")
- a date tied to a fact ("HBM4 ships in H2 2026")

Do NOT extract:
- qualitative statements with no figure ("the moat is durable")
- section headings
- the report's own meta-commentary about sources or methodology

Rules:
- One claim per distinct fact. A sentence stating both a revenue figure AND a
  growth rate yields TWO claims — they can be independently right or wrong.
- Each claim must be self-contained: include the company or subject name, the
  metric, the figure, and the period. A reader must be able to check it without
  reading the surrounding text.
- Copy figures exactly as written. Do not convert units or round.
- If the text contains no verifiable numeric claim, return an empty array.

Return ONLY a JSON array, no prose:
[
  {"claim": "<self-contained claim>", "subject": "<company or topic>", "metric": "<what is measured>"}
]

TEXT:
---
{TEXT}
---
"""


@dataclass
class Claim:
    """一条待核对的断言。"""

    claim: str
    subject: str = ""
    metric: str = ""
    section: str = ""
    numbers: list[str] = field(default_factory=list)
    cited_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.M)
_LINK_URL = re.compile(r"\]\((https?://[^)\s]+)")


def _sections(report_md: str) -> list[tuple[str, str]]:
    """把报告按标题切成 (标题, 正文) —— 断言要记住自己来自哪一节。"""
    marks = list(_HEADING.finditer(report_md))
    if not marks:
        return [("", report_md)]
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(report_md)
        out.append((m.group(1).strip(), report_md[m.end():end]))
    return out


def _chunks(text: str, size: int = _CHUNK_CHARS) -> list[str]:
    """按段落边界切块,避免把一句话劈开。"""
    paras, cur, out = text.split("\n\n"), "", []
    for p in paras:
        if len(cur) + len(p) > size and cur:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur.strip():
        out.append(cur)
    return out


async def _extract_chunk(cfg, text: str, section: str) -> list[Claim]:
    prompt = EXTRACT_PROMPT.replace("{TEXT}", text)
    try:
        # asyncio.wait_for 是硬性上限,不依赖底层客户端的超时行为。
        # 实测并发大请求撞上网络抖动时,单个调用会挂住不返回,而 gpt-researcher
        # 自带的 10 次重试会把 600s 的超时放大到一个多小时;整批抽取因此停滞。
        # 这里宁可丢掉这一块的断言,也不让整轮卡死。
        raw = await asyncio.wait_for(
            create_chat_completion(
                model=cfg.fast_llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                llm_provider=cfg.fast_llm_provider,
                max_tokens=cfg.fast_token_limit,
                llm_kwargs={**cfg.llm_kwargs, "timeout": CALL_TIMEOUT},
            ),
            timeout=CALL_TIMEOUT * 2,
        )
    except asyncio.TimeoutError:
        logger.warning("断言抽取超时(该块跳过,%ds)", CALL_TIMEOUT * 2)
        return []
    except Exception as e:
        logger.warning("断言抽取失败(该块跳过):%s", e)
        return []

    try:
        parsed = json_repair.loads(raw)
    except Exception:
        logger.warning("断言抽取返回的不是合法 JSON,该块跳过")
        return []
    if not isinstance(parsed, list):
        return []

    out: list[Claim] = []
    for item in parsed[:MAX_CLAIMS_PER_CHUNK]:
        if not isinstance(item, dict):
            continue
        body = str(item.get("claim", "")).strip()
        if not body:
            continue
        nums = extract_numbers(body)
        if not nums:
            # 模型偶尔会抽出没有数字的句子,这类判不准,直接丢掉。
            continue
        out.append(
            Claim(
                claim=body,
                subject=str(item.get("subject", "")).strip(),
                metric=str(item.get("metric", "")).strip(),
                section=section,
                numbers=nums,
                cited_urls=_LINK_URL.findall(body),
            )
        )
    return out


async def extract_claims(cfg, report_md: str, concurrency: int = 6) -> list[Claim]:
    """从一份报告里抽出全部数字型断言。

    表格行单独排除:表格里的数字几乎从不带上下文,抽出来的断言不自足
    (只有 "$23.86B" 而没有主语和期间),核对时无从下手。
    """
    prose = "\n".join(
        l for l in (report_md or "").splitlines() if not l.strip().startswith("|")
    )
    jobs = [
        (sec, chunk)
        for sec, body in _sections(prose)
        for chunk in _chunks(body)
        if extract_numbers(chunk)          # 整块没数字就不必调模型
    ]
    if not jobs:
        return []

    sem = asyncio.Semaphore(concurrency)

    async def run(sec, chunk):
        async with sem:
            return await _extract_chunk(cfg, chunk, sec)

    batches = await asyncio.gather(*[run(s, c) for s, c in jobs])

    # 去重:同一条断言可能被相邻块重复抽出
    seen, out = set(), []
    for claims in batches:
        for c in claims:
            key = re.sub(r"\s+", " ", c.claim.lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
    return out
