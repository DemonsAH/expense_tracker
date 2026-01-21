from datetime import date
from typing import List, Optional, Set
import csv
from pathlib import Path

from expense_tracker.models import Expense
from expense_tracker.storage import ExpenseStorage


class ExpenseService:
    def __init__(self, storage: ExpenseStorage | None = None):
        self.storage = storage or ExpenseStorage()

    # ---------- CRUD ----------

    def add_expense(
        self,
        description: str,
        amount: float,
        category: Optional[str] = None,
        spent_on: Optional[date] = None,
    ) -> Expense:
        expenses = self.storage.get_all_expenses()
        date = spent_on or date.today()
        expense = Expense(
            id=self.storage.next_id(),
            description=description,
            amount=amount,
            date=date,
            category=category,
        )

        expenses.append(expense)
        self.storage.save_all_expenses(expenses)
        warning = self.budget_warning_for_month(month=date.month, year=date.year)
        return expense, warning

    def update_expense(
        self,
        expense_id: int,
        description: Optional[str] = None,
        amount: Optional[float] = None,
        category: Optional[str] = None,
    ) -> Expense:
        expenses = self.storage.get_all_expenses()

        for e in expenses:
            if e.id == expense_id:
                if description is not None:
                    e.description = description
                if amount is not None:
                    if amount < 0:
                        raise ValueError("Amount cannot be negative")
                    e.amount = amount
                if category is not None:
                    e.category = category
                self.storage.save_all_expenses(expenses)
                warning = self.budget_warning_for_month(month=e.date.month, year=e.date.year)
                return e, warning

        raise ValueError(f"Expense with ID {expense_id} not found")

    def delete_expense(self, expense_id: int) -> None:
        expenses = self.storage.get_all_expenses()
        new_expenses = [e for e in expenses if e.id != expense_id]

        if len(new_expenses) == len(expenses):
            raise ValueError(f"Expense with ID {expense_id} not found")

        self.storage.save_all_expenses(new_expenses)

    # ---------- Query ----------

    def list_expenses(self) -> List[Expense]:
        return self.storage.get_all_expenses()

    def total_expenses(self) -> float:
        return sum(e.amount for e in self.storage.get_all_expenses())

    def total_expenses_for_month(self, month: int, year: Optional[int] = None) -> float:
        if not 1 <= month <= 12:
            raise ValueError("Month must be between 1 and 12")

        year = year or date.today().year

        return self.spent_for_month(month=month, year=year)
    
    def get_categories(self) -> Set[str]:
        """
        Return all existing non-empty categories (case-sensitive as stored).
        """
        categories: Set[str] = set()
        for e in self.storage.get_all_expenses():
            if e.category is not None and e.category.strip():
                categories.add(e.category.strip())
        return categories

    def list_expenses_by_category(self, category: str) -> List["Expense"]:
        """
        Filter expenses by exact category match.
        """
        cat = category.strip()
        if not cat:
            raise ValueError("Category cannot be empty")

        return [
            e for e in self.storage.get_all_expenses()
            if (e.category is not None and e.category.strip() == cat)
        ]
    
    def export_to_csv(self, file_path: str) -> Path:
        """
        Export all expenses to a CSV file.
        Returns the Path of the written file.
        """
        path = Path(file_path)

        expenses = self.storage.get_all_expenses()

        # 确保父目录存在
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            # header
            writer.writerow(["id", "date", "description", "amount", "category"])

            for e in expenses:
                writer.writerow([
                    e.id,
                    e.date.isoformat(),
                    e.description,
                    e.amount,
                    e.category or "",   # Optional[str] → 空字符串
                ])

        return path
    
    def _month_key(self, year: int, month: int) -> str:
        return f"{year:04d}-{month:02d}"

    def set_monthly_budget(self, amount: float, month: Optional[int] = None, year: Optional[int] = None) -> str:
        if amount < 0:
            raise ValueError("Budget cannot be negative")
        month = month or date.today().month
        year = year or date.today().year
        if not (1 <= month <= 12):
            raise ValueError("Month must be between 1 and 12")

        key = self._month_key(year, month)
        self.storage.set_budget(key, float(amount))
        return key

    def get_monthly_budget(self, month: Optional[int] = None, year: Optional[int] = None) -> Optional[float]:
        month = month or date.today().month
        year = year or date.today().year
        if not (1 <= month <= 12):
            raise ValueError("Month must be between 1 and 12")

        key = self._month_key(year, month)
        return self.storage.get_budgets().get(key)

    def spent_for_month(self, month: int, year: int) -> float:
        return sum(
            e.amount
            for e in self.storage.get_all_expenses()
            if e.date.year == year and e.date.month == month
        )
    
    def budget_warning_for_month(self, month: int, year: int) -> Optional[str]:
        budget = self.get_monthly_budget(month=month, year=year)
        if budget is None:
            return None
        spent = self.spent_for_month(month, year)
        if spent > budget:
            key = self._month_key(year, month)
            return f"# Warning: Budget exceeded for {key}. Budget: ${budget:g}, Spent: ${spent:g}"
        return None
