"""Run a fixed multimodal receipt extraction test with SiliconFlow/Qwen."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from expense_tracker.qwen_receipt_extractor import ValidationError, extract_receipt


def safe_print(message: str) -> None:
    text = message.encode("ascii", errors="backslashreplace").decode("ascii")
    print(text)


def save_outputs(
    *,
    image_path: Path,
    model: str,
    raw_response: dict,
    content: str,
    parsed_json: dict,
) -> tuple[Path, Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = model.replace("/", "_")
    stem = image_path.stem
    output_dir = image_path.parent
    raw_path = output_dir / f"{stem}_{timestamp}_{safe_model}_raw.json"
    text_path = output_dir / f"{stem}_{timestamp}_{safe_model}_content.txt"
    parsed_path = output_dir / f"{stem}_{timestamp}_{safe_model}_receipt.json"
    raw_path.write_text(
        json.dumps(raw_response, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    text_path.write_text(content, encoding="utf-8")
    parsed_path.write_text(
        json.dumps(parsed_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return raw_path, text_path, parsed_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image",
        default="test_receipts/test1.jpg",
        help="Path to the receipt image to test.",
    )
    parser.add_argument(
        "--owners",
        default="owners.json",
        help="Path to owners.json.",
    )
    parser.add_argument(
        "--model",
        default="Qwen/Qwen3.6-27B",
        help="SiliconFlow model name.",
    )
    parser.add_argument(
        "--json-mode",
        action="store_true",
        help="Also send response_format={type: json_object}.",
    )
    args = parser.parse_args()

    image_path = Path(args.image)
    try:
        result = extract_receipt(
            image_path=image_path,
            owners_path=args.owners,
            model=args.model,
            use_json_mode=args.json_mode,
        )
    except ValidationError as exc:
        safe_print(f"VALIDATION_FAILED: {exc}")
        return 1
    except Exception as exc:  # pragma: no cover - integration script
        safe_print(f"REQUEST_FAILED: {exc}")
        return 2

    raw_path, text_path, parsed_path = save_outputs(
        image_path=image_path,
        model=args.model,
        raw_response=result.raw_response,
        content=result.content,
        parsed_json=result.parsed_json,
    )

    print("TEST_SUCCESS")
    print(f"merchant: {result.parsed_json['merchant']}")
    print(f"purchase_date: {result.parsed_json['purchase_date']}")
    print(f"total_amount: {result.parsed_json['total_amount']}")
    print(f"items_count: {len(result.parsed_json['items'])}")
    print(f"raw_response_file: {raw_path}")
    print(f"content_text_file: {text_path}")
    print(f"parsed_json_file: {parsed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
