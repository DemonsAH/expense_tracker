"""Fixed SiliconFlow/Qwen receipt extraction flow for multimodal testing."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request


CATEGORY_VALUES = (
    "SNACKS",
    "PERSONAL_CARE",
    "HOUSEHOLD",
    "DRINK",
    "MEAT",
    "VEGGIE",
    "FRUIT",
    "OTHER",
    "DINING",
)

OWNER_MODE_VALUES = ("normal", "receipt_owner", "item_owner")

SYSTEM_PROMPT = (
    "You are a receipt extraction engine. "
    "Return a single JSON object only. "
    "Do not output markdown, explanations, comments, or code fences."
)


@dataclass
class ExtractResult:
    raw_response: dict[str, Any]
    content: str
    parsed_json: dict[str, Any]


class ValidationError(ValueError):
    """Raised when extracted data does not match the required schema."""


def load_dotenv_file(dotenv_path: str | Path = ".env") -> None:
    path = Path(dotenv_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_owners(owners_path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(owners_path).read_text(encoding="utf-8"))
    owners = data.get("owners")
    if not isinstance(owners, list) or not owners:
        raise ValidationError("owners.json must contain a non-empty 'owners' array.")
    return owners


def build_prompt(owners: list[dict[str, Any]]) -> str:
    owner_lines = []
    for owner in owners:
        owner_lines.append(
            f'- id="{owner["id"]}", marker="{owner["marker"]}", '
            f'name="{owner["name"]}", is_me={str(owner["is_me"]).lower()}'
        )

    allowed_owner_ids = ", ".join(owner["id"] for owner in owners)
    categories = ", ".join(CATEGORY_VALUES)

    return f"""你将看到一张购物小票图片。请直接提取并返回一个 JSON 对象。

归属人配置：
{chr(10).join(owner_lines)}

要求：
1. 只输出 JSON，不要输出任何解释、注释或代码块。
2. 所有金额、数量使用数字。
3. 日期格式必须是 YYYY-MM-DD。
4. currency 固定为 EUR。
5. owner_mode 只能是：normal, receipt_owner, item_owner。
6. default_owner_id 和 owner_id 只能从这些 id 中选择：{allowed_owner_ids}。
7. category 只能是：{categories}。
8. 识别整单归属标记和商品行末归属标记。
9. 保留小票上所有条目，包括取消项 (Storno/Sofortstorno) 和负数项。取消项用负的 total_price 表示。
10. 若无法确定 payment_method 或 owner_marker，可填 null。
11. items 中每个商品都必须有 name、normalized_name、category、quantity、unit_price、total_price、owner_id、owner_marker。
12. 输出必须符合以下结构：

{{
  "merchant": "string",
  "purchase_date": "YYYY-MM-DD",
  "currency": "EUR",
  "total_amount": 0.0,
  "payment_method": "string or null",
  "owner_mode": "normal | receipt_owner | item_owner",
  "default_owner_id": "string",
  "receipt_owner_marker": "string or null",
  "items": [
    {{
      "name": "string",
      "normalized_name": "string",
      "category": "SNACKS | PERSONAL_CARE | HOUSEHOLD | DRINK | MEAT | VEGGIE | FRUIT | OTHER | DINING",
      "quantity": 0.0,
      "unit_price": 0.0,
      "total_price": 0.0,
      "owner_id": "string",
      "owner_marker": "string or null"
    }}
  ]
}}"""


def image_to_data_url(image_path: str | Path) -> str:
    path = Path(image_path)
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def make_payload(
    *,
    model: str,
    prompt: str,
    image_data_url: str,
    use_json_mode: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url, "detail": "high"},
                    },
                    {"type": "text", "text": prompt},
                ],
            },
        ],
        "stream": False,
        "temperature": 0.1,
        "top_p": 0.1,
        "max_tokens": 3000,
    }

    if use_json_mode:
        payload["response_format"] = {"type": "json_object"}

    return payload


def call_siliconflow(
    *,
    api_key: str,
    base_url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_response_content(response_json: dict[str, Any]) -> str:
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValidationError(f"Unexpected API response shape: {response_json}") from exc

    if not isinstance(content, str) or not content.strip():
        raise ValidationError("Model returned empty content.")

    return strip_code_fences(content)


def validate_number(value: Any, field_name: str) -> None:
    if not isinstance(value, (int, float)):
        raise ValidationError(f"Field '{field_name}' must be a number.")


def validate_receipt_json(data: dict[str, Any], owners: list[dict[str, Any]]) -> None:
    required_top_level = {
        "merchant": str,
        "purchase_date": str,
        "currency": str,
        "total_amount": (int, float),
        "payment_method": (str, type(None)),
        "owner_mode": str,
        "default_owner_id": str,
        "receipt_owner_marker": (str, type(None)),
        "items": list,
    }
    for field_name, expected_type in required_top_level.items():
        if field_name not in data:
            raise ValidationError(f"Missing top-level field '{field_name}'.")
        if not isinstance(data[field_name], expected_type):
            raise ValidationError(f"Field '{field_name}' has invalid type.")

    if data["currency"] != "EUR":
        raise ValidationError("currency must be 'EUR'.")
    if data["owner_mode"] not in OWNER_MODE_VALUES:
        raise ValidationError("owner_mode is invalid.")

    owner_ids = {owner["id"] for owner in owners}
    if data["default_owner_id"] not in owner_ids:
        raise ValidationError("default_owner_id is not in owners.json.")

    items = data["items"]
    if not items:
        raise ValidationError("items must not be empty.")

    required_item_fields = {
        "name": str,
        "normalized_name": str,
        "category": str,
        "quantity": (int, float),
        "unit_price": (int, float),
        "total_price": (int, float),
        "owner_id": str,
        "owner_marker": (str, type(None)),
    }
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"items[{index}] must be an object.")
        for field_name, expected_type in required_item_fields.items():
            if field_name not in item:
                raise ValidationError(f"items[{index}] missing field '{field_name}'.")
            if not isinstance(item[field_name], expected_type):
                raise ValidationError(f"items[{index}].{field_name} has invalid type.")

        if item["category"] not in CATEGORY_VALUES:
            raise ValidationError(f"items[{index}].category is invalid.")
        if item["owner_id"] not in owner_ids:
            raise ValidationError(f"items[{index}].owner_id is not in owners.json.")
        validate_number(item["quantity"], f"items[{index}].quantity")
        validate_number(item["unit_price"], f"items[{index}].unit_price")
        validate_number(item["total_price"], f"items[{index}].total_price")


def extract_receipt(
    *,
    image_path: str | Path,
    owners_path: str | Path,
    model: str,
    use_json_mode: bool = False,
) -> ExtractResult:
    load_dotenv_file()

    api_key = os.environ.get("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValidationError("SILICONFLOW_API_KEY is missing.")

    base_url = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    owners = load_owners(owners_path)
    prompt = build_prompt(owners)
    image_data_url = image_to_data_url(image_path)
    payload = make_payload(
        model=model,
        prompt=prompt,
        image_data_url=image_data_url,
        use_json_mode=use_json_mode,
    )
    response_json = call_siliconflow(api_key=api_key, base_url=base_url, payload=payload)
    content = parse_response_content(response_json)
    parsed_json = json.loads(content)
    validate_receipt_json(parsed_json, owners)
    return ExtractResult(
        raw_response=response_json,
        content=content,
        parsed_json=parsed_json,
    )

