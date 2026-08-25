"""Service helpers for the desktop GUI."""

from __future__ import annotations

import json
import os
import shutil
import sys
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from expense_tracker.reports import update_monthly_report
from expense_tracker.schemas.domain import ReceiptItemRecord, ReceiptRecord, ReceiptStore, RemovedItemRecord
from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode
from expense_tracker.schemas.owners import OwnersConfig, load_owners_config
from expense_tracker.storage import load_receipt_store, save_receipt_store


MONEY_TOLERANCE = Decimal("0.05")


@dataclass
class AppPaths:
    project_root: Path
    store_path: Path
    owners_path: Path
    reports_dir: Path
    rejected_dir: Path


# 自动化处理流目录：receipt_input/未处理 -> 已处理 -> 已校对
RECEIPT_INPUT_DIR = "receipt_input"
INCOMING_DIR = "未处理"
PROCESSED_DIR = "已处理"
REVIEWED_DIR = "已校对"


def receipt_flow_dirs(project_root: str | Path) -> tuple[Path, Path, Path]:
    base = Path(project_root) / RECEIPT_INPUT_DIR
    return base / INCOMING_DIR, base / PROCESSED_DIR, base / REVIEWED_DIR


def ensure_receipt_flow_dirs(project_root: str | Path) -> None:
    for directory in receipt_flow_dirs(project_root):
        directory.mkdir(parents=True, exist_ok=True)


def _build_unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def move_verified_receipt_image(
    project_root: str | Path,
    image_path: str,
    receipt_id: str,
) -> str | None:
    """把已校对小票的图片从 已处理/ 移到 已校对/，并按 receipt id 重命名。

    仅当图片位于 receipt_input/已处理/ 下时才移动；否则原样返回（不越权移动
    其它目录的图片）。返回新路径，无需移动时返回 None。
    """
    if not image_path:
        return None
    source = Path(image_path)
    _, processed_dir, reviewed_dir = receipt_flow_dirs(project_root)
    try:
        source.relative_to(processed_dir)
    except ValueError:
        return None

    destination = _build_unique_destination(reviewed_dir / f"{receipt_id}{source.suffix}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return str(destination)


@dataclass
class ReportListEntry:
    report_month: str
    json_path: Path
    html_path: Path
    schema_path: Path | None = None
    generated_at: str | None = None


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def exe_build_time() -> str:
    """Return the build timestamp of the running executable, for GUI display.

    In a frozen (PyInstaller) build the timestamp is read from the exe file
    itself (i.e. when it was last packaged); when running from source, report
    that mode instead so the user knows this is not the packaged exe.
    """
    if not _is_frozen():
        return "源码模式"
    try:
        mtime = Path(sys.executable).stat().st_mtime
    except OSError:
        return "未知"
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def find_project_root(start: Path | None = None) -> Path:
    candidates: list[Path] = []
    if start is not None:
        candidates.append(start.resolve())
    if _is_frozen():
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.append(Path.cwd().resolve())
    candidates.append(Path(__file__).resolve())

    seen: set[Path] = set()
    for candidate in candidates:
        current = candidate if candidate.is_dir() else candidate.parent
        while current not in seen:
            seen.add(current)
            if (current / "owners.json").exists() and (current / "src").exists():
                return current
            if current.parent == current:
                break
            current = current.parent

    return Path.cwd().resolve()


def default_app_paths() -> AppPaths:
    root = find_project_root()
    return AppPaths(
        project_root=root,
        store_path=root / "data" / "receipts.json",
        owners_path=root / "owners.json",
        reports_dir=root / "reports",
        rejected_dir=root / "rejected_receipts",
    )


def load_app_state(paths: AppPaths) -> tuple[ReceiptStore, OwnersConfig]:
    store = load_receipt_store(paths.store_path)
    owners = load_owners_config(paths.owners_path)
    return store, owners


def list_reports(reports_dir: str | Path) -> list[ReportListEntry]:
    root = Path(reports_dir)
    if not root.exists():
        return []

    entries: list[ReportListEntry] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        json_path = child / "report.json"
        html_path = child / "report.html"
        if not json_path.exists() or not html_path.exists():
            continue
        schema_path = root / "_schema" / "monthly_report.schema.json"
        generated_at = None
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            generated_at = payload.get("meta", {}).get("generated_at")
            report_month = payload.get("meta", {}).get("report_month", child.name)
        except Exception:
            report_month = child.name
        entries.append(
            ReportListEntry(
                report_month=report_month,
                json_path=json_path,
                html_path=html_path,
                schema_path=schema_path if schema_path.exists() else None,
                generated_at=generated_at,
            )
        )
    return sorted(entries, key=lambda item: item.report_month, reverse=True)


def generate_report(paths: AppPaths, year: int, month: int, *, write_schema: bool = True):
    return update_monthly_report(
        year=year,
        month=month,
        store_path=paths.store_path,
        owners_path=paths.owners_path,
        output_dir=paths.reports_dir,
        write_schema=write_schema,
    )


def _to_decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except Exception as exc:
        raise ValueError(f"Invalid decimal value for {field_name}.") from exc


def step_down_items(items: list[dict], indices: list[int]) -> None:
    """把选中 item 的价格整体下移一位，用于修复称重小票金额错位。

    就地修改 items：在最后一个选中项之后插入一个新 item（名字为 "new"，
    价格为最后一个选中项的价格）；然后选中项的价格依次向前一个选中项复制
    （最后一个 ← 倒数第二个，…，第二个 ← 第一个）；第一个选中项价格置 0。
    indices 会被去重并按升序排序。
    """
    selection = sorted(set(indices))
    if not selection:
        return

    last_index = selection[-1]
    new_item = dict(items[last_index])
    new_item["name"] = "new"
    new_item["normalized_name"] = "new"

    for index in range(len(selection) - 1, 0, -1):
        items[selection[index]]["total_price"] = items[selection[index - 1]]["total_price"]
    items[selection[0]]["total_price"] = "0.00"

    items.insert(last_index + 1, new_item)


def is_splittable_item(item: dict) -> bool:
    """quantity 为大于 1 的整数时可拆分为多个 quantity=1 的 item。"""
    try:
        quantity = Decimal(str(item.get("quantity")))
    except Exception:
        return False
    return quantity == int(quantity) and quantity > 1


def split_quantity_items(items: list[dict], indices: list[int]) -> list[int]:
    """把选中的 quantity>1 整数 item 拆成多个 quantity=1 的 item（就地修改）。

    每个拆分项继承原项的名称/品类/单价/归属，quantity=1、total_price=unit_price
    （拆分前后总额一致），id 清空（保存时由 save_receipt_edit 重新生成）。
    返回实际被拆分的 item 原始索引列表；跳过非可拆分项。
    """
    targets = [i for i in sorted(set(indices)) if is_splittable_item(items[i])]
    for index in reversed(targets):
        item = items[index]
        count = int(Decimal(str(item["quantity"])))
        unit = Decimal(str(item["unit_price"]))
        split_items = [
            {
                **item,
                "id": "",
                "quantity": "1",
                "unit_price": f"{unit:.2f}",
                "total_price": f"{unit:.2f}",
            }
            for _ in range(count)
        ]
        items[index:index + 1] = split_items
    return targets


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except Exception as exc:
        raise ValueError("purchase_date must be YYYY-MM-DD.") from exc


def _build_receipt_item_record(receipt_id: str, item_data: dict[str, Any], owner_ids: set[str], existing_id: str | None = None) -> ReceiptItemRecord:
    owner_id = str(item_data["owner_id"]).strip()
    if owner_id not in owner_ids:
        raise ValueError(f"Unknown owner_id: {owner_id}")

    return ReceiptItemRecord(
        id=existing_id or str(item_data.get("id") or ""),
        receipt_id=receipt_id,
        name=str(item_data["name"]).strip(),
        normalized_name=str(item_data["normalized_name"]).strip(),
        category=ItemCategory(str(item_data["category"]).strip()),
        quantity=_to_decimal(item_data["quantity"], "quantity"),
        unit_price=_to_decimal(item_data["unit_price"], "unit_price"),
        total_price=_to_decimal(item_data["total_price"], "total_price"),
        owner_id=owner_id,
        owner_marker=(str(item_data["owner_marker"]).strip().upper() or None) if item_data.get("owner_marker") else None,
    )


def _build_removed_item_record(item_data: dict[str, Any], owner_ids: set[str]) -> RemovedItemRecord:
    owner_id = str(item_data["owner_id"]).strip()
    if owner_id not in owner_ids:
        raise ValueError(f"Unknown owner_id: {owner_id}")

    return RemovedItemRecord(
        name=str(item_data["name"]).strip(),
        normalized_name=str(item_data["normalized_name"]).strip(),
        category=ItemCategory(str(item_data["category"]).strip()),
        quantity=_to_decimal(item_data["quantity"], "removed.quantity"),
        unit_price=_to_decimal(item_data["unit_price"], "removed.unit_price"),
        total_price=_to_decimal(item_data["total_price"], "removed.total_price"),
        owner_id=owner_id,
        owner_marker=(str(item_data["owner_marker"]).strip().upper() or None) if item_data.get("owner_marker") else None,
        reason=str(item_data["reason"]).strip(),
        related_index=int(item_data["related_index"]) if item_data.get("related_index") not in (None, "") else None,
    )


def save_receipt_edit(
    paths: AppPaths,
    receipt_data: dict[str, Any],
    item_data: list[dict[str, Any]],
    removed_item_data: list[dict[str, Any]] | None = None,
) -> ReceiptRecord:
    store, owners = load_app_state(paths)
    owner_ids = {owner.id for owner in owners.owners}
    removed_item_data = removed_item_data or []

    receipt_id = str(receipt_data.get("id") or "").strip()
    existing = next((receipt for receipt in store.receipts if receipt.id == receipt_id), None)
    is_new = existing is None

    if is_new:
        store.last_receipt_id += 1
        receipt_id = f"receipt_{store.last_receipt_id}"

    next_item_number = store.last_item_id
    built_items: list[ReceiptItemRecord] = []
    for raw_item in item_data:
        item_id = str(raw_item.get("id") or "")
        if not item_id or item_id.startswith("draft-"):
            next_item_number += 1
            item_id = f"item_{next_item_number}"
        built_items.append(_build_receipt_item_record(receipt_id, raw_item, owner_ids, existing_id=item_id))
    store.last_item_id = max(store.last_item_id, next_item_number)

    built_removed_items = [_build_removed_item_record(raw_item, owner_ids) for raw_item in removed_item_data]
    total_amount = _to_decimal(receipt_data["total_amount"], "total_amount")
    calculated_total = sum((item.total_price for item in built_items), start=Decimal("0"))
    if abs(calculated_total - total_amount) > MONEY_TOLERANCE:
        raise ValueError(
            f"total_amount ({total_amount}) does not match the sum of formal items ({calculated_total})."
        )

    default_owner_id = str(receipt_data["default_owner_id"]).strip()
    if default_owner_id not in owner_ids:
        raise ValueError(f"Unknown default_owner_id: {default_owner_id}")

    now = datetime.now(timezone.utc)
    created_at = existing.created_at if existing else now
    reviewed_at = existing.reviewed_at if existing else None
    is_verified = bool(receipt_data.get("is_verified", False))
    if is_verified and reviewed_at is None:
        reviewed_at = now
    # A verified receipt is confirmed by a human: promote the status.
    if is_verified:
        ocr_status = OcrStatus.VERIFIED
    else:
        ocr_status = OcrStatus(str(receipt_data.get("ocr_status") or OcrStatus.PENDING.value).strip())

    image_path = str(receipt_data.get("image_path") or "").strip() or f"manual://{receipt_id}"
    image_hash = str(receipt_data.get("image_hash") or "").strip() or f"manual-hash::{receipt_id}"

    record = ReceiptRecord(
        id=receipt_id,
        merchant=str(receipt_data["merchant"]).strip(),
        purchase_date=_parse_date(str(receipt_data["purchase_date"])),
        currency=str(receipt_data.get("currency") or "EUR").strip().upper(),
        total_amount=total_amount,
        payment_method=(str(receipt_data["payment_method"]).strip() or None) if receipt_data.get("payment_method") is not None else None,
        default_owner_id=default_owner_id,
        owner_mode=OwnerMode(str(receipt_data.get("owner_mode") or "normal").strip()),
        receipt_owner_marker=(str(receipt_data["receipt_owner_marker"]).strip().upper() or None) if receipt_data.get("receipt_owner_marker") else None,
        image_path=image_path,
        image_hash=image_hash,
        ocr_raw_text=receipt_data.get("ocr_raw_text"),
        is_verified=is_verified,
        ocr_status=ocr_status,
        ocr_attempts=int(receipt_data.get("ocr_attempts") or 0),
        ocr_failure_reason=(str(receipt_data["ocr_failure_reason"]).strip() or None) if receipt_data.get("ocr_failure_reason") else None,
        review_notes=(str(receipt_data["review_notes"]).strip() or None) if receipt_data.get("review_notes") else None,
        created_at=created_at,
        updated_at=now,
        reviewed_at=reviewed_at,
        items=built_items,
        removed_items=built_removed_items,
    )

    # 人工确认（verify）后：把图片从 已处理/ 移到 已校对/（按 receipt id 重命名），
    # 并更新记录里的路径。
    if is_verified and image_path and not image_path.startswith("manual://"):
        moved_image = move_verified_receipt_image(paths.project_root, image_path, receipt_id)
        if moved_image:
            record.image_path = moved_image

    if is_new:
        store.receipts.append(record)
    else:
        index = next(index for index, receipt in enumerate(store.receipts) if receipt.id == receipt_id)
        store.receipts[index] = record

    save_receipt_store(store, paths.store_path)
    return record


def delete_receipt(paths: AppPaths, receipt_id: str) -> None:
    store = load_receipt_store(paths.store_path)
    original_count = len(store.receipts)
    store.receipts = [receipt for receipt in store.receipts if receipt.id != receipt_id]
    if len(store.receipts) == original_count:
        raise ValueError(f"Receipt not found: {receipt_id}")
    save_receipt_store(store, paths.store_path)


def build_new_receipt_draft(paths: AppPaths) -> dict[str, Any]:
    _, owners = load_app_state(paths)
    default_owner = next(owner.id for owner in owners.owners if owner.is_me)
    return {
        "id": "",
        "merchant": "",
        "purchase_date": date.today().isoformat(),
        "currency": "EUR",
        "total_amount": "0.00",
        "payment_method": "",
        "default_owner_id": default_owner,
        "owner_mode": OwnerMode.NORMAL.value,
        "receipt_owner_marker": "",
        "image_path": "",
        "image_hash": "",
        "ocr_raw_text": "",
        "is_verified": False,
        "ocr_status": OcrStatus.PENDING.value,
        "ocr_attempts": 0,
        "ocr_failure_reason": "",
        "review_notes": "",
        "items": [],
        "removed_items": [],
    }


def receipt_to_edit_payload(receipt: ReceiptRecord) -> dict[str, Any]:
    return {
        "id": receipt.id,
        "merchant": receipt.merchant,
        "purchase_date": receipt.purchase_date.isoformat(),
        "currency": receipt.currency,
        "total_amount": f"{receipt.total_amount:.2f}",
        "payment_method": receipt.payment_method or "",
        "default_owner_id": receipt.default_owner_id,
        "owner_mode": receipt.owner_mode.value,
        "receipt_owner_marker": receipt.receipt_owner_marker or "",
        "image_path": receipt.image_path,
        "image_hash": receipt.image_hash,
        "ocr_raw_text": receipt.ocr_raw_text or "",
        "is_verified": receipt.is_verified,
        "ocr_status": receipt.ocr_status.value,
        "ocr_attempts": receipt.ocr_attempts,
        "ocr_failure_reason": receipt.ocr_failure_reason or "",
        "review_notes": receipt.review_notes or "",
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "normalized_name": item.normalized_name,
                "category": item.category.value,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
                "total_price": str(item.total_price),
                "owner_id": item.owner_id,
                "owner_marker": item.owner_marker or "",
            }
            for item in receipt.items
        ],
        "removed_items": [
            {
                "name": item.name,
                "normalized_name": item.normalized_name,
                "category": item.category.value,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
                "total_price": str(item.total_price),
                "owner_id": item.owner_id,
                "owner_marker": item.owner_marker or "",
                "reason": item.reason,
                "related_index": item.related_index if item.related_index is not None else "",
            }
            for item in receipt.removed_items
        ],
    }


def open_path(path: str | Path) -> None:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
        return
    webbrowser.open(target.resolve().as_uri())


def open_html_report(path: str | Path) -> None:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    webbrowser.open(target.resolve().as_uri())


def _move_source_to_processed(
    project_root: str | Path,
    image_path: str | Path,
    receipt_id: str,
) -> str | None:
    """把源图片移动到 已处理/ 并按 receipt id 重命名（无论来源在哪里）。

    trigger 成功后调用：不管图片在"未处理"、桌面还是其它位置，都统一归档
    为 {receipt_id}{suffix}，保证后续 verify 能正常流转进 已校对/。图片已经
    在目标位置且名字一致时返回 None（幂等）。返回新路径。
    """
    source = Path(image_path)
    if not source.exists():
        return None
    _, processed_dir, _ = receipt_flow_dirs(project_root)
    target = processed_dir / f"{receipt_id}{source.suffix}"
    try:
        if source.resolve() == target.resolve():
            return None
    except OSError:
        pass

    destination = _build_unique_destination(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return str(destination)


def _update_receipt_image_path(store_path: str | Path, receipt_id: str, new_image_path: str) -> None:
    store = load_receipt_store(store_path)
    for receipt in store.receipts:
        if receipt.id == receipt_id:
            receipt.image_path = new_image_path
            break
    save_receipt_store(store, store_path)


def trigger_ingestion(
    paths: AppPaths,
    image_path: str | Path,
    *,
    use_llm_parser: bool = False,
    use_preprocess: bool = False,
) -> tuple[str, str, dict | None]:
    """Trigger a single receipt ingestion pipeline from the GUI (PRD 8.1).

    Returns (receipt_id, raw_ocr_text, preprocess_info).
    """
    from expense_tracker.pipelines.receipt_ingestion import ingest_receipt_with_retries

    image = Path(image_path)
    if not image.exists():
        raise FileNotFoundError(image)

    from expense_tracker.pipelines.receipt_ingestion import ReceiptAttemptError

    try:
        result = ingest_receipt_with_retries(
            image_path=image,
            owners_path=paths.owners_path,
            store_path=paths.store_path,
            max_attempts=3,
            use_llm_parser=use_llm_parser,
            use_preprocess=use_preprocess,
            failure_output_dir=paths.rejected_dir,
        )
        receipt_id = result.receipt_record.id
        # 无论图片来自哪个目录，处理成功后都统一移入"已处理"并按 receipt id
        # 重命名，保证 verify 后能正常流转进"已校对"。
        moved_image = _move_source_to_processed(paths.project_root, image, receipt_id)
        if moved_image:
            _update_receipt_image_path(paths.store_path, receipt_id, moved_image)
        return receipt_id, result.content, result.preprocess_info
    except ReceiptAttemptError as exc:
        raise ReceiptAttemptError(str(exc), content=exc.content) from exc


def reopen_failed_receipt(paths: AppPaths, failed_index: int) -> str:
    """Re-process a failed receipt by moving its image back to the incoming directory (PRD 8.2)."""
    from expense_tracker.storage.file_index import compute_file_sha256
    from expense_tracker.storage.json_store import load_receipt_store, save_receipt_store

    store = load_receipt_store(paths.store_path)
    if failed_index < 0 or failed_index >= len(store.failed_ocr_records):
        raise IndexError(f"Invalid failed record index: {failed_index}")

    record = store.failed_ocr_records[failed_index]
    archived = Path(record.archived_image_path)

    incoming_dir = paths.project_root / "receipt_input"
    incoming_dir.mkdir(parents=True, exist_ok=True)

    dest = incoming_dir / archived.name
    if dest.exists():
        dest = incoming_dir / f"{archived.stem}_reopen{archived.suffix}"

    dest.write_bytes(archived.read_bytes())

    del store.failed_ocr_records[failed_index]
    save_receipt_store(store, paths.store_path)

    return str(dest)
