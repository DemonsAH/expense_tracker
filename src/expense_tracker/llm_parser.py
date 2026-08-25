"""LLM-based OCR text parser — converts raw OCR HTML text into ExtractedReceipt JSON."""

from __future__ import annotations

import json
import re

from expense_tracker.llm_client import build_chat_model
from expense_tracker.schemas.extraction import ExtractedReceipt

RECEIPT_PARSE_PROMPT = """You are a receipt data extractor for German supermarket receipts, drugstore receipts (dm, Rossmann), and restaurant bills.

Below is the raw OCR output from a receipt. It may contain plain text lines (store name, address, date) followed by an HTML <table> with rows of <tr><td>...</td></tr> cells.

Extract the structured information and return ONLY a valid JSON object — no explanations, no markdown fences, no code blocks.

The JSON MUST contain ALL of these top-level fields: merchant, purchase_date, currency, total_amount, payment_method, owner_mode, default_owner_id, receipt_owner_marker, items.
Each item in "items" MUST contain ALL of these fields: name, normalized_name, category, quantity, unit_price, total_price, owner_id, owner_marker.

━━━ CATEGORIES ━━━

Assign each item ONE of these categories based on what the product is:

- SNACKS: Chips, candy, chocolate, nuts, cookies, crackers, ice cream, cakes, pastries, baked goods from supermarket bakery, gum, packaged desserts, cereal bars. (零食、糖果、薯片、巧克力、烘焙食品、冰淇淋)
- PERSONAL_CARE: Shampoo, soap, body wash, toothpaste, deodorant, cosmetics, skincare, sanitary products, toilet paper, tissues, razors, diapers, hand cream, sunscreen. (个人护理、洗发水、肥皂、化妆品、卫生纸)
- HOUSEHOLD: Cleaning supplies, detergent, sponges, trash bags, light bulbs, batteries, kitchenware, stationery, candles, pet food/supplies, aluminum foil, plastic wrap. (家居清洁、厨房用品、文具、宠物食品、电池)
- DRINK: Water (still/sparkling), soda, juice, beer, wine, spirits, coffee beans/grounds, tea, milk, plant milk, energy drinks. (饮料、水、啤酒、咖啡、牛奶、果汁)
- MEAT: Beef, pork, chicken, turkey, fish, seafood, sausage, cold cuts, deli meats, minced meat, bacon. (肉类、香肠、鱼、家禽)
- VEGGIE: Fresh vegetables, salad greens, herbs, mushrooms, tofu, canned vegetables, frozen vegetables, potatoes, onions, garlic, cucumbers, peppers. (蔬菜、沙拉、豆腐、蘑菇)
- FRUIT: Apples, bananas, oranges, berries, grapes, melons, lemons, canned fruit, dried fruit. (水果、浆果)
- DINING: Restaurant meals, takeout orders, delivery, cafeteria purchases, bakery items from a standalone bakery, Döner, pizza by slice, food court items. (餐厅就餐、外卖、面包店)
- OTHER: Anything that does not fit the above categories (e.g., tobacco, magazines, gift cards, non-food items without a clear category, deposits/Pfand on bottles, donation/add-on items). (其他无法归类的商品)

━━━ FIELD RULES ━━━

Toplevel fields:
- merchant: The store/restaurant name. For REWE: "REWE" or "Rewe". For dm: "dm-drogerie markt". Extract from the first few text lines.
- purchase_date: Convert German date formats (DD.MM.YYYY) to YYYY-MM-DD. Use the date found after "Datum:" or in the header.
- currency: Always "EUR".
- total_amount: The final sum (after "SUMME EUR", "Summe", "Total", "Gesamtbetrag", "Rechnungsbetrag"). Must be a positive number.
- payment_method: Look for "MasterCard", "Visa", "EC-Karte", "Maestro", "Bar", "Girocard", "Payback", "American Express". If nothing found, use null.
- owner_mode: Always "normal".
- default_owner_id: Always "me".
- receipt_owner_marker: Always null.

Per-item fields (MANDATORY — every item MUST have all of these):
- name: The raw product name as printed on the receipt, e.g. "KODAK Gold 200 36". DO NOT skip this field.
- normalized_name: Clean the product name — remove weights ("0,750 kg"), quantity markers ("2x"), trailing single letters (" A", " B"), keep the core product description. e.g. "KODAK Gold 200 36".
- category: One of SNACKS, PERSONAL_CARE, HOUSEHOLD, DRINK, MEAT, VEGGIE, FRUIT, DINING, OTHER.
- quantity: A number > 0. For standard items use 1.0. For weighed items parse the weight in kg. For multi-packs like "2x0.5L", quantity = 2.
- unit_price: Price per single unit. For weighed items this is price/kg. For standard items unit_price ≈ total_price / quantity.
- total_price: The line price from the receipt. German decimal comma (,) must become a dot (.) — "1,29" → 1.29.
- owner_id: Always "me".
- owner_marker: Always null.

Special cases:
- Storno / Sofortstorno (cancellations): use negative total_price, keep the item.
- Pfand / Leergut (deposit): real items, category = OTHER.
- Leergut (returned empties): negative total_price.
- Weighed items (e.g. "0,750 kg"): quantity = 0.750, unit_price = price per kg.

EXAMPLE OUTPUT:
{
  "merchant": "dm-drogerie markt",
  "purchase_date": "2026-07-01",
  "currency": "EUR",
  "total_amount": 15.97,
  "payment_method": "Bar",
  "owner_mode": "normal",
  "default_owner_id": "me",
  "receipt_owner_marker": null,
  "items": [
    {
      "name": "KODAK Gold 200 36",
      "normalized_name": "KODAK Gold 200 36",
      "category": "OTHER",
      "quantity": 1.0,
      "unit_price": 15.97,
      "total_price": 15.97,
      "owner_id": "me",
      "owner_marker": null
    }
  ]
}

OCR OUTPUT:
{ocr_text}"""


def parse_ocr_with_llm(ocr_text: str) -> ExtractedReceipt:
    """Send OCR text to the LLM and return a validated ExtractedReceipt.

    Uses the model specified in EXPENSE_TRACKER_LLM_MODEL (default: DeepSeek-V4-Flash).
    """
    llm = build_chat_model()
    prompt = RECEIPT_PARSE_PROMPT.replace("{ocr_text}", ocr_text)

    response = llm.invoke(prompt)
    content = response.content.strip()

    # Strip markdown fences — robust: find the JSON between first { and last }
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start:end + 1]

    data = json.loads(content)

    # Fixup: LLM sometimes omits fields — fill in safe defaults
    data.setdefault("owner_mode", "normal")
    data.setdefault("default_owner_id", "me")
    data.setdefault("receipt_owner_marker", None)
    data.setdefault("payment_method", None)

    for item in data.get("items", []):
        # If name is missing but normalized_name exists, copy it
        if not item.get("name") and item.get("normalized_name"):
            item["name"] = item["normalized_name"]
        item.setdefault("owner_id", "me")
        item.setdefault("owner_marker", None)

    return ExtractedReceipt.model_validate(data)