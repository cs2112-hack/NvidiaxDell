# Review packet: catala

target: {"module": "claimwindow", "path": "catala/modules/claimwindow.catala_en"}

## Source document (authoritative)

### EXP-POL E-6.1 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-6 Submission and time limits

A claim must be submitted within 60 days after the date on which the
expense was incurred.

### EXP-POL E-6.2 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-6 Submission and time limits

By way of exception to E-6.1, a claim submitted after 60 days but
within 120 days may be reimbursed where the Employee's line manager certifies
that the delay was for good reason.

### EXP-POL E-6.3 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-6 Submission and time limits

No claim submitted more than 120 days after the expense was incurred
will be reimbursed in any circumstances.


## Artefact under review

```
# Expense Reimbursement Policy — submission and time limits

> Module ClaimWindow

## Prologue — declarations

```catala-metadata
declaration scope ClaimAdmissible:
  input expense_incurred_date content date
  input submission_date content date
  input delay_certified_for_good_reason content boolean
  output days_elapsed content integer
  output admissible content boolean
```

## E-6.1 The 60-day window

| EXP-POL E-6.1 (002-expense-reimbursement-policy.md:94)
|
| A claim must be submitted within 60 days after the date on which the
| expense was incurred.

```catala
scope ClaimAdmissible:
  definition days_elapsed equals
    integer of ((submission_date - expense_incurred_date) / (1 day))

  label e6_1 definition admissible equals days_elapsed <= 60

  assertion submission_date >= expense_incurred_date
```

## E-6.2 Late claim certified for good reason

| EXP-POL E-6.2 (002-expense-reimbursement-policy.md:97)
|
| By way of exception to E-6.1, a claim submitted after 60 days but
| within 120 days may be reimbursed where the Employee's line manager certifies
| that the delay was for good reason.

```catala
scope ClaimAdmissible:
  label e6_2 exception e6_1 definition admissible
    under condition
      days_elapsed > 60
      and days_elapsed <= 120
      and delay_certified_for_good_reason
    consequence equals true
```

## E-6.3 The 120-day long-stop

| EXP-POL E-6.3 (002-expense-reimbursement-policy.md:101)
|
| No claim submitted more than 120 days after the expense was incurred
| will be reimbursed in any circumstances.

```catala
scope ClaimAdmissible:
  label e6_3 exception e6_2 definition admissible
    under condition days_elapsed > 120
    consequence equals false
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/claimwindow.catala_en",
  "scopes": {
    "ClaimAdmissible": {
      "input": [
        "expense_incurred_date",
        "submission_date",
        "delay_certified_for_good_reason"
      ],
      "output": [
        "days_elapsed",
        "admissible"
      ],
      "internal": [],
      "context": []
    }
  }
}