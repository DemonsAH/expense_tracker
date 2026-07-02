"""Automation entrypoints for scheduled monthly report generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from expense_tracker.reports import WrittenMonthlyReport, update_monthly_report


@dataclass
class MonthlyReportJobResult:
    action: str
    target_month: str
    json_path: Path
    html_path: Path
    schema_path: Path | None = None
    written: WrittenMonthlyReport | None = None


def get_previous_month(today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def _target_report_paths(output_dir: str | Path, year: int, month: int) -> tuple[str, Path, Path]:
    report_month = f"{year:04d}-{month:02d}"
    base_dir = Path(output_dir) / report_month
    return report_month, base_dir / "report.json", base_dir / "report.html"


def run_previous_month_report_job(
    *,
    today: date | None = None,
    store_path: str | Path = "data/receipts.json",
    owners_path: str | Path | None = "owners.json",
    output_dir: str | Path = "reports",
    write_schema: bool = True,
    force: bool = False,
) -> MonthlyReportJobResult:
    year, month = get_previous_month(today=today)
    report_month, json_path, html_path = _target_report_paths(output_dir, year, month)

    if not force and json_path.exists() and html_path.exists():
        return MonthlyReportJobResult(
            action="skipped_existing",
            target_month=report_month,
            json_path=json_path,
            html_path=html_path,
            schema_path=(Path(output_dir) / "_schema" / "monthly_report.schema.json") if write_schema else None,
        )

    written = update_monthly_report(
        year=year,
        month=month,
        store_path=store_path,
        owners_path=owners_path,
        output_dir=output_dir,
        write_schema=write_schema,
    )
    return MonthlyReportJobResult(
        action="generated",
        target_month=report_month,
        json_path=written.json_path,
        html_path=written.html_path,
        schema_path=written.schema_path,
        written=written,
    )
