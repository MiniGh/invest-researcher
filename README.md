<div align="center">

# 研市 ResearchDesk

**美股基本面研究助手**

输入一个投研问题,得到一份带出处的研究报告。

</div>

<!-- 截图占位:首屏 -->
<!-- ![首屏](docs/images/hero.png) -->

---

## 能回答什么问题

它认得五类投研问题,每一类走不同的拆解策略。

| 你问的是 | 它会给你 | 示例 |
|---|---|---|
| **公司画像** | 单家公司的业务、财务、竞争位置、催化剂、风险 | 分析英伟达最新季度财务表现 |
| **公司对比** | 2–4 家公司的对齐对比矩阵,逐维度解读 | 对比 NVDA、AMD、INTC 的 AI 芯片战略 |
| **行业横切** | 市场规模、驱动、阻力、竞争格局 + 代表公司卡片 | 美国电动车电池行业格局 |
| **产业链纵切** | 上中下游拆解、各环节经济性与卡点、每环节龙头 | 美国半导体产业链从上游到下游 |
| **主题受益** | 主题叙事、按传导机制分类的受益方、代表标的 | 哪些美股最受益于 AI 基建 |

问题不属于这五类时(比如"怎么学价值投资"),它走通用研究路径,照样出报告。

<!-- 截图占位:研究计划面板 -->
<!-- ![研究计划](docs/images/plan.png) -->

## 和直接问 AI 有什么不同

**它不是一次问答,是一轮研究。**

- **先判断问题类型,再决定怎么查。** 问"产业链"和问"某公司财报",需要的信息结构完全不同,拆解方式也不同。
- **分层展开。** 问产业链时,它先查出这条链有哪几个环节,再针对每个环节分别去查经济性、卡点、龙头公司,最后逐家取财务数据 —— 一个问题会展开成三十来条检索。
- **数字带出处。** 报告里的每个数字都来自检索到的原文,附来源链接;查不到就写明"未找到",不做填充。
- **只看美股。** 代表公司一律限定为美国上市标的(含 ADR,如台积电 TSM、比亚迪 BYDDY)。

界面上能实时看到它的判断和展开过程 —— 识别成了哪一类、拆出了哪些环节、每个环节挂了哪些标的。

<!-- 截图占位:研究报告 -->
<!-- ![研究报告](docs/images/report.png) -->

## 快速开始

**环境:** Python 3.11 及以上。

```bash
git clone https://github.com/MiniGh/invest-researcher.git
cd invest-researcher
pip install -r requirements.txt
```

**配置密钥。** 在项目根目录建一个 `.env` 文件:

```env
TAVILY_API_KEY=你的-tavily-key
DEEPSEEK_API_KEY=你的-deepseek-key
OPENAI_API_KEY=你的-siliconflow-key
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
```

| 变量 | 用途 | 申请地址 |
|---|---|---|
| `TAVILY_API_KEY` | 网页检索 | [tavily.com](https://tavily.com) |
| `DEEPSEEK_API_KEY` | 生成模型(分类、摘要、写报告) | [platform.deepseek.com](https://platform.deepseek.com) |
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` | 向量嵌入,默认用硅基流动的 bge-m3 | [siliconflow.cn](https://siliconflow.cn) |

> 嵌入服务走的是 OpenAI 兼容接口,所以变量名叫 `OPENAI_*`,填的是硅基流动的密钥和地址。

**启动:**

```bash
python -m uvicorn main:app --reload
```

打开 http://localhost:8000 即可。

## 怎么用

输入框里直接写问题,中英文都可以。**推荐用英文提问** —— 英文财经信源的覆盖面明显更好,报告本身也是英文输出。输入框下方有五条示例问题,点一下自动填入。

一次研究通常两到五分钟,取决于问题类型:公司画像最快,产业链和主题最慢(要展开三层)。过程中可以看到:

- **研究计划** —— 它把问题识别成了哪一类、拆出了哪些节点、每个节点找到哪些标的
- **执行日志** —— 逐条检索、抓取、摘要的实时过程
- **研究报告** —— 流式生成,完成后可导出 PDF / Word / Markdown

报告完成后可以在下方继续追问。

**高级设置**(默认折叠)里可以调:

| 选项 | 说明 |
|---|---|
| 报告类型 | 标准(约 2 分钟)/ 详尽(约 5 分钟)/ 资料清单 / 深度研究 |
| 报告语气 | 分析型(默认)/ 中立型 / 正式型 |
| 资料来源 | 全网检索 / 本地文档 / 混合 |
| 每条检索抓取网页数 | 默认 5,越大越全但越慢 |
| 限定域名 | 留空则由系统按问题类型自动决定是否套用财经站点白名单 |

## 换用其他模型

默认配置在 `gpt_researcher/config/variables/default.py`:

```python
"FAST_LLM": "deepseek:deepseek-v4-flash",      # 分类、摘要等高频调用
"SMART_LLM": "deepseek:deepseek-v4-flash",     # 写报告
"STRATEGIC_LLM": "deepseek:deepseek-v4-pro",   # 复杂推理
"EMBEDDING": "custom:Pro/BAAI/bge-m3",
```

改成 `openai:gpt-4o-mini` 之类即可切换供应商,格式是 `供应商:模型名`。

## 命令行用法

不想开浏览器时:

```bash
python cli.py "Analyze the value chain of the US semiconductor industry" \
  --report_type research_report --tone analytical
```

报告会写到 `outputs/` 目录。

---

<div align="center">

研究辅助工具,输出内容不构成投资建议。

基于 [GPT Researcher](https://github.com/assafelovic/gpt-researcher) 开源内核构建。

</div>
