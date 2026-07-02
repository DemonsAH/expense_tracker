"""Debug parser for test2."""
import sys
sys.path.insert(0, "src")
from pathlib import Path
from expense_tracker.ocr_parser import (
    _extract_text_section, _parse_rewe_text_items, _extract_merchant,
    _extract_total_amount, _extract_date, _extract_payment_method,
)

text = Path("test_receipts/test2_ocr_result.txt").read_text(encoding="utf-8")
ts, hs = None, None
table_start = text.find("<table>")
if table_start >= 0:
    ts = text[:table_start].strip()
    hs = text[table_start:].strip()
else:
    ts = text.strip()
    hs = ""

print(f"table_start={table_start}")
print(f"text_section ({len(ts)} chars):")
print(ts)
print(f"\nhtml_section ({len(hs)} chars, first 500):")
print(hs[:500])

print(f"\nmerchant: {_extract_merchant(ts)}")

items = _parse_rewe_text_items(ts, hs)
print(f"parsed items: {len(items)}")
for it in items:
    print(f"  {it}")

print(f"merchant: {_extract_merchant(ts)}")