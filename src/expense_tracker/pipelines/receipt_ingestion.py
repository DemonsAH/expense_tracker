"""Receipt ingestion helpers: parse and validate step-1 model output."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from expense_tracker.ocr_client import run_ocr, strip_grounding
from expense_tracker.ocr_parser import parse_ocr_to_extracted_receipt
from expense_tracker.pipelines.receipt_validation import (
    ReceiptValidationResult,
    validate_extracted_receipt_business_rules,
)
from expense_tracker.pipelines.receipt_postprocess import ProcessedReceiptItems, process_extracted_receipt_items
from expense_tracker.pipelines.retry_policy import is_retryable_ingestion_error
from expense_tracker.schemas import extracted_to_receipt_record
from expense_tracker.schemas.domain import ReceiptRecord
from expense_tracker.schemas.enums import OcrStatus
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

# GLM-OCR 提示词必须是 "OCR"（见 GLM-OCR本地部署指南.md §8.1，写别的会退化）
OCR_PROMPT = "OCR"

_OCR_MODEL = "local-glm-ocr"
_LLM_MODEL = "deepseek-v4-flash"


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
    preprocess_info: dict | None = None


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


def _run_ocr(image: Path, *, use_preprocess: bool, prompt: str) -> tuple[str, dict | None]:
    """Run OCR on the image, optionally after receipt preprocessing.

    When preprocessing is enabled, the image is cropped from the (black)
    background and perspective-corrected per 图片预处理指南.md before being
    sent to the OCR model. The preprocessed image is written to a temp file,
    and the original file is never modified.
    """
    if not use_preprocess:
        return run_ocr(image, prompt=prompt, keep_grounding=True), None

    # 延迟导入：开关关闭时避免 cv2 的启动开销
    import cv2
    from expense_tracker.preprocess import load_image, preprocess

    result = preprocess(load_image(image))
    print(
        "[preprocess] method={} found_receipt={} info={}".format(
            result.method, result.found_receipt, result.info
        )
    )

    fd, tmp_path = tempfile.mkstemp(prefix=f"{image.stem}_preprocessed_", suffix=".png")
    os.close(fd)
    try:
        if not cv2.imwrite(tmp_path, result.image):
            raise RuntimeError("Failed to write preprocessed image to temp file")
        text = run_ocr(Path(tmp_path), prompt=prompt, keep_grounding=True)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return text, {
        "method": result.method,
        "found_receipt": result.found_receipt,
        "info": result.info,
    }


def _ingest_receipt_attempt(
    image: Path,
    *,
    owners_path: str | Path,
    save_artifacts: bool,
    artifact_output_dir: str | Path | None,
    persist_store: bool,
    store_path: str | Path,
    use_llm_parser: bool = False,
    use_preprocess: bool = False,
) -> ReceiptIngestionResult:
    owners = load_owners_config(owners_path)
    image_hash = compute_file_sha256(image)

    raw_ocr_text, preprocess_info = _run_ocr(
        image,
        use_preprocess=use_preprocess,
        prompt=OCR_PROMPT,
    )

    try:
        if use_llm_parser:
            from expense_tracker.llm_grounding_parser import parse_grounding_with_deepseek

            extracted = parse_grounding_with_deepseek(
                raw_ocr_text,
                owners_path=owners_path,
            )
            model = _LLM_MODEL
        else:
            extracted = parse_ocr_to_extracted_receipt(
                strip_grounding(raw_ocr_text),
                owners_path=owners_path,
            )
            model = _OCR_MODEL
        validation = validate_extracted_receipt_business_rules(
            extracted,
            owners=owners,
        )
        # 任何业务校验问题（总金额不匹配、单项价格不匹配等）都降级为
        # "needs review"：receipt 仍会入库，用户在 GUI 里逐项人工核对修改，
        # 而不是直接拒绝归档。称重/交错排版小票的单价×数量往往对不上，
        # 强行拒绝会导致整张小票丢失。
        review_notes = None
        if not validation.is_valid:
            review_notes = (
                "自动识别存在校验问题，待人工核对。"
                + "校验问题: " + ", ".join(validation.issues)
            )
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
            raw_text=raw_ocr_text,
        )
        if review_notes is not None:
            receipt_record.ocr_status = OcrStatus.NEEDS_REVIEW
            receipt_record.ocr_failure_reason = "business_validation_issues"
            receipt_record.review_notes = review_notes
            # Keep the OCR-extracted total so the reviewer can see the gap
            # between the receipt total and the parsed item sum.
            receipt_record.total_amount = extracted.total_amount
        else:
            # 校验通过（含总价核对一致）：标记自动识别成功，等待人工确认。
            receipt_record.ocr_status = OcrStatus.SUCCESS
        if persist_store and store is not None:
            append_receipt_record(store, receipt_record)
            save_receipt_store(store, store_path)
    except Exception as exc:
        raise ReceiptAttemptError(str(exc), content=raw_ocr_text) from exc

    content_path = None
    receipt_path = None
    if save_artifacts:
        content_path, receipt_path = save_extraction_artifacts(
            image_path=image,
            model=model,
            content=raw_ocr_text,
            extracted=extracted,
            output_dir=artifact_output_dir,
        )

    return ReceiptIngestionResult(
        image_path=image,
        model=model,
        content=raw_ocr_text,
        extracted=extracted,
        processed_items=processed_items,
        receipt_record=receipt_record,
        owners=owners,
        validation=validation,
        content_path=content_path,
        receipt_path=receipt_path,
        preprocess_info=preprocess_info,
    )


def _make_item_id_factory(receipt_key: str):
    counter = {"value": 0}

    def next_item_id() -> str:
        counter["value"] += 1
        return f"item_{receipt_key}_{counter['value']}"

    return next_item_id


def ingest_receipt_once(
    image_path: str | Path,
    *,
    owners_path: str | Path = "owners.json",
    save_artifacts: bool = True,
    artifact_output_dir: str | Path | None = None,
    persist_store: bool = True,
    store_path: str | Path = "data/receipts.json",
    archive_failures: bool = True,
    failure_output_dir: str | Path = "rejected_receipts",
    use_llm_parser: bool = False,
    use_preprocess: bool = False,
) -> ReceiptIngestionResult:
    """Run one end-to-end extraction attempt: call model, validate, and save."""
    image = Path(image_path)
    try:
        return _ingest_receipt_attempt(
            image=image,
            owners_path=owners_path,
            save_artifacts=save_artifacts,
            artifact_output_dir=artifact_output_dir,
            persist_store=persist_store,
            store_path=store_path,
            use_llm_parser=use_llm_parser,
            use_preprocess=use_preprocess,
        )
    except ReceiptAttemptError as exc:
        archived_image_path = None
        failure_path = None
        if archive_failures:
            archived_image_path, _, failure_path = save_failure_artifacts(
                image_path=image,
                model=_LLM_MODEL if use_llm_parser else _OCR_MODEL,
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


def ingest_receipt_with_retries(
    image_path: str | Path,
    *,
    owners_path: str | Path = "owners.json",
    max_attempts: int = 3,
    save_artifacts: bool = True,
    artifact_output_dir: str | Path | None = None,
    persist_store: bool = True,
    store_path: str | Path = "data/receipts.json",
    archive_failures: bool = True,
    failure_output_dir: str | Path = "rejected_receipts",
    use_llm_parser: bool = False,
    use_preprocess: bool = False,
) -> ReceiptIngestionResult:
    """Retry receipt ingestion up to max_attempts and archive all failed attempts."""
    image = Path(image_path)
    failures: list[ReceiptAttemptFailure] = []

    for attempt_number in range(1, max_attempts + 1):
        try:
            result = _ingest_receipt_attempt(
                image=image,
                owners_path=owners_path,
                save_artifacts=save_artifacts,
                artifact_output_dir=artifact_output_dir,
                persist_store=persist_store,
                store_path=store_path,
                use_llm_parser=use_llm_parser,
                use_preprocess=use_preprocess,
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
                    model=_LLM_MODEL if use_llm_parser else _OCR_MODEL,
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

            raise ReceiptAttemptError(
                str(exc)
                + (
                    f" | attempts={attempt_number} | archived_image_path={archived_image_path} | failure_path={failure_path}"
                    if archive_failures
                    else f" | attempts={attempt_number}"
                ),
                content=exc.content,
            ) from exc