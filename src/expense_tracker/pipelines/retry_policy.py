"""Retry policy definitions for receipt ingestion."""

from __future__ import annotations


RETRYABLE_ERROR_MARKERS = (
    "Model output is not valid JSON",
    "Model output does not match ExtractedReceipt schema",
    "default_owner_id_not_found",
    ".owner_id_not_found",
    ".total_price_mismatch",
    "receipt_total_mismatch",
)


def is_retryable_ingestion_error(message: str) -> bool:
    """Return whether a failed attempt should trigger another model call."""
    return any(marker in message for marker in RETRYABLE_ERROR_MARKERS)
