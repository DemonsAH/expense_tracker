"""Parse OCR HTML table output into ExtractedReceipt JSON.

Handles German supermarket receipts (dm, REWE) with multiple layouts:
  1. dm: 3-column table  (name | price | qty)
  2. REWE text-items: items in pre-table text, prices in 2-col table
  3. REWE merged: complex multi-column with colspan
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from expense_tracker.schemas.enums import ItemCategory, OwnerMode
from expense_tracker.schemas.extraction import ExtractedReceipt, ExtractedReceiptItem
from expense_tracker.schemas.owners import OwnersConfig, load_owners_config

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------
DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
TABLE_RE = re.compile(r"<table>(.*?)</table>", re.DOTALL)
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)

MONEY_RE = re.compile(r"(?:EUR\s*)?(-?\d+[,.]\d{2})", re.IGNORECASE)
WEIGHT_RE = re.compile(r"(\d+[,.]\d{3})\s*kg", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Category keywords
# ---------------------------------------------------------------------------
CATEGORY_MAP: list[tuple[str, ItemCategory]] = [
    ("zahncreme", ItemCategory.PERSONAL_CARE),
    ("zahnpasta", ItemCategory.PERSONAL_CARE),
    ("dusch", ItemCategory.PERSONAL_CARE),
    ("enhaarung", ItemCategory.PERSONAL_CARE),
    ("shampoo", ItemCategory.PERSONAL_CARE),
    ("seife", ItemCategory.PERSONAL_CARE),
    ("creme", ItemCategory.PERSONAL_CARE),
    ("balea", ItemCategory.PERSONAL_CARE),
    ("eisberg", ItemCategory.VEGGIE),
    ("goldmais", ItemCategory.VEGGIE),
    ("mais", ItemCategory.VEGGIE),
    ("karotte", ItemCategory.VEGGIE),
    ("zwiebel", ItemCategory.VEGGIE),
    ("kartoffel", ItemCategory.VEGGIE),
    ("gemuse", ItemCategory.VEGGIE),
    ("hulmenkoh", ItemCategory.VEGGIE),
    ("kohl", ItemCategory.VEGGIE),
    ("banane", ItemCategory.FRUIT),
    ("apfel", ItemCategory.FRUIT),
    ("appeld", ItemCategory.FRUIT),
    ("mango", ItemCategory.FRUIT),
    ("mangiant", ItemCategory.FRUIT),
    ("exotic", ItemCategory.FRUIT),
    ("frutiv", ItemCategory.FRUIT),
    ("pfhisigi", ItemCategory.FRUIT),
    ("pistazie", ItemCategory.SNACKS),
    ("fanta", ItemCategory.DRINK),
    ("green tea", ItemCategory.DRINK),
    ("tee", ItemCategory.DRINK),
    ("hunig", ItemCategory.DRINK),
    ("zena", ItemCategory.DRINK),
    ("pfand", ItemCategory.OTHER),
    ("leergut", ItemCategory.OTHER),
    ("flaschenpfand", ItemCategory.OTHER),
    ("mehrwegpfand", ItemCategory.OTHER),
    ("artikel", ItemCategory.OTHER),
    ("kodak", ItemCategory.OTHER),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_decimal(text: str) -> Decimal:
    text = text.strip().replace("EUR", "").replace("€", "").strip()
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation:
        return Decimal("0")


def _parse_german_date(text: str) -> date | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))


def _guess_category(name: str, normalized_name: str) -> ItemCategory:
    haystack = f"{name} {normalized_name}".lower()
    for keyword, cat in CATEGORY_MAP:
        if keyword in haystack:
            return cat
    return ItemCategory.OTHER


def _normalize_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\d+[,.]\d{3}\s*kg", "", name, flags=re.IGNORECASE)
    name = re.sub(r"×\S+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+[A-Za-z]\s*$", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _is_noise_row(text: str) -> bool:
    """True if this cell text belongs to a non-item row (VAT, footer, etc.)."""
    low = text.lower()
    noise = [
        "summel", "summe", "mwst", "steuer", "brutto", "netto",
        "zahlung erfolgt", "tse-signatur", "tse-", "capt.-ref",
        "approved", "betrag eur", "geg. mastercard", "geg.",
        "mastercard eur", "visa eur",
        "payback", "für diesen einkauf",
        "du sammellst", "offnungszeiten",
        "must-satz",
    ]
    for kw in noise:
        if kw in low:
            return True
    # Match "A= 19,0%" or "B= 7,0%" or "1=19,00%" steuer rows
    if re.search(r"[AB1]=\s*\d+[,.]\d+%", text):
        return True
    return False


# ---------------------------------------------------------------------------
# Format: dm (3-column: name | price | qty)
# ---------------------------------------------------------------------------
def _parse_dm(html_section: str) -> list[dict]:
    """Parse dm-style table rows."""
    items: list[dict] = []
    tables = TABLE_RE.findall(html_section)
    for tbl in tables:
        rows = ROW_RE.findall(tbl)
        for row in rows:
            cells = CELL_RE.findall(row)
            if len(cells) < 2:
                continue
            name = cells[0].strip()
            if _is_noise_row(name):
                continue
            price = _parse_decimal(cells[1])
            if price == 0:
                continue
            # Skip payment rows with negative prices
            if price < 0:
                continue
            items.append({"name": name, "price": price, "marker": None})
    return items


# ---------------------------------------------------------------------------
# Format: REWE text-items (items in text section, prices in simple table)
# ---------------------------------------------------------------------------
def _parse_rewe_text_items(text_section: str, html_section: str) -> list[dict]:
    """Parse REWE where items are listed as text lines before the price table."""
    # Extract item names from text lines
    text_lines = [l.strip() for l in text_section.split("\n") if l.strip()]
    item_lines: list[str] = []
    skip_kws = [
        "str.", "strasse", "aachen", "uid", "datum", "uhrzeit",
        "beleg", "trace", "bezahlung", "tse", "kundenbeleg",
        "markt", "kasse", "bon", "bedien", "ts", "nr.",
        "non-text", "bank:",
        # Noise from OCR
        "pfense", "f00m", "thfv", "b0r1", "kre", "reme",
    ]
    for line in text_lines:
        low = line.lower()
        if any(kw in low for kw in skip_kws):
            continue
        if len(line) > 3 and not line.startswith("www") and "http" not in low:
            # Also skip lines that look like TSE noise
            if re.match(r'^[a-f0-9]{10,}$', low):
                continue
            item_lines.append(line)

    # Extract prices from table
    price_entries: list[dict] = []
    tables = TABLE_RE.findall(html_section)
    for tbl in tables:
        rows = ROW_RE.findall(tbl)
        for row in rows:
            cells = CELL_RE.findall(row)
            raw_text = " ".join(c.strip() for c in cells)
            if _is_noise_row(raw_text):
                continue
            # Iterate cells by index to pair prices with adjacent markers
            for ci, cell in enumerate(cells):
                text = cell.strip()
                m = MONEY_RE.search(text)
                if not m:
                    continue
                price = _parse_decimal(m.group(1))
                if price <= 0:
                    continue
                # Extract marker: first check rest of this cell, then adjacent cell
                marker = None
                rest = text[m.end():].strip()
                marker_m = re.match(r"([A-Za-z])\s*[\\*]?\s*$", rest)
                if marker_m:
                    marker = marker_m.group(1).upper()
                elif ci + 1 < len(cells):
                    adj = cells[ci + 1].strip()
                    if len(adj) <= 3 and adj.replace("*", "").replace(" ", "").isalpha():
                        marker = adj.upper().rstrip("*").strip()
                price_entries.append({"name": "", "price": price, "marker": marker})

    # Match names to prices sequentially (REWE lists items then prices)
    non_pfand_names = [n for n in item_lines if "pfand" not in n.lower()]
    pfand_names = [n for n in item_lines if "pfand" in n.lower()]

    non_pfand_prices = [p for p in price_entries if p["price"] != _parse_decimal("0,25")]
    pfand_prices = [p for p in price_entries if p["price"] == _parse_decimal("0,25")]

    for i, entry in enumerate(non_pfand_prices):
        if i < len(non_pfand_names):
            entry["name"] = non_pfand_names[i]

    for i, entry in enumerate(pfand_prices):
        if i < len(pfand_names):
            entry["name"] = pfand_names[i]
        else:
            entry["name"] = f"PFAND {entry['price']} EURO"

    return non_pfand_prices + pfand_prices


# ---------------------------------------------------------------------------
# Format: REWE merged (complex multi-column with colspan)
# ---------------------------------------------------------------------------
def _parse_rewe_merged(html_section: str) -> list[dict]:
    """Parse complex REWE tables where items and prices are merged into cells."""
    items: list[dict] = []
    tables = TABLE_RE.findall(html_section)
    for tbl in tables:
        rows = ROW_RE.findall(tbl)
        for row in rows:
            cells = CELL_RE.findall(row)
            raw_text = " ".join(c.strip() for c in cells)
            if _is_noise_row(raw_text):
                continue

            # Skip rows that are just headers or empty prices
            clean_text = re.sub(r"<img>", "", raw_text).strip()
            if not clean_text or len(clean_text) < 3:
                continue

            # Find all money values in this row
            money_matches = list(MONEY_RE.finditer(raw_text))
            if not money_matches:
                continue

            # Filter: keep only money matches that look like item prices,
            # not per-kg references or noise
            valid_matches: list[tuple[int, int, str, str]] = []  # (start, end, raw_text, name_context)
            # Collect all names before the first money
            name_segments: list[str] = []

            prev = 0
            for m in money_matches:
                val = m.group(1)
                # Check context: is this preceded by "EUR/kg"? Skip if so
                context_before = raw_text[max(0, m.start() - 20):m.start()]
                if "EUR/kg" in context_before or "€/kg" in context_before:
                    prev = m.end()
                    continue

                # Skip values that look like weight numbers (3 decimal places in per-kg context)
                if "kg" in raw_text[:m.start() + 30]:
                    # This might be a weight, not a price
                    weight_match = re.search(
                        r"(\d+[,.]\d{3})\s*kg", raw_text[:m.start() + 20], re.IGNORECASE
                    )
                    if weight_match and m.group(1) == weight_match.group(1):
                        prev = m.end()
                        continue

                segment = raw_text[prev:m.start()].strip()
                prev = m.end()
                name_segments.append(segment)

                # Extract marker after price (first word or single letter)
                after = raw_text[m.end():].strip()
                marker = None
                words = after.split()
                if words:
                    first_word = words[0].rstrip("*").strip()
                    if len(first_word) == 1 and first_word.isalpha():
                        marker = first_word.upper()

                valid_matches.append((m.start(), m.end(), val, segment, marker))

            if not valid_matches:
                continue

            for vm in valid_matches:
                val = vm[2]
                segment = vm[3]
                marker = vm[4]
                price = _parse_decimal(val)
                if price == 0:
                    continue

                name = segment if segment else raw_text[:vm[0]].strip()
                # Clean up name
                name = re.sub(r"[,.]\d{3}", "", name).strip()  # remove 0,750
                name = re.sub(r"×\S+", "", name).strip()  # remove ×Sofortastone
                name = re.sub(r"\d+[,.]\d{2}\s*EUR/kg", "", name).strip()  # remove per-kg price
                name = re.sub(r"\s+", " ", name).strip()

                if not name or len(name) < 2:
                    continue

                items.append({"name": name, "price": price, "marker": marker})

    return items


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------
def parse_ocr_to_extracted_receipt(
    ocr_text: str,
    *,
    owners_path: str | Path = "owners.json",
) -> ExtractedReceipt:
    """Parse OCR output text into an ExtractedReceipt."""
    owners = load_owners_config(owners_path)
    owner_ids = {o.id for o in owners.owners}
    marker_to_id = {o.marker.upper(): o.id for o in owners.owners}
    me_id = next((o.id for o in owners.owners if o.is_me), next(iter(owner_ids)) if owner_ids else "me")

    # Split text section and HTML section
    table_start = ocr_text.find("<table>")
    if table_start >= 0:
        text_section = ocr_text[:table_start].strip()
        html_section = ocr_text[table_start:].strip()
    else:
        text_section = ocr_text.strip()
        html_section = ""

    # Detect format
    is_dm = "dm-drogerie" in text_section.lower() or "dm\n" in text_section.lower()

    if is_dm:
        merchant = "dm-Drogerie markt"
        raw_items = _parse_dm(html_section)
    elif _has_many_item_names_in_text(text_section):
        # REWE text-items: many item name lines before table
        merchant = _extract_merchant(text_section)
        raw_items = _parse_rewe_text_items(text_section, html_section)
    elif html_section and "colspan" in html_section:
        # REWE merged: few text lines, complex table with colspan
        merchant = _extract_merchant(text_section)
        raw_items = _parse_rewe_merged(html_section)
    else:
        merchant = _extract_merchant(text_section)
        raw_items = _parse_rewe_text_items(text_section, html_section)

    # Extract date
    purchase_date = _extract_date(ocr_text)

    # Extract total
    total_amount = _extract_total_amount(text_section, html_section)

    # Extract payment method
    payment_method = _extract_payment_method(html_section)

    # Owner mode detection
    owner_mode, default_owner_id, receipt_owner_marker = _detect_owner_mode(
        text_section, raw_items, marker_to_id, me_id, owner_ids
    )

    # Build items
    items: list[ExtractedReceiptItem] = _build_items(raw_items, marker_to_id, default_owner_id, me_id)

    if not items:
        raise ValueError("No items parsed from OCR output")

    # Fallback total
    if total_amount == 0:
        total_amount = sum(item.total_price for item in items)

    return ExtractedReceipt(
        merchant=merchant,
        purchase_date=purchase_date,
        currency="EUR",
        total_amount=total_amount,
        payment_method=payment_method,
        owner_mode=owner_mode,
        default_owner_id=default_owner_id,
        receipt_owner_marker=receipt_owner_marker,
        items=items,
    )


def _has_many_item_names_in_text(text_section: str) -> bool:
    """Check if text section has many item-like lines (REWE text-items format)."""
    lines = [l.strip() for l in text_section.split("\n") if l.strip()]
    skip_kws = [
        "str.", "strasse", "aachen", "uid", "datum", "uhrzeit",
        "beleg", "trace", "bezahlung", "tse", "kur",
        "markt", "kasse", "bon", "nr.", "non-text",
    ]
    item_like = 0
    for line in lines:
        low = line.lower()
        if any(kw in low for kw in skip_kws):
            continue
        if len(line) > 3 and not line.startswith("www"):
            if not re.match(r'^[a-f0-9]{10,}$', low):
                item_like += 1
    return item_like >= 5


def _extract_merchant(text_section: str) -> str:
    lines = text_section.split("\n")
    clean = [l.strip() for l in lines if l.strip() and len(l.strip()) > 3]
    for line in clean:
        low = line.lower()
        if "dm-drogerie" in low:
            return "dm-Drogerie markt"
    # REWE detection
    for line in clean:
        low = line.lower()
        if "rewe" in low:
            return "REWE"
    # "REMÉ" is OCR misread of "REWE"
    for line in clean:
        if "rem" in line.lower() and len(line) < 10:
            return "REWE"
    if clean:
        return clean[0]
    return "Unknown"


def _extract_date(ocr_text: str) -> date:
    # "Datum:" pattern
    m = re.search(r"Datum:\s*(\d{2})\.(\d{2})\.(\d{4})", ocr_text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    # First DD.MM.YYYY in text
    m = DATE_RE.search(ocr_text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return date.today()


def _extract_total_amount(text_section: str, html_section: str) -> Decimal:
    combined = f"{text_section}\n{html_section}"
    # Look for "SUMME" or "SUM" followed by EUR amount
    m = re.search(r"(?:SUMME|SUMMEL|SUM)\s*(?:EUR\s*)?(\d+[,.]\d{2})", combined, re.IGNORECASE)
    if m:
        return _parse_decimal(m.group(1))
    return Decimal("0")


def _extract_payment_method(html_section: str) -> str | None:
    for pat, method in [
        (r"Master[Cc]ard", "MasterCard"),
        (r"Visa", "Visa"),
        (r"EC-Karte|EC\s+Karte|girocard", "EC-Karte"),
        (r"Bar", "Bar"),
    ]:
        if re.search(pat, html_section):
            return method
    return None


def _detect_owner_mode(
    text_section: str,
    raw_items: list[dict],
    marker_to_id: dict[str, str],
    me_id: str,
    owner_ids: set[str],
) -> tuple[OwnerMode, str, str | None]:
    owner_mode = OwnerMode.NORMAL
    default_owner_id = me_id
    receipt_owner_marker = None

    # Check for @M style markers in text
    m = re.search(r"@([A-Za-z])", text_section)
    if m:
        raw = m.group(1).upper()
        receipt_owner_marker = raw
        if raw in marker_to_id:
            default_owner_id = marker_to_id[raw]
            owner_mode = OwnerMode.RECEIPT_OWNER

    # If items have individual markers, upgrade to item_owner
    has_markers = any(
        it["marker"] and it["marker"] in marker_to_id
        for it in raw_items
    )
    if has_markers and owner_mode == OwnerMode.NORMAL:
        owner_mode = OwnerMode.ITEM_OWNER

    return owner_mode, default_owner_id, receipt_owner_marker


def _build_items(
    raw_items: list[dict],
    marker_to_id: dict[str, str],
    default_owner_id: str,
    me_id: str,
) -> list[ExtractedReceiptItem]:
    items: list[ExtractedReceiptItem] = []
    for raw in raw_items:
        name = raw["name"] if raw["name"] else "Unknown Item"
        normalized = _normalize_name(name)
        category = _guess_category(name, normalized)
        total_price = raw["price"]
        quantity = Decimal("1")
        unit_price = total_price

        # Weight
        wm = WEIGHT_RE.search(name, re.IGNORECASE)
        if wm:
            quantity = _parse_decimal(wm.group(1))
            if quantity != 0:
                unit_price = total_price / quantity

        # Owner
        item_marker = raw["marker"]
        if item_marker:
            item_marker = item_marker.strip().upper().rstrip("*").strip()
        owner_id = (
            marker_to_id.get(item_marker, default_owner_id)
            if item_marker and item_marker in marker_to_id
            else default_owner_id
        )

        items.append(
            ExtractedReceiptItem(
                name=name,
                normalized_name=normalized,
                category=category,
                quantity=max(quantity, Decimal("0.001")),
                unit_price=abs(unit_price),
                total_price=total_price,
                owner_id=owner_id,
                owner_marker=item_marker,
            )
        )
    return items