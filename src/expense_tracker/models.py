from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from typing import Optional


@dataclass
class Expense:
    """
    Expense data model
    """
    id: int
    description: str
    amount: float
    date: Date
    category: Optional[str] = None

    def __post_init__(self):
        if not self.description or not self.description.strip():
            raise ValueError("Description cannot be empty")

        if self.amount < 0:
            raise ValueError("Amount cannot be negative")

        if isinstance(self.date, str):
            # Allow loading from JSON
            self.date = datetime.strptime(self.date, "%Y-%m-%d").date()

    def to_dict(self) -> dict:
        """
        Convert Expense to a JSON-serializable dict
        """
        return {
            "id": self.id,
            "description": self.description,
            "amount": self.amount,
            "date": self.date.isoformat(),
            "category": self.category,
        }

    @staticmethod
    def from_dict(data: dict) -> "Expense":
        """
        Create Expense object from dict
        """
        return Expense(
            id=data["id"],
            description=data["description"],
            amount=float(data["amount"]),
            date=data["date"],
            category=data.get("category"),
        )
