"""共享 bootstrap 解析器 —— L1 树展开里"从一批 context 里抠出下一层实体列表"。

Slice 3.2 把这套逻辑埋在 sector_landscape 的 `PLAYER_EXTRACT_PROMPT`/`_extract_players`
里;Slice 3.3 的 value_chain / theme_analysis 各要用它两次(拆环节/类别 + 拆每节点
龙头),所以提取成共享模块(prepare/08 开放问题预定)。

两个 helper,都是 **FAST_LLM 一次调用 + json_repair + 代码层兜底过滤,永不抛错**:
- `parse_string_list`   → list[str]    (产业链环节名 / 主题受益类别名)
- `parse_company_list`  → list[CompanyTarget](节点龙头股,带 US-listed 严约束)

失败一律返回 [],由调用方 strategy 据此走降级路径(三层防御的第 3 层)。
"""
import logging

import json_repair

from ..utils.llm import create_chat_completion
from .schema import CompanyTarget

logger = logging.getLogger(__name__)


STRING_LIST_PROMPT = """\
From the research text below, {instruction}.

Output ONLY a JSON array of short strings, no other text, no markdown fences, \
no leading or trailing prose:
["item one", "item two", ...]

Rules:
- Output at most {max_n} items, the most clearly supported by the text.
- Each item is a short noun phrase (a few words), not a sentence.
- Output an empty array [] if the text does not support any item.

Research text:
---
{text}
---
"""


COMPANY_LIST_PROMPT = """\
From the research text below, identify the top {max_n} leading **US-listed** \
public companies for: {scope_label}. This is for a US-equity investment \
research report — non-US-listed companies are out of scope.

Output ONLY a JSON array with this exact schema, no other text, no markdown \
fences, no leading or trailing prose:
[{{"name": "Full company name", "ticker": "TICKER"}}, ...]

Strict rules:
- ONLY US-listed companies (NYSE / NASDAQ / NYSE American primary listing).
- Each company MUST have a confirmed US ticker. If you are not sure about the
  US ticker, EXCLUDE that company rather than guessing or setting ticker to null.
- Foreign companies trading via ADRs ARE allowed if the ADR has a US ticker
  (e.g., BYD as BYDDY, TSMC as TSM). Foreign companies WITHOUT a US listing
  (e.g., CATL, LG Energy Solution, Panasonic) are EXCLUDED.
- Output at most {max_n} companies. Prefer the most clearly mentioned among
  eligible US-listed names.
- Output an empty array [] if no US-listed company is surfaced in the text.

Research text:
---
{text}
---
"""


async def _fast_llm(cfg, prompt: str, max_tokens: int = 400):
    """跑一次 FAST_LLM,失败返回 None(永不抛错)。"""
    try:
        return await create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=cfg.fast_llm_model,
            llm_provider=cfg.fast_llm_provider,
            max_tokens=max_tokens,
            llm_kwargs=cfg.llm_kwargs,
        )
    except Exception as e:
        logger.warning(f"bootstrap parser LLM call failed: {e}")
        return None


async def parse_string_list(cfg, text: str, instruction: str, max_n: int) -> list[str]:
    """从 text 里抠出一组短字符串(环节名 / 类别名)。失败返回 []。

    instruction 例:"list the distinct value-chain segments"。
    """
    if not text:
        return []
    prompt = STRING_LIST_PROMPT.format(
        instruction=instruction, max_n=max_n, text=text
    )
    response = await _fast_llm(cfg, prompt)
    if response is None:
        return []
    try:
        parsed = json_repair.loads(response)
    except Exception as e:
        logger.warning(
            f"parse_string_list JSON parse failed: {e}; raw={response[:200]!r}"
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(f"parse_string_list non-list: {response[:200]!r}")
        return []
    out: list[str] = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out[:max_n]


async def parse_company_list(
    cfg, text: str, scope_label: str, max_n: int
) -> list[CompanyTarget]:
    """从 text 里抠出一组 US-listed 公司(name+ticker)。失败返回 []。

    scope_label 注入到 prompt,告诉 LLM 在哪个范围里挑龙头,例:
    "the EV battery industry" / "the wafer fabrication segment of semiconductors"
    / "compute hardware exposure to AI infrastructure"。

    US-listed 严约束(case-3 修):ADR 允许(BYDDY/TSM),无美股的(CATL/LGES)排除;
    LLM 偶尔无视 prompt 输出 ticker=null,这里代码层兜底过滤。
    """
    if not text:
        return []
    prompt = COMPANY_LIST_PROMPT.format(
        scope_label=scope_label, max_n=max_n, text=text
    )
    response = await _fast_llm(cfg, prompt)
    if response is None:
        return []
    try:
        parsed = json_repair.loads(response)
    except Exception as e:
        logger.warning(
            f"parse_company_list JSON parse failed: {e}; raw={response[:200]!r}"
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(f"parse_company_list non-list: {response[:200]!r}")
        return []
    out: list[CompanyTarget] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        ticker = item.get("ticker")
        # 严格 US-listed:name + ticker 必须同时有,否则丢弃(代码层兜底)
        if not name or not ticker:
            continue
        out.append(CompanyTarget(name=str(name), ticker=str(ticker)))
    return out[:max_n]
