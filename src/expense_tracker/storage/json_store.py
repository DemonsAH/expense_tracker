"""JSON-backed persistence for receipt records and failed OCR records."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from expense_tracker.schemas.domain import FailedOcrRecord, ReceiptRecord, ReceiptStore


DEFAULT_STORE_PATH = Path("data/receipts.json")


def _normalize_legacy_store_payload(data: dict) -> dict:
    payload = dict(data)
    failed_records = payload.get("failed_ocr_records", [])
    normalized_failed_records = []
    for record in failed_records:
        item = dict(record)
        if "archived_image_path" not in item and "rejected_copy_path" in item:
            item["archived_image_path"] = item["rejected_copy_path"]
        if "failure_reason" not in item and "reason" in item:
            item["failure_reason"] = item["reason"]
        if "created_at" not in item:
            item["created_at"] = "1970-01-01T00:00:00+00:00"
        if "raw_outputs" not in item:
            item["raw_outputs"] = []
        normalized_failed_records.append(item)

    payload["failed_ocr_records"] = normalized_failed_records

    receipts = payload.get("receipts", [])
    normalized_receipts = []
    for record in receipts:
        item = dict(record)
        if "image_hash" not in item:
            item["image_hash"] = f"legacy::{item.get('image_path', 'unknown')}"
        normalized_receipts.append(item)
    payload["receipts"] = normalized_receipts
    return payload


def load_receipt_store(store_path: str | Path = DEFAULT_STORE_PATH) -> ReceiptStore:
    path = Path(store_path)
    if not path.exists():
        return ReceiptStore()

    data = json.loads(path.read_text(encoding="utf-8"))
    data = _normalize_legacy_store_payload(data)
    return ReceiptStore.model_validate(data)


def save_receipt_store(store: ReceiptStore, store_path: str | Path = DEFAULT_STORE_PATH) -> Path:
    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(store.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def next_receipt_id(store: ReceiptStore) -> str:
    store.last_receipt_id += 1
    return f"receipt_{store.last_receipt_id}"


def make_item_id_factory(store: ReceiptStore):
    def next_item_id() -> str:
        store.last_item_id += 1
        return f"item_{store.last_item_id}"

    return next_item_id


def append_receipt_record(
    store: ReceiptStore,
    record: ReceiptRecord,
) -> None:
    store.receipts.append(record)


def append_failed_ocr_record(
    store: ReceiptStore,
    *,
    image_path: str,
    archived_image_path: str,
    image_hash: str | None,
    attempts: int,
    failure_reason: str,
    raw_outputs: list[str],
) -> None:
    store.failed_ocr_records.append(
        FailedOcrRecord(
            image_path=image_path,
            archived_image_path=archived_image_path,
            image_hash=image_hash,
            attempts=attempts,
            failure_reason=failure_reason,
            raw_outputs=raw_outputs,
            created_at=datetime.now(timezone.utc),
        )
    )


def has_processed_image(
    store: ReceiptStore,
    *,
    image_path: str | None = None,
    image_hash: str | None = None,
) -> bool:
    for receipt in store.receipts:
        if image_hash and receipt.image_hash == image_hash:
            return True
        if image_path and receipt.image_path == image_path:
            return True
    return False
