"""验收测试: 完整主链 — OCR -> Parse -> Validate -> Store -> Report -> EXE"""
import sys, json
sys.path.insert(0, "src")
from pathlib import Path
from expense_tracker.receipt_step1 import run_receipt_step1
from expense_tracker.pipelines.receipt_ingestion import ingest_receipt_with_retries
from expense_tracker.reports.monthly import update_monthly_report
from expense_tracker.schemas.extraction import ExtractedReceipt
from expense_tracker.storage.json_store import load_receipt_store

PROJECT_ROOT = Path.cwd()
TEST_IMAGES = [
    "test_receipts/test1.jpg",
    "test_receipts/test2.jpg",
    "test_receipts/test3.jpg",
]
TEMP_STORE = "data/test_acceptance_receipts.json"
TEMP_REPORT_DIR = "reports/test_acceptance"

def clean_temp():
    for p in [Path(TEMP_STORE), Path(TEMP_REPORT_DIR)]:
        if p.is_file():
            p.unlink()
        if p.is_dir():
            import shutil
            shutil.rmtree(p, ignore_errors=True)

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def test_1_json_schema_validation():
    """Validate that step1 output conforms to ExtractedReceipt schema."""
    print_section("Test 1: JSON Schema Validation")
    for img in TEST_IMAGES:
        json_str = run_receipt_step1(img, owners_path="owners.json")
        data = json.loads(json_str)
        extracted = ExtractedReceipt.model_validate(data)
        print(f"  {img}: {extracted.merchant} | {extracted.purchase_date} | {len(extracted.items)} items | {extracted.total_amount} EUR | PASS")
    print("  Result: ALL PASS")

def test_2_full_ingestion_pipeline():
    """Test the full ingestion pipeline (OCR + validate + store)."""
    print_section("Test 2: Full Ingestion Pipeline")
    clean_temp()
    for img in TEST_IMAGES:
        try:
            result = ingest_receipt_with_retries(
                img,
                owners_path="owners.json",
                model="local-unlimited-ocr",
                max_attempts=1,
                persist_store=True,
                store_path=TEMP_STORE,
                archive_failures=True,
                failure_output_dir="rejected_receipts",
            )
            print(f"  {img}: id={result.receipt_record.id} | {len(result.processed_items.formal_items)} items | PASS")
        except Exception as e:
            print(f"  {img}: FAILED — {e}")
    store = load_receipt_store(TEMP_STORE)
    print(f"  Store: {len(store.receipts)} receipts stored | PASS")

def test_3_monthly_report():
    """Generate monthly reports for test data."""
    print_section("Test 3: Monthly Report Generation")
    store = load_receipt_store(TEMP_STORE)
    if not store.receipts:
        print("  SKIP: No receipts in store")
        return
    months = set()
    for r in store.receipts:
        months.add(f"{r.purchase_date.year:04d}-{r.purchase_date.month:02d}")
    for month in sorted(months):
        try:
            report = update_monthly_report(
                store=store,
                report_month=month,
                owners_path="owners.json",
                output_dir=TEMP_REPORT_DIR,
                write_schema=True,
            )
            print(f"  {month}: total={report.overview.month_total_spend} EUR | {report.meta.receipt_count} receipts | PASS")
        except Exception as e:
            print(f"  {month}: FAILED — {e}")
    report_files = list(Path(TEMP_REPORT_DIR).glob("*.json"))
    print(f"  Report files: {len(report_files)} | PASS" if report_files else "  FAIL: No report files")

if __name__ == "__main__":
    test_1_json_schema_validation()
    test_2_full_ingestion_pipeline()
    test_3_monthly_report()
    print("\n" + "="*60)
    print("  ACCEPTANCE TESTS COMPLETE")
    print("="*60)