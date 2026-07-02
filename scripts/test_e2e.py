"""End-to-end test: image -> OCR -> parse -> JSON, using local Unlimited-OCR."""
import sys
sys.path.insert(0, "src")
from pathlib import Path
from expense_tracker.receipt_step1 import run_receipt_step1

for name in ["test1", "test2", "test3"]:
    img = f"test_receipts/{name}.jpg"
    print(f"\n{'='*60}")
    print(f"=== {name}: {img} ===")
    try:
        result = run_receipt_step1(img, owners_path="owners.json")
        print(f"OK: {len(result)} chars")
        # Print first 500 chars
        print(result[:500])
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

print("\n=== DONE ===")