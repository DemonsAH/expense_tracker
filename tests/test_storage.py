"""Unit tests for storage module: json_store, file_index, artifacts, directory_flow.

Covers PRD sections:
  - 7.3 (failure archiving)
  - 9.5 (JSON persistence)
  - 3.2 (unique filenames)
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from expense_tracker.schemas.domain import (
    FailedOcrRecord,
    ReceiptItemRecord,
    ReceiptRecord,
    ReceiptStore,
)
from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode
from expense_tracker.schemas.extraction import ExtractedReceipt
from expense_tracker.storage.file_index import compute_file_sha256
from expense_tracker.storage.directory_flow import move_source_file
from expense_tracker.storage.artifacts import (
    build_artifact_paths,
    save_extraction_artifacts,
    save_failure_artifacts,
    save_retry_failure_artifacts,
)
from expense_tracker.storage.json_store import (
    DEFAULT_STORE_PATH,
    append_failed_ocr_record,
    append_receipt_record,
    load_receipt_store,
    make_item_id_factory,
    next_receipt_id,
    save_receipt_store,
    _normalize_legacy_store_payload,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dummy_receipt_record(receipt_id: str = "receipt_1") -> ReceiptRecord:
    return ReceiptRecord(
        id=receipt_id,
        merchant="REWE",
        purchase_date=datetime(2026, 5, 4).date(),
        currency="EUR",
        total_amount=Decimal("4.50"),
        payment_method=None,
        default_owner_id="me",
        owner_mode=OwnerMode.NORMAL,
        receipt_owner_marker=None,
        image_path="test.jpg",
        image_hash="hash-test",
        ocr_raw_text="{}",
        is_verified=False,
        ocr_status=OcrStatus.PENDING,
        ocr_attempts=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        items=[
            ReceiptItemRecord(
                id="item_1",
                receipt_id=receipt_id,
                name="Water",
                normalized_name="water",
                category=ItemCategory.DRINK,
                quantity=Decimal("1"),
                unit_price=Decimal("2.50"),
                total_price=Decimal("2.50"),
                owner_id="me",
            ),
        ],
        removed_items=[],
    )


# ===========================================================================
# PRD 9.5: json_store unit tests
# ===========================================================================

class TestJsonStore:
    """PRD 9.5: JSON-backed persistence for receipts and failed_ocr_records."""

    def test_load_empty_store_when_file_missing(self):
        store = load_receipt_store("nonexistent_path_12345.json")
        assert isinstance(store, ReceiptStore)
        assert store.receipts == []
        assert store.last_receipt_id == 0

    def test_save_and_load_roundtrip(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            store_path = Path(f.name)

        try:
            store = ReceiptStore()
            record = _dummy_receipt_record()
            append_receipt_record(store, record)
            save_receipt_store(store, store_path)

            loaded = load_receipt_store(store_path)
            assert len(loaded.receipts) == 1
            assert loaded.receipts[0].id == "receipt_1"
            assert loaded.receipts[0].merchant == "REWE"
            assert loaded.last_receipt_id == 0  # ID counter not persisted in this path
        finally:
            store_path.unlink(missing_ok=True)

    def test_next_receipt_id_increments(self):
        store = ReceiptStore()
        assert next_receipt_id(store) == "receipt_1"
        assert next_receipt_id(store) == "receipt_2"
        assert next_receipt_id(store) == "receipt_3"

    def test_make_item_id_factory_increments(self):
        store = ReceiptStore()
        factory = make_item_id_factory(store)
        assert factory() == "item_1"
        assert factory() == "item_2"
        assert factory() == "item_3"

    def test_append_failed_ocr_record(self):
        store = ReceiptStore()
        append_failed_ocr_record(
            store,
            image_path="bad.jpg",
            archived_image_path="rejected/bad.jpg",
            image_hash="hash-bad",
            attempts=3,
            failure_reason="receipt_total_mismatch",
            raw_outputs=["{}", "{}"],
        )
        assert len(store.failed_ocr_records) == 1
        f = store.failed_ocr_records[0]
        assert f.image_path == "bad.jpg"
        assert f.archived_image_path == "rejected/bad.jpg"
        assert f.attempts == 3
        assert f.failure_reason == "receipt_total_mismatch"
        assert len(f.raw_outputs) == 2

    def test_legacy_store_normalization_handles_old_keys(self):
        """Ensure legacy store with rejected_copy_path/reason is normalized."""
        legacy = {
            "last_receipt_id": 0,
            "last_item_id": 0,
            "receipts": [
                {
                    "id": "r1",
                    "merchant": "dm",
                    "purchase_date": "2026-05-01",
                    "currency": "EUR",
                    "total_amount": 10.00,
                    "payment_method": None,
                    "default_owner_id": "me",
                    "owner_mode": "normal",
                    "receipt_owner_marker": None,
                    "image_path": "r1.jpg",
                    "ocr_raw_text": "{}",
                    "is_verified": False,
                    "ocr_status": "success",
                    "ocr_attempts": 1,
                    "created_at": "2026-05-02T00:00:00+00:00",
                    "updated_at": "2026-05-02T00:00:00+00:00",
                    "items": [],
                    "removed_items": [],
                }
            ],
            "failed_ocr_records": [
                {
                    "image_path": "f1.jpg",
                    "rejected_copy_path": "rejected/f1.jpg",
                    "reason": "bad json",
                    "attempts": 1,
                }
            ],
        }
        normalized = _normalize_legacy_store_payload(legacy)
        failed = normalized["failed_ocr_records"][0]
        assert failed["failure_reason"] == "bad json"  # reason -> failure_reason normalized
        assert failed["created_at"] == "1970-01-01T00:00:00+00:00"
        assert failed["raw_outputs"] == []

        receipt = normalized["receipts"][0]
        assert "image_hash" in receipt


# ===========================================================================
# file_index unit tests
# ===========================================================================

class TestFileIndex:
    def test_compute_sha256_is_deterministic(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("hello world")
            path = Path(f.name)

        try:
            h1 = compute_file_sha256(path)
            h2 = compute_file_sha256(path)
            assert h1 == h2
            assert len(h1) == 64  # SHA-256 hex digest
            # Verify against known hash
            assert h1 == hashlib.sha256(b"hello world").hexdigest()
        finally:
            path.unlink(missing_ok=True)

    def test_different_files_different_hashes(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f1:
            f1.write("content A")
            p1 = Path(f1.name)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f2:
            f2.write("content B")
            p2 = Path(f2.name)

        try:
            assert compute_file_sha256(p1) != compute_file_sha256(p2)
        finally:
            p1.unlink(missing_ok=True)
            p2.unlink(missing_ok=True)


# ===========================================================================
# PRD 7.3: artifacts (save raw outputs, archive failures)
# ===========================================================================

class TestArtifacts:
    """PRD 7.3: save raw model outputs and failure artifacts with timestamps."""

    def test_build_artifact_paths(self):
        content_path, receipt_path = build_artifact_paths(
            image_path=Path("receipts/test1.jpg"),
            model="local-unlimited-ocr",
            output_dir=Path("artifacts"),
        )
        assert content_path.parent == Path("artifacts")
        assert receipt_path.parent == Path("artifacts")
        assert content_path.stem.endswith("_content")
        assert receipt_path.stem.endswith("_receipt")
        assert "local-unlimited-ocr" in content_path.stem

    def test_save_extraction_artifacts_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            image_path = tmp_dir / "test.jpg"
            image_path.write_bytes(b"fake image")

            extracted = ExtractedReceipt.model_validate({
                "merchant": "REWE",
                "purchase_date": "2026-05-04",
                "currency": "EUR",
                "total_amount": 4.50,
                "payment_method": "card",
                "owner_mode": "normal",
                "default_owner_id": "me",
                "receipt_owner_marker": None,
                "items": [{
                    "name": "Water", "normalized_name": "water",
                    "category": "DRINK", "quantity": 1,
                    "unit_price": 2.50, "total_price": 2.50,
                    "owner_id": "me",
                }],
            })

            content_path, receipt_path = save_extraction_artifacts(
                image_path=image_path,
                model="local-unlimited-ocr",
                content="{\"merchant\": \"REWE\"}",
                extracted=extracted,
                output_dir=tmp_dir / "artifacts",
            )
            assert content_path.exists()
            assert receipt_path.exists()
            assert content_path.read_text(encoding="utf-8") == "{\"merchant\": \"REWE\"}"

    def test_save_failure_artifacts_copies_image_and_writes_metadata(self):
        """PRD 7.3: 保存原始图片、每次模型返回内容、失败原因、尝试次数、时间戳."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            image_path = tmp_dir / "fail_test.jpg"
            image_path.write_bytes(b"fake receipt image")

            archived, content_p, failure_p = save_failure_artifacts(
                image_path=image_path,
                model="Qwen/Qwen3.6-27B",
                failure_reason="receipt_total_mismatch",
                content="invalid json",
                output_dir=tmp_dir / "rejected",
            )

            assert archived.exists()
            assert failure_p.exists()
            assert content_p is not None and content_p.exists()
            assert archived.read_bytes() == b"fake receipt image"

            failure_data = json.loads(failure_p.read_text(encoding="utf-8"))
            assert failure_data["failure_reason"] == "receipt_total_mismatch"
            assert failure_data["model"] == "Qwen/Qwen3.6-27B"
            assert "created_at" in failure_data

    def test_save_retry_failure_artifacts_records_all_attempts(self):
        """PRD 7.2: 每次尝试都需保留模型返回文本."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            image_path = tmp_dir / "retry_test.jpg"
            image_path.write_bytes(b"fake image")

            failures = [
                {"attempt_number": 1, "failure_reason": "bad json", "content": "not-json"},
                {"attempt_number": 2, "failure_reason": "owner_id_not_found", "content": "{\"x\":1}"},
                {"attempt_number": 3, "failure_reason": "receipt_total_mismatch", "content": "{}"},
            ]

            archived, content_paths, failure_p = save_retry_failure_artifacts(
                image_path=image_path,
                model="local-unlimited-ocr",
                failures=failures,
                output_dir=tmp_dir / "rejected",
            )

            assert archived.exists()
            assert len(content_paths) == 3
            for cp in content_paths:
                assert cp.exists()

            failure_data = json.loads(failure_p.read_text(encoding="utf-8"))
            assert len(failure_data["attempts"]) == 3
            assert failure_data["attempts"][0]["attempt_number"] == 1


# ===========================================================================
# PRD 3.2: directory_flow (move source files, unique filenames)
# ===========================================================================

class TestDirectoryFlow:
    """PRD 3.2: unique filename, avoid overwrites, move on success/failure."""

    def test_move_source_file_creates_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            src_root = Path(tmp) / "incoming"
            src_root.mkdir()
            src_file = src_root / "receipt.jpg"
            src_file.write_bytes(b"img")

            dest_root = Path(tmp) / "processed"

            moved = move_source_file(src_file, source_root=src_root, destination_root=dest_root)
            assert moved.exists()
            assert not src_file.exists()
            assert moved.parent == dest_root

    def test_move_source_file_avoids_collision(self):
        """PRD 3.2: 保存的文件需具备唯一文件名，避免覆盖."""
        with tempfile.TemporaryDirectory() as tmp:
            src_root = Path(tmp) / "incoming"
            dest_root = Path(tmp) / "processed"
            src_root.mkdir()
            dest_root.mkdir()

            # Pre-create a file with the same name at destination
            (dest_root / "receipt.jpg").write_bytes(b"existing")

            src_file = src_root / "receipt.jpg"
            src_file.write_bytes(b"new content")

            moved = move_source_file(src_file, source_root=src_root, destination_root=dest_root)
            assert moved.exists()
            assert moved.stem == "receipt_1"  # unique suffix added
            assert moved.read_bytes() == b"new content"