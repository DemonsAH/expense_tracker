from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from expense_tracker.automation import get_previous_month, run_previous_month_report_job
from expense_tracker.reports import WrittenMonthlyReport, build_monthly_report
from tests.test_reports import make_store


def test_get_previous_month_handles_normal_and_january_cases() -> None:
    assert get_previous_month(date(2026, 5, 14)) == (2026, 4)
    assert get_previous_month(date(2026, 1, 2)) == (2025, 12)


def test_run_previous_month_report_job_generates_when_missing(monkeypatch) -> None:
    output_dir = Path(tempfile.mkdtemp(dir="data"))

    def fake_update_monthly_report(*, year, month, store_path, owners_path, output_dir, write_schema):
        built = build_monthly_report(make_store(), year=year, month=month, owners_path=owners_path)
        return WrittenMonthlyReport(
            report=built,
            json_path=Path(output_dir) / built.meta.report_month / "report.json",
            html_path=Path(output_dir) / built.meta.report_month / "report.html",
            schema_path=Path(output_dir) / "_schema" / "monthly_report.schema.json" if write_schema else None,
        )

    monkeypatch.setattr("expense_tracker.automation.report_jobs.update_monthly_report", fake_update_monthly_report)

    result = run_previous_month_report_job(
        today=date(2026, 5, 14),
        output_dir=output_dir,
        write_schema=True,
    )

    assert result.action == "generated"
    assert result.target_month == "2026-04"
    assert result.written is not None
    assert result.schema_path == output_dir / "_schema" / "monthly_report.schema.json"


def test_run_previous_month_report_job_skips_existing_report() -> None:
    output_dir = Path(tempfile.mkdtemp(dir="data"))
    report_dir = output_dir / "2026-04"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text("{}", encoding="utf-8")
    (report_dir / "report.html").write_text("<html></html>", encoding="utf-8")

    result = run_previous_month_report_job(
        today=date(2026, 5, 14),
        output_dir=output_dir,
        write_schema=False,
    )

    assert result.action == "skipped_existing"
    assert result.target_month == "2026-04"
    assert result.written is None
