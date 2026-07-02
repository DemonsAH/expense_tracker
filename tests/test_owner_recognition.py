"""Unit tests for owner recognition logic (PRD 6.4, 6.5).

Covers:
  - 6.4: normal / receipt_owner / item_owner 三种模式
  - 6.4: 行级标记优先, 整单回退, Me 兜底
  - 6.5: 手写标记规范 @M, @A, @B, 归属人识别开关
  - ocr_parser marker detection & owner assignment
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from expense_tracker.schemas.enums import OwnerMode
from expense_tracker.schemas.owners import OwnersConfig, OwnerConfig
from expense_tracker.ocr_parser import (
    _detect_owner_mode,
    _build_items,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_marker_map(owners: OwnersConfig) -> dict[str, str]:
    return {o.marker.upper(): o.id for o in owners.owners}


def _make_owner_ids(owners: OwnersConfig) -> set[str]:
    return {o.id for o in owners.owners}


def _find_me_id(owners: OwnersConfig) -> str:
    return next(o.id for o in owners.owners if o.is_me)


def _standard_owners() -> OwnersConfig:
    return OwnersConfig(owners=[
        OwnerConfig(id="me", name="Me", marker="M", is_me=True),
        OwnerConfig(id="alice", name="Alice", marker="A", is_me=False),
        OwnerConfig(id="bob", name="Bob", marker="B", is_me=False),
    ])


# ===========================================================================
# PRD 6.4: _detect_owner_mode — mode detection from text and items
# ===========================================================================

class TestDetectOwnerMode:
    """Tests for _detect_owner_mode: determines owner_mode from @markers and item markers."""

    def test_normal_mode_when_no_markers(self):
        """PRD 6.4 normal: no markers → NORMAL, default Me."""
        owners = _standard_owners()
        mode, default_id, receipt_marker = _detect_owner_mode(
            text_section="REWE\nAachen\nDatum: 04.05.2026\n",
            raw_items=[
                {"name": "Water", "price": Decimal("2.50"), "marker": None},
                {"name": "Apple", "price": Decimal("2.00"), "marker": None},
            ],
            marker_to_id=_make_marker_map(owners),
            me_id=_find_me_id(owners),
            owner_ids=_make_owner_ids(owners),
        )
        assert mode == OwnerMode.NORMAL
        assert default_id == "me"
        assert receipt_marker is None

    def test_receipt_owner_mode_from_at_marker(self):
        """PRD 6.4 receipt_owner: @A marker → RECEIPT_OWNER, default to Alice."""
        owners = _standard_owners()
        mode, default_id, receipt_marker = _detect_owner_mode(
            text_section="@A\nREWE\nAachen\nDatum: 04.05.2026\n",
            raw_items=[
                {"name": "Water", "price": Decimal("2.50"), "marker": None},
            ],
            marker_to_id=_make_marker_map(owners),
            me_id=_find_me_id(owners),
            owner_ids=_make_owner_ids(owners),
        )
        assert mode == OwnerMode.RECEIPT_OWNER
        assert default_id == "alice"
        assert receipt_marker == "A"

    def test_receipt_owner_marker_at_case_insensitive(self):
        """@a should be detected as @A."""
        owners = _standard_owners()
        mode, default_id, receipt_marker = _detect_owner_mode(
            text_section="@b\nREWE\n",
            raw_items=[],
            marker_to_id=_make_marker_map(owners),
            me_id=_find_me_id(owners),
            owner_ids=_make_owner_ids(owners),
        )
        assert mode == OwnerMode.RECEIPT_OWNER
        assert default_id == "bob"
        assert receipt_marker == "B"

    def test_item_owner_mode_when_items_have_different_markers(self):
        """PRD 6.4 item_owner: items have individual markers → ITEM_OWNER."""
        owners = _standard_owners()
        mode, default_id, receipt_marker = _detect_owner_mode(
            text_section="REWE\nAachen\nDatum: 04.05.2026\n",
            raw_items=[
                {"name": "Water", "price": Decimal("2.50"), "marker": "A"},
                {"name": "Apple", "price": Decimal("2.00"), "marker": "B"},
            ],
            marker_to_id=_make_marker_map(owners),
            me_id=_find_me_id(owners),
            owner_ids=_make_owner_ids(owners),
        )
        assert mode == OwnerMode.ITEM_OWNER
        assert default_id == "me"

    def test_item_owner_mode_overrides_normal(self):
        """Even without @X, individual item markers upgrade to ITEM_OWNER."""
        owners = _standard_owners()
        mode, default_id, receipt_marker = _detect_owner_mode(
            text_section="REWE\nAachen\nDatum: 04.05.2026\n",
            raw_items=[
                {"name": "Water", "price": Decimal("2.50"), "marker": "A"},
            ],
            marker_to_id=_make_marker_map(owners),
            me_id=_find_me_id(owners),
            owner_ids=_make_owner_ids(owners),
        )
        assert mode == OwnerMode.ITEM_OWNER

    def test_receipt_owner_with_item_markers_stays_receipt_owner(self):
        """When both @X and item markers exist, mode stays RECEIPT_OWNER."""
        owners = _standard_owners()
        mode, default_id, receipt_marker = _detect_owner_mode(
            text_section="@A\nREWE\nAachen\nDatum: 04.05.2026\n",
            raw_items=[
                {"name": "Water", "price": Decimal("2.50"), "marker": "B"},
            ],
            marker_to_id=_make_marker_map(owners),
            me_id=_find_me_id(owners),
            owner_ids=_make_owner_ids(owners),
        )
        # Already RECEIPT_OWNER from @A, won't downgrade
        assert mode == OwnerMode.RECEIPT_OWNER
        assert default_id == "alice"


# ===========================================================================
# PRD 6.4: _build_items — owner assignment with fallback cascade
# ===========================================================================

class TestBuildItems:
    """Tests for _build_items: item-level marker → receipt default → Me fallback."""

    def test_item_marker_assigns_owner(self):
        """PRD 6.4 item_owner: 行级标记优先."""
        owners = _standard_owners()
        raw = [
            {"name": "Water", "price": Decimal("2.50"), "marker": "A"},
            {"name": "Bread", "price": Decimal("3.00"), "marker": "B"},
        ]
        items = _build_items(raw, _make_marker_map(owners), "me", "me")
        assert items[0].owner_id == "alice"
        assert items[0].owner_marker == "A"
        assert items[1].owner_id == "bob"
        assert items[1].owner_marker == "B"

    def test_unmarked_item_falls_back_to_default(self):
        """PRD 6.4: 未标记商品回退到整单归属."""
        owners = _standard_owners()
        raw = [
            {"name": "Water", "price": Decimal("2.50"), "marker": None},
            {"name": "Bread", "price": Decimal("3.00"), "marker": "A"},
        ]
        items = _build_items(raw, _make_marker_map(owners), "bob", "me")
        assert items[0].owner_id == "bob"  # falls back to default_owner_id
        assert items[0].owner_marker is None
        assert items[1].owner_id == "alice"  # marked

    def test_default_falls_back_to_me(self):
        """PRD 6.4: 若整单也未标记，则回退到 Me."""
        owners = _standard_owners()
        raw = [
            {"name": "Water", "price": Decimal("2.50"), "marker": None},
        ]
        items = _build_items(raw, _make_marker_map(owners), "me", "me")
        assert items[0].owner_id == "me"

    def test_unknown_marker_uses_default(self):
        """If marker letter not in owners, fall back to default_owner_id."""
        owners = _standard_owners()
        raw = [
            {"name": "Water", "price": Decimal("2.50"), "marker": "Z"},
        ]
        items = _build_items(raw, _make_marker_map(owners), "alice", "me")
        assert items[0].owner_id == "alice"  # falls back to default, not me
        assert items[0].owner_marker == "Z"

    def test_weight_quantity_is_parsed(self):
        """PRD 6.1: 称重商品 quantity=weight, unit_price=total_price/weight."""
        owners = _standard_owners()
        raw = [
            {"name": "Banane 0.500 kg", "price": Decimal("1.50"), "marker": None},
        ]
        items = _build_items(raw, _make_marker_map(owners), "me", "me")
        assert items[0].quantity == Decimal("0.500")
        assert items[0].unit_price == Decimal("3.00")  # 1.50 / 0.500
        assert items[0].total_price == Decimal("1.50")

    def test_quantity_at_least_epsilon(self):
        """Quantity should never be zero, minimum 0.001."""
        owners = _standard_owners()
        raw = [
            {"name": "Empty weight item 0.000 kg", "price": Decimal("1.00"), "marker": None},
        ]
        items = _build_items(raw, _make_marker_map(owners), "me", "me")
        assert items[0].quantity >= Decimal("0.001")


# ===========================================================================
# PRD 6.4: owner_id validation in pipelines (reused from existing tests)
# ===========================================================================

class TestOwnerIdValidation:
    """Verify pipeline validation catches invalid owner_ids."""

    def test_valid_owner_ids_pass(self):
        from expense_tracker.pipelines.receipt_validation import (
            validate_extracted_receipt_business_rules,
        )
        from expense_tracker.schemas.extraction import ExtractedReceipt

        extracted = ExtractedReceipt.model_validate({
            "merchant": "REWE",
            "purchase_date": "2026-05-04",
            "currency": "EUR",
            "total_amount": 5.00,
            "payment_method": "card",
            "owner_mode": "normal",
            "default_owner_id": "me",
            "receipt_owner_marker": None,
            "items": [
                {
                    "name": "Water", "normalized_name": "water",
                    "category": "DRINK", "quantity": 2,
                    "unit_price": 2.50, "total_price": 5.00,
                    "owner_id": "me",
                },
            ],
        })
        result = validate_extracted_receipt_business_rules(
            extracted, owners=_standard_owners(),
        )
        assert result.is_valid

    def test_invalid_default_owner_id_rejected(self):
        from expense_tracker.pipelines.receipt_validation import (
            validate_extracted_receipt_business_rules,
        )
        from expense_tracker.schemas.extraction import ExtractedReceipt

        extracted = ExtractedReceipt.model_validate({
            "merchant": "REWE",
            "purchase_date": "2026-05-04",
            "currency": "EUR",
            "total_amount": 2.50,
            "payment_method": "card",
            "owner_mode": "normal",
            "default_owner_id": "ghost",
            "receipt_owner_marker": None,
            "items": [
                {
                    "name": "Water", "normalized_name": "water",
                    "category": "DRINK", "quantity": 1,
                    "unit_price": 2.50, "total_price": 2.50,
                    "owner_id": "ghost",
                },
            ],
        })
        result = validate_extracted_receipt_business_rules(
            extracted, owners=_standard_owners(),
        )
        assert not result.is_valid
        assert "default_owner_id_not_found" in result.issues

    def test_invalid_item_owner_id_rejected(self):
        from expense_tracker.pipelines.receipt_validation import (
            validate_extracted_receipt_business_rules,
        )
        from expense_tracker.schemas.extraction import ExtractedReceipt

        extracted = ExtractedReceipt.model_validate({
            "merchant": "REWE",
            "purchase_date": "2026-05-04",
            "currency": "EUR",
            "total_amount": 2.50,
            "payment_method": "card",
            "owner_mode": "item_owner",
            "default_owner_id": "me",
            "receipt_owner_marker": None,
            "items": [
                {
                    "name": "Water", "normalized_name": "water",
                    "category": "DRINK", "quantity": 1,
                    "unit_price": 2.50, "total_price": 2.50,
                    "owner_id": "ghost",
                    "owner_marker": "G",
                },
            ],
        })
        result = validate_extracted_receipt_business_rules(
            extracted, owners=_standard_owners(),
        )
        assert not result.is_valid
        assert any("owner_id_not_found" in issue for issue in result.issues)


# ===========================================================================
# PRD 6.5: 手写标记规范 — marker normalization across boundaries
# ===========================================================================

class TestMarkerNormalization:
    """PRD 6.5: @M, @A, @B on receipt; M, A, B on items. All normalized to uppercase."""

    def test_extracted_receipt_item_normalizes_marker(self):
        from expense_tracker.schemas.extraction import ExtractedReceiptItem
        item = ExtractedReceiptItem.model_validate({
            "name": "Water", "normalized_name": "water",
            "category": "DRINK", "quantity": 1,
            "unit_price": 2.50, "total_price": 2.50,
            "owner_id": "me",
            "owner_marker": " a ",  # lowercase with spaces
        })
        assert item.owner_marker == "A"

    def test_extracted_receipt_normalizes_receipt_marker(self):
        from expense_tracker.schemas.extraction import ExtractedReceipt
        r = ExtractedReceipt.model_validate({
            "merchant": "REWE",
            "purchase_date": "2026-05-04",
            "currency": "EUR",
            "total_amount": 2.50,
            "owner_mode": "receipt_owner",
            "default_owner_id": "me",
            "receipt_owner_marker": " b ",
            "items": [
                {
                    "name": "Water", "normalized_name": "water",
                    "category": "DRINK", "quantity": 1,
                    "unit_price": 2.50, "total_price": 2.50,
                    "owner_id": "me",
                },
            ],
        })
        assert r.receipt_owner_marker == "B"

    def test_owner_config_normalizes_marker(self):
        from expense_tracker.schemas.owners import OwnerConfig
        o = OwnerConfig(id="me", name="Me", marker="m", is_me=True)
        assert o.marker == "M"

    def test_gui_save_normalizes_marker(self):
        """GUI services normalize owner_marker on save."""
        from expense_tracker.gui.services import _build_receipt_item_record

        record = _build_receipt_item_record(
            receipt_id="r1",
            item_data={
                "name": "Water", "normalized_name": "water",
                "category": "DRINK", "quantity": "1",
                "unit_price": "2.50", "total_price": "2.50",
                "owner_id": "me", "owner_marker": " a ",
            },
            owner_ids={"me", "alice"},
            existing_id="item_1",
        )
        assert record.owner_marker == "A"


# ===========================================================================
# PRD 6.5: owner recognition toggle (OFF → NORMAL mode, default Me)
# ===========================================================================

class TestOwnerRecognitionToggle:
    """PRD 6.5: 可开启/关闭归属人识别; 关闭时统一进入 normal 模式."""

    def test_toggle_off_forces_normal_mode(self):
        """When owner recognition is OFF, always return NORMAL with Me."""
        owners = _standard_owners()
        # Simulate the toggle by ignoring @markers in text and item markers
        # This is what should happen when config["owner_recognition_enabled"] == False
        mode, default_id, receipt_marker = _detect_owner_mode(
            text_section="@A\nREWE\nAachen\nDatum: 04.05.2026\n",
            raw_items=[
                {"name": "Water", "price": Decimal("2.50"), "marker": "A"},
            ],
            marker_to_id={},  # empty marker map simulates toggle OFF
            me_id=_find_me_id(owners),
            owner_ids=_make_owner_ids(owners),
        )
        # No marker map → @A and item markers are ignored
        assert mode == OwnerMode.NORMAL
        assert default_id == _find_me_id(owners)

    def test_toggle_on_allows_all_modes(self):
        """When owner recognition is ON, markers are respected."""
        owners = _standard_owners()
        mode, default_id, receipt_marker = _detect_owner_mode(
            text_section="@B\nREWE\nAachen\nDatum: 04.05.2026\n",
            raw_items=[
                {"name": "Water", "price": Decimal("2.50"), "marker": "A"},
            ],
            marker_to_id=_make_marker_map(owners),  # full map
            me_id=_find_me_id(owners),
            owner_ids=_make_owner_ids(owners),
        )
        assert mode == OwnerMode.RECEIPT_OWNER
        assert default_id == "bob"