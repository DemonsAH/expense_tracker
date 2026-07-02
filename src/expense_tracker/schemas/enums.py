"""Shared enums for receipt extraction and persistence."""

from __future__ import annotations

from enum import Enum


class ItemCategory(str, Enum):
    SNACKS = "SNACKS"
    PERSONAL_CARE = "PERSONAL_CARE"
    HOUSEHOLD = "HOUSEHOLD"
    DRINK = "DRINK"
    MEAT = "MEAT"
    VEGGIE = "VEGGIE"
    FRUIT = "FRUIT"
    OTHER = "OTHER"
    DINING = "DINING"


class OwnerMode(str, Enum):
    NORMAL = "normal"
    RECEIPT_OWNER = "receipt_owner"
    ITEM_OWNER = "item_owner"


class OcrStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"

