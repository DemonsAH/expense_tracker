"""LangChain chat model factory for SiliconFlow-hosted Qwen models."""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from expense_tracker.config import get_required_env, load_dotenv_file
from expense_tracker.tracing import configure_langsmith_tracing_env


def build_qwen_chat_model(
    *,
    model: str = "Qwen/Qwen3.6-27B",
    temperature: float = 0.1,
    max_tokens: int = 3000,
) -> ChatOpenAI:
    """Build a LangChain chat client against SiliconFlow's OpenAI-compatible API."""
    load_dotenv_file()
    configure_langsmith_tracing_env()

    api_key = get_required_env("SILICONFLOW_API_KEY")
    base_url = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        model_kwargs={"response_format": {"type": "json_object"}},
        metadata={
            "ls_provider": "siliconflow",
            "ls_model_name": model,
            "integration": "langchain_openai",
        },
    )
