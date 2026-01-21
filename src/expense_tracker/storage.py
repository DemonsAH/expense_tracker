import json
import os
from pathlib import Path
from typing import List

from expense_tracker.models import Expense


DEFAULT_DIR = Path.home() / ".expense_tracker"
DEFAULT_FILE = DEFAULT_DIR / "expenses.json"


class ExpenseStorage:
    def __init__(self, file_path: Path | None = None):
        self.file_path = file_path or DEFAULT_FILE
        self._ensure_storage_exists()

    def _ensure_storage_exists(self) -> None:
        """
        Ensure data directory and JSON file exist
        """
        if not self.file_path.parent.exists():
            self.file_path.parent.mkdir(parents=True)

        if not self.file_path.exists():
            self._write_data({"last_id": 0, "expenses": [], "budgets": {}})

    def _read_data(self) -> dict:
        with open(self.file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_data(self, data: dict) -> None:
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # -------- public API --------

    def get_all_expenses(self) -> List[Expense]:
        data = self._read_data()
        return [Expense.from_dict(e) for e in data["expenses"]]

    def save_all_expenses(self, expenses: List[Expense]) -> None:
        data = self._read_data()
        data["expenses"] = [e.to_dict() for e in expenses]
        self._write_data(data)

    def next_id(self) -> int:
        data = self._read_data()
        data["last_id"] += 1
        self._write_data(data)
        return data["last_id"]
    
    def get_budgets(self) -> dict:
        return self._read_data()["budgets"]

    def set_budget(self, key: str, amount: float) -> None:
        data = self._read_data()
        data["budgets"][key] = amount
        self._write_data(data)

