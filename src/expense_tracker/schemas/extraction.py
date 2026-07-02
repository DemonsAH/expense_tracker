"""Schemas for direct model output from the multimodal extraction step."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from expense_tracker.schemas.enums import ItemCategory, OwnerMode


Money = Decimal
Quantity = Decimal


class ExtractedReceiptItem(BaseModel):
    name: str = Field(min_length=1)
    normalized_name: str = Field(min_length=1)
    category: ItemCategory
    quantity: Quantity = Field(gt=0)
    unit_price: Money = Field(ge=0)
    total_price: Money
    owner_id: str = Field(min_length=1)
    owner_marker: str | None = None

    @field_validator("owner_marker")
    @classmethod
    def normalize_owner_marker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        return value or None


class ExtractedReceipt(BaseModel):
    merchant: str = Field(min_length=1)
    purchase_date: date
    currency: str = Field(min_length=1)
    total_amount: Money = Field(ge=0)
    payment_method: str | None = None
    owner_mode: OwnerMode
    default_owner_id: str = Field(min_length=1)
    receipt_owner_marker: str | None = None
    items: list[ExtractedReceiptItem] = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != "EUR":
            raise ValueError("currency must be EUR.")
        return normalized

    @field_validator("receipt_owner_marker")
    @classmethod
    def normalize_receipt_owner_marker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        return value or None
