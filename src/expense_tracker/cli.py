"""Command-line entrypoint for receipt ingestion workflows."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from expense_tracker.automation import run_ingest_directory_job, run_previous_month_report_job
from expense_tracker.pipelines import ingest_receipt_with_retries
from expense_tracker.reports import update_monthly_report
from expense_tracker.storage import compute_file_sha256, has_processed_image, load_receipt_store
from expense_tracker.tracing import flush_traces


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expense-tracker",
        description="Expense Tracker receipt ingestion CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Ingest a single receipt image through the full pipeline.",
    )
    ingest_parser.add_argument("image_path", help="Path to the receipt image.")
    ingest_parser.add_argument(
        "--owners",
        default="owners.json",
        help="Path to owners.json.",
    )
    ingest_parser.add_argument(
        "--model",
        default="Qwen/Qwen3.6-27B",
        help="SiliconFlow model name.",
    )
    ingest_parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum number of retry attempts.",
    )
    ingest_parser.add_argument(
        "--artifact-dir",
        default=None,
        help="Directory for successful extraction artifacts.",
    )
    ingest_parser.add_argument(
        "--failure-dir",
        default="rejected_receipts",
        help="Directory for failed attempts and archived receipts.",
    )
    ingest_parser.add_argument(
        "--store-path",
        default="data/receipts.json",
        help="JSON store path for persisted receipts.",
    )
    ingest_parser.add_argument(
        "--no-store",
        action="store_true",
        help="Run the pipeline without persisting receipt data.",
    )
    ingest_parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Disable failed-attempt archiving.",
    )
    ingest_parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print the final receipt_record as JSON.",
    )

    ingest_dir_parser = subparsers.add_parser(
        "ingest-dir",
        help="Ingest all supported receipt images in a directory.",
    )
    ingest_dir_parser.add_argument("directory", help="Directory containing receipt images.")
    ingest_dir_parser.add_argument(
        "--owners",
        default="owners.json",
        help="Path to owners.json.",
    )
    ingest_dir_parser.add_argument(
        "--model",
        default="Qwen/Qwen3.6-27B",
        help="SiliconFlow model name.",
    )
    ingest_dir_parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum number of retry attempts per image.",
    )
    ingest_dir_parser.add_argument(
        "--artifact-dir",
        default=None,
        help="Directory for successful extraction artifacts.",
    )
    ingest_dir_parser.add_argument(
        "--failure-dir",
        default="rejected_receipts",
        help="Directory for failed attempts and archived receipts.",
    )
    ingest_dir_parser.add_argument(
        "--store-path",
        default="data/receipts.json",
        help="JSON store path for persisted receipts.",
    )
    ingest_dir_parser.add_argument(
        "--no-store",
        action="store_true",
        help="Run the pipeline without persisting receipt data.",
    )
    ingest_dir_parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Disable failed-attempt archiving.",
    )
    ingest_dir_parser.add_argument(
        "--no-skip-processed",
        action="store_true",
        help="Do not skip images that are already present in the JSON store.",
    )

    report_parser = subparsers.add_parser(
        "generate-report",
        help="Generate one monthly report as JSON and HTML.",
    )
    report_parser.add_argument(
        "report_month",
        nargs="?",
        default=None,
        help="Target month in YYYY-MM format.",
    )
    report_parser.add_argument(
        "--store-path",
        default="data/receipts.json",
        help="JSON store path for persisted receipts.",
    )
    report_parser.add_argument(
        "--owners",
        default="owners.json",
        help="Path to owners.json.",
    )
    report_parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory for generated report files.",
    )
    report_parser.add_argument(
        "--write-schema",
        action="store_true",
        help="Also export the monthly report JSON schema.",
    )

    report_job_parser = subparsers.add_parser(
        "run-report-job",
        help="Run the scheduled previous-month report job.",
    )
    report_job_parser.add_argument(
        "--store-path",
        default="data/receipts.json",
        help="JSON store path for persisted receipts.",
    )
    report_job_parser.add_argument(
        "--owners",
        default="owners.json",
        help="Path to owners.json.",
    )
    report_job_parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory for generated report files.",
    )
    report_job_parser.add_argument(
        "--write-schema",
        action="store_true",
        help="Also export the monthly report JSON schema.",
    )
    report_job_parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate the report even if JSON and HTML already exist.",
    )

    ingest_job_parser = subparsers.add_parser(
        "run-ingest-job",
        help="Run the scheduled directory ingestion job.",
    )
    ingest_job_parser.add_argument("directory", help="Directory containing receipt images.")
    ingest_job_parser.add_argument(
        "--owners",
        default="owners.json",
        help="Path to owners.json.",
    )
    ingest_job_parser.add_argument(
        "--model",
        default="Qwen/Qwen3.6-27B",
        help="SiliconFlow model name.",
    )
    ingest_job_parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum number of retry attempts per image.",
    )
    ingest_job_parser.add_argument(
        "--artifact-dir",
        default=None,
        help="Directory for successful extraction artifacts.",
    )
    ingest_job_parser.add_argument(
        "--failure-dir",
        default="rejected_receipts",
        help="Directory for failed attempts and archived receipts.",
    )
    ingest_job_parser.add_argument(
        "--processed-dir",
        default="processed_receipts",
        help="Directory where successfully handled incoming files are moved.",
    )
    ingest_job_parser.add_argument(
        "--store-path",
        default="data/receipts.json",
        help="JSON store path for persisted receipts.",
    )
    ingest_job_parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Disable failed-attempt archiving.",
    )
    ingest_job_parser.add_argument(
        "--duplicate-policy",
        choices=["skip-success", "retry-failed-only", "force-reprocess"],
        default="skip-success",
        help="Duplicate handling policy for already-seen images.",
    )
    ingest_job_parser.add_argument(
        "--no-skip-processed",
        action="store_true",
        help="Deprecated shortcut for --duplicate-policy force-reprocess.",
    )
    ingest_job_parser.add_argument(
        "--recursive",
        action="store_true",
        help="Scan the target directory recursively.",
    )
    return parser


def _run_ingest(args: argparse.Namespace) -> int:
    result = ingest_receipt_with_retries(
        image_path=args.image_path,
        owners_path=args.owners,
        model=args.model,
        max_attempts=args.max_attempts,
        save_artifacts=True,
        artifact_output_dir=args.artifact_dir,
        persist_store=not args.no_store,
        store_path=args.store_path,
        archive_failures=not args.no_archive,
        failure_output_dir=args.failure_dir,
    )

    print("INGEST_SUCCESS")
    print(f"receipt_id: {result.receipt_record.id}")
    print(f"attempt_count: {result.attempt_count}")
    print(f"formal_items: {len(result.processed_items.formal_items)}")
    print(f"removed_items: {len(result.processed_items.removed_items)}")
    if result.content_path:
        print(f"content_path: {result.content_path}")
    if result.receipt_path:
        print(f"receipt_artifact_path: {result.receipt_path}")
    print(f"store_path: {Path(args.store_path)}")

    if args.print_json:
        print(result.receipt_record.model_dump_json(indent=2))
    return 0


def _iter_image_paths(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _should_skip_processed_image(store, image_path: Path) -> bool:
    image_hash = compute_file_sha256(image_path)
    return has_processed_image(
        store,
        image_path=str(image_path),
        image_hash=image_hash,
    )


def _run_ingest_dir(args: argparse.Namespace) -> int:
    directory = Path(args.directory)
    if not directory.exists() or not directory.is_dir():
        raise ValueError(f"Directory not found: {directory}")

    image_paths = _iter_image_paths(directory)
    if not image_paths:
        raise ValueError(f"No supported receipt images found in: {directory}")

    success_count = 0
    failure_count = 0
    skipped_count = 0
    store = None
    if not args.no_store:
        store = load_receipt_store(args.store_path)

    print(f"INGEST_DIR_START: {directory}")
    print(f"images_found: {len(image_paths)}")

    for image_path in image_paths:
        if store is not None and not args.no_skip_processed:
            if _should_skip_processed_image(store, image_path):
                skipped_count += 1
                print(f"SKIPPED: {image_path.name} | already_processed")
                continue

        try:
            result = ingest_receipt_with_retries(
                image_path=image_path,
                owners_path=args.owners,
                model=args.model,
                max_attempts=args.max_attempts,
                save_artifacts=True,
                artifact_output_dir=args.artifact_dir,
                persist_store=not args.no_store,
                store_path=args.store_path,
                archive_failures=not args.no_archive,
                failure_output_dir=args.failure_dir,
            )
            success_count += 1
            print(
                f"SUCCESS: {image_path.name} | receipt_id={result.receipt_record.id} | "
                f"attempts={result.attempt_count} | formal_items={len(result.processed_items.formal_items)}"
            )
        except Exception as exc:
            failure_count += 1
            print(f"FAILED: {image_path.name} | {exc}")

    print("INGEST_DIR_DONE")
    print(f"success_count: {success_count}")
    print(f"failure_count: {failure_count}")
    print(f"skipped_count: {skipped_count}")
    return 0 if failure_count == 0 else 1


def _parse_report_month(value: str) -> tuple[int, int]:
    try:
        year_text, month_text = value.split("-", maxsplit=1)
        year = int(year_text)
        month = int(month_text)
    except ValueError as exc:
        raise ValueError(f"Invalid report month '{value}'. Expected YYYY-MM.") from exc

    if month < 1 or month > 12:
        raise ValueError(f"Invalid report month '{value}'. Month must be between 01 and 12.")
    return year, month


def _default_report_month(today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def _run_generate_report(args: argparse.Namespace) -> int:
    if args.report_month is None:
        year, month = _default_report_month()
    else:
        year, month = _parse_report_month(args.report_month)
    written = update_monthly_report(
        year=year,
        month=month,
        store_path=args.store_path,
        owners_path=args.owners,
        output_dir=args.output_dir,
        write_schema=args.write_schema,
    )

    print("REPORT_GENERATED")
    print(f"report_month: {written.report.meta.report_month}")
    print(f"json_path: {written.json_path}")
    print(f"html_path: {written.html_path}")
    if written.schema_path:
        print(f"schema_path: {written.schema_path}")
    print(f"receipt_count: {written.report.meta.receipt_count}")
    return 0


def _run_report_job(args: argparse.Namespace) -> int:
    result = run_previous_month_report_job(
        store_path=args.store_path,
        owners_path=args.owners,
        output_dir=args.output_dir,
        write_schema=args.write_schema,
        force=args.force,
    )
    if result.action == "skipped_existing":
        print("REPORT_JOB_SKIPPED")
    else:
        print("REPORT_JOB_GENERATED")
    print(f"report_month: {result.target_month}")
    print(f"json_path: {result.json_path}")
    print(f"html_path: {result.html_path}")
    if result.schema_path:
        print(f"schema_path: {result.schema_path}")
    return 0


def _run_ingest_job(args: argparse.Namespace) -> int:
    duplicate_policy = "force-reprocess" if args.no_skip_processed else args.duplicate_policy
    result = run_ingest_directory_job(
        args.directory,
        owners_path=args.owners,
        model=args.model,
        max_attempts=args.max_attempts,
        artifact_output_dir=args.artifact_dir,
        failure_output_dir=args.failure_dir,
        processed_output_dir=args.processed_dir,
        store_path=args.store_path,
        archive_failures=not args.no_archive,
        duplicate_policy=duplicate_policy,
        recursive=args.recursive,
    )
    print("INGEST_JOB_DONE")
    print(f"directory: {result.directory}")
    print(f"images_found: {result.images_found}")
    print(f"duplicate_policy: {result.duplicate_policy}")
    print(f"success_count: {result.success_count}")
    print(f"failure_count: {result.failure_count}")
    print(f"skipped_count: {result.skipped_count}")
    print(f"moved_success_count: {len(result.moved_success_files)}")
    print(f"moved_failed_count: {len(result.moved_failed_files)}")
    print(f"moved_skipped_count: {len(result.moved_skipped_files)}")
    return 0 if result.failure_count == 0 else 1


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.command == "ingest":
            return _run_ingest(args)
        if args.command == "ingest-dir":
            return _run_ingest_dir(args)
        if args.command == "generate-report":
            return _run_generate_report(args)
        if args.command == "run-report-job":
            return _run_report_job(args)
        if args.command == "run-ingest-job":
            return _run_ingest_job(args)
        parser.error(f"Unknown command: {args.command}")
        return 2
    except Exception as exc:
        print("INGEST_FAILED")
        print(str(exc))
        return 1
    finally:
        flush_traces()


if __name__ == "__main__":
    raise SystemExit(main())
