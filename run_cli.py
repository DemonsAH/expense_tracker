"""PyInstaller CLI entrypoint: expose the expense-tracker CLI as a console exe."""

from __future__ import annotations

from expense_tracker.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
