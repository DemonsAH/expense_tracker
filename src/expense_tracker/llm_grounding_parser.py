"""LLM-based parser: OCR text -> ExtractedReceipt JSON.

This module is a standalone alternative to the local GLM-OCR pipeline
(ocr_client + ocr_parser). It sends the raw OCR text produced by the
GLM-OCR model to DeepSeek's official API (deepseek-v4-flash) and asks it to
convert the receipt layout into the ExtractedReceipt schema.

The implementation depends only on the ``openai`` SDK and the project schemas;
it does not import anything from the OCR client or parser.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from openai import OpenAI

from expense_tracker.config import get_required_env, load_dotenv_file
from expense_tracker.schemas.extraction import ExtractedReceipt
from expense_tracker.schemas.owners import OwnersConfig, load_owners_config

DEEPSEEK_MODEL_ENV = "EXPENSE_TRACKER_LLM_MODEL"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_BASE_URL_ENV = "DEEPSEEK_BASE_URL"

# Official DeepSeek API endpoint.
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
# DeepSeek V4-Flash defaults to thinking mode; we disable it explicitly.
THINKING_DISABLED = {"type": "disabled"}

MAX_OUTPUT_TOKENS = 4096


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
GROUNDING_RECEIPT_PROMPT = """You are a receipt data extractor for German supermarket receipts (REWE, dm) and drugstore receipts.

Below is the OCR grounding output from a receipt image. It consists of text blocks, each wrapped as:

  <|det|>label [x1, y1, x2, y2]<|/det|>text

where:
- "label" is a block type hint (e.g. "text", "title")
- [x1, y1, x2, y2] is the bounding box of the text on the original image (top-left to bottom-right, in image pixel coordinates)
- "text" is the recognized text inside that box

Use the coordinates to reconstruct the layout:
- Blocks on the same row share a similar y1/y2 range -> they belong to the same line.
- On item lines, the product name is on the left and the price / quantity / owner marker are on the right (same row).
- The total ("SUMME EUR", "Summe", "Gesamtbetrag") and payment lines are usually at the bottom.
- An "@" marker at the top (e.g. "@A", "@B") marks the whole receipt's owner.

CRITICAL layout rules for pairing names with prices:
- Match each product-name block (left side, smaller x1) with the price block on the SAME row (right side, similar y1/y2). Do not guess a price for a product unless a price block exists on its row.
- A price may appear inside an HTML <table> (each <tr> is one row) OR as a separate text block on the right side. Read prices from BOTH sources.
- Prices that belong to "SUMME"/"Summe" rows or the payment line are NOT item prices - the SUMME amount is the total_amount, never an item.
- If a row has no visible price, still keep the product but only if you can infer its price confidently; otherwise exclude it rather than inventing one.
- REWE receipts: the "@" or single letter after a price (e.g. "2,49 B") is an owner marker, not part of the price.

PRICE INSIDE THE NAME:
- Some product names already CONTAIN their own price, especially Pfand/deposit lines: e.g. "PFAND 0,25 EURO" means a 0.25 deposit charged, "2 Stk x 0.25" means 2 pieces at 0.25 each (total 0.50). Use that embedded amount as the item's total_price/unit_price - do not re-derive it from the right-side price column and do not guess another value.
- "PFAND ..." / "Pfand ..." / "Leergut ..." lines: the embedded price is the actual amount counted in the receipt total.

Extract the structured information and return ONLY a valid JSON object - no explanations, no markdown fences, no code blocks.

The JSON MUST contain ALL of these top-level fields: merchant, purchase_date, currency, total_amount, payment_method, owner_mode, default_owner_id, receipt_owner_marker, items.
Each item in "items" MUST contain ALL of these fields: name, normalized_name, category, quantity, unit_price, total_price, owner_id, owner_marker.

Field rules:

- merchant: the store name. For REWE use "REWE"; for dm use "dm-Drogerie markt".
- purchase_date: convert German date formats (DD.MM.YYYY) to YYYY-MM-DD.
- currency: always "EUR".
- total_amount: the final positive sum from the "SUMME" / "Summe" / "Gesamtbetrag" line.
- payment_method: "MasterCard", "Visa", "EC-Karte", "Bar", ... or null if not visible.
- owner_mode: "normal" if no @marker and no per-item markers; "receipt_owner" if an @A/@B receipt marker exists; "item_owner" if items carry per-item owner markers (e.g. a single letter A/B/C after the price).
- default_owner_id: always "me" (the application resolves it to the configured personal owner).
- receipt_owner_marker: the @marker letter (A/B/C/...) if any, else null.

Per-item rules:
- name: the raw product name exactly as printed.
- normalized_name: cleaned name - remove weights ("0,750 kg"), quantity markers ("2x"), trailing owner letters (" A"), keep the core product description.
- category: one of SNACKS, PERSONAL_CARE, HOUSEHOLD, DRINK, MEAT, VEGGIE, FRUIT, DINING, OTHER (OTHER for Pfand/Leergut and anything unclassifiable).
- quantity: > 0. Standard items: 1. For weighed items ("0,750 kg"): the weight in kg. For multi-packs ("2x0.5L"): 2.
- unit_price: price per unit; for weighed items the price per kg; for standard items total_price / quantity.
- total_price: the line price. German comma decimals become dots ("1,29" -> 1.29). Storno/Sofortstorno/Leergut lines are negative.
- owner_id: always "me" unless the row shows an explicit owner letter, in which case use "me" for the personal owner's letter and keep the letter in owner_marker.
- owner_marker: the per-item owner letter if any (e.g. "A", "B"), else null.

EXAMPLE OUTPUT:
{{
  "merchant": "REWE",
  "purchase_date": "2026-05-04",
  "currency": "EUR",
  "total_amount": 12.77,
  "payment_method": "MasterCard",
  "owner_mode": "normal",
  "default_owner_id": "me",
  "receipt_owner_marker": null,
  "items": [
    {{
      "name": "BANANE BIO 0,750 kg",
      "normalized_name": "BANANE BIO",
      "category": "FRUIT",
      "quantity": 0.75,
      "unit_price": 1.79,
      "total_price": 1.34,
      "owner_id": "me",
      "owner_marker": null
    }}
  ]
}}

GROUNDING OUTPUT:
{grounding_text}"""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
def build_deepseek_client() -> OpenAI:
    """Build an OpenAI client pointed at the official DeepSeek API.

    Reads DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, and EXPENSE_TRACKER_LLM_MODEL
    from the environment / .env file.
    """
    load_dotenv_file()
    api_key = get_required_env(DEEPSEEK_API_KEY_ENV)
    base_url = os.environ.get(DEEPSEEK_BASE_URL_ENV, DEFAULT_DEEPSEEK_BASE_URL)
    return OpenAI(api_key=api_key, base_url=base_url)


def _resolve_me_placeholder(payload: dict, owners: OwnersConfig) -> None:
    """Replace the "me" placeholder owner ids with the configured is_me owner id."""
    me_id = next((o.id for o in owners.owners if o.is_me), None)
    if me_id is None or me_id == "me":
        return

    def _resolve(value):
        return me_id if value == "me" else value

    if payload.get("default_owner_id") == "me":
        payload["default_owner_id"] = me_id
    for item in payload.get("items", []):
        if item.get("owner_id") == "me":
            item["owner_id"] = me_id


def parse_grounding_with_deepseek(
    grounding_text: str,
    *,
    owners_path: str | Path = "owners.json",
) -> ExtractedReceipt:
    """Send grounding blocks to DeepSeek V4-Flash and return an ExtractedReceipt.

    Thinking mode is disabled so the model emits the JSON directly.
    """
    owners = load_owners_config(owners_path)
    client = build_deepseek_client()
    model = os.environ.get(
        DEEPSEEK_MODEL_ENV, "deepseek-v4-flash"
    ).strip()

    prompt = GROUNDING_RECEIPT_PROMPT.replace("{grounding_text}", grounding_text)

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKENS,
        extra_body={"thinking": THINKING_DISABLED},
    )

    content = (response.choices[0].message.content or "").strip()

    # Strip markdown fences: keep the JSON between the first { and the last }.
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start:end + 1]

    data = json.loads(content)

    # Safe defaults for fields the model sometimes omits.
    data.setdefault("owner_mode", "normal")
    data.setdefault("default_owner_id", "me")
    data.setdefault("receipt_owner_marker", None)
    data.setdefault("payment_method", None)

    # Fallback for a missing/null purchase_date: extract a German date
    # (DD.MM.YYYY) from the raw OCR text; if none is found, use today.
    purchase_date = data.get("purchase_date")
    if not purchase_date:
        data["purchase_date"] = _extract_date_from_text(grounding_text)

    for item in data.get("items", []):
        if not item.get("name") and item.get("normalized_name"):
            item["name"] = item["normalized_name"]
        item.setdefault("owner_id", "me")
        item.setdefault("owner_marker", None)

    _resolve_me_placeholder(data, owners)

    return ExtractedReceipt.model_validate(data)


def _extract_date_from_text(text: str) -> str:
    """Extract the first German DD.MM.YYYY date from OCR text as YYYY-MM-DD.

    Falls back to today's date (ISO format) if no usable date is found.
    """
    # Prefer a date following "Datum:" when present.
    m = re.search(r"Datum:\s*(\d{2})\.(\d{2})\.(\d{4})", text)
    if not m:
        m = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", text)
    if not m:
        return date.today().isoformat()
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return date.today().isoformat()
