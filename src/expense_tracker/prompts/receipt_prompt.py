"""Prompt builder for structured receipt extraction."""

from __future__ import annotations

import json
from pathlib import Path


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


def load_owners(owners_path: str | Path) -> list[dict]:
    data = json.loads(Path(owners_path).read_text(encoding="utf-8"))
    owners = data.get("owners")
    if not isinstance(owners, list) or not owners:
        raise ValueError("owners.json must contain a non-empty 'owners' array.")
    return owners


def build_receipt_prompt(*, owners_path: str | Path = "owners.json") -> str:
    owners = load_owners(owners_path)
    owner_lines = [
        (
            f'- id="{owner["id"]}", marker="{owner["marker"]}", '
            f'name="{owner["name"]}", is_me={str(owner["is_me"]).lower()}'
        )
        for owner in owners
    ]
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

