"""Post-processing rules for formal receipt items.

Sofortstorno / cancellation items are no longer treated as special — both original
and reversed (negative) lines are kept as formal items.  This means the post-processing
step currently performs no filtering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from expense_tracker.pipelines.receipt_validation import DEFAULT_MONEY_TOLERANCE
from expense_tracker.schemas.extraction import ExtractedReceipt, ExtractedReceiptItem


@dataclass
class RemovedReceiptItem:
    item: ExtractedReceiptItem
    reason: str
    related_index: int | None = None


@dataclass
class ProcessedReceiptItems:
    formal_items: list[ExtractedReceiptItem] = field(default_factory=list)
    removed_items: list[RemovedReceiptItem] = field(default_factory=list)


def process_extracted_receipt_items(
    extracted: ExtractedReceipt,
    *,
    money_tolerance: Decimal = DEFAULT_MONEY_TOLERANCE,
) -> ProcessedReceiptItems:
    """Keep all items (including Storno / negative lines) as formal items."""
    return ProcessedReceiptItems(
        formal_items=list(extracted.items),
        removed_items=[],
    )
