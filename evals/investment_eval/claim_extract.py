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

# 抽取模型固定为 Qwen3-30B-Instruct,不跟随 FAST_LLM。
#
# 实测同一个 1708 字符的分块、max_tokens=2000:
#   Qwen/Qwen3-30B-A3B-Instruct-2507    15.7s  ✅
#   Qwen/Qwen3.6-35B-A3B                81.5s  ✅
#   deepseek-ai/DeepSeek-V4-Flash      175s 超时 ❌
# 当前 FAST_LLM 正是 DeepSeek-V4-Flash —— 它在"长提示词 + 长 JSON 输出"这种
# 形态下不可用,并发时整批抽取会全部超时。抽取只是把句子拆开、不做判断,
# 换个便宜快模型不影响结果质量。
# 抽取只是把报告里的数字型断言拆出来,不做质量判断,所以不受"不能与写手同门"
# 的约束(那条约束见 judge.py 的 FORBIDDEN_JUDGE_SUBSTR)。硅基流动账户余额为零
# (连免费档都 402),所以和判定一起走 DeepSeek 官方直连。
#
# EXTRACT_EXTRA_BODY 是这里的关键,不是可选优化。同一个 2053 字符的 chunk 实测:
#     deepseek-v4-flash  原样                    超时 >190s
#     deepseek-v4-flash  reasoning_effort=minimal  57.2s
#     deepseek-v4-flash  thinking=disabled          4.0s   ← 采用
#     deepseek-v4-pro    原样                     150.5s
#     GLM-5.3-Flash      原样                    超时 >190s
# 47 倍差距。原因是思考型模型在出结果前烧掉大量隐藏推理 token,而抽取是机械
# 拆句,推理没有收益。GLM 同样超时,说明这不是厂商问题。
#
# 这里有过一次错误归因:上一版把模型硬编码成 Qwen3-30B-A3B-Instruct-2507,
# 当时的结论写成"DeepSeek 慢、Qwen 快"—— 真正的变量是那个模型名里的
# "Instruct"(非思考变体),不是厂商。按厂商归因导致换厂商时又踩了同一个坑。
EXTRACT_MODEL = "deepseek-v4-flash"
EXTRACT_PROVIDER = "deepseek"
EXTRACT_EXTRA_BODY = {"thinking": {"type": "disabled"}}
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
- When a sentence gives a headline figure followed by COMPARISON BASELINES,
  extract ONLY the headline figure. Comparison baselines belong to a different
  period than the sentence's subject, and splitting them off reliably produces
  claims whose period is wrong.
    Text:  "FQ3 2026 revenue was $41.46 billion, versus $23.86 billion the
            prior quarter and $9.30 billion a year earlier."
    Right: one claim — "Micron FQ3 2026 revenue was $41.46 billion"
    Wrong: "FQ3 2026 revenue was $23.86 billion in the prior quarter"
           "FQ3 2026 revenue was $9.30 billion a year earlier"
    The two wrong claims read as assertions about FQ3 2026 and will be judged
    as contradicting the source, even though the report was correct.
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


async def _extract_chunk(cfg, text: str, section: str, attempts: int = 2) -> list[Claim]:
    """抽取单块。超时会重试一次 —— 实测超时来自网络抖动而非模型能力,
    重试通常就过了;直接跳过会让这一节的断言整段缺失。"""
    prompt = EXTRACT_PROMPT.replace("{TEXT}", text)
    raw = None
    for _attempt in range(attempts):
      try:
        # asyncio.wait_for 是硬性上限,不依赖底层客户端的超时行为。
        # 实测并发大请求撞上网络抖动时,单个调用会挂住不返回,而 gpt-researcher
        # 自带的 10 次重试会把 600s 的超时放大到一个多小时;整批抽取因此停滞。
        # 这里宁可丢掉这一块的断言,也不让整轮卡死。
        raw = await asyncio.wait_for(
            create_chat_completion(
                model=EXTRACT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                llm_provider=EXTRACT_PROVIDER,
                max_tokens=2000,
                llm_kwargs={
                    **cfg.llm_kwargs,
                    "timeout": CALL_TIMEOUT,
                    "extra_body": EXTRACT_EXTRA_BODY,
                },
            ),
            timeout=CALL_TIMEOUT + 10,
        )
        break
      except asyncio.TimeoutError:
        logger.warning("断言抽取超时(第 %d 次,%ds)", _attempt + 1, CALL_TIMEOUT)
      except Exception as e:
        logger.warning("断言抽取失败(第 %d 次):%s", _attempt + 1, e)
    if raw is None:
        logger.warning("断言抽取重试耗尽,该块跳过(%d 字符)", len(text))
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


async def extract_claims(cfg, report_md: str, concurrency: int = 2) -> list[Claim]:
    """从一份报告里抽出全部数字型断言。

    表格行单独排除:表格里的数字几乎从不带上下文,抽出来的断言不自足
    (只有 "$23.86B" 而没有主语和期间),核对时无从下手。

    并发默认 2:实测并发 6 时 6 块里 5 块超时,而串行单块只要 3.6s。
    判定环节并发 8 却正常 —— 差别在于抽取的提示词长一个数量级。
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
