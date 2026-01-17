# Expense Tracker
This repository is for a private pratice project: Expense Tracker implemented with python. 
## Features
Application runs from cmd and has the following features:

- Users can add an expense with a description and amount.

- Users can update an expense.

- Users can delete an expense.

- Users can view all expenses.

- Users can view a summary of all expenses.

- Users can view a summary of expenses for a specific month (of current year).

## Additional Features
Here are some additional features I may add to the application (implementation determined by availability):

- Add expense categories and allow users to filter expenses by category.

- Allow users to set a budget for each month and show a warning when the user exceeds the budget.

- Allow users to export expenses to a CSV file.

## How to use
The list of commands and their expected output is shown below:

```
$ expense-tracker add --description "Lunch" --amount 20
# Expense added successfully (ID: 1)

$ expense-tracker add --description "Dinner" --amount 10
# Expense added successfully (ID: 2)

$ expense-tracker list
# ID  Date       Description  Amount
# 1   2024-08-06  Lunch        $20
# 2   2024-08-06  Dinner       $10

$ expense-tracker summary
# Total expenses: $30

$ expense-tracker delete --id 2
# Expense deleted successfully

$ expense-tracker summary
# Total expenses: $20

$ expense-tracker summary --month 8
# Total expenses for August: $20
```