"""Schemas for persisted receipt data and review state."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode


class ReceiptItemRecord(BaseModel):
    id: str = Field(min_length=1)
    receipt_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    normalized_name: str = Field(min_length=1)
    category: ItemCategory
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    total_price: Decimal
    owner_id: str = Field(min_length=1)
    owner_marker: str | None = None


class RemovedItemRecord(BaseModel):
    name: str = Field(min_length=1)
    normalized_name: str = Field(min_length=1)
    category: ItemCategory
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    total_price: Decimal
    owner_id: str = Field(min_length=1)
    owner_marker: str | None = None
    reason: str = Field(min_length=1)
    related_index: int | None = None


class ReceiptRecord(BaseModel):
    id: str = Field(min_length=1)
    merchant: str = Field(min_length=1)
    purchase_date: date
    currency: str = Field(min_length=1)
    total_amount: Decimal = Field(ge=0)
    payment_method: str | None = None
    default_owner_id: str = Field(min_length=1)
    owner_mode: OwnerMode
    receipt_owner_marker: str | None = None
    image_path: str = Field(min_length=1)
    image_hash: str = Field(min_length=1)
    ocr_raw_text: str | None = None
    is_verified: bool = False
    ocr_status: OcrStatus = OcrStatus.PENDING
    ocr_attempts: int = Field(default=0, ge=0)
    ocr_failure_reason: str | None = None
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None = None
    items: list[ReceiptItemRecord] = Field(default_factory=list)
    removed_items: list[RemovedItemRecord] = Field(default_factory=list)


class FailedOcrRecord(BaseModel):
    image_path: str = Field(min_length=1)
    archived_image_path: str = Field(min_length=1)
    image_hash: str | None = None
    attempts: int = Field(ge=1)
    failure_reason: str = Field(min_length=1)
    raw_outputs: list[str] = Field(default_factory=list)
    created_at: datetime

    model_config = {
        "extra": "ignore",
    }


class ReceiptStore(BaseModel):
    last_receipt_id: int = 0
    last_item_id: int = 0
    receipts: list[ReceiptRecord] = Field(default_factory=list)
    failed_ocr_records: list[FailedOcrRecord] = Field(default_factory=list)
    budgets: dict[str, Decimal] = Field(default_factory=dict)

    model_config = {
        "extra": "ignore",
    }
