from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from expense_tracker import cli
from expense_tracker.pipelines import (
    ReceiptAttemptFailure,
    ReceiptIngestionResult,
    ReceiptValidationResult,
    ingest_receipt_with_retries,
    parse_extracted_receipt,
    process_extracted_receipt_items,
)
from expense_tracker.pipelines.receipt_ingestion import ReceiptAttemptError
from expense_tracker.schemas import ExtractedReceipt
from expense_tracker.schemas.converters import extracted_to_receipt_record
from expense_tracker.schemas.domain import ReceiptStore
from expense_tracker.schemas.owners import OwnersConfig


def make_extracted_receipt() -> ExtractedReceipt:
    return ExtractedReceipt.model_validate(
        {
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
                    "quantity": 1,
                    "unit_price": 2.00,
                    "total_price": 2.00,
                    "owner_id": "me",
                    "owner_marker": None,
                },
            ],
        }
    )


def make_item_id_factory(prefix: str = "item"):
    counter = {"value": 0}

    def next_item_id() -> str:
        counter["value"] += 1
        return f"{prefix}_{counter['value']}"

    return next_item_id


def make_ingestion_result(image_path: Path) -> ReceiptIngestionResult:
    extracted = make_extracted_receipt()
    processed = process_extracted_receipt_items(extracted)
    record = extracted_to_receipt_record(
        extracted,
        processed_items=processed,
        receipt_id="receipt_1",
        image_path=str(image_path),
        image_hash="hash-1",
        item_id_factory=make_item_id_factory(),
        raw_text="{}",
    )
    return ReceiptIngestionResult(
        image_path=image_path,
        model="Qwen/Qwen3.6-27B",
        content="{}",
        extracted=extracted,
        processed_items=processed,
        receipt_record=record,
        owners=OwnersConfig.model_validate(
            {
                "owners": [
                    {"id": "me", "name": "Me", "marker": "M", "is_me": True},
                    {"id": "alice", "name": "Alice", "marker": "A", "is_me": False},
                ]
            }
        ),
        validation=ReceiptValidationResult(is_valid=True, issues=[]),
    )


def test_parse_extracted_receipt_accepts_valid_schema() -> None:
    content = """
    {
      "merchant": "REWE",
      "purchase_date": "2026-05-04",
      "currency": "EUR",
      "total_amount": 4.5,
      "payment_method": null,
      "owner_mode": "normal",
      "default_owner_id": "me",
      "receipt_owner_marker": null,
      "items": [
        {
          "name": "Water",
          "normalized_name": "water",
          "category": "DRINK",
          "quantity": 1,
          "unit_price": 4.5,
          "total_price": 4.5,
          "owner_id": "me",
          "owner_marker": null
        }
      ]
    }
    """

    parsed = parse_extracted_receipt(content)

    assert parsed.merchant == "REWE"
    assert parsed.items[0].category.value == "DRINK"


def test_parse_extracted_receipt_rejects_schema_mismatch() -> None:
    content = """
    {
      "merchant": "REWE",
      "purchase_date": "2026-05-04",
      "currency": "EUR",
      "total_amount": 4.5,
      "payment_method": null,
      "owner_mode": "normal",
      "default_owner_id": "me",
      "receipt_owner_marker": null,
      "items": [
        {
          "name": "Water",
          "normalized_name": "water",
          "category": "DRINK",
          "quantity": 0,
          "unit_price": 4.5,
          "total_price": 4.5,
          "owner_id": "me",
          "owner_marker": null
        }
      ]
    }
    """

    with pytest.raises(ValueError, match="ExtractedReceipt schema"):
        parse_extracted_receipt(content)


def test_ingest_receipt_with_retries_retries_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = Path("retry.jpg")
    result = make_ingestion_result(image_path)
    attempts = {"count": 0}

    def fake_attempt(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ReceiptAttemptError("Business validation failed: receipt_total_mismatch", content="bad")
        return result

    monkeypatch.setattr("expense_tracker.pipelines.receipt_ingestion._ingest_receipt_attempt", fake_attempt)

    final_result = ingest_receipt_with_retries(image_path, max_attempts=3, archive_failures=False, persist_store=False)

    assert attempts["count"] == 2
    assert final_result.attempt_count == 2
    assert final_result.previous_failures == [
        ReceiptAttemptFailure(
            attempt_number=1,
            failure_reason="Business validation failed: receipt_total_mismatch",
            content="bad",
        )
    ]


def test_cancellation_items_are_kept_in_formal_items() -> None:
    """PRD 6.2: Storno/Sofortstorno items are kept in formal items, no auto-removal."""
    extracted = ExtractedReceipt.model_validate(
        {
            **make_extracted_receipt().model_dump(mode="json"),
            "total_amount": Decimal("0"),
            "items": [
                {
                    "name": "Water",
                    "normalized_name": "water",
                    "category": "DRINK",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("2.50"),
                    "total_price": Decimal("2.50"),
                    "owner_id": "me",
                    "owner_marker": None,
                },
                {
                    "name": "Sofortstorno",
                    "normalized_name": "sofortstorno",
                    "category": "OTHER",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("2.50"),
                    "total_price": Decimal("-2.50"),
                    "owner_id": "me",
                    "owner_marker": None,
                },
            ],
        }
    )

    processed = process_extracted_receipt_items(extracted)
    record = extracted_to_receipt_record(
        extracted,
        processed_items=processed,
        receipt_id="receipt_cancel",
        image_path="cancel.jpg",
        image_hash="hash-cancel",
        item_id_factory=make_item_id_factory("cancel"),
        raw_text="{}",
    )

    assert record.total_amount == Decimal("0")
    assert len(record.items) == 2
    assert record.removed_items == []


def test_leergut_negative_item_is_kept_in_formal_items() -> None:
    extracted = ExtractedReceipt.model_validate(
        {
            **make_extracted_receipt().model_dump(mode="json"),
            "total_amount": Decimal("1.75"),
            "items": [
                {
                    "name": "Water",
                    "normalized_name": "water",
                    "category": "DRINK",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("2.50"),
                    "total_price": Decimal("2.50"),
                    "owner_id": "me",
                    "owner_marker": None,
                },
                {
                    "name": "Pfand",
                    "normalized_name": "pfand",
                    "category": "OTHER",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("0.75"),
                    "total_price": Decimal("-0.75"),
                    "owner_id": "me",
                    "owner_marker": None,
                },
            ],
        }
    )

    processed = process_extracted_receipt_items(extracted)
    record = extracted_to_receipt_record(
        extracted,
        processed_items=processed,
        receipt_id="receipt_pfand",
        image_path="pfand.jpg",
        image_hash="hash-pfand",
        item_id_factory=make_item_id_factory("pfand"),
        raw_text="{}",
    )

    assert len(record.items) == 2
    assert record.items[1].total_price == Decimal("-0.75")
    assert record.total_amount == Decimal("1.75")


def test_ingest_dir_skip_helper_detects_processed_image(monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = Path("receipt.jpg")

    monkeypatch.setattr(cli, "compute_file_sha256", lambda _path: "hash-1")
    monkeypatch.setattr(cli, "has_processed_image", lambda store, image_path=None, image_hash=None: True)
    assert cli._should_skip_processed_image(ReceiptStore(), image_path) is True
