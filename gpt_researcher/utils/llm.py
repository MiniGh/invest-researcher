"""LLM utilities for GPT Researcher.

This module provides utility functions for interacting with various
LLM providers through a unified interface.
"""
from __future__ import annotations

import logging
import os
from typing import Any
import asyncio

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

from gpt_researcher.llm_provider.generic.base import (
    NO_SUPPORT_TEMPERATURE_MODELS,
    SUPPORT_REASONING_EFFORT_MODELS,
    ReasoningEfforts,
)

from ..prompts import PromptFamily
from .costs import estimate_llm_cost
from .validators import Subtopics


def get_llm(llm_provider: str, **kwargs):
    """Get an LLM provider instance.

    Args:
        llm_provider: The name of the LLM provider (e.g., 'openai', 'anthropic').
        **kwargs: Additional keyword arguments passed to the provider.

    Returns:
        A GenericLLMProvider instance configured for the specified provider.
    """
    from gpt_researcher.llm_provider import GenericLLMProvider
    return GenericLLMProvider.from_provider(llm_provider, **kwargs)


# 调用 LLM API（OpenAI/Claude/etc）并处理流式输出
# 不可重试的失败:凭据 / 计费 / 权限问题。这类错误重试没有意义,而默认 10 次
# 尝试 + 指数退避会白等 63 秒才放弃 —— 一次深度研究几十次调用就是几十分钟,
# 且最终仍然失败。实测硅基流动余额耗尽时,整轮评估在无用重试上耗掉大半时间,
# 而 L0-A 分类只是静默降级到"其他",故障看起来像模型判断变差,不像账户欠费。
_PERMANENT_STATUS = (401, 402, 403, 404)


def _is_permanent_failure(exc: Exception) -> bool:
    """判断异常是否属于重试也不会好的一类。"""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and status in _PERMANENT_STATUS:
        return True
    resp = getattr(exc, "response", None)
    resp_status = getattr(resp, "status_code", None)
    if isinstance(resp_status, int) and resp_status in _PERMANENT_STATUS:
        return True
    text = str(exc)
    if any(f"Error code: {s}" in text for s in _PERMANENT_STATUS):
        return True
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "balance is insufficient",
            "insufficient_quota",
            "invalid api key",
            "incorrect api key",
            "authentication",
        )
    )


async def create_chat_completion(
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = 0.4,
        max_tokens: int | None = 4000,
        llm_provider: str | None = None,
        stream: bool = False,
        websocket: Any | None = None,
        llm_kwargs: dict[str, Any] | None = None,
        cost_callback: callable = None,
        reasoning_effort: str | None = ReasoningEfforts.Medium.value,
        **kwargs
) -> str:
    """Create a chat completion using the OpenAI API
    Args:
        messages (list[dict[str, str]]): The messages to send to the chat completion.
        model (str, optional): The model to use. Defaults to None.
        temperature (float, optional): The temperature to use. Defaults to 0.4.
        max_tokens (int, optional): The max tokens to use. Defaults to 4000.
        llm_provider (str, optional): The LLM Provider to use.
        stream (bool): Whether to stream the response. Defaults to False.
        webocket (WebSocket): The websocket used in the currect request,
        llm_kwargs (dict[str, Any], optional): Additional LLM keyword arguments. Defaults to None.
        cost_callback: Callback function for updating cost.
        reasoning_effort (str, optional): Reasoning effort for OpenAI's reasoning models. Defaults to 'low'.
        **kwargs: Additional keyword arguments.
    Returns:
        str: The response from the chat completion.
    """
    # validate input
    if model is None:
        raise ValueError("Model cannot be None")
    if max_tokens is not None and max_tokens > 32001:
        raise ValueError(
            f"Max tokens cannot be more than 32,000, but got {max_tokens}")

    # Get the provider from supported providers
    provider_kwargs = {'model': model}

    if llm_kwargs:
        provider_kwargs.update(llm_kwargs)

    if model in SUPPORT_REASONING_EFFORT_MODELS:
        provider_kwargs['reasoning_effort'] = reasoning_effort

    if model not in NO_SUPPORT_TEMPERATURE_MODELS:
        provider_kwargs['temperature'] = temperature
        provider_kwargs['max_tokens'] = max_tokens
    else:
        provider_kwargs['temperature'] = None
        provider_kwargs['max_tokens'] = None

    if llm_provider == "openai":
        base_url = os.environ.get("OPENAI_BASE_URL", None)
        if base_url:
            provider_kwargs['openai_api_base'] = base_url

    provider = get_llm(llm_provider, **provider_kwargs)
    response = ""
    # create response
    max_attempts = 1 if (stream and websocket is not None) else 10
    last_exception: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await provider.get_chat_response(
                messages, stream, websocket, **kwargs
            )
        except Exception as exc:
            last_exception = exc
            if _is_permanent_failure(exc):
                logging.getLogger(__name__).error(
                    f"LLM request failed with a non-retryable error, giving up "
                    f"after attempt {attempt}/{max_attempts}: {exc}"
                )
                break
            logging.getLogger(__name__).warning(
                f"LLM request failed (attempt {attempt}/{max_attempts}): {exc}"
            )
            if attempt < max_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
                continue
            break

        if not response:
            last_exception = RuntimeError("Empty response from LLM provider")
            logging.getLogger(__name__).warning(
                f"LLM returned empty response (attempt {attempt}/{max_attempts})"
            )
            if attempt < max_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
                continue
            break

        if cost_callback:
            llm_costs = estimate_llm_cost(str(messages), response)
            cost_callback(llm_costs)

        return response

    logging.error(f"Failed to get response from {llm_provider} API")
    raise RuntimeError(f"Failed to get response from {llm_provider} API") from last_exception


async def construct_subtopics(
    task: str,
    data: str,
    config,
    subtopics: list = [],
    prompt_family: type[PromptFamily] | PromptFamily = PromptFamily,
    **kwargs
) -> list:
    """
    Construct subtopics based on the given task and data.

    Args:
        task (str): The main task or topic.
        data (str): Additional data for context.
        config: Configuration settings.
        subtopics (list, optional): Existing subtopics. Defaults to [].
        prompt_family (PromptFamily): Family of prompts
        **kwargs: Additional keyword arguments.

    Returns:
        list: A list of constructed subtopics.
    """
    try:
        parser = PydanticOutputParser(pydantic_object=Subtopics)

        prompt = PromptTemplate(
            template=prompt_family.generate_subtopics_prompt(),
            input_variables=["task", "data", "subtopics", "max_subtopics"],
            partial_variables={
                "format_instructions": parser.get_format_instructions()},
        )

        provider_kwargs = {'model': config.smart_llm_model}

        if config.llm_kwargs:
            provider_kwargs.update(config.llm_kwargs)

        if config.smart_llm_model in SUPPORT_REASONING_EFFORT_MODELS:
            provider_kwargs['reasoning_effort'] = ReasoningEfforts.High.value
        else:
            provider_kwargs['temperature'] = config.temperature
            provider_kwargs['max_tokens'] = config.smart_token_limit

        provider = get_llm(config.smart_llm_provider, **provider_kwargs)

        model = provider.llm

        chain = prompt | model | parser

        output = await chain.ainvoke({
            "task": task,
            "data": data,
            "subtopics": subtopics,
            "max_subtopics": config.max_subtopics
        }, **kwargs)

        return output

    except Exception as e:
        print("Exception in parsing subtopics : ", e)
        logging.getLogger(__name__).error("Exception in parsing subtopics : \n {e}")
        return subtopics
