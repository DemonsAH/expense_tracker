"""Expense Tracker package."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "ReceiptAttemptFailure",
    "ReceiptIngestionResult",
    "ingest_receipt_once",
    "ingest_receipt_with_retries",
    "parse_extracted_receipt",
]


def __getattr__(name: str):
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