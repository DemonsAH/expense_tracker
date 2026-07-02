"""Helpers for saving model outputs and parsed receipt artifacts."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from expense_tracker.schemas.extraction import ExtractedReceipt


def build_artifact_paths(
    *,
    image_path: str | Path,
    model: str,
    output_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    image = Path(image_path)
    base_dir = Path(output_dir) if output_dir else image.parent
    base_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = model.replace("/", "_")
    stem = image.stem

    content_path = base_dir / f"{stem}_{timestamp}_{safe_model}_content.txt"
    receipt_path = base_dir / f"{stem}_{timestamp}_{safe_model}_receipt.json"
    return content_path, receipt_path


def save_extraction_artifacts(
    *,
    image_path: str | Path,
    model: str,
    content: str,
    extracted: ExtractedReceipt,
    output_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    content_path, receipt_path = build_artifact_paths(
        image_path=image_path,
        model=model,
        output_dir=output_dir,
    )
    content_path.write_text(content, encoding="utf-8")
    receipt_path.write_text(
        json.dumps(extracted.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return content_path, receipt_path


def save_failure_artifacts(
    *,
    image_path: str | Path,
    model: str,
    failure_reason: str,
    content: str | None = None,
    output_dir: str | Path = "rejected_receipts",
) -> tuple[Path, Path | None, Path]:
    image = Path(image_path)
    base_dir = Path(output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = model.replace("/", "_")
    stem = image.stem
    suffix = image.suffix or ".jpg"

    archived_image_path = base_dir / f"{stem}_{timestamp}_{safe_model}{suffix}"
    failure_json_path = base_dir / f"{stem}_{timestamp}_{safe_model}_failure.json"
    content_path = None
    if content is not None:
        content_path = base_dir / f"{stem}_{timestamp}_{safe_model}_content.txt"
        content_path.write_text(content, encoding="utf-8")

    shutil.copy2(image, archived_image_path)
    failure_payload = {
        "image_path": str(image),
        "archived_image_path": str(archived_image_path),
        "model": model,
        "failure_reason": failure_reason,
        "content_path": str(content_path) if content_path else None,
        "created_at": datetime.now().isoformat(),
    }
    failure_json_path.write_text(
        json.dumps(failure_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return archived_image_path, content_path, failure_json_path


def save_retry_failure_artifacts(
    *,
    image_path: str | Path,
    model: str,
    failures: list[dict],
    output_dir: str | Path = "rejected_receipts",
) -> tuple[Path, list[Path], Path]:
    image = Path(image_path)
    base_dir = Path(output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = model.replace("/", "_")
    stem = image.stem
    suffix = image.suffix or ".jpg"

    archived_image_path = base_dir / f"{stem}_{timestamp}_{safe_model}{suffix}"
    failure_json_path = base_dir / f"{stem}_{timestamp}_{safe_model}_failure.json"
    shutil.copy2(image, archived_image_path)

    content_paths: list[Path] = []
    failure_attempts: list[dict] = []
    for failure in failures:
        attempt_number = failure["attempt_number"]
        content = failure.get("content")
        content_path = None
        if content:
            content_path = base_dir / (
                f"{stem}_{timestamp}_{safe_model}_attempt{attempt_number}_content.txt"
            )
            content_path.write_text(content, encoding="utf-8")
            content_paths.append(content_path)

        failure_attempts.append(
            {
                "attempt_number": attempt_number,
                "failure_reason": failure["failure_reason"],
                "content_path": str(content_path) if content_path else None,
            }
        )

    failure_payload = {
        "image_path": str(image),
        "archived_image_path": str(archived_image_path),
        "model": model,
        "attempts": failure_attempts,
        "created_at": datetime.now().isoformat(),
    }
    failure_json_path.write_text(
        json.dumps(failure_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return archived_image_path, content_paths, failure_json_path
