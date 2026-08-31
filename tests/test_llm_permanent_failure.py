"""不可重试错误的短路(A2)。

背景:硅基流动余额耗尽时返回 402,而重试逻辑把它当成瞬时抖动,重试 10 次 +
指数退避,单次调用白等 63 秒才放弃。一轮评估几十次调用就是几十分钟无效等待,
而表层症状只是 L0-A 静默降级到"其他"—— 看起来像模型判断变差,不像账户欠费。
"""
import pytest

from gpt_researcher.utils.llm import _is_permanent_failure


class _RespErr(Exception):
    """模拟带 response.status_code 的 SDK 异常。"""

    def __init__(self, status):
        super().__init__(f"http error {status}")

        class _R:
            status_code = status

        self.response = _R()


@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_permanent_by_status_attr(status):
    exc = Exception("boom")
    exc.status_code = status
    assert _is_permanent_failure(exc)


@pytest.mark.parametrize("status", [401, 402, 403])
def test_permanent_by_response_status(status):
    assert _is_permanent_failure(_RespErr(status))


def test_permanent_by_openai_error_text():
    """实际线上收到的形状:openai SDK 把状态码拼进了消息文本。"""
    exc = Exception(
        "Error code: 402 - {'code': 30001, 'message': 'Sorry, your account "
        "balance is insufficient', 'data': None}"
    )
    assert _is_permanent_failure(exc)


@pytest.mark.parametrize("text", [
    "Sorry, your account balance is insufficient",
    "You exceeded your current quota: insufficient_quota",
    "Incorrect API key provided",
    "Invalid API key",
    "authentication failed",
])
def test_permanent_by_message(text):
    assert _is_permanent_failure(Exception(text))


@pytest.mark.parametrize("exc", [
    Exception("Error code: 429 - rate limit exceeded"),
    Exception("Error code: 500 - internal server error"),
    Exception("Read timed out"),
    _RespErr(503),
    TimeoutError("timeout"),
])
def test_transient_errors_still_retry(exc):
    """限流 / 5xx / 超时必须仍然重试 —— 短路过头会把可恢复的失败变成硬失败。"""
    assert not _is_permanent_failure(exc)


# ---------------- 智谱 provider 接线 ----------------
#
# 写作模型从硅基流动上的 DeepSeek 换成智谱 GLM。这里锁住两件容易悄悄坏掉的事:
# provider 名被接受、以及 LLM 不去抢 EMBEDDING 在用的 OPENAI_* 变量。

def test_zhipuai_provider_is_registered():
    from gpt_researcher.llm_provider.generic.base import _SUPPORTED_PROVIDERS
    assert "zhipuai" in _SUPPORTED_PROVIDERS


def test_zhipuai_uses_its_own_credentials(monkeypatch):
    """base_url 与 key 都来自 ZHIPUAI_*,不碰 OPENAI_*(那是 EMBEDDING 的)。"""
    from gpt_researcher.llm_provider.generic.base import GenericLLMProvider

    monkeypatch.setenv("ZHIPUAI_API_KEY", "zhipu-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "siliconflow-key-for-embedding")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1")

    llm = GenericLLMProvider.from_provider("zhipuai", model="GLM-5.3-Flash").llm
    base = getattr(llm, "openai_api_base", None) or getattr(llm, "base_url", None)
    assert "bigmodel.cn" in str(base), base
    assert getattr(llm, "model_name", None) == "GLM-5.3-Flash"


def test_zhipuai_missing_key_says_where_to_put_it(monkeypatch):
    """裸 KeyError 说不清该改哪个文件的哪一行。"""
    from gpt_researcher.llm_provider.generic.base import GenericLLMProvider

    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ZHIPUAI_API_KEY"):
        GenericLLMProvider.from_provider("zhipuai", model="GLM-5.3-Flash")
