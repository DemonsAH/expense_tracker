from __future__ import annotations

import tempfile
from pathlib import Path

from expense_tracker.automation import run_ingest_directory_job


def test_run_ingest_directory_job_skips_processed_images(monkeypatch) -> None:
    root = Path(tempfile.mkdtemp(dir="data"))
    processed_dir = Path(tempfile.mkdtemp(dir="data"))
    (root / "receipt1.jpg").write_bytes(b"one")
    (root / "receipt2.jpg").write_bytes(b"two")

    monkeypatch.setattr("expense_tracker.automation.ingest_jobs.load_receipt_store", lambda _path: object())
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs._should_skip_processed_image", lambda store, image_path: image_path.name == "receipt1.jpg")
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs._has_failed_ocr_record", lambda store, image_path: False)

    called: list[str] = []

    def fake_ingest_receipt_with_retries(**kwargs):
        called.append(Path(kwargs["image_path"]).name)
        return object()

    monkeypatch.setattr("expense_tracker.automation.ingest_jobs.ingest_receipt_with_retries", fake_ingest_receipt_with_retries)

    result = run_ingest_directory_job(root, processed_output_dir=processed_dir)

    assert result.images_found == 2
    assert result.success_count == 1
    assert result.skipped_count == 1
    assert result.failure_count == 0
    assert result.skipped_files == ["receipt1.jpg"]
    assert called == ["receipt2.jpg"]
    assert (processed_dir / "receipt1.jpg").exists()
    assert (processed_dir / "receipt2.jpg").exists()
    assert not (root / "receipt1.jpg").exists()
    assert not (root / "receipt2.jpg").exists()


def test_run_ingest_directory_job_retry_failed_only_policy(monkeypatch) -> None:
    root = Path(tempfile.mkdtemp(dir="data"))
    processed_dir = Path(tempfile.mkdtemp(dir="data"))
    (root / "receipt1.jpg").write_bytes(b"one")
    (root / "receipt2.jpg").write_bytes(b"two")

    monkeypatch.setattr("expense_tracker.automation.ingest_jobs.load_receipt_store", lambda _path: object())
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs._should_skip_processed_image", lambda store, image_path: False)
    monkeypatch.setattr(
        "expense_tracker.automation.ingest_jobs._has_failed_ocr_record",
        lambda store, image_path: image_path.name == "receipt2.jpg",
    )

    called: list[str] = []
    monkeypatch.setattr(
        "expense_tracker.automation.ingest_jobs.ingest_receipt_with_retries",
        lambda **kwargs: called.append(Path(kwargs["image_path"]).name) or object(),
    )

    result = run_ingest_directory_job(
        root,
        duplicate_policy="retry-failed-only",
        processed_output_dir=processed_dir,
    )

    assert result.duplicate_policy == "retry-failed-only"
    assert result.skipped_files == ["receipt1.jpg"]
    assert result.success_files == ["receipt2.jpg"]
    assert called == ["receipt2.jpg"]


def test_run_ingest_directory_job_force_reprocess_policy(monkeypatch) -> None:
    root = Path(tempfile.mkdtemp(dir="data"))
    processed_dir = Path(tempfile.mkdtemp(dir="data"))
    (root / "receipt.jpg").write_bytes(b"one")

    monkeypatch.setattr("expense_tracker.automation.ingest_jobs.load_receipt_store", lambda _path: object())
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs._should_skip_processed_image", lambda store, image_path: True)
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs._has_failed_ocr_record", lambda store, image_path: False)

    called: list[str] = []
    monkeypatch.setattr(
        "expense_tracker.automation.ingest_jobs.ingest_receipt_with_retries",
        lambda **kwargs: called.append(Path(kwargs["image_path"]).name) or object(),
    )

    result = run_ingest_directory_job(
        root,
        duplicate_policy="force-reprocess",
        processed_output_dir=processed_dir,
    )

    assert result.skipped_count == 0
    assert result.success_files == ["receipt.jpg"]
    assert called == ["receipt.jpg"]


def test_run_ingest_directory_job_supports_recursive_scan(monkeypatch) -> None:
    root = Path(tempfile.mkdtemp(dir="data"))
    processed_dir = Path(tempfile.mkdtemp(dir="data"))
    nested = root / "nested"
    nested.mkdir()
    (nested / "receipt.jpg").write_bytes(b"nested")

    monkeypatch.setattr("expense_tracker.automation.ingest_jobs.load_receipt_store", lambda _path: object())
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs._should_skip_processed_image", lambda store, image_path: False)
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs._has_failed_ocr_record", lambda store, image_path: False)
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs.ingest_receipt_with_retries", lambda **kwargs: object())

    result = run_ingest_directory_job(root, recursive=True, processed_output_dir=processed_dir)

    assert result.images_found == 1
    assert result.success_files == ["receipt.jpg"]
    assert (processed_dir / "nested" / "receipt.jpg").exists()


def test_run_ingest_directory_job_tracks_failures(monkeypatch) -> None:
    root = Path(tempfile.mkdtemp(dir="data"))
    failure_dir = Path(tempfile.mkdtemp(dir="data"))
    (root / "receipt.jpg").write_bytes(b"bad")

    monkeypatch.setattr("expense_tracker.automation.ingest_jobs.load_receipt_store", lambda _path: object())
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs._should_skip_processed_image", lambda store, image_path: False)
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs._has_failed_ocr_record", lambda store, image_path: False)

    def fail_ingest(**kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("expense_tracker.automation.ingest_jobs.ingest_receipt_with_retries", fail_ingest)

    result = run_ingest_directory_job(root, failure_output_dir=failure_dir)

    assert result.success_count == 0
    assert result.failure_count == 1
    assert result.failed_files == ["receipt.jpg"]
    assert (failure_dir / "_source" / "receipt.jpg").exists()


def test_run_ingest_directory_job_rejects_invalid_duplicate_policy(monkeypatch) -> None:
    root = Path(tempfile.mkdtemp(dir="data"))
    (root / "receipt.jpg").write_bytes(b"x")
    monkeypatch.setattr("expense_tracker.automation.ingest_jobs.load_receipt_store", lambda _path: object())

    try:
        run_ingest_directory_job(root, duplicate_policy="unknown-policy")
    except ValueError as exc:
        assert "Invalid duplicate policy" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid duplicate policy")
