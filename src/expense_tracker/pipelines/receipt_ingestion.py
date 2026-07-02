"""Receipt ingestion helpers: parse and validate step-1 model output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from expense_tracker.pipelines.receipt_validation import (
    ReceiptValidationResult,
    validate_extracted_receipt_business_rules,
)
from expense_tracker.pipelines.receipt_postprocess import ProcessedReceiptItems, process_extracted_receipt_items
from expense_tracker.pipelines.retry_policy import is_retryable_ingestion_error
from expense_tracker.receipt_step1 import run_receipt_step1
from expense_tracker.schemas import extracted_to_receipt_record
from expense_tracker.schemas.domain import ReceiptRecord
from expense_tracker.schemas.extraction import ExtractedReceipt
from expense_tracker.schemas.owners import OwnersConfig, load_owners_config
from expense_tracker.storage.artifacts import (
    save_extraction_artifacts,
    save_failure_artifacts,
    save_retry_failure_artifacts,
)
from expense_tracker.storage.file_index import compute_file_sha256
from expense_tracker.storage.json_store import (
    append_failed_ocr_record,
    append_receipt_record,
    load_receipt_store,
    make_item_id_factory,
    next_receipt_id,
    save_receipt_store,
)
from expense_tracker.tracing import receipt_traceable


@dataclass
class ReceiptAttemptFailure:
    attempt_number: int
    failure_reason: str
    content: str | None = None


@dataclass
class ReceiptIngestionResult:
    image_path: Path
    model: str
    content: str
    extracted: ExtractedReceipt
    processed_items: ProcessedReceiptItems
    receipt_record: ReceiptRecord
    owners: OwnersConfig
    validation: ReceiptValidationResult
    content_path: Path | None = None
    receipt_path: Path | None = None
    archived_image_path: Path | None = None
    failure_path: Path | None = None
    attempt_count: int = 1
    previous_failures: list[ReceiptAttemptFailure] = field(default_factory=list)


class ReceiptAttemptError(ValueError):
    """Raised when a single extraction attempt fails."""

    def __init__(self, message: str, *, content: str | None = None):
        super().__init__(message)
        self.content = content


def parse_extracted_receipt(content: str) -> ExtractedReceipt:
    """Parse raw model text into the validated extraction schema."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model output is not valid JSON: {exc}") from exc

    try:
        return ExtractedReceipt.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValueError(f"Model output does not match ExtractedReceipt schema: {exc}") from exc


def _ingest_receipt_attempt(
    image: Path,
    *,
    owners_path: str | Path,
    model: str,
    save_artifacts: bool,
    artifact_output_dir: str | Path | None,
    persist_store: bool,
    store_path: str | Path,
) -> ReceiptIngestionResult:
    owners = load_owners_config(owners_path)
    image_hash = compute_file_sha256(image)
    content = run_receipt_step1(
        image_path=image,
        owners_path=owners_path,
        model=model,
    )

    try:
        extracted = parse_extracted_receipt(content)
        validation = validate_extracted_receipt_business_rules(
            extracted,
            owners=owners,
        )
        if not validation.is_valid:
            raise ValueError("Business validation failed: " + ", ".join(validation.issues))
        processed_items = process_extracted_receipt_items(extracted)
        if persist_store:
            store = load_receipt_store(store_path)
            receipt_id = next_receipt_id(store)
            item_id_factory = make_item_id_factory(store)
        else:
            store = None
            receipt_id = f"receipt_{image.stem}"
            item_id_factory = _make_item_id_factory(image.stem)

        receipt_record = extracted_to_receipt_record(
            extracted,
            processed_items=processed_items,
            receipt_id=receipt_id,
            image_path=str(image),
            image_hash=image_hash,
            item_id_factory=item_id_factory,
            raw_text=content,
        )
        if persist_store and store is not None:
            append_receipt_record(store, receipt_record)
            save_receipt_store(store, store_path)
    except Exception as exc:
        raise ReceiptAttemptError(str(exc), content=content) from exc

    content_path = None
    receipt_path = None
    if save_artifacts:
        content_path, receipt_path = save_extraction_artifacts(
            image_path=image,
            model=model,
            content=content,
            extracted=extracted,
            output_dir=artifact_output_dir,
        )

    return ReceiptIngestionResult(
        image_path=image,
        model=model,
        content=content,
        extracted=extracted,
        processed_items=processed_items,
        receipt_record=receipt_record,
        owners=owners,
        validation=validation,
        content_path=content_path,
        receipt_path=receipt_path,
    )


def _make_item_id_factory(receipt_key: str):
    counter = {"value": 0}

    def next_item_id() -> str:
        counter["value"] += 1
        return f"item_{receipt_key}_{counter['value']}"

    return next_item_id


@receipt_traceable(
    name="receipt_ingestion_once",
    run_type="chain",
    metadata={"pipeline_stage": "step1_parse_validate_save"},
)
def ingest_receipt_once(
    image_path: str | Path,
    *,
    owners_path: str | Path = "owners.json",
    model: str = "Qwen/Qwen3.6-27B",
    save_artifacts: bool = True,
    artifact_output_dir: str | Path | None = None,
    persist_store: bool = True,
    store_path: str | Path = "data/receipts.json",
    archive_failures: bool = True,
    failure_output_dir: str | Path = "rejected_receipts",
) -> ReceiptIngestionResult:
    """Run one end-to-end extraction attempt: call model, validate, and save."""
    image = Path(image_path)
    try:
        return _ingest_receipt_attempt(
            image=image,
            owners_path=owners_path,
            model=model,
            save_artifacts=save_artifacts,
            artifact_output_dir=artifact_output_dir,
            persist_store=persist_store,
            store_path=store_path,
        )
    except ReceiptAttemptError as exc:
        archived_image_path = None
        failure_path = None
        if archive_failures:
            archived_image_path, _, failure_path = save_failure_artifacts(
                image_path=image,
                model=model,
                failure_reason=str(exc),
                content=exc.content,
                output_dir=failure_output_dir,
            )
            if persist_store:
                store = load_receipt_store(store_path)
                append_failed_ocr_record(
                    store,
                    image_path=str(image),
                    archived_image_path=str(archived_image_path),
                    image_hash=compute_file_sha256(image),
                    attempts=1,
                    failure_reason=str(exc),
                    raw_outputs=[exc.content] if exc.content else [],
                )
                save_receipt_store(store, store_path)
        raise ValueError(
            str(exc)
            + (
                f" | archived_image_path={archived_image_path} | failure_path={failure_path}"
                if archive_failures
                else ""
            )
        ) from exc


@receipt_traceable(
    name="receipt_ingestion_with_retries",
    run_type="chain",
    metadata={"pipeline_stage": "retry_loop"},
)
def ingest_receipt_with_retries(
    image_path: str | Path,
    *,
    owners_path: str | Path = "owners.json",
    model: str = "Qwen/Qwen3.6-27B",
    max_attempts: int = 3,
    save_artifacts: bool = True,
    artifact_output_dir: str | Path | None = None,
    persist_store: bool = True,
    store_path: str | Path = "data/receipts.json",
    archive_failures: bool = True,
    failure_output_dir: str | Path = "rejected_receipts",
) -> ReceiptIngestionResult:
    """Retry receipt ingestion up to max_attempts and archive all failed attempts."""
    image = Path(image_path)
    failures: list[ReceiptAttemptFailure] = []

    for attempt_number in range(1, max_attempts + 1):
        try:
            result = _ingest_receipt_attempt(
                image=image,
                owners_path=owners_path,
                model=model,
                save_artifacts=save_artifacts,
                artifact_output_dir=artifact_output_dir,
                persist_store=persist_store,
                store_path=store_path,
            )
            result.attempt_count = attempt_number
            result.previous_failures = failures
            return result
        except ReceiptAttemptError as exc:
            failures.append(
                ReceiptAttemptFailure(
                    attempt_number=attempt_number,
                    failure_reason=str(exc),
                    content=exc.content,
                )
            )

            should_retry = (
                attempt_number < max_attempts
                and is_retryable_ingestion_error(str(exc))
            )
            if should_retry:
                continue

            archived_image_path = None
            failure_path = None
            if archive_failures:
                archived_image_path, _, failure_path = save_retry_failure_artifacts(
                    image_path=image,
                    model=model,
                    failures=[
                        {
                            "attempt_number": failure.attempt_number,
                            "failure_reason": failure.failure_reason,
                            "content": failure.content,
                        }
                        for failure in failures
                    ],
                    output_dir=failure_output_dir,
                )
                if persist_store:
                    store = load_receipt_store(store_path)
                    append_failed_ocr_record(
                        store,
                        image_path=str(image),
                        archived_image_path=str(archived_image_path),
                        image_hash=compute_file_sha256(image),
                        attempts=attempt_number,
                        failure_reason=str(exc),
                        raw_outputs=[
                            failure.content
                            for failure in failures
                            if failure.content
                        ],
                    )
                    save_receipt_store(store, store_path)

            raise ValueError(
                str(exc)
                + (
                    f" | attempts={attempt_number} | archived_image_path={archived_image_path} | failure_path={failure_path}"
                    if archive_failures
                    else f" | attempts={attempt_number}"
                )
            ) from exc
