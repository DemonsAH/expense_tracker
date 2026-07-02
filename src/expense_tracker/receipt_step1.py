"""Step 1: send a receipt image through local Unlimited-OCR, then parse to JSON."""

from __future__ import annotations

import json
from pathlib import Path

from expense_tracker.ocr_client import run_ocr
from expense_tracker.ocr_parser import parse_ocr_to_extracted_receipt
from expense_tracker.tracing import receipt_traceable


@receipt_traceable(
    name="receipt_step1",
    run_type="chain",
    metadata={"pipeline_stage": "step1_ocr_and_parse"},
)
def run_receipt_step1(
    image_path: str | Path,
    *,
    owners_path: str | Path = "owners.json",
    model: str = "local-unlimited-ocr",
) -> str:
    """Run local OCR on a receipt image and return the ExtractedReceipt JSON.

    This replaces the old SiliconFlow/Qwen API call with the local Unlimited-OCR
    pipeline (OCR -> text -> parser -> ExtractedReceipt JSON).
    """
    image = Path(image_path)
    if not image.exists():
        raise FileNotFoundError(f"Image not found: {image}")

    # Step 1a: Run local OCR
    ocr_text = run_ocr(image)

    # Step 1b: Parse OCR text into ExtractedReceipt
    extracted = parse_ocr_to_extracted_receipt(ocr_text, owners_path=owners_path)

    # Return as JSON string (matching the old interface contract)
    return extracted.model_dump_json()


def image_to_data_url(image_path: str | Path) -> str:
    """Encode a local image as a data URL (kept for compatibility)."""
    import base64
    import mimetypes
    path = Path(image_path)
    mime_type, _ = mimetypes.guess_type(path.name)
    mime_type = mime_type or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"