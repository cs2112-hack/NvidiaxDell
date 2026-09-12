# Review packet: catala

target: {"module": "groundtransport", "path": "catala/modules/groundtransport.catala_en"}

## Source document (authoritative)

### EXP-POL E-5.1 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-5 Ground transport

Standard class rail travel is reimbursable at actual cost.

### EXP-POL E-5.2 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-5 Ground transport

First class rail travel is reimbursable only where the ticket cost does
not exceed the standard class fare for the same journey booked on the day of
travel, or where Pre-Approval has been obtained.

### EXP-POL E-5.3 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-5 Ground transport

Private hire and taxi journeys are reimbursable at actual cost up to
£60 per journey, save that a journey to or from an airport before 06:00 or after
22:00 is reimbursable at actual cost without limit.


## Artefact under review

```
# Expense Reimbursement Policy — ground transport

> Module GroundTransport

> Using ExpenseDefs

## Prologue — declarations

```catala-metadata
declaration enumeration RailClass:
  -- StandardClass
  -- FirstClass

declaration scope RailFare:
  input class_of_travel content RailClass
  input ticket_cost content money
  input standard_class_fare_same_day content money
  input pre_approval content ExpenseDefs.PreApproval
  input incurred_on content date
  internal is_standard_class content boolean
  internal is_first_class content boolean
  internal pre_approval_obtained content boolean
  output reimbursable_amount content money

declaration scope TaxiFare:
  input journey_cost content money
  input is_airport_journey content boolean
  input journey_start_hour content integer
  input journey_start_minute content integer
  internal minutes_after_midnight content integer
  output reimbursable_amount content money
```

## E-5.1 Standard class rail

| EXP-POL E-5.1 (002-expense-reimbursement-policy.md:82)
|
| Standard class rail travel is reimbursable at actual cost.

```catala
scope RailFare:
  definition is_standard_class equals class_of_travel with pattern StandardClass

  label e5_1 definition reimbursable_amount
    under condition is_standard_class
    consequence equals ticket_cost
```

## E-5.2 First class rail

| EXP-POL E-5.2 (002-expense-reimbursement-policy.md:84)
|
| First class rail travel is reimbursable only where the ticket cost does
| not exceed the standard class fare for the same journey booked on the day of
| travel, or where Pre-Approval has been obtained.

```catala
scope RailFare:
  definition is_first_class equals class_of_travel with pattern FirstClass

  definition pre_approval_obtained equals
    (output of ExpenseDefs.PreApprovalValid with {
       -- pre_approval: pre_approval
       -- expense_incurred_at: incurred_on
       -- expense_amount: ticket_cost
     }).valid

  label e5_2 definition reimbursable_amount
    under condition is_first_class
    consequence equals $0.00

  label e5_2_permitted exception e5_2 definition reimbursable_amount
    under condition
      is_first_class
      and (ticket_cost <= standard_class_fare_same_day
           or pre_approval_obtained)
    consequence equals ticket_cost
```

## E-5.3 Private hire and taxi

| EXP-POL E-5.3 (002-expense-reimbursement-policy.md:88)
|
| Private hire and taxi journeys are reimbursable at actual cost up to
| £60 per journey, save that a journey to or from an airport before 06:00 or after
| 22:00 is reimbursable at actual cost without limit.

```catala
scope TaxiFare:
  definition minutes_after_midnight equals
    journey_start_hour * 60 + journey_start_minute

  label e5_3 definition reimbursable_amount equals
    Money.min of journey_cost, $60.00

  label e5_3_airport exception e5_3 definition reimbursable_amount
    under condition
      is_airport_journey
      and (minutes_after_midnight < 6 * 60
           or minutes_after_midnight > 22 * 60)
    consequence equals journey_cost
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/groundtransport.catala_en",
  "scopes": {
    "RailFare": {
      "input": [
        "class_of_travel",
        "ticket_cost",
        "standard_class_fare_same_day",
        "pre_approval",
        "incurred_on"
      ],
      "output": [
        "reimbursable_amount"
      ],
      "internal": [
        "is_standard_class",
        "is_first_class",
        "pre_approval_obtained"
      ],
      "context": []
    },
    "TaxiFare": {
      "input": [
        "journey_cost",
        "is_airport_journey",
        "journey_start_hour",
        "journey_start_minute"
      ],
      "output": [
        "reimbursable_amount"
      ],
      "internal": [
        "minutes_after_midnight"
      ],
      "context": []
    }
  }
}