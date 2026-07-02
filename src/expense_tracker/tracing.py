"""LangSmith tracing helpers for the receipt pipeline."""

from __future__ import annotations

import os
from typing import Any

from langsmith import traceable
from langchain_core.tracers.langchain import wait_for_all_tracers

from expense_tracker.config import get_bool_env


def tracing_enabled() -> bool:
    """Return whether LangSmith tracing is enabled for the current process."""
    if "EXPENSE_TRACKER_ENABLE_LANGSMITH" in os.environ:
        return get_bool_env("EXPENSE_TRACKER_ENABLE_LANGSMITH", default=False)
    return get_bool_env("LANGSMITH_TRACING", default=False)


def configure_langsmith_tracing_env() -> bool:
    """Apply the project-level LangSmith switch onto LANGSMITH_TRACING."""
    enabled = tracing_enabled()
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    return enabled


def flush_traces() -> None:
    """Wait for async LangSmith uploads to complete before process exit."""
    wait_for_all_tracers()


def receipt_traceable(*, name: str, run_type: str = "chain", metadata: dict[str, Any] | None = None):
    """Create a traceable decorator that respects LANGSMITH_TRACING."""
    configure_langsmith_tracing_env()
    return traceable(
        name=name,
        run_type=run_type,
        metadata=metadata or {},
    )
