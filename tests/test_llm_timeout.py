"""LLM 调用超时的回归测试。

不设超时会导致请求无限挂起:实测一次 value_chain 研究在写报告阶段卡死
59 分钟,进程 67 分钟只消耗 6 秒 CPU,始终挂着一条到代理的连接等响应,
不会自行恢复。langchain 的 ChatOpenAI 默认不设 timeout,一次丢包就永久等待。

这里锁定三件事:默认配置里有有限超时、Config 能读到、且它确实落到底层
httpx 客户端而不是被静默忽略。
"""
import os

import pytest

from gpt_researcher.config.variables.default import DEFAULT_CONFIG

# 同一次运行里正常调用耗时为 8s / 86s / 271s,最长约 4.5 分钟。
# 超时必须显著高于此值以免误杀,同时远低于"无限"。
MIN_REASONABLE_TIMEOUT = 300
MAX_REASONABLE_TIMEOUT = 1800


def test_default_config_sets_a_finite_llm_timeout():
    kwargs = DEFAULT_CONFIG.get("LLM_KWARGS", {})
    assert "timeout" in kwargs, "LLM_KWARGS 必须设置 timeout,否则请求会无限挂起"
    assert MIN_REASONABLE_TIMEOUT <= kwargs["timeout"] <= MAX_REASONABLE_TIMEOUT


def test_default_config_sets_retries():
    kwargs = DEFAULT_CONFIG.get("LLM_KWARGS", {})
    assert kwargs.get("max_retries", 0) >= 1, "超时后至少要重试一次,否则一次抖动就丢一条 sub-query"


def test_config_exposes_llm_kwargs():
    from gpt_researcher.config import Config

    cfg = Config()
    assert cfg.llm_kwargs.get("timeout") == DEFAULT_CONFIG["LLM_KWARGS"]["timeout"]


def test_timeout_reaches_the_underlying_http_client(monkeypatch):
    """关键用例:参数被 ChatOpenAI 接受不等于生效,必须验证它传到了 httpx。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")

    from gpt_researcher.llm_provider.generic.base import GenericLLMProvider

    kwargs = DEFAULT_CONFIG["LLM_KWARGS"]
    provider = GenericLLMProvider.from_provider(
        "openai", model="gpt-4o-mini", temperature=0, max_tokens=16, **kwargs
    )
    llm = provider.llm

    assert llm.request_timeout == pytest.approx(float(kwargs["timeout"]))
    assert llm.max_retries == kwargs["max_retries"]

    inner = getattr(llm.client, "_client", None)
    inner = getattr(inner, "_client", inner)
    timeout = getattr(inner, "timeout", None)
    assert timeout is not None, "取不到底层 httpx 客户端的 timeout,断言链已失效"
    assert float(timeout.read or timeout.connect) == pytest.approx(float(kwargs["timeout"]))
