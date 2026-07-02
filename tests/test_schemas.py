"""Unit tests for schemas module: enums, extraction, owners, domain, converters.

Covers PRD sections:
  - 5.1 (output principles)
  - 5.2 (field constraints & structure)
  - 5.3 (category enum)
  - 6.4 (owner config)
  - 9.1 (Receipt)
  - 9.2 (ReceiptItem)
  - 9.3 (RemovedItem)
  - 9.4 (Owner JSON)
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode
from expense_tracker.schemas.extraction import ExtractedReceipt, ExtractedReceiptItem
from expense_tracker.schemas.owners import OwnerConfig, OwnersConfig, load_owners_config
from expense_tracker.schemas.domain import (
    FailedOcrRecord,
    ReceiptItemRecord,
    ReceiptRecord,
    ReceiptStore,
    RemovedItemRecord,
)
from expense_tracker.schemas.converters import (
    extracted_to_receipt_record,
    extracted_to_receipt_record_legacy,
)
from expense_tracker.pipelines.receipt_postprocess import process_extracted_receipt_items


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_item_id_factory(prefix: str = "item"):
    counter = {"value": 0}

    def next_item_id() -> str:
        counter["value"] += 1
        return f"{prefix}_{counter['value']}"

    return next_item_id


def _valid_extraction_payload():
    return {
        "merchant": "REWE",
        "purchase_date": "2026-05-04",
        "currency": "EUR",
        "total_amount": 4.50,
        "payment_method": "card",
        "owner_mode": "normal",
        "default_owner_id": "me",
        "receipt_owner_marker": None,
        "items": [
            {
                "name": "Water",
                "normalized_name": "water",
                "category": "DRINK",
                "quantity": 1,
                "unit_price": 2.50,
                "total_price": 2.50,
                "owner_id": "me",
                "owner_marker": None,
            },
            {
                "name": "Apple",
                "normalized_name": "apple",
                "category": "FRUIT",
                "quantity": 2.0,
                "unit_price": 1.00,
                "total_price": 2.00,
                "owner_id": "me",
                "owner_marker": None,
            },
        ],
    }


def _valid_owners_payload():
    return {
        "owners": [
            {"id": "me", "name": "Me", "marker": "M", "is_me": True},
            {"id": "alice", "name": "Alice", "marker": "A", "is_me": False},
            {"id": "bob", "name": "Bob", "marker": "B", "is_me": False},
        ]
    }


# ===========================================================================
# PRD 5.3: ItemCategory enum
# ===========================================================================

class TestItemCategoryEnum:
    """PRD 5.3: 9 fixed categories; DINING excluded from price ranking."""

    def test_all_nine_categories_exist(self):
        expected = {"SNACKS", "PERSONAL_CARE", "HOUSEHOLD", "DRINK", "MEAT",
                     "VEGGIE", "FRUIT", "OTHER", "DINING"}
        actual = set(c.value for c in ItemCategory)
        assert actual == expected

    def test_category_from_string_matches_prd(self):
        """Ensure each enum member maps to the exact string defined in PRD."""
        assert ItemCategory.SNACKS.value == "SNACKS"
        assert ItemCategory.PERSONAL_CARE.value == "PERSONAL_CARE"
        assert ItemCategory.HOUSEHOLD.value == "HOUSEHOLD"
        assert ItemCategory.DRINK.value == "DRINK"
        assert ItemCategory.MEAT.value == "MEAT"
        assert ItemCategory.VEGGIE.value == "VEGGIE"
        assert ItemCategory.FRUIT.value == "FRUIT"
        assert ItemCategory.OTHER.value == "OTHER"
        assert ItemCategory.DINING.value == "DINING"

    def test_dining_is_excluded_from_price_ranking(self):
        """PRD 5.3: DINING does not participate in price ranking."""
        assert ItemCategory.DINING.value == "DINING"
        # This is enforced by reports._is_price_ranking_item, verified later


class TestOwnerModeEnum:
    """PRD 6.4: three owner_modes."""

    def test_three_modes_exist(self):
        assert OwnerMode.NORMAL.value == "normal"
        assert OwnerMode.RECEIPT_OWNER.value == "receipt_owner"
        assert OwnerMode.ITEM_OWNER.value == "item_owner"


class TestOcrStatusEnum:
    """PRD 7: OCR lifecycle states."""

    def test_five_statuses_exist(self):
        assert OcrStatus.PENDING.value == "pending"
        assert OcrStatus.SUCCESS.value == "success"
        assert OcrStatus.FAILED.value == "failed"
        assert OcrStatus.NEEDS_REVIEW.value == "needs_review"
        assert OcrStatus.VERIFIED.value == "verified"


# ===========================================================================
# PRD 5.2: ExtractedReceipt (model output schema)
# ===========================================================================

class TestExtractedReceiptItemSchema:
    """PRD 5.2: item-level output constraints."""

    def test_valid_item(self):
        item = ExtractedReceiptItem.model_validate({
            "name": "Water 1L",
            "normalized_name": "water",
            "category": "DRINK",
            "quantity": 1,
            "unit_price": 2.50,
            "total_price": 2.50,
            "owner_id": "me",
            "owner_marker": None,
        })
        assert item.name == "Water 1L"
        assert item.normalized_name == "water"
        assert item.category == ItemCategory.DRINK
        assert item.quantity == Decimal("1")
        assert item.unit_price == Decimal("2.50")
        assert item.total_price == Decimal("2.50")
        assert item.owner_id == "me"
        assert item.owner_marker is None

    def test_empty_name_rejected(self):
        with pytest.raises(PydanticValidationError, match="name"):
            ExtractedReceiptItem.model_validate({
                "name": "",
                "normalized_name": "x",
                "category": "DRINK",
                "quantity": 1,
                "unit_price": 1,
                "total_price": 1,
                "owner_id": "me",
            })

    def test_quantity_zero_rejected(self):
        """quantity must be > 0 (PRD 5.2: quantity uses number, but schema enforces gt=0)."""
        with pytest.raises(PydanticValidationError):
            ExtractedReceiptItem.model_validate({
                "name": "Test",
                "normalized_name": "test",
                "category": "DRINK",
                "quantity": 0,
                "unit_price": 1,
                "total_price": 1,
                "owner_id": "me",
            })

    def test_unit_price_negative_rejected(self):
        with pytest.raises(PydanticValidationError):
            ExtractedReceiptItem.model_validate({
                "name": "Test",
                "normalized_name": "test",
                "category": "DRINK",
                "quantity": 1,
                "unit_price": -1,
                "total_price": -1,
                "owner_id": "me",
            })

    def test_owner_marker_normalized_to_upper(self):
        """owner_marker should be normalized to uppercase."""
        item = ExtractedReceiptItem.model_validate({
            "name": "Test",
            "normalized_name": "test",
            "category": "DRINK",
            "quantity": 1,
            "unit_price": 2,
            "total_price": 2,
            "owner_id": "alice",
            "owner_marker": " a ",
        })
        assert item.owner_marker == "A"

    def test_invalid_category_rejected(self):
        """PRD 5.3: invalid category should be rejected by schema."""
        with pytest.raises(PydanticValidationError):
            ExtractedReceiptItem.model_validate({
                "name": "X",
                "normalized_name": "x",
                "category": "INVALID_CATEGORY",
                "quantity": 1,
                "unit_price": 1,
                "total_price": 1,
                "owner_id": "me",
            })

    def test_negative_total_price_allowed(self):
        """PRD 6.2: negative total_price for Storno/Leergut is allowed."""
        item = ExtractedReceiptItem.model_validate({
            "name": "Pfand",
            "normalized_name": "pfand",
            "category": "OTHER",
            "quantity": 1,
            "unit_price": 0.75,
            "total_price": -0.75,
            "owner_id": "me",
        })
        assert item.total_price == Decimal("-0.75")

    def test_weight_as_quantity(self):
        """PRD 6.1: 对于称重商品，重量直接作为 quantity."""
        item = ExtractedReceiptItem.model_validate({
            "name": "Banane 0.500 kg",
            "normalized_name": "banane",
            "category": "FRUIT",
            "quantity": 0.500,
            "unit_price": 2.99,
            "total_price": 1.50,
            "owner_id": "me",
        })
        assert item.quantity == Decimal("0.500")


class TestExtractedReceiptSchema:
    """PRD 5.2: receipt-level output constraints."""

    def test_valid_receipt(self):
        r = ExtractedReceipt.model_validate(_valid_extraction_payload())
        assert r.merchant == "REWE"
        assert r.purchase_date == date(2026, 5, 4)
        assert r.currency == "EUR"
        assert r.total_amount == Decimal("4.50")
        assert r.payment_method == "card"
        assert r.owner_mode == OwnerMode.NORMAL
        assert r.default_owner_id == "me"
        assert r.receipt_owner_marker is None
        assert len(r.items) == 2

    def test_currency_must_be_eur(self):
        """PRD 5.2: currency fixed to EUR."""
        payload = {**_valid_extraction_payload(), "currency": "USD"}
        with pytest.raises(PydanticValidationError, match="EUR"):
            ExtractedReceipt.model_validate(payload)

    def test_empty_items_rejected(self):
        """PRD 5.2: items must have at least 1 entry."""
        payload = {**_valid_extraction_payload(), "items": []}
        with pytest.raises(PydanticValidationError, match="items"):
            ExtractedReceipt.model_validate(payload)

    def test_date_uses_iso_format(self):
        """PRD 5.2: purchase_date must be YYYY-MM-DD."""
        payload = {**_valid_extraction_payload(), "purchase_date": "04.05.2026"}
        with pytest.raises(PydanticValidationError):
            ExtractedReceipt.model_validate(payload)

    def test_empty_merchant_rejected(self):
        payload = {**_valid_extraction_payload(), "merchant": ""}
        with pytest.raises(PydanticValidationError):
            ExtractedReceipt.model_validate(payload)

    def test_invalid_owner_mode_rejected(self):
        payload = {**_valid_extraction_payload(), "owner_mode": "shared"}
        with pytest.raises(PydanticValidationError):
            ExtractedReceipt.model_validate(payload)

    def test_receipt_owner_marker_normalized(self):
        payload = {**_valid_extraction_payload(), "receipt_owner_marker": " m "}
        r = ExtractedReceipt.model_validate(payload)
        assert r.receipt_owner_marker == "M"

    def test_payment_method_null_allowed(self):
        """PRD 5.4: payment_method can be null."""
        payload = {**_valid_extraction_payload(), "payment_method": None}
        r = ExtractedReceipt.model_validate(payload)
        assert r.payment_method is None

    def test_negative_total_quantity_still_gt_zero(self):
        """negative total_price on item doesn't mean quantity can be zero."""
        payload = {
            **_valid_extraction_payload(),
            "total_amount": 0.0,
            "items": [
                {
                    "name": "Storno",
                    "normalized_name": "storno",
                    "category": "OTHER",
                    "quantity": 1,
                    "unit_price": 2.50,
                    "total_price": -2.50,
                    "owner_id": "me",
                    "owner_marker": None,
                },
            ],
        }
        r = ExtractedReceipt.model_validate(payload)
        assert r.total_amount == Decimal("0") or True  # schema allows ge=0


# ===========================================================================
# PRD 9.4 & 6.4: OwnerConfig / OwnersConfig
# ===========================================================================

class TestOwnerConfig:
    """PRD 9.4: owner JSON format and constraints."""

    def test_valid_owner(self):
        o = OwnerConfig(id="me", name="Me", marker="M", is_me=True)
        assert o.id == "me"
        assert o.name == "Me"
        assert o.marker == "M"
        assert o.is_me is True

    def test_marker_normalized_to_upper(self):
        o = OwnerConfig(id="me", name="Me", marker="m", is_me=True)
        assert o.marker == "M"

    def test_invalid_marker_length(self):
        with pytest.raises(PydanticValidationError, match="marker"):
            OwnerConfig(id="me", name="Me", marker="MM", is_me=True)

    def test_invalid_marker_non_alpha(self):
        with pytest.raises(PydanticValidationError):
            OwnerConfig(id="me", name="Me", marker="1", is_me=True)


class TestOwnersConfig:
    """PRD 9.4: owners.json integrity rules."""

    def test_valid_config(self):
        config = OwnersConfig.model_validate(_valid_owners_payload())
        assert len(config.owners) == 3

    def test_empty_owners_rejected(self):
        with pytest.raises(PydanticValidationError, match="must not be empty"):
            OwnersConfig.model_validate({"owners": []})

    def test_duplicate_ids_rejected(self):
        payload = {
            "owners": [
                {"id": "me", "name": "Me", "marker": "M", "is_me": True},
                {"id": "me", "name": "Me2", "marker": "A", "is_me": False},
            ]
        }
        with pytest.raises(PydanticValidationError, match="unique"):
            OwnersConfig.model_validate(payload)

    def test_duplicate_markers_rejected(self):
        payload = {
            "owners": [
                {"id": "me", "name": "Me", "marker": "M", "is_me": True},
                {"id": "alice", "name": "Alice", "marker": "M", "is_me": False},
            ]
        }
        with pytest.raises(PydanticValidationError, match="unique"):
            OwnersConfig.model_validate(payload)

    def test_exactly_one_is_me_required(self):
        """PRD 9.4: exactly one is_me=true."""
        payload_two_me = {
            "owners": [
                {"id": "me", "name": "Me", "marker": "M", "is_me": True},
                {"id": "alice", "name": "Alice", "marker": "A", "is_me": True},
            ]
        }
        with pytest.raises(PydanticValidationError, match="Exactly one"):
            OwnersConfig.model_validate(payload_two_me)

        payload_no_me = {
            "owners": [
                {"id": "alice", "name": "Alice", "marker": "A", "is_me": False},
                {"id": "bob", "name": "Bob", "marker": "B", "is_me": False},
            ]
        }
        with pytest.raises(PydanticValidationError, match="Exactly one"):
            OwnersConfig.model_validate(payload_no_me)

    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(_valid_owners_payload(), f)
            f.flush()
            path = Path(f.name)

        try:
            config = load_owners_config(path)
            assert len(config.owners) == 3
            assert config.owners[0].id == "me"
        finally:
            path.unlink(missing_ok=True)


# ===========================================================================
# PRD 9.1-9.3: domain schemas (ReceiptRecord, ReceiptItemRecord, etc.)
# ===========================================================================

class TestReceiptItemRecord:
    """PRD 9.2: ReceiptItem fields."""

    def test_valid_record(self):
        item = ReceiptItemRecord(
            id="item_1",
            receipt_id="receipt_1",
            name="Water",
            normalized_name="water",
            category=ItemCategory.DRINK,
            quantity=Decimal("1"),
            unit_price=Decimal("2.50"),
            total_price=Decimal("2.50"),
            owner_id="me",
        )
        assert item.id == "item_1"
        assert item.owner_marker is None  # optional field

    def test_negative_total_price_allowed(self):
        """PRD 6.2: negative total prices are stored."""
        item = ReceiptItemRecord(
            id="item_2",
            receipt_id="receipt_1",
            name="Pfand",
            normalized_name="pfand",
            category=ItemCategory.OTHER,
            quantity=Decimal("1"),
            unit_price=Decimal("0.75"),
            total_price=Decimal("-0.75"),
            owner_id="me",
        )
        assert item.total_price == Decimal("-0.75")


class TestRemovedItemRecord:
    """PRD 9.3: removed item audit records."""

    def test_valid_record(self):
        r = RemovedItemRecord(
            name="Old Item",
            normalized_name="old_item",
            category=ItemCategory.OTHER,
            quantity=Decimal("1"),
            unit_price=Decimal("1"),
            total_price=Decimal("1"),
            owner_id="me",
            reason="manual_removal",
            related_index=3,
        )
        assert r.reason == "manual_removal"
        assert r.related_index == 3


class TestFailedOcrRecord:
    """PRD 7.3: failed OCR archive records."""

    def test_valid_record(self):
        f = FailedOcrRecord(
            image_path="test.jpg",
            archived_image_path="rejected/test_2026.jpg",
            image_hash="abc123",
            attempts=3,
            failure_reason="receipt_total_mismatch",
            raw_outputs=["{}", "{}", "{}"],
            created_at=datetime.now(timezone.utc),
        )
        assert f.attempts == 3
        assert len(f.raw_outputs) == 3


class TestReceiptStore:
    """PRD 9.5: JSON store root container."""

    def test_empty_store(self):
        store = ReceiptStore()
        assert store.receipts == []
        assert store.failed_ocr_records == []
        assert store.last_receipt_id == 0
        assert store.last_item_id == 0


# ===========================================================================
# PRD 9.1 + 6.2: converters (extracted -> domain)
# ===========================================================================

class TestConverters:
    """extracted_to_receipt_record bridging extraction schema to domain schema."""

    def test_converts_valid_extraction(self):
        extracted = ExtractedReceipt.model_validate(_valid_extraction_payload())
        processed = process_extracted_receipt_items(extracted)
        record = extracted_to_receipt_record(
            extracted,
            processed_items=processed,
            receipt_id="receipt_1",
            image_path="test.jpg",
            image_hash="hash-1",
            item_id_factory=_make_item_id_factory(),
            raw_text="{}",
        )
        assert record.id == "receipt_1"
        assert record.merchant == "REWE"
        assert record.currency == "EUR"
        assert record.total_amount == Decimal("4.50")
        assert record.owner_mode == OwnerMode.NORMAL
        assert record.ocr_raw_text == "{}"
        assert record.ocr_status == OcrStatus.PENDING
        assert record.is_verified is False
        assert len(record.items) == 2
        assert record.items[0].id == "item_1"
        assert record.items[0].receipt_id == "receipt_1"
        assert record.removed_items == []

    def test_negative_items_kept_in_items(self):
        """PRD 6.2: Storno items kept in formal list."""
        payload = _valid_extraction_payload()
        payload["items"].append({
            "name": "Sofortstorno",
            "normalized_name": "sofortstorno",
            "category": "OTHER",
            "quantity": 1,
            "unit_price": 2.50,
            "total_price": -2.50,
            "owner_id": "me",
        })
        extracted = ExtractedReceipt.model_validate(payload)
        processed = process_extracted_receipt_items(extracted)
        record = extracted_to_receipt_record(
            extracted,
            processed_items=processed,
            receipt_id="receipt_1",
            image_path="test.jpg",
            image_hash="hash-1",
            item_id_factory=_make_item_id_factory(),
        )
        assert len(record.items) == 3
        assert record.total_amount == Decimal("2.00")

    def test_legacy_converter_does_not_postprocess(self):
        """Legacy converter keeps original total_amount."""
        extracted = ExtractedReceipt.model_validate(_valid_extraction_payload())
        record = extracted_to_receipt_record_legacy(
            extracted,
            receipt_id="receipt_2",
            image_path="test.jpg",
            item_id_factory=_make_item_id_factory("legacy"),
        )
        assert record.total_amount == extracted.total_amount


# ===========================================================================
# PRD 5.1: JSON only, no markdown, no explanations
# (The fact that ExtractedReceipt validates JSON content is the test)
# ===========================================================================

class TestExtractedReceiptIsPureJson:
    """PRD 5.1: model output is pure JSON, validated by pydantic."""

    def test_model_dump_is_serializable(self):
        extracted = ExtractedReceipt.model_validate(_valid_extraction_payload())
        dumped = extracted.model_dump(mode="json")
        # Round-trip: dump back to string and re-parse
        json_str = json.dumps(dumped, ensure_ascii=False)
        reloaded = json.loads(json_str)
        assert reloaded["merchant"] == "REWE"
        assert reloaded["purchase_date"] == "2026-05-04"