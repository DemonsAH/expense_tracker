"""Converters between model-output schemas and persisted domain schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from expense_tracker.schemas.domain import ReceiptItemRecord, ReceiptRecord, RemovedItemRecord
from expense_tracker.schemas.extraction import ExtractedReceipt

if TYPE_CHECKING:
    from expense_tracker.pipelines.receipt_postprocess import ProcessedReceiptItems


def extracted_to_receipt_record(
    extracted: ExtractedReceipt,
    *,
    processed_items: ProcessedReceiptItems,
    receipt_id: str,
    image_path: str,
    image_hash: str,
    item_id_factory,
    raw_text: str | None = None,
) -> ReceiptRecord:
    now = datetime.now(timezone.utc)
    formal_total_amount = sum(
        (item.total_price for item in processed_items.formal_items),
        start=Decimal("0"),
    )
    items = []
    for item in processed_items.formal_items:
        item_id = item_id_factory()
        items.append(
            ReceiptItemRecord(
                id=item_id,
                receipt_id=receipt_id,
                name=item.name,
                normalized_name=item.normalized_name,
                category=item.category,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total_price=item.total_price,
                owner_id=item.owner_id,
                owner_marker=item.owner_marker,
            )
        )

    removed_items = [
        RemovedItemRecord(
            name=removed.item.name,
            normalized_name=removed.item.normalized_name,
            category=removed.item.category,
            quantity=removed.item.quantity,
            unit_price=removed.item.unit_price,
            total_price=removed.item.total_price,
            owner_id=removed.item.owner_id,
            owner_marker=removed.item.owner_marker,
            reason=removed.reason,
            related_index=removed.related_index,
        )
        for removed in processed_items.removed_items
    ]

    return ReceiptRecord(
        id=receipt_id,
        merchant=extracted.merchant,
        purchase_date=extracted.purchase_date,
        currency=extracted.currency,
        # Persist the formal-accounting total after cancellation post-processing.
        total_amount=formal_total_amount,
        payment_method=extracted.payment_method,
        default_owner_id=extracted.default_owner_id,
        owner_mode=extracted.owner_mode,
        receipt_owner_marker=extracted.receipt_owner_marker,
        image_path=image_path,
        image_hash=image_hash,
        ocr_raw_text=raw_text,
        created_at=now,
        updated_at=now,
        items=items,
        removed_items=removed_items,
    )


def extracted_to_receipt_record_legacy(
    extracted: ExtractedReceipt,
    *,
    receipt_id: str,
    image_path: str,
    item_id_factory,
    raw_text: str | None = None,
) -> ReceiptRecord:
    now = datetime.now(timezone.utc)
    items = []
    for item in extracted.items:
        item_id = item_id_factory()
        items.append(
            ReceiptItemRecord(
                id=item_id,
                receipt_id=receipt_id,
                name=item.name,
                normalized_name=item.normalized_name,
                category=item.category,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total_price=item.total_price,
                owner_id=item.owner_id,
                owner_marker=item.owner_marker,
            )
        )

    return ReceiptRecord(
        id=receipt_id,
        merchant=extracted.merchant,
        purchase_date=extracted.purchase_date,
        currency=extracted.currency,
        total_amount=extracted.total_amount,
        payment_method=extracted.payment_method,
        default_owner_id=extracted.default_owner_id,
        owner_mode=extracted.owner_mode,
        receipt_owner_marker=extracted.receipt_owner_marker,
        image_path=image_path,
        image_hash=f"legacy::{image_path}",
        ocr_raw_text=raw_text,
        created_at=now,
        updated_at=now,
        items=items,
    )
