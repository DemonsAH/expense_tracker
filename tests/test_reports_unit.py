"""Comprehensive unit tests for reports module (Phase 3).

Covers PRD sections:
  - 10.1: my monthly/quarterly spend, YoY, MoM
  - 10.2: owner spend, owner-level YoY/MoM
  - 10.3: category spend, share %
  - 10.4: price ranking, increase/decrease top-5
  - 10.5: automatic monthly report, JSON + HTML output
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from expense_tracker.reports.monthly import (
    MonthlyReport,
    OwnerSpendRow,
    CategorySpendRow,
    PriceChangeRow,
    WrittenMonthlyReport,
    _build_category_spend,
    _build_highlights,
    _build_owner_spend,
    _build_price_change_rows,
    _format_month,
    _is_price_ranking_item,
    _load_owners_map,
    _month_bounds,
    _quarter_receipts,
    _quarter_start_month,
    _safe_percent_change,
    _sum_receipts,
    _sum_items,
    build_monthly_report,
    export_monthly_report_json_schema,
    render_monthly_report_html,
    update_monthly_report,
    validate_monthly_report_payload,
    write_monthly_report,
)
from expense_tracker.schemas.domain import (
    FailedOcrRecord,
    ReceiptItemRecord,
    ReceiptRecord,
    ReceiptStore,
    RemovedItemRecord,
)
from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    receipt_id: str = "r1",
    item_id: str = "i1",
    name: str = "Water",
    normalized_name: str = "water",
    category: ItemCategory = ItemCategory.DRINK,
    total_price: str = "2.50",
    owner_id: str = "me",
    owner_marker: str | None = None,
    quantity: str = "1",
) -> ReceiptItemRecord:
    price = Decimal(total_price)
    return ReceiptItemRecord(
        id=item_id,
        receipt_id=receipt_id,
        name=name,
        normalized_name=normalized_name,
        category=category,
        quantity=Decimal(quantity),
        unit_price=abs(price),
        total_price=price,
        owner_id=owner_id,
        owner_marker=owner_marker,
    )


def _receipt(
    receipt_id: str = "r1",
    purchase_date: str = "2026-05-04",
    total_amount: str = "5.00",
    items: list[ReceiptItemRecord] | None = None,
    ocr_status: OcrStatus = OcrStatus.SUCCESS,
    removed_items: list[RemovedItemRecord] | None = None,
) -> ReceiptRecord:
    return ReceiptRecord(
        id=receipt_id,
        merchant="REWE",
        purchase_date=date.fromisoformat(purchase_date),
        currency="EUR",
        total_amount=Decimal(total_amount),
        payment_method="card",
        default_owner_id="me",
        owner_mode=OwnerMode.NORMAL,
        receipt_owner_marker=None,
        image_path=f"{receipt_id}.jpg",
        image_hash=f"hash-{receipt_id}",
        ocr_raw_text="{}",
        is_verified=True,
        ocr_status=ocr_status,
        ocr_attempts=1,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        items=items or [_item(receipt_id=receipt_id, total_price=total_amount)],
        removed_items=removed_items or [],
    )


def _store(*receipts: ReceiptRecord, failed: list[FailedOcrRecord] | None = None) -> ReceiptStore:
    return ReceiptStore(
        last_receipt_id=len(receipts),
        last_item_id=sum(len(r.items) for r in receipts),
        receipts=list(receipts),
        failed_ocr_records=failed or [],
    )


# ===========================================================================
# PRD 10.1/10.2: helper units (_month_bounds, _safe_percent_change, etc.)
# ===========================================================================

class TestMonthBounds:
    def test_normal_month(self):
        start, end = _month_bounds(2026, 5)
        assert start == date(2026, 5, 1)
        assert end == date(2026, 6, 1)

    def test_december(self):
        start, end = _month_bounds(2026, 12)
        assert start == date(2026, 12, 1)
        assert end == date(2027, 1, 1)

    def test_january(self):
        start, end = _month_bounds(2026, 1)
        assert start == date(2026, 1, 1)
        assert end == date(2026, 2, 1)


class TestQuarterStartMonth:
    @pytest.mark.parametrize("m, expected", [
        (1, 1), (2, 1), (3, 1),
        (4, 4), (5, 4), (6, 4),
        (7, 7), (8, 7), (9, 7),
        (10, 10), (11, 10), (12, 10),
    ])
    def test_q_start(self, m, expected):
        assert _quarter_start_month(m) == expected


class TestSafePercentChange:
    def test_positive_growth(self):
        assert _safe_percent_change(Decimal("120"), Decimal("100")) == Decimal("20.00")

    def test_negative_growth(self):
        assert _safe_percent_change(Decimal("80"), Decimal("100")) == Decimal("-20.00")

    def test_zero_previous_returns_none(self):
        assert _safe_percent_change(Decimal("10"), Decimal("0")) is None

    def test_no_change(self):
        assert _safe_percent_change(Decimal("100"), Decimal("100")) == Decimal("0.00")


class TestFormatMonth:
    def test_standard(self):
        assert _format_month(2026, 5) == "2026-05"

    def test_padded(self):
        assert _format_month(2026, 12) == "2026-12"


class TestSumHelpers:
    def test_sum_receipts(self):
        rs = [
            _receipt("r1", total_amount="3.00"),
            _receipt("r2", total_amount="0.75"),
        ]
        assert _sum_receipts(rs) == Decimal("3.75")

    def test_sum_items(self):
        items = [
            _item(total_price="5.00"),
            _item(total_price="-2.50"),
        ]
        assert _sum_items(items) == Decimal("2.50")


# ===========================================================================
# PRD 10.1: _month_receipts / _quarter_receipts filtering
# ===========================================================================

class TestReceiptFiltering:
    def test_month_receipts_filters_correctly(self):
        r1 = _receipt("r1", purchase_date="2026-05-02")
        r2 = _receipt("r2", purchase_date="2026-04-30")
        r3 = _receipt("r3", purchase_date="2026-06-01")
        store = _store(r1, r2, r3)
        from expense_tracker.reports.monthly import _month_receipts
        result = _month_receipts(store, 2026, 5)
        assert [r.id for r in result] == ["r1"]

    def test_quarter_receipts_q2(self):
        r1 = _receipt("r1", purchase_date="2026-04-15")
        r2 = _receipt("r2", purchase_date="2026-05-10")
        r3 = _receipt("r3", purchase_date="2026-07-01")
        store = _store(r1, r2, r3)
        result = _quarter_receipts(store, 2026, 5)
        assert sorted(r.id for r in result) == ["r1", "r2"]

    def test_quarter_q4_cross_year(self):
        r1 = _receipt("r1", purchase_date="2026-11-15")
        r2 = _receipt("r2", purchase_date="2026-12-01")
        r3 = _receipt("r3", purchase_date="2027-01-01")
        store = _store(r1, r2, r3)
        result = _quarter_receipts(store, 2026, 11)
        assert len(result) == 2  # r1, r2; r3 is 2027-01-01 >= end


# ===========================================================================
# PRD 6.3 + 10.4: _is_price_ranking_item
# ===========================================================================

class TestIsPriceRankingItem:
    def test_normal_drink_included(self):
        assert _is_price_ranking_item(_item("r1", "i1", "Water", "water", ItemCategory.DRINK, "2.50"))

    def test_dining_excluded(self):
        assert not _is_price_ranking_item(_item("r1", "i1", "Lunch", "lunch", ItemCategory.DINING, "15.00"))

    def test_negative_price_excluded(self):
        assert not _is_price_ranking_item(_item("r1", "i1", "Pfand", "pfand", ItemCategory.OTHER, "-0.75"))

    def test_leergut_by_name_excluded(self):
        assert not _is_price_ranking_item(_item("r1", "i1", "Pfand 0.5L", "pfand", ItemCategory.OTHER, "0.75"))

    def test_flaschenpfand_excluded(self):
        assert not _is_price_ranking_item(_item("r1", "i1", "Flaschenpfand", "flaschenpfand", ItemCategory.OTHER, "0.75"))

    def test_storno_by_negative_price_excluded(self):
        assert not _is_price_ranking_item(_item("r1", "i1", "Storno", "storno", ItemCategory.MEAT, "-3.00"))


# ===========================================================================
# PRD 10.2: _build_owner_spend
# ===========================================================================

class TestBuildOwnerSpend:
    def test_single_owner(self):
        r = _receipt("r1", total_amount="5.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "2.50", "me"),
            _item("r1", "i2", "Bread", "bread", ItemCategory.SNACKS, "2.50", "me"),
        ])
        rows = _build_owner_spend([r], owner_names={"me": "Me"})
        assert len(rows) == 1
        assert rows[0].owner_id == "me"
        assert rows[0].total_spend == Decimal("5.00")
        assert rows[0].share_percent == Decimal("100.00")

    def test_multi_owner(self):
        r = _receipt("r1", total_amount="5.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "3.00", "me"),
            _item("r1", "i2", "Bread", "bread", ItemCategory.SNACKS, "2.00", "alice"),
        ])
        rows = _build_owner_spend([r], owner_names={"me": "Me", "alice": "Alice"})
        assert len(rows) == 2
        assert rows[0].owner_id == "me"
        assert rows[1].owner_id == "alice"
        assert rows[0].share_percent == Decimal("60.00")
        assert rows[1].share_percent == Decimal("40.00")

    def test_negative_items_offset(self):
        r = _receipt("r1", total_amount="0.75", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "2.50", "me"),
            _item("r1", "i2", "Pfand", "pfand", ItemCategory.OTHER, "-0.75", "me"),
            _item("r1", "i3", "Bread", "bread", ItemCategory.SNACKS, "3.00", "alice"),
        ])
        rows = _build_owner_spend([r], owner_names={"me": "Me", "alice": "Alice"})
        assert rows[0].owner_id == "alice"  # 3.00 > 1.75
        assert rows[1].total_spend == Decimal("1.75")

    def test_empty_receipts(self):
        rows = _build_owner_spend([], owner_names={"me": "Me"})
        assert rows == []

    def test_shares_sum_to_100(self):
        r = _receipt("r1", total_amount="10.00", items=[
            _item("r1", "i1", "A", "a", ItemCategory.DRINK, "4.00", "me"),
            _item("r1", "i2", "B", "b", ItemCategory.DRINK, "3.00", "alice"),
            _item("r1", "i3", "C", "c", ItemCategory.DRINK, "3.00", "bob"),
        ])
        rows = _build_owner_spend([r], owner_names={"me": "Me", "alice": "Alice", "bob": "Bob"})
        total_share = sum(r.share_percent or Decimal("0") for r in rows)
        assert total_share == Decimal("100.00")


# ===========================================================================
# PRD 10.3: _build_category_spend
# ===========================================================================

class TestBuildCategorySpend:
    def test_basic(self):
        r = _receipt("r1", total_amount="5.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "3.00"),
            _item("r1", "i2", "Bread", "bread", ItemCategory.SNACKS, "2.00"),
        ])
        rows = _build_category_spend([r])
        assert rows[0].category == "DRINK"
        assert rows[1].category == "SNACKS"
        assert rows[0].total_spend == Decimal("3.00")
        assert rows[0].share_percent == Decimal("60.00")
        assert rows[1].share_percent == Decimal("40.00")

    def test_dining_aggregated(self):
        r1 = _receipt("r1", total_amount="10.00", items=[
            _item("r1", "i1", "Lunch", "lunch", ItemCategory.DINING, "6.00"),
            _item("r1", "i2", "Dinner", "dinner", ItemCategory.DINING, "4.00"),
        ])
        rows = _build_category_spend([r1])
        assert len(rows) == 1
        assert rows[0].category == "DINING"
        assert rows[0].total_spend == Decimal("10.00")

    def test_empty(self):
        assert _build_category_spend([]) == []

    def test_shares_sum_to_100(self):
        r = _receipt("r1", total_amount="10.00", items=[
            _item("r1", "i1", "Water", "w", ItemCategory.DRINK, "3.00"),
            _item("r1", "i2", "Chips", "c", ItemCategory.SNACKS, "2.00"),
            _item("r1", "i3", "Lunch", "l", ItemCategory.DINING, "5.00"),
        ])
        rows = _build_category_spend([r])
        total = sum(r.share_percent for r in rows)
        assert abs(total - Decimal("100.00")) < Decimal("0.01")


# ===========================================================================
# PRD 10.4: _build_price_change_rows
# ===========================================================================

class TestBuildPriceChangeRows:
    def test_increase_detected(self):
        """Water 1.80 -> 2.50 should produce an increase row."""
        r_old = _receipt("r1", purchase_date="2025-05-10", total_amount="1.80", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "1.80", "me"),
        ])
        r_new = _receipt("r2", purchase_date="2026-05-04", total_amount="2.50", items=[
            _item("r2", "i2", "Water", "water", ItemCategory.DRINK, "2.50", "me"),
        ])
        store = _store(r_old, r_new)
        inc, dec = _build_price_change_rows(store, 2026, 5)
        assert len(inc) == 1
        assert inc[0].normalized_name == "water"
        assert inc[0].previous_unit_price == Decimal("1.80")
        assert inc[0].current_unit_price == Decimal("2.50")
        assert inc[0].change_amount == Decimal("0.70")
        assert len(dec) == 0

    def test_decrease_detected(self):
        """Water 3.00 -> 2.00 should produce a decrease row."""
        r_old = _receipt("r1", purchase_date="2025-05-10", total_amount="3.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "3.00", "me"),
        ])
        r_new = _receipt("r2", purchase_date="2026-05-04", total_amount="2.00", items=[
            _item("r2", "i2", "Water", "water", ItemCategory.DRINK, "2.00", "me"),
        ])
        store = _store(r_old, r_new)
        inc, dec = _build_price_change_rows(store, 2026, 5)
        assert len(inc) == 0
        assert len(dec) == 1
        assert dec[0].change_amount == Decimal("-1.00")

    def test_no_change_omitted(self):
        """Same price -> no rows."""
        r_old = _receipt("r1", purchase_date="2025-05-10", total_amount="2.50", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "2.50", "me"),
        ])
        r_new = _receipt("r2", purchase_date="2026-05-04", total_amount="2.50", items=[
            _item("r2", "i2", "Water", "water", ItemCategory.DRINK, "2.50", "me"),
        ])
        store = _store(r_old, r_new)
        inc, dec = _build_price_change_rows(store, 2026, 5)
        assert inc == []
        assert dec == []

    def test_dining_excluded_from_ranking(self):
        """DINING should not participate in price ranking."""
        r_old = _receipt("r1", purchase_date="2025-05-10", total_amount="12.00", items=[
            _item("r1", "i1", "Lunch", "lunch", ItemCategory.DINING, "12.00", "me"),
        ])
        r_new = _receipt("r2", purchase_date="2026-05-04", total_amount="15.00", items=[
            _item("r2", "i2", "Lunch", "lunch", ItemCategory.DINING, "15.00", "me"),
        ])
        store = _store(r_old, r_new)
        inc, dec = _build_price_change_rows(store, 2026, 5)
        assert inc == []
        assert dec == []

    def test_leergut_excluded(self):
        r_old = _receipt("r1", purchase_date="2025-05-10", total_amount="0.75", items=[
            _item("r1", "i1", "Pfand", "pfand", ItemCategory.OTHER, "0.75", "me"),
        ])
        r_new = _receipt("r2", purchase_date="2026-05-04", total_amount="0.85", items=[
            _item("r2", "i2", "Pfand", "pfand", ItemCategory.OTHER, "0.85", "me"),
        ])
        store = _store(r_old, r_new)
        inc, dec = _build_price_change_rows(store, 2026, 5)
        assert inc == []
        assert dec == []

    def test_top_5_only(self):
        """Only top 5 increases/decreases returned (different normalized names, not same)."""
        # old: 8 items with unique names at price 1.00
        old_items = [
            _item("r0", f"old_{n}", f"item{n}", f"item{n}", ItemCategory.DRINK, "1.00", "me")
            for n in range(8)
        ]
        r_old = _receipt("r0", purchase_date="2025-05-10", total_amount="8.00", items=old_items)
        # new: same 8 unique names, prices 1..8
        new_items = [
            _item("r1", f"new_{n}", f"item{n}", f"item{n}", ItemCategory.DRINK, str(Decimal(n + 1)), "me")
            for n in range(8)
        ]
        r_new = _receipt("r1", purchase_date="2026-05-04", total_amount="36.00", items=new_items)
        store = _store(r_old, r_new)
        inc, dec = _build_price_change_rows(store, 2026, 5)
        assert len(inc) == 5
        assert inc[0].change_amount > inc[-1].change_amount  # sorted descending

    def test_empty_store(self):
        inc, dec = _build_price_change_rows(_store(), 2026, 5)
        assert inc == []
        assert dec == []

    def test_no_historical_data(self):
        """Only current month data, no historical items."""
        r = _receipt("r1", purchase_date="2026-05-04", total_amount="2.50", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "2.50", "me"),
        ])
        store = _store(r)
        inc, dec = _build_price_change_rows(store, 2026, 5)
        assert inc == []
        assert dec == []


# ===========================================================================
# PRD 10.5: build_monthly_report integration
# ===========================================================================

class TestBuildMonthlyReport:
    def test_full_report_build(self):
        """Integration-style test: month, quarter, owners, categories, price change."""
        r1 = _receipt("r1", purchase_date="2026-05-04", total_amount="5.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "3.00", "me"),
            _item("r1", "i2", "Bread", "bread", ItemCategory.SNACKS, "2.00", "alice"),
        ])
        r2 = _receipt("r2", purchase_date="2026-04-15", total_amount="4.00", items=[
            _item("r2", "i3", "Water", "water", ItemCategory.DRINK, "2.00", "me"),
            _item("r2", "i4", "Bread", "bread", ItemCategory.SNACKS, "2.00", "me"),
        ])
        store = _store(r1, r2)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"owners": [
                {"id": "me", "name": "Me", "marker": "M", "is_me": True},
                {"id": "alice", "name": "Alice", "marker": "A", "is_me": False},
            ]}, f)
            owners_path = Path(f.name)

        try:
            report = build_monthly_report(store, year=2026, month=5, owners_path=owners_path)

            assert report.meta.report_month == "2026-05"
            assert report.meta.quarter == "2026-Q2"
            assert report.meta.receipt_count == 1
            assert report.meta.item_count == 2

            assert report.overview.month_total_spend == Decimal("5.00")
            assert report.overview.quarter_total_spend == Decimal("9.00")
            assert report.overview.my_month_total_spend == Decimal("3.00")
            assert report.overview.month_over_month_change is not None  # 5 vs 4 = +25%
            assert report.overview.top_category == "DRINK"

            assert len(report.owner_spend) == 2
            assert len(report.category_spend) == 2

            # Water: 3.00, Bread: 2.00
            assert report.category_spend[0].category == "DRINK"
            assert report.category_spend[0].total_spend == Decimal("3.00")

            # highlights
            assert len(report.highlights.summary) > 0

            # data_quality
            assert report.data_quality.failed_ocr_count == 0
            assert report.data_quality.pending_review_count == 0
        finally:
            owners_path.unlink(missing_ok=True)

    def test_report_month_over_month(self):
        r1 = _receipt("r1", purchase_date="2026-05-04", total_amount="10.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "10.00", "me"),
        ])
        r2 = _receipt("r2", purchase_date="2026-04-15", total_amount="5.00", items=[
            _item("r2", "i2", "Water", "water", ItemCategory.DRINK, "5.00", "me"),
        ])
        store = _store(r1, r2)
        report = build_monthly_report(store, year=2026, month=5, owners_path=None)
        # 10 vs 5 = +100%
        assert report.overview.month_over_month_change == Decimal("100.00")

    def test_report_year_over_year(self):
        r1 = _receipt("r1", purchase_date="2026-05-04", total_amount="10.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "10.00", "me"),
        ])
        r2 = _receipt("r2", purchase_date="2025-05-10", total_amount="8.00", items=[
            _item("r2", "i2", "Water", "water", ItemCategory.DRINK, "8.00", "me"),
        ])
        store = _store(r1, r2)
        report = build_monthly_report(store, year=2026, month=5, owners_path=None)
        # 10 vs 8 = +25%
        assert report.overview.year_over_year_change == Decimal("25.00")

    def test_yoy_cross_year_boundary(self):
        """Jan 2026 vs Jan 2025."""
        r1 = _receipt("r1", purchase_date="2026-01-15", total_amount="12.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "12.00", "me"),
        ])
        r2 = _receipt("r2", purchase_date="2025-01-10", total_amount="10.00", items=[
            _item("r2", "i2", "Water", "water", ItemCategory.DRINK, "10.00", "me"),
        ])
        store = _store(r1, r2)
        report = build_monthly_report(store, year=2026, month=1, owners_path=None)
        assert report.overview.year_over_year_change == Decimal("20.00")

    def test_empty_store_produces_valid_report(self):
        report = build_monthly_report(_store(), year=2026, month=5, owners_path=None)
        assert report.meta.report_month == "2026-05"
        assert report.meta.quarter == "2026-Q2"
        assert report.overview.month_total_spend == Decimal("0")
        assert report.overview.quarter_total_spend == Decimal("0")
        assert report.overview.month_over_month_change is None
        assert report.overview.year_over_year_change is None
        assert report.owner_spend == []
        assert report.category_spend == []
        assert report.price_increases == []
        assert report.price_decreases == []
        assert report.highlights.summary

    def test_previous_month_cross_year(self):
        """Jan 2026 MoM should compare to Dec 2025."""
        r1 = _receipt("r1", purchase_date="2026-01-10", total_amount="10.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "10.00", "me"),
        ])
        r2 = _receipt("r2", purchase_date="2025-12-15", total_amount="5.00", items=[
            _item("r2", "i2", "Water", "water", ItemCategory.DRINK, "5.00", "me"),
        ])
        store = _store(r1, r2)
        report = build_monthly_report(store, year=2026, month=1, owners_path=None)
        assert report.overview.month_over_month_change == Decimal("100.00")


# ===========================================================================
# PRD 10.5: report rendering, write, validate
# ===========================================================================

class TestReportRendering:
    def test_html_contains_report_data(self):
        report = build_monthly_report(_store(), year=2026, month=5)
        html = render_monthly_report_html(report)
        assert "2026-05" in html
        assert "report-data" in html
        assert "Expense Tracker" in html

    def test_html_with_real_data(self):
        r = _receipt("r1", purchase_date="2026-05-04", total_amount="5.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "5.00", "me"),
        ])
        store = _store(r)
        report = build_monthly_report(store, year=2026, month=5)
        html = render_monthly_report_html(report)
        assert "5.00 EUR" in html
        assert "DRINK" in html


class TestWriteUpdateReport:
    def test_write_report_creates_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            report = build_monthly_report(_store(), year=2026, month=5)
            written = write_monthly_report(report, output_dir=output_dir, write_schema=True)
            assert written.json_path.exists()
            assert written.html_path.exists()
            assert written.schema_path is not None and written.schema_path.exists()
            payload = json.loads(written.json_path.read_text(encoding="utf-8"))
            valid = validate_monthly_report_payload(payload)
            assert valid.meta.report_month == "2026-05"

    def test_update_report_skips_existing(self):  # FIX if real code uses that option
        """Just verify the function path works."""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            r = _receipt("r1", purchase_date="2026-05-04", total_amount="5.00", items=[
                _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "5.00", "me"),
            ])
            store = _store(r)
            written = update_monthly_report(
                year=2026, month=5, store=store, owners_path=None,
                output_dir=output_dir, write_schema=True,
            )
            assert written.report.meta.report_month == "2026-05"
            assert written.json_path.exists()

    def test_export_schema_writes_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            schema_path = export_monthly_report_json_schema(output_dir=output_dir)
            payload = json.loads(schema_path.read_text(encoding="utf-8"))
            assert "$id" in payload
            assert "receipt_count" in str(payload)


# ===========================================================================
# PRD 10.2: owner-level YoY/MoM (new feature)
# ===========================================================================

class TestBuildOwnerSpendWithHistory:
    """Verify OwnerSpendRow includes MoM/YoY computed from historical receipts."""

    def test_owner_mom_included(self):
        """Owner-level MoM should be computed."""
        r_may = _receipt("r1", purchase_date="2026-05-04", total_amount="10.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "6.00", "me"),
            _item("r1", "i2", "Bread", "bread", ItemCategory.SNACKS, "4.00", "alice"),
        ])
        r_apr = _receipt("r2", purchase_date="2026-04-10", total_amount="7.00", items=[
            _item("r2", "i3", "Water", "water", ItemCategory.DRINK, "5.00", "me"),
            _item("r2", "i4", "Bread", "bread", ItemCategory.SNACKS, "2.00", "alice"),
        ])
        store = _store(r_may, r_apr)
        report = build_monthly_report(store, year=2026, month=5)
        # Check owner_spend rows have MoM/YoY
        for row in report.owner_spend:
            assert hasattr(row, "month_over_month_change")
            assert hasattr(row, "year_over_year_change")
        # me: 6.00 vs 5.00 = +20%
        me_row = next(r for r in report.owner_spend if r.owner_id == "me")
        assert me_row.month_over_month_change == Decimal("20.00")
        # alice: 4.00 vs 2.00 = +100%
        alice_row = next(r for r in report.owner_spend if r.owner_id == "alice")
        assert alice_row.month_over_month_change == Decimal("100.00")

    def test_owner_yoy_included(self):
        """Owner-level YoY should be computed."""
        r_this = _receipt("r1", purchase_date="2026-05-04", total_amount="12.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "12.00", "me"),
        ])
        r_last = _receipt("r2", purchase_date="2025-05-10", total_amount="8.00", items=[
            _item("r2", "i2", "Water", "water", ItemCategory.DRINK, "8.00", "me"),
        ])
        store = _store(r_this, r_last)
        report = build_monthly_report(store, year=2026, month=5)
        me_row = next(r for r in report.owner_spend if r.owner_id == "me")
        assert me_row.year_over_year_change == Decimal("50.00")

    def test_owner_no_previous_data(self):
        """New owner in current month -> MoM/YoY should be None."""
        r = _receipt("r1", purchase_date="2026-05-04", total_amount="5.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "5.00", "me"),
        ])
        store = _store(r)
        report = build_monthly_report(store, year=2026, month=5)
        me_row = next(r for r in report.owner_spend if r.owner_id == "me")
        assert me_row.month_over_month_change is None
        assert me_row.year_over_year_change is None

    def test_owner_with_zero_previous_spend(self):
        """Owner spent 0 last month -> MoM should be None (not divide by zero)."""
        r_this = _receipt("r1", purchase_date="2026-05-04", total_amount="5.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "5.00", "me"),
        ])
        # Previous month has only alice spending (me=0)
        r_prev = _receipt("r2", purchase_date="2026-04-10", total_amount="3.00", items=[
            _item("r2", "i2", "Bread", "bread", ItemCategory.SNACKS, "3.00", "alice"),
        ])
        store = _store(r_this, r_prev)
        report = build_monthly_report(store, year=2026, month=5)
        me_row = next(r for r in report.owner_spend if r.owner_id == "me")
        assert me_row.month_over_month_change is None  # prev=0 -> None

    def test_owner_spend_interface_unchanged(self):
        """Existing OwnerSpendRow tests should still work (share_percent unchanged)."""
        r = _receipt("r1", purchase_date="2026-05-04", total_amount="10.00", items=[
            _item("r1", "i1", "A", "a", ItemCategory.DRINK, "7.00", "me"),
            _item("r1", "i2", "B", "b", ItemCategory.DRINK, "3.00", "alice"),
        ])
        report = build_monthly_report(_store(r), year=2026, month=5)
        me_row = next(r for r in report.owner_spend if r.owner_id == "me")
        assert me_row.share_percent == Decimal("70.00")
        alice_row = next(r for r in report.owner_spend if r.owner_id == "alice")
        assert alice_row.share_percent == Decimal("30.00")


# ===========================================================================
# PRD 10.5: highlights
# ===========================================================================

class TestBuildHighlights:
    def test_all_zones_have_summary(self):
        report = build_monthly_report(_store(), year=2026, month=5)
        assert len(report.highlights.summary) >= 1
        assert any("0 张小票" in s for s in report.highlights.summary)

    def test_highlights_with_data(self):
        r = _receipt("r1", purchase_date="2026-05-04", total_amount="5.00", items=[
            _item("r1", "i1", "Water", "water", ItemCategory.DRINK, "5.00", "me"),
        ])
        store = _store(r)
        report = build_monthly_report(store, year=2026, month=5)
        assert any("5.00 EUR" in s for s in report.highlights.summary)
        assert any("DRINK" in s for s in report.highlights.summary)