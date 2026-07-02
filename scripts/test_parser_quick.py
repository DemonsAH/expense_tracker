"""Quick test for OCR parser against all three test receipts."""
import sys, json, re
from pathlib import Path
sys.path.insert(0, "src")
from expense_tracker.ocr_parser import (
    parse_ocr_to_extracted_receipt, TABLE_RE, ROW_RE, CELL_RE, MONEY_RE
)

FIXTURES = [
    ("test1", "test_receipts/test1_ocr_result.txt"),
    ("test2", "test_receipts/test2_ocr_result.txt"),
    ("test3", "test_receipts/test3_ocr_result.txt"),
]

def debug_parse(text, name):
    """Print detailed debug info."""
    print(f"\n{'='*60}")
    print(f"=== {name} DEBUG ===")
    
    table_start = text.find("<table>")
    ts = text[:table_start].strip() if table_start >= 0 else text.strip()
    hs = text[table_start:].strip() if table_start >= 0 else ""
    
    print(f"table_start={table_start}")
    print(f"text_section first 300:\n{ts[:300]}")
    print(f"html_section first 500:\n{hs[:500]}")
    
    # List all tables
    tables = TABLE_RE.findall(hs)
    print(f"\nTables found: {len(tables)}")
    for ti, tbl in enumerate(tables):
        rows = ROW_RE.findall(tbl)
        print(f"  Table {ti}: {len(rows)} rows")
        for ri, row in enumerate(rows[:10]):
            cells = CELL_RE.findall(row)
            cell_texts = [c.strip()[:40] for c in cells]
            print(f"    Row {ri}: {cell_texts}")

for name, path in FIXTURES:
    text = Path(path).read_text(encoding="utf-8")
    debug_parse(text, name)
    print(f"\n  === TRY PARSE {name} ===")
    try:
        r = parse_ocr_to_extracted_receipt(text, owners_path="owners.json")
        print(f"merchant: {r.merchant}")
        print(f"date: {r.purchase_date}")
        print(f"total: {r.total_amount}")
        print(f"payment: {r.payment_method}")
        print(f"owner_mode: {r.owner_mode.value}")
        print(f"items ({len(r.items)}):")
        for i, it in enumerate(r.items):
            print(f"  [{i}] {it.name} | qty={it.quantity} | total={it.total_price} | cat={it.category.value} | owner={it.owner_id} | marker={it.owner_marker}")
    except Exception as e:
        print(f"ERROR: {e}")
print("\n=== DONE ===")