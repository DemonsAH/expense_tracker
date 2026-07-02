from __future__ import annotations

from pathlib import Path

from expense_tracker import cli
from expense_tracker.automation import IngestJobResult, MonthlyReportJobResult
from expense_tracker.reports import WrittenMonthlyReport, build_monthly_report
from tests.test_reports import make_store


def test_generate_report_cli_calls_report_updater(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_update_monthly_report(*, year, month, store_path, owners_path, output_dir, write_schema):
        captured.update(
            year=year,
            month=month,
            store_path=store_path,
            owners_path=owners_path,
            output_dir=output_dir,
            write_schema=write_schema,
        )
        built = build_monthly_report(
            make_store(),
            year=year,
            month=month,
            owners_path=owners_path,
        )
        return WrittenMonthlyReport(
            report=built,
            json_path=Path(output_dir) / built.meta.report_month / "report.json",
            html_path=Path(output_dir) / built.meta.report_month / "report.html",
            schema_path=Path(output_dir) / "_schema" / "monthly_report.schema.json",
        )

    monkeypatch.setattr(cli, "update_monthly_report", fake_update_monthly_report)

    exit_code = cli._run_generate_report(
        type(
            "Args",
            (),
            {
                "report_month": "2026-05",
                "store_path": "data/receipts.json",
                "owners": "owners.json",
                "output_dir": "reports",
                "write_schema": True,
            },
        )()
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured == {
        "year": 2026,
        "month": 5,
        "store_path": "data/receipts.json",
        "owners_path": "owners.json",
        "output_dir": "reports",
        "write_schema": True,
    }
    assert "REPORT_GENERATED" in output
    assert "report_month: 2026-05" in output
    assert "json_path: reports\\2026-05\\report.json" in output
    assert "schema_path: reports\\_schema\\monthly_report.schema.json" in output


def test_generate_report_cli_defaults_to_previous_month(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_update_monthly_report(*, year, month, store_path, owners_path, output_dir, write_schema):
        captured.update(
            year=year,
            month=month,
            store_path=store_path,
            owners_path=owners_path,
            output_dir=output_dir,
            write_schema=write_schema,
        )
        built = build_monthly_report(
            make_store(),
            year=2026,
            month=4,
            owners_path=owners_path,
        )
        return WrittenMonthlyReport(
            report=built,
            json_path=Path(output_dir) / built.meta.report_month / "report.json",
            html_path=Path(output_dir) / built.meta.report_month / "report.html",
            schema_path=None,
        )

    monkeypatch.setattr(cli, "update_monthly_report", fake_update_monthly_report)
    monkeypatch.setattr(cli, "_default_report_month", lambda: (2026, 4))

    exit_code = cli._run_generate_report(
        type(
            "Args",
            (),
            {
                "report_month": None,
                "store_path": "data/receipts.json",
                "owners": "owners.json",
                "output_dir": "reports",
                "write_schema": False,
            },
        )()
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured["year"] == 2026
    assert captured["month"] == 4
    assert "report_month: 2026-04" in output


def test_default_report_month_uses_previous_calendar_month() -> None:
    assert cli._default_report_month(cli.date(2026, 5, 14)) == (2026, 4)
    assert cli._default_report_month(cli.date(2026, 1, 3)) == (2025, 12)


def test_parse_report_month_rejects_invalid_input() -> None:
    try:
        cli._parse_report_month("2026/05")
    except ValueError as exc:
        assert "Expected YYYY-MM" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid report month format")


def test_run_report_job_cli_reports_skipped_existing(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_run_previous_month_report_job(*, store_path, owners_path, output_dir, write_schema, force):
        captured.update(
            store_path=store_path,
            owners_path=owners_path,
            output_dir=output_dir,
            write_schema=write_schema,
            force=force,
        )
        return MonthlyReportJobResult(
            action="skipped_existing",
            target_month="2026-04",
            json_path=Path(output_dir) / "2026-04" / "report.json",
            html_path=Path(output_dir) / "2026-04" / "report.html",
            schema_path=None,
            written=None,
        )

    monkeypatch.setattr(cli, "run_previous_month_report_job", fake_run_previous_month_report_job)

    exit_code = cli._run_report_job(
        type(
            "Args",
            (),
            {
                "store_path": "data/receipts.json",
                "owners": "owners.json",
                "output_dir": "reports",
                "write_schema": False,
                "force": False,
            },
        )()
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured["force"] is False
    assert "REPORT_JOB_SKIPPED" in output
    assert "report_month: 2026-04" in output


def test_run_ingest_job_cli_passes_through_options(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_run_ingest_directory_job(
        directory,
        *,
        owners_path,
        model,
        max_attempts,
        artifact_output_dir,
        failure_output_dir,
        processed_output_dir,
        store_path,
        archive_failures,
        duplicate_policy,
        recursive,
    ):
        captured.update(
            directory=directory,
            owners_path=owners_path,
            model=model,
            max_attempts=max_attempts,
            artifact_output_dir=artifact_output_dir,
            failure_output_dir=failure_output_dir,
            processed_output_dir=processed_output_dir,
            store_path=store_path,
            archive_failures=archive_failures,
            duplicate_policy=duplicate_policy,
            recursive=recursive,
        )
        return IngestJobResult(
            directory=Path(directory),
            images_found=3,
            success_count=2,
            failure_count=0,
            skipped_count=1,
        )

    monkeypatch.setattr(cli, "run_ingest_directory_job", fake_run_ingest_directory_job)

    exit_code = cli._run_ingest_job(
        type(
            "Args",
            (),
            {
                "directory": "incoming",
                "owners": "owners.json",
                "model": "Qwen/Qwen3.6-27B",
                "max_attempts": 3,
                "artifact_dir": "artifacts",
                "failure_dir": "rejected_receipts",
                "processed_dir": "processed_receipts",
                "store_path": "data/receipts.json",
                "duplicate_policy": "retry-failed-only",
                "no_archive": False,
                "no_skip_processed": False,
                "recursive": True,
            },
        )()
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured["directory"] == "incoming"
    assert captured["processed_output_dir"] == "processed_receipts"
    assert captured["duplicate_policy"] == "retry-failed-only"
    assert captured["recursive"] is True
    assert "INGEST_JOB_DONE" in output
    assert "images_found: 3" in output
    assert "skipped_count: 1" in output


def test_run_ingest_job_cli_no_skip_processed_maps_to_force_reprocess(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_run_ingest_directory_job(directory, **kwargs):
        captured.update(kwargs)
        return IngestJobResult(
            directory=Path(directory),
            images_found=1,
            success_count=1,
            failure_count=0,
            skipped_count=0,
            duplicate_policy=kwargs["duplicate_policy"],
        )

    monkeypatch.setattr(cli, "run_ingest_directory_job", fake_run_ingest_directory_job)

    exit_code = cli._run_ingest_job(
        type(
            "Args",
            (),
            {
                "directory": "incoming",
                "owners": "owners.json",
                "model": "Qwen/Qwen3.6-27B",
                "max_attempts": 3,
                "artifact_dir": None,
                "failure_dir": "rejected_receipts",
                "processed_dir": "processed_receipts",
                "store_path": "data/receipts.json",
                "duplicate_policy": "skip-success",
                "no_archive": False,
                "no_skip_processed": True,
                "recursive": False,
            },
        )()
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured["duplicate_policy"] == "force-reprocess"
    assert "duplicate_policy: force-reprocess" in output
