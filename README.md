# Expense Tracker

Expense Tracker is a Python project for receipt ingestion, validation, retry, archiving, and local persistence.

Current implementation focus:

- LangChain-based image-to-JSON extraction with `Qwen/Qwen3.6-27B` via SiliconFlow
- Business validation for totals, owner IDs, negative items, cancellation items, and `Leergut/Pfand`
- Automatic retry up to 3 attempts
- Failed-attempt archiving with model output text preserved
- Local JSON store persistence
- CLI entrypoint for end-to-end testing

## Environment

Recommended project environment:

```powershell
conda activate .\.conda
```

If needed, install the project in editable mode so the `expense-tracker` command is available:

```powershell
pip install -e .
```

## Required Configuration

Create or update `.env` in the project root:

```env
SILICONFLOW_API_KEY=your_api_key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1

LANGSMITH_API_KEY=your_langsmith_api_key
LANGSMITH_PROJECT=expense-tracker
EXPENSE_TRACKER_ENABLE_LANGSMITH=true
```

Notes:

- `EXPENSE_TRACKER_ENABLE_LANGSMITH=true` enables LangSmith tracing for the current pipeline.
- If you do not want tracing, set it to `false`.

## Owners Configuration

The project reads owner configuration from `owners.json`.

Requirements:

- `id` must be unique
- `marker` must be unique
- exactly one owner must have `is_me=true`

## CLI Usage

The CLI is the recommended way to run the full pipeline during development tests.

### 1. Show Help

If you have not installed the project as an editable package yet:

```powershell
$env:PYTHONPATH="src"
python -m expense_tracker.cli --help
```

If you already ran `pip install -e .`:

```powershell
expense-tracker --help
```

### 2. Ingest One Receipt Image

Without editable install:

```powershell
$env:PYTHONPATH="src"
python -m expense_tracker.cli ingest test_receipts/test1.jpg --print-json
```

With editable install:

```powershell
expense-tracker ingest test_receipts/test1.jpg --print-json
```

### 3. Ingest a Directory

Without editable install:

```powershell
$env:PYTHONPATH="src"
python -m expense_tracker.cli ingest-dir test_receipts
```

With editable install:

```powershell
expense-tracker ingest-dir test_receipts
```

This command scans supported image files in the directory and runs the full ingestion pipeline on each one.

By default, `ingest-dir` skips images that are already present in the JSON store. The skip decision uses both:

- `image_path`
- file content hash (`sha256`)

If you want to force reprocessing, use:

```powershell
expense-tracker ingest-dir test_receipts --no-skip-processed
```

### 4. Useful Options

```powershell
expense-tracker ingest test_receipts/test1.jpg `
  --owners owners.json `
  --model Qwen/Qwen3.6-27B `
  --max-attempts 3 `
  --artifact-dir test_receipts `
  --failure-dir rejected_receipts `
  --store-path data/receipts.json `
  --print-json
```

Available flags:

- `--owners`: owner config file path
- `--model`: SiliconFlow model name
- `--max-attempts`: retry count, default `3`
- `--artifact-dir`: successful artifact output directory
- `--failure-dir`: failed archive directory
- `--store-path`: JSON store path
- `--no-store`: run without writing to the JSON store
- `--no-archive`: run without archiving failures
- `--print-json`: print final `receipt_record`

The `ingest-dir` command supports the same core options except `--print-json`.
It also supports `--no-skip-processed`.

### 5. Generate One Monthly Report

Without editable install:

```powershell
$env:PYTHONPATH="src"
python -m expense_tracker.cli generate-report --write-schema
```

With editable install:

```powershell
expense-tracker generate-report --write-schema
```

By default, `generate-report` builds the report for the previous calendar month.
If you want a specific month, pass it explicitly:

```powershell
expense-tracker generate-report 2026-05 --write-schema
```

This command reads the JSON store and writes:

- `reports/YYYY-MM/report.json`
- `reports/YYYY-MM/report.html`
- `reports/_schema/monthly_report.schema.json` when `--write-schema` is enabled

### 6. Run The Scheduled Report Job

This command is intended for Task Scheduler / cron style automation.
It targets the previous calendar month and skips regeneration when both
`report.json` and `report.html` already exist.

```powershell
expense-tracker run-report-job --write-schema
```

If you want to rebuild even when files already exist:

```powershell
expense-tracker run-report-job --write-schema --force
```

### 7. Run The Scheduled Ingestion Job

This command is intended for Task Scheduler / cron style automation.
It scans one directory, processes supported receipt images, and by default
skips files already present in the JSON store.
Handled source files are moved out of `incoming`:

- successful files -> `processed_receipts/`
- skipped duplicate files -> `processed_receipts/`
- failed files -> `rejected_receipts/_source/`

```powershell
expense-tracker run-ingest-job incoming_receipts --recursive
```

Useful options:

- `--duplicate-policy skip-success`: skip files that already succeeded before
- `--duplicate-policy retry-failed-only`: only reprocess files that previously failed OCR
- `--duplicate-policy force-reprocess`: always process everything again
- `--no-skip-processed`: deprecated shortcut for `--duplicate-policy force-reprocess`
- `--recursive`: scan nested folders
- `--processed-dir`: choose where successful and skipped source files are moved
- `--artifact-dir`: choose where successful extraction artifacts are written
- `--failure-dir`: choose where failed attempts are archived

## GUI Usage

The project also includes a desktop GUI for receipt review and manual report generation.

If you have installed the project in editable mode:

```powershell
expense-tracker-gui
```

Without editable install:

```powershell
$env:PYTHONPATH="src"
python -m expense_tracker.gui.app
```

The GUI currently supports:

- browsing scanned receipts from `data/receipts.json`
- creating, editing, and deleting receipt records
- editing formal receipt items
- reviewing failed OCR records and opening archived files
- generating monthly HTML/JSON reports manually
- opening generated `report.html` and `report.json`

For a packaged Windows build, the generated executable is:

- `dist/ExpenseTrackerGUI.exe`

## What the CLI Does

The `ingest` command runs the whole current pipeline:

1. Read one image
2. Call Qwen through SiliconFlow
3. Parse model JSON output
4. Validate schema
5. Validate business rules
6. Retry automatically when the result is retryable
7. Post-process cancellation items
8. Save successful artifacts
9. Persist successful records to `data/receipts.json`
10. Archive failed attempts to `rejected_receipts/`

The `ingest-dir` command repeats the same process for every supported image in one directory and prints a summary of success and failure counts.

## Output Files

### Success

Successful runs may produce:

- `*_content.txt`: raw model output text
- `*_receipt.json`: validated extraction artifact

These are usually written to the image directory or the directory passed via `--artifact-dir`.

### Failure

Failed runs are archived under `rejected_receipts/` by default.

Archived files include:

- copied original image
- one `*_attemptN_content.txt` per failed attempt
- one `*_failure.json` describing all attempts

## Current Business Rules

### Negative Items

Negative `total_price` is allowed only for:

- cancellation items such as `Storno` and `Sofortstorno`
- `Leergut/Pfand` related items

Any other negative item triggers validation failure.

### Cancellation Handling

Cancellation handling rules:

- the negative cancellation line itself is excluded from formal items
- the matched original positive item is also excluded
- excluded items are retained in audit data as `removed_items`

### Leergut / Pfand Handling

`Leergut/Pfand` rules:

- legal as negative items
- kept in formal receipt items
- included in monthly total spending
- excluded later from price-ranking analysis

## Persistence

Current local persistence uses JSON:

- main store: `data/receipts.json`

The JSON store maintains:

- `last_receipt_id`
- `last_item_id`
- `receipts`
- `failed_ocr_records`

## Architecture Note

For future integrations such as WhatsApp-triggered receipt ingestion, prefer calling the Python pipeline function directly:

- `ingest_receipt_with_retries(...)`

The CLI should remain the main human-facing development and testing entrypoint, while external integrations should usually call Python functions rather than shelling out to the CLI.

## TODO

Current suggested next steps for development:

- Separate directory roles more clearly:
  - incoming receipt directory
  - successful processed directory
  - failed archive directory
- Add configurable directory scanning workflow for scheduled ingestion
- Add store-level duplicate policy options:
  - skip by `image_hash`
  - retry failed-only images
  - force reprocess mode
- Add a dedicated service layer for external triggers such as WhatsApp
- Add a save-incoming-file helper for message-triggered receipt ingestion
- Add monthly statistics and report preparation based on formal receipt items
- Add price-ranking analysis that excludes:
  - `DINING`
  - cancellation-related removed items
  - `Leergut/Pfand`
- Add export helpers for CSV report output while keeping JSON as the main store
- Add manual review workflow support for:
  - failed receipts
  - corrected receipt revalidation
  - audit trail persistence
- Add GUI screens for:
  - failed receipt review
  - receipt list and detail view
  - edited field submission
- Add batch command enhancements:
  - recursive directory scan
  - summary report file output
  - dry-run mode
- Add tests for:
  - schema validation
  - retry behavior
  - cancellation matching
  - `Leergut/Pfand` handling
  - duplicate skip logic
