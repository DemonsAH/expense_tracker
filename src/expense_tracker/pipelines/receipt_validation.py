"""Business validation rules for extracted receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from expense_tracker.schemas.extraction import ExtractedReceipt
from expense_tracker.schemas.owners import OwnersConfig


DEFAULT_MONEY_TOLERANCE = Decimal("0.05")
NEGATIVE_TOTAL_ALLOWED_CANCELLATION_PATTERNS = (
    r"\bstorno\b",
    r"\bsofortstorno\b",
    r"\bcancel(?:led|ation)?\b",
    r"\bruecknahme\b",
    r"\brucknahme\b",
)
NEGATIVE_TOTAL_ALLOWED_LEERGUT_PATTERNS = (
    r"\bleergut\b",
    r"\bpfand\b",
    r"\bflaschenpfand\b",
    r"\bmehrwegpfand\b",
)


@dataclass
class ReceiptValidationResult:
    is_valid: bool
    issues: list[str] = field(default_factory=list)


def _within_tolerance(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    return abs(left - right) <= tolerance


def _matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    normalized = text.strip().lower()
    return any(re.search(pattern, normalized) for pattern in patterns)


def is_cancellation_item(name: str, normalized_name: str) -> bool:
    haystack = f"{name} {normalized_name}"
    return _matches_any_pattern(haystack, NEGATIVE_TOTAL_ALLOWED_CANCELLATION_PATTERNS)


def is_leergut_item(name: str, normalized_name: str) -> bool:
    haystack = f"{name} {normalized_name}"
    return _matches_any_pattern(haystack, NEGATIVE_TOTAL_ALLOWED_LEERGUT_PATTERNS)


def validate_extracted_receipt_business_rules(
    extracted: ExtractedReceipt,
    *,
    owners: OwnersConfig,
    money_tolerance: Decimal = DEFAULT_MONEY_TOLERANCE,
) -> ReceiptValidationResult:
    issues: list[str] = []
    owner_ids = {owner.id for owner in owners.owners}

    if extracted.default_owner_id not in owner_ids:
        issues.append("default_owner_id_not_found")

    item_total_sum = Decimal("0")
    for index, item in enumerate(extracted.items):
        if item.owner_id not in owner_ids:
            issues.append(f"items[{index}].owner_id_not_found")

        negative_item = item.total_price < 0

        expected_total = item.quantity * item.unit_price
        if negative_item:
            expected_total = -expected_total
        if not _within_tolerance(expected_total, item.total_price, money_tolerance):
            issues.append(f"items[{index}].total_price_mismatch")

        item_total_sum += item.total_price

    if not _within_tolerance(item_total_sum, extracted.total_amount, money_tolerance):
        issues.append("receipt_total_mismatch")

    return ReceiptValidationResult(
        is_valid=not issues,
        issues=issues,
    )
