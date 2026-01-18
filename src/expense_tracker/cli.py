import argparse
from datetime import date
from typing import Optional

from expense_tracker.services import ExpenseService


def _fmt_money(amount: float) -> str:
    # money amount format
    if float(amount).is_integer():
        return f"${int(amount)}"
    return f"${amount:.2f}"


def _month_name(month: int) -> str:
    # month number -> month names for summary output --month
    names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]
    return names[month - 1]


def cmd_add(args: argparse.Namespace) -> int:
    service = ExpenseService()
    expense = service.add_expense(
        description=args.description,
        amount=args.amount,
        category=args.category,
        spent_on=None,  # current date as default date
    )
    print(f"# Expense added successfully (ID: {expense.id})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    service = ExpenseService()
    expenses = service.list_expenses()

    if not expenses:
        print("# No expenses found.")
        return 0

    # header
    print("# ID  Date        Description  Amount")
    for e in expenses:
        # expense output format
        print(f"# {e.id:<3} {e.date.isoformat():<10}  {e.description:<11}  {_fmt_money(e.amount)}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    service = ExpenseService()

    # update at least one field
    if args.description is None and args.amount is None and args.category is None:
        raise ValueError("Nothing to update. Provide --description and/or --amount and/or --category.")

    service.update_expense(
        expense_id=args.id,
        description=args.description,
        amount=args.amount,
        category=args.category,
    )
    print("# Expense updated successfully")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    service = ExpenseService()
    service.delete_expense(args.id)
    print("# Expense deleted successfully")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    service = ExpenseService()

    if args.month is None:
        total = service.total_expenses()
        print(f"# Total expenses: {_fmt_money(total)}")
        return 0

    # month summary (current year)
    month = args.month
    total = service.total_expenses_for_month(month=month, year=date.today().year)
    print(f"# Total expenses for {_month_name(month)}: {_fmt_money(total)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expense-tracker",
        description="A simple CLI expense tracker."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = subparsers.add_parser("add", help="Add an expense")
    p_add.add_argument("--description", required=True, help="Expense description")
    p_add.add_argument("--amount", required=True, type=float, help="Expense amount (non-negative)")
    p_add.add_argument("--category", required=False, default=None, help="Expense category (optional)")
    p_add.set_defaults(func=cmd_add)

    # list
    p_list = subparsers.add_parser("list", help="List all expenses")
    p_list.set_defaults(func=cmd_list)

    # update
    p_upd = subparsers.add_parser("update", help="Update an expense by ID")
    p_upd.add_argument("--id", required=True, type=int, help="Expense ID")
    p_upd.add_argument("--description", required=False, default=None, help="New description")
    p_upd.add_argument("--amount", required=False, type=float, default=None, help="New amount (non-negative)")
    p_upd.add_argument("--category", required=False, default=None, help="New category")
    p_upd.set_defaults(func=cmd_update)

    # delete
    p_del = subparsers.add_parser("delete", help="Delete an expense by ID")
    p_del.add_argument("--id", required=True, type=int, help="Expense ID")
    p_del.set_defaults(func=cmd_delete)

    # summary
    p_sum = subparsers.add_parser("summary", help="Show expense summary")
    p_sum.add_argument("--month", required=False, type=int, default=None, help="Month (1-12) of current year")
    p_sum.set_defaults(func=cmd_summary)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except ValueError as e:
        # error output format
        print(f"# Error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
