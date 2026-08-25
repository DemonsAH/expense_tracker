"""OpenAI-compatible LLM client for SiliconFlow-hosted models."""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from expense_tracker.config import get_required_env, load_dotenv_file


def build_chat_model(
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> ChatOpenAI:
    """Build a LangChain ChatOpenAI client against SiliconFlow's API.

    Reads SILICONFLOW_API_KEY, SILICONFLOW_BASE_URL, and
    EXPENSE_TRACKER_LLM_MODEL from .env.
    """
    load_dotenv_file()

    api_key = get_required_env("SILICONFLOW_API_KEY")
    base_url = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    model = model or os.environ.get("EXPENSE_TRACKER_LLM_MODEL", "deepseek-ai/DeepSeek-V4-Flash")

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )