"""研究证据快照(Slice E1)。

**为什么需要它**

`outputs/task_*.json` 只存了报告正文和事件日志 —— 没有任何一条原文。
评估"数据幻觉率"需要拿报告里的数字去核对**当时看到的那份原文**,而不是
"现在重新访问那个 URL 拿到的内容"(网页会改会挂,改了就分不清是模型编的
还是网页变了)。上游 `evals/hallucination_eval` 是同进程内存里跑完就地评估、
用完即丢,无法离线重评、无法换 judge 重跑、无法在改了写作模板之后做前后对比。

所以这里把一次研究的**报告 + 它当时看过的全部原文 + 带来源的上下文块**
打成一个 JSON 落盘。它既是评估的输入,也是"改模板 → 重评 → 对比"这个
迭代循环的地基。

**上下文块从哪来**

`PromptFamily.pretty_print_docs` 把每份资料渲染成固定三行:

    Source: <url>
    Title: <title>
    Content: <正文...>

`parse_context_blocks` 按这个格式把 `researcher.context` 反解成结构化块。
投研 strategy 会往 context 里额外拼入 extractor 卡片和骨架行(它们没有
Source 头),这部分作为 `unattributed` 单独留存 —— 溯源率要把它算进分母。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"

# pretty_print_docs 的块头。Content 后面的正文可能跨多行,所以按"下一个块头"切尾。
_BLOCK_HEADER = re.compile(
    r"^Source:[ \t]?(?P<source>.*)\n"
    r"Title:[ \t]?(?P<title>.*)\n"
    r"Content:[ \t]?",
    re.MULTILINE,
)


@dataclass
class ContextChunk:
    """一段进了写作 prompt 的上下文,连同它声称的来源。"""

    source_url: str
    title: str
    content: str

    @property
    def has_real_source(self) -> bool:
        """来源是否是一个真 URL。

        修复前的产物里这里会是字面量 "None"(compression.py fast path 丢了
        url→source 转录),必须能被识别出来 —— 这正是溯源率要度量的东西。
        """
        return self.source_url.startswith(("http://", "https://"))


@dataclass
class SourceDoc:
    """一份原始资料(卷宗)。来自 researcher.get_research_sources()。"""

    url: str
    title: str = ""
    raw_content: str = ""


@dataclass
class ResearchArtifact:
    """一次研究的完整存档。"""

    research_id: str
    query: str
    label: str  # L0-A 判定
    created_at: str  # ISO8601
    report_md: str
    sources: list[SourceDoc] = field(default_factory=list)
    context_chunks: list[ContextChunk] = field(default_factory=list)
    unattributed_context: str = ""  # extractor 卡片、骨架行等没有 Source 头的部分
    run_config: dict = field(default_factory=dict)  # 哪套模型/检索器产出的,前后对比要用

    # ---------- 落盘 / 加载 ----------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ResearchArtifact":
        return cls(
            **{
                **d,
                "sources": [SourceDoc(**s) for s in d.get("sources", [])],
                "context_chunks": [
                    ContextChunk(**c) for c in d.get("context_chunks", [])
                ],
            }
        )

    def save(self, results_dir: Path | str = RESULTS_DIR) -> Path:
        results_dir = Path(results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        path = results_dir / f"{self.research_id}.json"
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    @classmethod
    def load(cls, path: Path | str) -> "ResearchArtifact":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # ---------- 便利视图 ----------

    @property
    def source_urls(self) -> set[str]:
        """卷宗里所有真实 URL —— 溯源率判断"引用是否真在资料里"的依据。"""
        return {s.url for s in self.sources if s.url.startswith(("http://", "https://"))}

    def sources_by_url(self) -> dict[str, SourceDoc]:
        return {s.url: s for s in self.sources}


def parse_context_blocks(context) -> tuple[list[ContextChunk], str]:
    """把 researcher.context 反解成 (带来源的块, 无来源的残余)。

    Args:
        context: `researcher.context`。可能是 list[str](原生形态)或 str
                 (投研 strategy 拼接后覆盖成的合并字符串)。

    Returns:
        (chunks, unattributed)。任一为空都是合法结果 —— 例如纯 extractor
        卡片的上下文会解出 0 个块、全部落进 unattributed。
    """
    if isinstance(context, (list, tuple)):
        text = "\n\n".join(str(c) for c in context)
    else:
        text = str(context or "")

    matches = list(_BLOCK_HEADER.finditer(text))
    if not matches:
        return [], text.strip()

    chunks: list[ContextChunk] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunks.append(
            ContextChunk(
                source_url=m.group("source").strip(),
                title=m.group("title").strip(),
                content=text[m.end() : end].strip(),
            )
        )

    # 第一个块头之前的内容 = strategy 拼进去的骨架行/卡片
    return chunks, text[: matches[0].start()].strip()
