"""Phase 2 business rule edge-case tests (PRD 6.1, 6.2, 6.3, 9.3).

Covers:
  - 6.1: 称重商品, weight as quantity
  - 6.2: Storno/Leergut 取消项全部保留, 负数项自然抵消
  - 6.3: 正式数据 vs 排行数据规则
  - 9.3: removed_items 审计记录
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode
from expense_tracker.schemas.domain import (
    ReceiptItemRecord,
    ReceiptRecord,
    RemovedItemRecord,
)
from expense_tracker.pipelines.receipt_validation import (
    is_cancellation_item,
    is_leergut_item,
)
from expense_tracker.reports.monthly import _is_price_ranking_item
from expense_tracker.schemas.converters import extracted_to_receipt_record
from expense_tracker.pipelines.receipt_postprocess import process_extracted_receipt_items
from expense_tracker.schemas.extraction import ExtractedReceipt


# ===========================================================================
# PRD 6.1: weight-based goods
# ===========================================================================

class TestWeightHandling:
    """PRD 6.1: 称重商品 weight as quantity, unit_price=total_price/quantity."""

    def test_build_item_from_weight(self):
        from expense_tracker.ocr_parser import _build_items
        raw = [
            {"name": "Banane 0.500 kg", "price": Decimal("1.50"), "marker": None},
            {"name": "Apfel 1.200 kg", "price": Decimal("3.60"), "marker": None},
        ]
        marker_map = {"M": "me"}
        items = _build_items(raw, marker_map, "me", "me")
        assert items[0].quantity == Decimal("0.500")
        assert items[0].unit_price == Decimal("3.00")
        assert items[0].total_price == Decimal("1.50")
        assert items[1].quantity == Decimal("1.200")
        assert items[1].unit_price == Decimal("3.00")

    def test_weight_item_quantity_not_zero(self):
        """Weight parsing should never produce zero quantity."""
        from expense_tracker.ocr_parser import _build_items
        raw = [
            {"name": "Item 0.000 kg", "price": Decimal("5.00"), "marker": None},
        ]
        marker_map = {"M": "me"}
        items = _build_items(raw, marker_map, "me", "me")
        assert items[0].quantity >= Decimal("0.001")


# ===========================================================================
# PRD 6.2: all items kept, negative items offset total
# ===========================================================================

class TestCancellationKeptInFormalData:
    """PRD 6.2: 取消项和被取消原商品前后两条记录都保留, 负数自然抵消."""

    def test_storno_and_original_both_kept(self):
        extracted = ExtractedReceipt.model_validate({
            "merchant": "REWE",
            "purchase_date": "2026-05-04",
            "currency": "EUR",
            "total_amount": 0.00,
            "payment_method": "card",
            "owner_mode": "normal",
            "default_owner_id": "me",
            "items": [
                {
                    "name": "Wurst", "normalized_name": "wurst",
                    "category": "MEAT", "quantity": 1,
                    "unit_price": 3.00, "total_price": 3.00,
                    "owner_id": "me",
                },
                {
                    "name": "Sofortstorno Wurst", "normalized_name": "sofortstorno_wurst",
                    "category": "MEAT", "quantity": 1,
                    "unit_price": 3.00, "total_price": -3.00,
                    "owner_id": "me",
                },
            ],
        })
        processed = process_extracted_receipt_items(extracted)
        assert len(processed.formal_items) == 2
        assert processed.formal_items[0].total_price == Decimal("3.00")
        assert processed.formal_items[1].total_price == Decimal("-3.00")
        assert processed.removed_items == []

    def test_net_total_is_zero_after_cancellation(self):
        """两个商品抵消后 total_amount=0."""
        extracted = ExtractedReceipt.model_validate({
            "merchant": "REWE",
            "purchase_date": "2026-05-04",
            "currency": "EUR",
            "total_amount": 0.00,
            "payment_method": "card",
            "owner_mode": "normal",
            "default_owner_id": "me",
            "items": [
                {
                    "name": "Wurst", "normalized_name": "wurst",
                    "category": "MEAT", "quantity": 1,
                    "unit_price": 3.00, "total_price": 3.00,
                    "owner_id": "me",
                },
                {
                    "name": "Storno Wurst", "normalized_name": "storno_wurst",
                    "category": "MEAT", "quantity": 1,
                    "unit_price": 3.00, "total_price": -3.00,
                    "owner_id": "me",
                },
            ],
        })
        item_total = sum((i.total_price for i in extracted.items), start=Decimal("0"))
        assert item_total == Decimal("0")

    def test_leergut_reduces_net_total(self):
        """PRD 6.2: Leergut/Pfand 负数项参与月度支出总和自然抵消."""
        extracted = ExtractedReceipt.model_validate({
            "merchant": "REWE",
            "purchase_date": "2026-05-04",
            "currency": "EUR",
            "total_amount": 1.75,
            "payment_method": "card",
            "owner_mode": "normal",
            "default_owner_id": "me",
            "items": [
                {
                    "name": "Water", "normalized_name": "water",
                    "category": "DRINK", "quantity": 1,
                    "unit_price": 2.50, "total_price": 2.50,
                    "owner_id": "me",
                },
                {
                    "name": "Pfand", "normalized_name": "pfand",
                    "category": "OTHER", "quantity": 1,
                    "unit_price": 0.75, "total_price": -0.75,
                    "owner_id": "me",
                },
            ],
        })
        processed = process_extracted_receipt_items(extracted)
        net = sum((i.total_price for i in processed.formal_items), start=Decimal("0"))
        assert net == Decimal("1.75")

    def test_mixed_cancellation_and_leergut(self):
        """Test combined Storno + Leergut items."""
        extracted = ExtractedReceipt.model_validate({
            "merchant": "REWE",
            "purchase_date": "2026-05-04",
            "currency": "EUR",
            "total_amount": 0.75,
            "payment_method": "card",
            "owner_mode": "normal",
            "default_owner_id": "me",
            "items": [
                {
                    "name": "Water", "normalized_name": "water",
                    "category": "DRINK", "quantity": 2,
                    "unit_price": 2.50, "total_price": 5.00,
                    "owner_id": "me",
                },
                {
                    "name": "Storno Water", "normalized_name": "storno_water",
                    "category": "DRINK", "quantity": 1,
                    "unit_price": 2.50, "total_price": -2.50,
                    "owner_id": "me",
                },
                {
                    "name": "Pfand", "normalized_name": "pfand",
                    "category": "OTHER", "quantity": 2,
                    "unit_price": 0.75, "total_price": -1.50,
                    "owner_id": "me",
                },
                {
                    "name": "Banane", "normalized_name": "banane",
                    "category": "FRUIT", "quantity": 1,
                    "unit_price": 0.75, "total_price": 0.75,
                    "owner_id": "me",
                },
            ],
        })
        processed = process_extracted_receipt_items(extracted)
        assert len(processed.formal_items) == 4
        net = sum((i.total_price for i in processed.formal_items), start=Decimal("0"))
        assert net == Decimal("1.75")


# ===========================================================================
# PRD 6.3: price ranking exclusion rules
# ===========================================================================

class TestPriceRankingRules:
    """PRD 6.3: DINING, Leergut/Pfand, and negative items excluded from price ranking."""

    def _make_item(self, category: ItemCategory, total_price: Decimal, name: str = "test", normalized: str = "test"):
        return ReceiptItemRecord(
            id="i1", receipt_id="r1",
            name=name, normalized_name=normalized,
            category=category, quantity=Decimal("1"),
            unit_price=abs(total_price), total_price=total_price,
            owner_id="me",
        )

    def test_dining_excluded(self):
        item = self._make_item(ItemCategory.DINING, Decimal("15.00"))
        assert not _is_price_ranking_item(item)

    def test_other_categories_included(self):
        for cat in ItemCategory:
            if cat == ItemCategory.DINING:
                continue
            item = self._make_item(cat, Decimal("5.00"))
            assert _is_price_ranking_item(item), f"{cat} should be included"

    def test_negative_item_excluded(self):
        """PRD 6.3: 所有负数项不进入价格排行."""
        item = self._make_item(ItemCategory.SNACKS, Decimal("-2.50"))
        assert not _is_price_ranking_item(item)

    def test_leergut_excluded(self):
        """PRD 6.3: Leergut/Pfand 不进入价格排行."""
        item = self._make_item(ItemCategory.OTHER, Decimal("1.00"), "Pfand", "pfand")
        assert not _is_price_ranking_item(item)

        item2 = self._make_item(ItemCategory.OTHER, Decimal("0.50"), "Leergut", "leergut")
        assert not _is_price_ranking_item(item2)

        item3 = self._make_item(ItemCategory.OTHER, Decimal("0.25"), "Flaschenpfand", "flaschenpfand")
        assert not _is_price_ranking_item(item3)

    def test_storno_excluded(self):
        """PRD 6.3: 取消项不进入价格排行 (via negative price check)."""
        item = self._make_item(ItemCategory.MEAT, Decimal("-3.00"), "Storno", "storno_wurst")
        assert not _is_price_ranking_item(item)

    def test_normal_snacks_included(self):
        item = self._make_item(ItemCategory.SNACKS, Decimal("2.50"), "Chips", "chips")
        assert _is_price_ranking_item(item)


# ===========================================================================
# PRD 9.3: audit record preservation (removed_items)
# ===========================================================================

class TestAuditRecords:
    """PRD 9.3: removed_items 审计记录完整可查."""

    def test_removed_item_record_has_all_fields(self):
        r = RemovedItemRecord(
            name="Old Item",
            normalized_name="old_item",
            category=ItemCategory.OTHER,
            quantity=Decimal("1"),
            unit_price=Decimal("2.50"),
            total_price=Decimal("2.50"),
            owner_id="me",
            owner_marker="M",
            reason="manual_removal",
            related_index=3,
        )
        assert r.name == "Old Item"
        assert r.reason == "manual_removal"
        assert r.related_index == 3
        assert r.owner_marker == "M"

    def test_empty_removed_items_defaults_to_empty_list(self):
        store_receipt = ReceiptRecord(
            id="r1",
            merchant="REWE",
            purchase_date=date.today(),
            currency="EUR",
            total_amount=Decimal("10.00"),
            default_owner_id="me",
            owner_mode=OwnerMode.NORMAL,
            image_path="img.jpg",
            image_hash="hash",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            items=[],
        )
        assert store_receipt.removed_items == []

    def test_removed_items_survive_roundtrip(self):
        """Ensure removed_items are preserved in the converter and record."""
        extracted = ExtractedReceipt.model_validate({
            "merchant": "REWE",
            "purchase_date": "2026-05-04",
            "currency": "EUR",
            "total_amount": 5.00,
            "payment_method": None,
            "owner_mode": "normal",
            "default_owner_id": "me",
            "items": [
                {
                    "name": "Water", "normalized_name": "water",
                    "category": "DRINK", "quantity": 2,
                    "unit_price": 2.50, "total_price": 5.00,
                    "owner_id": "me",
                },
            ],
        })
        processed = process_extracted_receipt_items(extracted)

        counter = {"value": 0}
        def id_factory():
            counter["value"] += 1
            return f"item_{counter['value']}"

        record = extracted_to_receipt_record(
            extracted,
            processed_items=processed,
            receipt_id="receipt_audit",
            image_path="audit.jpg",
            image_hash="hash-audit",
            item_id_factory=id_factory,
        )
        assert len(record.items) == 1
        assert record.removed_items == []  # No removals expected
        # removed_items field exists and is serializable
        dumped = record.model_dump(mode="json")
        assert "removed_items" in dumped
        assert isinstance(dumped["removed_items"], list)


# ===========================================================================
# PRD 6.2: cancellation / leergut detection edge cases
# ===========================================================================

class TestCancellationDetectionEdgeCases:
    """Verify regex-based detection of cancellation/leergut patterns."""

    def test_storno_variants(self):
        assert is_cancellation_item("Storno", "storno")
        assert is_cancellation_item("Sofortstorno", "sofortstorno")
        assert is_cancellation_item("Ruecknahme", "ruecknahme")
        assert is_cancellation_item("Rucknahme", "rucknahme")
        assert is_cancellation_item("cancel", "cancel")

    def test_storno_not_in_norwegian_words(self):
        """'Storno' embedded in other words should still match."""
        # 'Storno' appears in 'storno_wurst' → matches
        assert is_cancellation_item("Sofortstorno Wurst", "sofortstorno_wurst")

    def test_leergut_variants(self):
        assert is_leergut_item("Leergut", "leergut")
        assert is_leergut_item("Pfand", "pfand")
        assert is_leergut_item("Flaschenpfand", "flaschenpfand")
        assert is_leergut_item("Mehrwegpfand", "mehrwegpfand")

    def test_normal_items_not_misdetected(self):
        assert not is_cancellation_item("Water", "water")
        assert not is_cancellation_item("Brot", "brot")
        assert not is_leergut_item("Water", "water")
        assert not is_leergut_item("Brot", "brot")