"""Expense Tracker package."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "ReceiptAttemptFailure",
    "ReceiptIngestionResult",
    "build_qwen_chat_model",
    "ingest_receipt_once",
    "ingest_receipt_with_retries",
    "parse_extracted_receipt",
    "run_receipt_step1",
]


def __getattr__(name: str):
    if name == "build_qwen_chat_model":
        return import_module("expense_tracker.llm_client").build_qwen_chat_model
    if name == "run_receipt_step1":
        return import_module("expense_tracker.receipt_step1").run_receipt_step1
    if name in {
        "ReceiptAttemptFailure",
        "ReceiptIngestionResult",
        "ingest_receipt_once",
        "ingest_receipt_with_retries",
        "parse_extracted_receipt",
    }:
        pipelines = import_module("expense_tracker.pipelines")
        return getattr(pipelines, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
