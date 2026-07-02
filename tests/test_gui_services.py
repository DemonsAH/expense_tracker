from __future__ import annotations

import json
import tempfile
from pathlib import Path

from datetime import datetime, timezone

from expense_tracker.gui.services import (
    AppPaths,
    build_new_receipt_draft,
    delete_receipt,
    list_reports,
    receipt_to_edit_payload,
    reopen_failed_receipt,
    save_receipt_edit,
    trigger_ingestion,
)
from expense_tracker.reports import update_monthly_report
from expense_tracker.schemas.domain import FailedOcrRecord, ReceiptItemRecord, ReceiptRecord, RemovedItemRecord
from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode
from expense_tracker.storage import load_receipt_store, save_receipt_store
from tests.test_reports import make_store


def make_test_paths() -> AppPaths:
    root = Path(tempfile.mkdtemp(dir="data"))
    owners_path = root / "owners.json"
    owners_path.write_text(
        json.dumps(
            {
                "owners": [
                    {"id": "chen", "name": "Qihong Chen", "marker": "Q", "is_me": True},
                    {"id": "fang", "name": "Yulin Fang", "marker": "F", "is_me": False},
                    {"id": "me", "name": "Legacy Me", "marker": "M", "is_me": False},
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return AppPaths(
        project_root=root,
        store_path=root / "data" / "receipts.json",
        owners_path=owners_path,
        reports_dir=root / "reports",
        rejected_dir=root / "rejected",
    )


def test_save_receipt_edit_creates_new_receipt_and_item_ids() -> None:
    paths = make_test_paths()
    draft = build_new_receipt_draft(paths)
    draft.update(
        merchant="Manual Entry",
        purchase_date="2026-05-14",
        total_amount="2.50",
        default_owner_id="chen",
    )
    draft["items"] = [
        {
            "id": "draft-item-1",
            "name": "Water",
            "normalized_name": "water",
            "category": "DRINK",
            "quantity": "1",
            "unit_price": "2.50",
            "total_price": "2.50",
            "owner_id": "chen",
            "owner_marker": "",
        }
    ]

    record = save_receipt_edit(paths, draft, draft["items"])
    store = load_receipt_store(paths.store_path)

    assert record.id == "receipt_1"
    assert record.items[0].id == "item_1"
    assert len(store.receipts) == 1


def test_save_receipt_edit_updates_existing_receipt() -> None:
    paths = make_test_paths()
    store = make_store()
    save_receipt_store(store, paths.store_path)

    existing = store.receipts[0]
    payload = receipt_to_edit_payload(existing)
    payload["merchant"] = "Updated Merchant"

    updated = save_receipt_edit(paths, payload, payload["items"], payload["removed_items"])
    refreshed = load_receipt_store(paths.store_path)

    assert updated.merchant == "Updated Merchant"
    assert refreshed.receipts[0].merchant == "Updated Merchant"


def test_delete_receipt_removes_record_from_store() -> None:
    paths = make_test_paths()
    store = make_store()
    save_receipt_store(store, paths.store_path)

    delete_receipt(paths, store.receipts[0].id)
    refreshed = load_receipt_store(paths.store_path)

    assert len(refreshed.receipts) == len(store.receipts) - 1


def test_list_reports_discovers_generated_report_files() -> None:
    paths = make_test_paths()
    save_receipt_store(make_store(), paths.store_path)
    update_monthly_report(
        year=2026,
        month=5,
        store_path=paths.store_path,
        owners_path=paths.owners_path,
        output_dir=paths.reports_dir,
        write_schema=True,
    )

    reports = list_reports(paths.reports_dir)

    assert len(reports) == 1
    assert reports[0].report_month == "2026-05"
    assert reports[0].html_path.exists()


def test_reopen_failed_receipt_moves_image_to_input_dir() -> None:
    """PRD 8.2: reopened failed receipt image is moved to receipt_input/."""
    paths = make_test_paths()
    paths.project_root.mkdir(parents=True, exist_ok=True)

    # Create an archived image
    rejected_dir = paths.rejected_dir
    rejected_dir.mkdir(parents=True, exist_ok=True)
    archived = rejected_dir / "failed_test.jpg"
    archived.write_bytes(b"fake receipt image")

    store = load_receipt_store(paths.store_path)
    store.failed_ocr_records.append(
        FailedOcrRecord(
            image_path="original.jpg",
            archived_image_path=str(archived),
            image_hash="hash1",
            attempts=3,
            failure_reason="receipt_total_mismatch",
            raw_outputs=["{}"],
            created_at=datetime.now(timezone.utc),
        )
    )
    save_receipt_store(store, paths.store_path)

    dest = reopen_failed_receipt(paths, 0)

    assert Path(dest).exists()
    assert Path(dest).read_bytes() == b"fake receipt image"
    assert Path(dest).parent == paths.project_root / "receipt_input"

    # Verify the failed record was removed
    refreshed = load_receipt_store(paths.store_path)
    assert len(refreshed.failed_ocr_records) == 0


def test_reopen_failed_receipt_avoids_filename_collision() -> None:
    """PRD 8.2: unique filename to avoid overwrite."""
    paths = make_test_paths()
    paths.project_root.mkdir(parents=True, exist_ok=True)

    rejected_dir = paths.rejected_dir
    rejected_dir.mkdir(parents=True, exist_ok=True)
    archived = rejected_dir / "failed_test.jpg"
    archived.write_bytes(b"img1")

    incoming_dir = paths.project_root / "receipt_input"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    (incoming_dir / "failed_test.jpg").write_bytes(b"existing")

    store = load_receipt_store(paths.store_path)
    store.failed_ocr_records.append(
        FailedOcrRecord(
            image_path="original.jpg",
            archived_image_path=str(archived),
            image_hash="hash1",
            attempts=1,
            failure_reason="test",
            raw_outputs=[],
            created_at=datetime.now(timezone.utc),
        )
    )
    save_receipt_store(store, paths.store_path)

    dest = reopen_failed_receipt(paths, 0)

    # Should not overwrite existing file
    assert Path(dest).exists()
    assert Path(dest).name != "failed_test.jpg"
    assert Path(dest).read_bytes() == b"img1"


def test_reopen_failed_receipt_invalid_index_raises() -> None:
    """PRD 8.2: invalid index raises IndexError."""
    paths = make_test_paths()
    import pytest
    with pytest.raises(IndexError):
        reopen_failed_receipt(paths, 0)  # empty store


def test_trigger_ingestion_rejects_missing_file() -> None:
    """trigger_ingestion should raise FileNotFoundError when image doesn't exist."""
    paths = make_test_paths()
    import pytest
    with pytest.raises(FileNotFoundError):
        trigger_ingestion(paths, "nonexistent.jpg")
