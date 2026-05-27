"""
StructuredExtractor —— L3 结构化抽取。

两次独立 LLM 调用(filing-only / web-only),deterministic merge,
每个字段的 trust_label 由"哪次调用填的"决定 —— 不让 LLM 自报来源。

Conflict(filing 和 web 同字段不同值):filing 主,web 值进 conflict 字段,
markdown 渲染时并列展示。
"""
import logging
from typing import Literal, Optional

import json_repair

from ..utils.llm import create_chat_completion
from .prompts import (
    EXTRACT_FROM_FILING_PROMPT,
    EXTRACT_FROM_WEB_PROMPT,
    MINI_EXTRACT_PROMPT,
)
from .schema import CompanyMetrics, CompanyTarget, FilingDoc, MetricField

logger = logging.getLogger(__name__)

# 截断防 token 爆;100K 字符 ~25K tokens(DeepSeek v4-flash 128K 上下文够用)。
# 之前 30K 太小:SEC iXBRL 文档前 30K 基本是 XBRL metadata,财务表格在后面,
# 被截掉了导致 filing pass 抽不到任何数字。
TEXT_TRUNCATE_LIMIT = 100000

# Schema 里要抽的数字字段列表(full 模式遍历全部;mini 模式 LLM 只输出前 3 个,
# 其他字段在 _parse_metric_fields 里因 .get() 拿不到 → 自动 skip,无需特殊处理)
METRIC_FIELDS = [
    "revenue",
    "yoy_growth",        # Slice 3.2 新增:同比增长率;mini 必填字段之一
    "gross_margin",
    "operating_margin",
    "net_income",
    "eps",
    "market_cap",
]

# mini 模式只填的字段(决定渲染时哪些一定要展示、哪些 null 也无所谓)
MINI_METRIC_FIELDS = ["revenue", "yoy_growth", "gross_margin"]


class StructuredExtractor:
    def __init__(self, cfg):
        self.cfg = cfg

    async def extract(
        self,
        filing: Optional[FilingDoc],
        web_context: list,
        target: CompanyTarget,
        mode: Literal["full", "mini"] = "full",
    ) -> CompanyMetrics:
        # Slice 3.2: mode 真正分支。
        # - full: filing pass + web pass + merge(原 Slice 2b/3.1 行为)
        # - mini: 跳 filing,只用 web 跑一次 mini prompt,3 字段(revenue/yoy_growth/gross_margin)
        web_text = (
            "\n\n".join(str(x) for x in web_context)
            if isinstance(web_context, list)
            else str(web_context or "")
        )

        if mode == "mini":
            return await self._extract_mini(web_text, target)

        # Pass 1: filing-only
        report_period: Optional[str] = None
        if filing and filing.raw_content:
            schema_filing, report_period = await self._extract_filing_pass(filing)
        else:
            schema_filing = {}

        # Pass 2: web-only(传 target 让 prompt 限定抽取范围;case 2 company_comparison
        # 给每家公司同一份混合 web_ctx,prompt 不指定 target 时 LLM 会跨公司混抽)
        schema_web = (
            await self._extract_web_pass(web_text, target) if web_text else {}
        )

        return self._merge(
            filing_fields=schema_filing,
            web_fields=schema_web,
            target=target,
            filing_retrieved=filing is not None,
            report_period=report_period,
        )

    # ------------------------------------------------------------------
    # Two pass implementations
    # ------------------------------------------------------------------

    async def _extract_filing_pass(self, filing: FilingDoc) -> tuple[dict, Optional[str]]:
        parsed = await self._llm_extract(
            text=filing.raw_content,
            prompt=EXTRACT_FROM_FILING_PROMPT,
            pass_name="filing",
        )
        if not parsed:
            return {}, None

        report_period = parsed.get("report_period") if isinstance(parsed, dict) else None
        fields = self._parse_metric_fields(
            parsed=parsed,
            trust_label="filing",
            source_url_default=filing.url,
        )
        return fields, report_period

    async def _extract_web_pass(
        self, web_text: str, target: CompanyTarget
    ) -> dict:
        parsed = await self._llm_extract(
            text=web_text,
            prompt=EXTRACT_FROM_WEB_PROMPT.format(
                company_label=self._format_company_label(target)
            ),
            pass_name="web",
        )
        if not parsed:
            return {}
        return self._parse_metric_fields(
            parsed=parsed,
            trust_label="web",
            source_url_default=None,
        )

    async def _extract_mini(
        self, web_text: str, target: CompanyTarget
    ) -> CompanyMetrics:
        """Slice 3.2 mini 抽取:单次 LLM 调用,3 字段,纯 web context。

        给 sector_landscape Level 2 玩家小卡片用。filing 不抓,成本与延迟都剪掉。
        Prompt 里塞 target 名,LLM 才知道在多公司混合 context 里抽哪家(case3 fix)。
        其他 METRIC_FIELDS 保持 null(merge 时不会被填,渲染时跳过)。
        """
        if not web_text:
            return CompanyMetrics(company=target, filing_retrieved=False)

        parsed = await self._llm_extract(
            text=web_text,
            prompt=MINI_EXTRACT_PROMPT.format(
                company_label=self._format_company_label(target)
            ),
            pass_name="mini",
        )
        web_fields = (
            self._parse_metric_fields(
                parsed=parsed,
                trust_label="web",
                source_url_default=None,
            )
            if parsed
            else {}
        )
        return self._merge(
            filing_fields={},
            web_fields=web_fields,
            target=target,
            filing_retrieved=False,
            report_period=None,
        )

    async def _llm_extract(
        self, text: str, prompt: str, pass_name: str = ""
    ) -> Optional[dict]:
        """一次 LLM 调用 → JSON dict;失败返回 None。"""
        if not text:
            return None
        try:
            response = await create_chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text[:TEXT_TRUNCATE_LIMIT]},
                ],
                model=self.cfg.smart_llm_model,
                llm_provider=self.cfg.smart_llm_provider,
                max_tokens=2000,
                llm_kwargs=self.cfg.llm_kwargs,
            )
        except Exception as e:
            logger.warning(f"StructuredExtractor: LLM call failed: {e}")
            return None

        # DEBUG (Slice 2b 调试):看 LLM 实际返回了啥(诊断 0/6 抽取失败)
        logger.info(
            f"StructuredExtractor[{pass_name}] raw response head: {response[:500]!r}"
        )

        try:
            parsed = json_repair.loads(response)
        except Exception as e:
            logger.warning(
                f"StructuredExtractor: JSON parse failed: {e}; raw={response[:200]!r}"
            )
            return None

        if not isinstance(parsed, dict):
            logger.warning(f"StructuredExtractor: non-dict response: {response[:200]!r}")
            return None
        return parsed

    @staticmethod
    def _format_company_label(target: CompanyTarget) -> str:
        """渲染成 prompt 里塞的 target label,例:'NVIDIA Corporation (NVDA)' 或 'BYD Company Limited'。"""
        if target.ticker:
            return f"{target.name} ({target.ticker})"
        return target.name

    @staticmethod
    def _parse_metric_fields(
        parsed: dict, trust_label: str, source_url_default: Optional[str]
    ) -> dict:
        """从 LLM 输出的 JSON 抽取每个 metric 字段 → MetricField dict。"""
        out: dict = {}
        for field_name in METRIC_FIELDS:
            v = parsed.get(field_name)
            if not v or not isinstance(v, dict):
                continue
            value = v.get("value")
            if value is None:
                continue
            out[field_name] = MetricField(
                value=value,
                unit=v.get("unit"),
                trust_label=trust_label,
                source_url=v.get("source_url") or source_url_default,
                as_of=v.get("as_of"),
            )
        return out

    # ------------------------------------------------------------------
    # Merge: filing 优先;同字段冲突时双值并列;两边都没 = 空
    # ------------------------------------------------------------------

    def _merge(
        self,
        filing_fields: dict,
        web_fields: dict,
        target: CompanyTarget,
        filing_retrieved: bool,
        report_period: Optional[str],
    ) -> CompanyMetrics:
        metrics = CompanyMetrics(
            company=target,
            filing_retrieved=filing_retrieved,
            report_period=report_period,
        )
        for field_name in METRIC_FIELDS:
            f = filing_fields.get(field_name)
            w = web_fields.get(field_name)
            chosen: Optional[MetricField]
            if f and w and f.value != w.value:
                # 冲突:filing 主,记下 web
                f.conflict = {
                    "web_value": w.value,
                    "web_source": w.source_url,
                }
                chosen = f
            elif f:
                chosen = f
            elif w:
                chosen = w
            else:
                chosen = None
            if chosen is not None:
                setattr(metrics, field_name, chosen)
        return metrics

    # ------------------------------------------------------------------
    # Markdown rendering(带 trust badge,供 post-append 到报告末尾)
    # ------------------------------------------------------------------

    def render_as_markdown(self, metrics: CompanyMetrics) -> str:
        company_label = metrics.company.name
        if metrics.company.ticker:
            company_label = f"{company_label} ({metrics.company.ticker})"

        lines = ["", f"## 📊 {company_label} —— 关键财务指标"]
        if metrics.report_period:
            lines.append(f"*Report period: {metrics.report_period}*")
        if not metrics.filing_retrieved:
            lines.append("> ⚠️ 本次未取到该公司财报,以下数字均来自网络。")
        lines.append("")
        lines.append("| 指标 | 值 | 来源 | as-of |")
        lines.append("|---|---|---|---|")

        any_field_present = False
        for field_name in METRIC_FIELDS:
            mf: MetricField = getattr(metrics, field_name)
            if mf.value is None:
                continue
            any_field_present = True
            badge = self._render_badge(mf.trust_label)
            value_str = self._render_value(mf)
            as_of = mf.as_of or "—"
            lines.append(f"| {field_name} | {value_str} | {badge} | {as_of} |")
            if mf.conflict:
                web_val = mf.conflict.get("web_value")
                web_src = mf.conflict.get("web_source")
                conflict_note = f"⚠️ 网络数据为 `{web_val}`"
                if web_src:
                    conflict_note += f" ([source]({web_src}))"
                lines.append(f"| ↳ | {conflict_note} |  |  |")

        if not any_field_present:
            lines.append("| (未抽到任何字段) | — | — | — |")

        if metrics.guidance:
            lines.append("")
            lines.append(f"**前瞻指引**: {metrics.guidance}")

        return "\n".join(lines)

    @staticmethod
    def _render_badge(label: str) -> str:
        return {
            "filing": "**【据财报】** ✓",
            "web": "**【据网络】** ⚠",
            "null": "—",
        }.get(label, "—")

    @staticmethod
    def _render_value(mf: MetricField) -> str:
        value = mf.value
        unit = mf.unit or ""
        body = f"{value} {unit}".strip()
        if mf.source_url:
            body += f" [↗]({mf.source_url})"
        return body
