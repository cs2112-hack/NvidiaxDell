# Review packet: catala

target: {"module": "expensedefs", "path": "catala/modules/expensedefs.catala_en"}

## Source document (authoritative)

### EXP-POL E-2.1 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-2 Definitions

"City Tier" means the tier assigned to the city in which the
expenditure is incurred, as published in the Finance intranet schedule, being
Tier 1, Tier 2 or Tier 3.

### EXP-POL E-2.2 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-2 Definitions

"Client-Billable Travel" means travel which is recoverable from a
client under the terms of an executed statement of work.

### EXP-POL E-2.3 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-2 Definitions

"Travel Day" means each calendar day, or part of a calendar day, on
which the Employee is away from their principal place of work overnight on
Company business. The day of departure and the day of return are each a Travel
Day.

### EXP-POL E-2.4 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-2 Definitions

"Pre-Approval" means written approval obtained before the expense is
incurred from a person holding delegated financial authority at or above the
threshold of the expense.


## Artefact under review

```
# Expense Reimbursement Policy — defined terms

> Module ExpenseDefs

## Prologue — declarations

```catala-metadata
declaration enumeration CityTier:
  -- Tier1
  -- Tier2
  -- Tier3

declaration structure ClientBillable:
  data is_client_billable content boolean
  data sow_per_diem content optional of money

declaration structure PreApproval:
  data obtained content boolean
  data in_writing content boolean
  data obtained_at content date
  data approver_authority_limit content money
  data specified_amount content optional of money

declaration scope TravelDayCount:
  input departure_date content date
  input return_date content date
  output travel_days content integer

declaration scope PreApprovalValid:
  input pre_approval content PreApproval
  input expense_incurred_at content date
  input expense_amount content money
  output valid content boolean
```

## E-2.1 City Tier

| EXP-POL E-2.1 (002-expense-reimbursement-policy.md:28)
|
| "City Tier" means the tier assigned to the city in which the
| expenditure is incurred, as published in the Finance intranet schedule, being
| Tier 1, Tier 2 or Tier 3.

## E-2.2 Client-Billable Travel

| EXP-POL E-2.2 (002-expense-reimbursement-policy.md:32)
|
| "Client-Billable Travel" means travel which is recoverable from a
| client under the terms of an executed statement of work.

## E-2.3 Travel Day

| EXP-POL E-2.3 (002-expense-reimbursement-policy.md:35)
|
| "Travel Day" means each calendar day, or part of a calendar day, on
| which the Employee is away from their principal place of work overnight on
| Company business. The day of departure and the day of return are each a Travel
| Day.

```catala
scope TravelDayCount:
  label e2_3 definition travel_days
    under condition return_date <= departure_date
    consequence equals 0

  label e2_3 definition travel_days
    under condition return_date > departure_date
    consequence equals
      integer of ((return_date - departure_date) / (1 day)) + 1
```

## E-2.4 Pre-Approval

| EXP-POL E-2.4 (002-expense-reimbursement-policy.md:40)
|
| "Pre-Approval" means written approval obtained before the expense is
| incurred from a person holding delegated financial authority at or above the
| threshold of the expense.

| NOTE: "obtained before the expense is incurred" is encoded as a strict
| comparison of *dates*, which is the finest granularity the corpus offers.
| An approval given earlier on the same calendar day as the expense was in fact
| obtained before the expense, but at date granularity it is indistinguishable
| from one given afterwards, and this encoding treats it as too late. The
| alternative — treating same-day approval as valid — would let an approval
| obtained after the spend qualify. Resolving this needs a timestamp the corpus
| does not define.

```catala
scope PreApprovalValid:
  definition valid equals
    pre_approval.obtained
    and pre_approval.in_writing
    and pre_approval.obtained_at < expense_incurred_at
    and pre_approval.approver_authority_limit >= expense_amount
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/expensedefs.catala_en",
  "scopes": {
    "TravelDayCount": {
      "input": [
        "departure_date",
        "return_date"
      ],
      "output": [
        "travel_days"
      ],
      "internal": [],
      "context": []
    },
    "PreApprovalValid": {
      "input": [
        "pre_approval",
        "expense_incurred_at",
        "expense_amount"
      ],
      "output": [
        "valid"
      ],
      "internal": [],
      "context": []
    }
  }
}