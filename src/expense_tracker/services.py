from datetime import date
from typing import List, Optional

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

        expense = Expense(
            id=self.storage.next_id(),
            description=description,
            amount=amount,
            date=spent_on or date.today(),
            category=category,
        )

        expenses.append(expense)
        self.storage.save_all_expenses(expenses)
        return expense

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
                return e

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

        return sum(
            e.amount
            for e in self.storage.get_all_expenses()
            if e.date.year == year and e.date.month == month
        )
