from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from expense_tracker.reports import (
    MONTHLY_REPORT_SCHEMA_NAME,
    MONTHLY_REPORT_SCHEMA_VERSION,
    build_monthly_report,
    export_monthly_report_json_schema,
    update_monthly_report,
    validate_monthly_report_payload,
    write_monthly_report,
)
from expense_tracker.schemas.domain import FailedOcrRecord, ReceiptItemRecord, ReceiptRecord, ReceiptStore, RemovedItemRecord
from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode


def make_receipt(
    *,
    receipt_id: str,
    purchase_date: str,
    total_amount: str,
    owner_items: list[tuple[str, str, str, str, str]],
    removed_items: list[tuple[str, str]] | None = None,
    ocr_status: OcrStatus = OcrStatus.SUCCESS,
) -> ReceiptRecord:
    parsed_date = date.fromisoformat(purchase_date)
    now = datetime(2026, 5, 14, tzinfo=timezone.utc)
    items = []
    for index, (name, normalized_name, category, total_price, owner_id) in enumerate(owner_items, start=1):
        price = Decimal(total_price)
        items.append(
            ReceiptItemRecord(
                id=f"{receipt_id}_item_{index}",
                receipt_id=receipt_id,
                name=name,
                normalized_name=normalized_name,
                category=ItemCategory(category),
                quantity=Decimal("1"),
                unit_price=abs(price),
                total_price=price,
                owner_id=owner_id,
                owner_marker=None,
            )
        )

    removed = [
        RemovedItemRecord(
            name=name,
            normalized_name=name.lower(),
            category=ItemCategory.OTHER,
            quantity=Decimal("1"),
            unit_price=Decimal("1"),
            total_price=Decimal("-1"),
            owner_id="me",
            owner_marker=None,
            reason=reason,
            related_index=None,
        )
        for name, reason in (removed_items or [])
    ]

    return ReceiptRecord(
        id=receipt_id,
        merchant="REWE",
        purchase_date=parsed_date,
        currency="EUR",
        total_amount=Decimal(total_amount),
        payment_method="card",
        default_owner_id="chen",
        owner_mode=OwnerMode.NORMAL,
        receipt_owner_marker=None,
        image_path=f"{receipt_id}.jpg",
        image_hash=f"hash-{receipt_id}",
        ocr_raw_text="{}",
        is_verified=True,
        ocr_status=ocr_status,
        ocr_attempts=1,
        ocr_failure_reason=None,
        review_notes=None,
        created_at=now,
        updated_at=now,
        reviewed_at=now,
        items=items,
        removed_items=removed,
    )


def make_store() -> ReceiptStore:
    return ReceiptStore(
        last_receipt_id=4,
        last_item_id=8,
        receipts=[
            make_receipt(
                receipt_id="receipt_1",
                purchase_date="2026-05-02",
                total_amount="7.50",
                owner_items=[
                    ("Water", "water", "DRINK", "2.50", "chen"),
                    ("Apple", "apple", "FRUIT", "2.00", "fang"),
                    ("Lunch", "lunch", "DINING", "3.00", "chen"),
                ],
                removed_items=[("Sofortstorno", "excluded_cancellation_line")],
            ),
            make_receipt(
                receipt_id="receipt_2",
                purchase_date="2026-05-10",
                total_amount="2.35",
                owner_items=[
                    ("Water", "water", "DRINK", "3.10", "chen"),
                    ("Pfand", "pfand", "OTHER", "-0.75", "chen"),
                ],
                ocr_status=OcrStatus.NEEDS_REVIEW,
            ),
            make_receipt(
                receipt_id="receipt_3",
                purchase_date="2026-04-10",
                total_amount="2.50",
                owner_items=[("Water", "water", "DRINK", "2.50", "chen")],
            ),
            make_receipt(
                receipt_id="receipt_4",
                purchase_date="2025-05-12",
                total_amount="1.80",
                owner_items=[("Water", "water", "DRINK", "1.80", "chen")],
            ),
        ],
        failed_ocr_records=[
            FailedOcrRecord(
                image_path="failed.jpg",
                archived_image_path="rejected/failed.jpg",
                image_hash="failed-hash",
                attempts=3,
                failure_reason="receipt_total_mismatch",
                raw_outputs=["bad-json"],
                created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            )
        ],
    )


def test_build_monthly_report_aligns_with_formal_receipt_data() -> None:
    report = build_monthly_report(make_store(), year=2026, month=5, owners_path="owners.json")

    assert report.meta.schema_name == MONTHLY_REPORT_SCHEMA_NAME
    assert report.meta.schema_version == MONTHLY_REPORT_SCHEMA_VERSION
    assert report.meta.report_month == "2026-05"
    assert report.overview.month_total_spend == Decimal("9.85")
    assert report.overview.quarter_total_spend == Decimal("12.35")
    assert report.overview.my_month_total_spend == Decimal("7.85")
    assert report.overview.top_category == "DRINK"
    assert report.owner_spend[0].owner_id == "chen"
    assert report.owner_spend[0].owner_name == "Qihong Chen"
    assert report.category_spend[0].category == "DRINK"
    assert report.price_increases[0].normalized_name == "water"
    assert report.price_increases[0].change_amount == Decimal("0.60")
    assert report.price_decreases == []
    assert all(row.normalized_name != "pfand" for row in report.price_increases)
    assert report.data_quality.failed_ocr_count == 1
    assert report.data_quality.pending_review_count == 1
    assert report.data_quality.removed_items_count == 1


def test_write_monthly_report_creates_json_and_html() -> None:
    output_dir = Path(tempfile.mkdtemp(dir="data"))
    report = build_monthly_report(make_store(), year=2026, month=5, owners_path="owners.json")

    written = write_monthly_report(report, output_dir=output_dir, write_schema=True)

    assert written.json_path.name == "report.json"
    assert written.html_path.name == "report.html"
    assert written.schema_path is not None
    payload = json.loads(written.json_path.read_text(encoding="utf-8"))
    html = written.html_path.read_text(encoding="utf-8")
    schema = json.loads(written.schema_path.read_text(encoding="utf-8"))
    validated = validate_monthly_report_payload(payload)
    assert validated.meta.schema_version == MONTHLY_REPORT_SCHEMA_VERSION
    assert payload["meta"]["report_month"] == "2026-05"
    assert payload["meta"]["schema_name"] == MONTHLY_REPORT_SCHEMA_NAME
    assert schema["x-schema-version"] == MONTHLY_REPORT_SCHEMA_VERSION
    assert "Expense Tracker Monthly Report" in html
    assert "report-data" in html


def test_update_monthly_report_supports_refresh_from_store_object() -> None:
    output_dir = Path(tempfile.mkdtemp(dir="data"))

    written = update_monthly_report(
        year=2026,
        month=5,
        store=make_store(),
        owners_path="owners.json",
        output_dir=output_dir,
        write_schema=True,
    )

    assert written.report.overview.month_total_spend == Decimal("9.85")
    assert written.json_path.exists()
    assert written.html_path.exists()
    assert written.schema_path is not None and written.schema_path.exists()


def test_export_monthly_report_json_schema_writes_stable_contract_file() -> None:
    output_dir = Path(tempfile.mkdtemp(dir="data"))

    schema_path = export_monthly_report_json_schema(output_dir=output_dir)

    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema_path.name == "monthly_report.schema.json"
    assert payload["$id"] == f"{MONTHLY_REPORT_SCHEMA_NAME}/{MONTHLY_REPORT_SCHEMA_VERSION}"
    assert payload["x-schema-name"] == MONTHLY_REPORT_SCHEMA_NAME
