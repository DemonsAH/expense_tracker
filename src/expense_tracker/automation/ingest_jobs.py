"""Automation entrypoints for scheduled directory ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from expense_tracker.pipelines import ingest_receipt_with_retries
from expense_tracker.storage import (
    compute_file_sha256,
    has_processed_image,
    load_receipt_store,
    move_source_file,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DUPLICATE_POLICIES = {"skip-success", "retry-failed-only", "force-reprocess"}


@dataclass
class IngestJobResult:
    directory: Path
    images_found: int
    success_count: int
    failure_count: int
    skipped_count: int
    success_files: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    moved_success_files: list[str] = field(default_factory=list)
    moved_failed_files: list[str] = field(default_factory=list)
    moved_skipped_files: list[str] = field(default_factory=list)
    duplicate_policy: str = "skip-success"


def _iter_job_image_paths(directory: Path, *, recursive: bool) -> list[Path]:
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _should_skip_processed_image(store, image_path: Path) -> bool:
    image_hash = compute_file_sha256(image_path)
    return has_processed_image(
        store,
        image_path=str(image_path),
        image_hash=image_hash,
    )


def _has_failed_ocr_record(store, image_path: Path) -> bool:
    image_hash = compute_file_sha256(image_path)
    for record in store.failed_ocr_records:
        if record.image_hash and record.image_hash == image_hash:
            return True
        if record.image_path == str(image_path):
            return True
    return False


def _validate_duplicate_policy(policy: str) -> str:
    if policy not in DUPLICATE_POLICIES:
        raise ValueError(
            f"Invalid duplicate policy '{policy}'. "
            f"Expected one of: {', '.join(sorted(DUPLICATE_POLICIES))}."
        )
    return policy


def _should_skip_by_policy(store, image_path: Path, duplicate_policy: str) -> bool:
    duplicate_policy = _validate_duplicate_policy(duplicate_policy)
    if duplicate_policy == "force-reprocess":
        return False
    if duplicate_policy == "retry-failed-only":
        return not _has_failed_ocr_record(store, image_path)
    return _should_skip_processed_image(store, image_path)


def run_ingest_directory_job(
    directory: str | Path,
    *,
    owners_path: str | Path = "owners.json",
    model: str = "Qwen/Qwen3.6-27B",
    max_attempts: int = 3,
    artifact_output_dir: str | Path | None = None,
    failure_output_dir: str | Path = "rejected_receipts",
    processed_output_dir: str | Path = "processed_receipts",
    store_path: str | Path = "data/receipts.json",
    archive_failures: bool = True,
    duplicate_policy: str = "skip-success",
    recursive: bool = False,
) -> IngestJobResult:
    duplicate_policy = _validate_duplicate_policy(duplicate_policy)
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Directory not found: {root}")

    image_paths = _iter_job_image_paths(root, recursive=recursive)
    if not image_paths:
        raise ValueError(f"No supported receipt images found in: {root}")

    store = load_receipt_store(store_path)
    result = IngestJobResult(
        directory=root,
        images_found=len(image_paths),
        success_count=0,
        failure_count=0,
        skipped_count=0,
        duplicate_policy=duplicate_policy,
    )

    for image_path in image_paths:
        if _should_skip_by_policy(store, image_path, duplicate_policy):
            result.skipped_count += 1
            result.skipped_files.append(image_path.name)
            moved_path = move_source_file(
                image_path,
                source_root=root,
                destination_root=processed_output_dir,
            )
            result.moved_skipped_files.append(str(moved_path))
            continue

        try:
            ingest_receipt_with_retries(
                image_path=image_path,
                owners_path=owners_path,
                model=model,
                max_attempts=max_attempts,
                save_artifacts=True,
                artifact_output_dir=artifact_output_dir,
                persist_store=True,
                store_path=store_path,
                archive_failures=archive_failures,
                failure_output_dir=failure_output_dir,
            )
            result.success_count += 1
            result.success_files.append(image_path.name)
            moved_path = move_source_file(
                image_path,
                source_root=root,
                destination_root=processed_output_dir,
            )
            result.moved_success_files.append(str(moved_path))
            store = load_receipt_store(store_path)
        except Exception:
            result.failure_count += 1
            result.failed_files.append(image_path.name)
            if image_path.exists():
                moved_path = move_source_file(
                    image_path,
                    source_root=root,
                    destination_root=Path(failure_output_dir) / "_source",
                )
                result.moved_failed_files.append(str(moved_path))
            store = load_receipt_store(store_path)

    return result
