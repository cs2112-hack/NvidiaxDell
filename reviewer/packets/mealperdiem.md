# Review packet: catala

target: {"module": "mealperdiem", "path": "catala/modules/mealperdiem.catala_en"}

## Source document (authoritative)

### EXP-POL E-3.1 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-3 Meal per diem

An Employee is entitled to reimbursement of meal expenses on each
Travel Day up to the following amounts:

  (a) Tier 1 city: £75 per Travel Day;
  (b) Tier 2 city: £55 per Travel Day;
  (c) Tier 3 city: £40 per Travel Day.

### EXP-POL E-3.2 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-3 Meal per diem

By way of exception to E-3.1, where the travel is Client-Billable
Travel, the applicable per diem is the amount specified in the relevant
statement of work, or if none is specified, 1.5 times the amount under E-3.1.

### EXP-POL E-3.3 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-3 Meal per diem

By way of exception to E-3.1 and E-3.2, where Pre-Approval has been
obtained for a specified higher amount, that amount applies.

### EXP-POL E-3.4 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-3 Meal per diem

Where a meal is provided at no cost to the Employee as part of a
conference, client event or airline service, the per diem for that Travel Day is
reduced by £15 in respect of each such meal.

### EXP-POL E-3.5 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-3 Meal per diem

No per diem is payable in respect of a Travel Day on which the Employee
does not incur any meal expense.

### EXP-POL E-7.2 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-7 Relationship with other documents

Where an Employee incurs expenses while engaged on Critical Incident
Response within the meaning of Annex C, paragraph C-2.5, the per diem under E-3.1
is increased by £20 per Travel Day and Pre-Approval is not required for that
increase.


## Artefact under review

```
# Expense Reimbursement Policy — meal per diem

> Module MealPerDiem

> Using ExpenseDefs

## Prologue — declarations

```catala-metadata
declaration scope MealPerDiemForDay:
  input city_tier content ExpenseDefs.CityTier
  input client_billable content ExpenseDefs.ClientBillable
  input pre_approval content ExpenseDefs.PreApproval
  input incurred_on content date
  input meals_provided_free content integer
  input meal_expense_incurred content money
  input on_critical_incident_response content boolean
  internal per_diem_under_e3_1 content money
  internal per_diem_apart_from_e3_3 content money
  internal pre_approval_effective content boolean
  internal e3_4_reduction content money
  output per_diem_cap content money
  output adjusted_per_diem content money
  output reimbursable_amount content money
```

## Order of operations

## E-3.1 Meal per diem — the tier table

| EXP-POL E-3.1 (002-expense-reimbursement-policy.md:46)
|
| An Employee is entitled to reimbursement of meal expenses on each
| Travel Day up to the following amounts:
|
|   (a) Tier 1 city: £75 per Travel Day;
|   (b) Tier 2 city: £55 per Travel Day;
|   (c) Tier 3 city: £40 per Travel Day.

```catala
declaration tier_meal_per_diem content money
  depends on tier content ExpenseDefs.CityTier
  equals
    match tier with pattern
    -- Tier1: $75.00
    -- Tier2: $55.00
    -- Tier3: $40.00

scope MealPerDiemForDay:
  label e3_1 definition per_diem_under_e3_1 equals
    tier_meal_per_diem of city_tier

  label e3_1_cap definition per_diem_cap equals per_diem_under_e3_1
```

## E-3.2 Client-Billable Travel

| EXP-POL E-3.2 (002-expense-reimbursement-policy.md:53)
|
| By way of exception to E-3.1, where the travel is Client-Billable
| Travel, the applicable per diem is the amount specified in the relevant
| statement of work, or if none is specified, 1.5 times the amount under E-3.1.

```catala
declaration client_billable_per_diem content money
  depends on cb content ExpenseDefs.ClientBillable,
             amount_under_e3_1 content money
  equals
    match cb.sow_per_diem with pattern
    -- Present content specified: specified
    -- Absent: amount_under_e3_1 * 1.5

scope MealPerDiemForDay:
  label e3_2 exception e3_1_cap definition per_diem_cap
    under condition client_billable.is_client_billable
    consequence equals
      client_billable_per_diem of client_billable, per_diem_under_e3_1
```

## E-3.3 Pre-Approved higher amount

| EXP-POL E-3.3 (002-expense-reimbursement-policy.md:57)
|
| By way of exception to E-3.1 and E-3.2, where Pre-Approval has been
| obtained for a specified higher amount, that amount applies.

```catala
scope MealPerDiemForDay:
  definition pre_approval_effective equals
    match pre_approval.specified_amount with pattern
    -- Absent: false
    -- Present content specified:
      (output of ExpenseDefs.PreApprovalValid with {
         -- pre_approval: pre_approval
         -- expense_incurred_at: incurred_on
         -- expense_amount: specified
       }).valid

  definition per_diem_apart_from_e3_3 equals
    if client_billable.is_client_billable
    then client_billable_per_diem of client_billable, per_diem_under_e3_1
    else per_diem_under_e3_1

  label e3_3 exception e3_2 definition per_diem_cap
    under condition
      pre_approval_effective
      and (match pre_approval.specified_amount with pattern
           -- Absent: false
           -- Present content specified:
             specified > per_diem_apart_from_e3_3)
    consequence equals
      match pre_approval.specified_amount with pattern
      -- Absent: $0.00
      -- Present content specified: specified
```

## E-3.4 Meals provided at no cost

| EXP-POL E-3.4 (002-expense-reimbursement-policy.md:60)
|
| Where a meal is provided at no cost to the Employee as part of a
| conference, client event or airline service, the per diem for that Travel Day is
| reduced by £15 in respect of each such meal.

```catala
scope MealPerDiemForDay:
  definition e3_4_reduction equals $15.00 * decimal of meals_provided_free

  definition adjusted_per_diem equals
    Money.max of $0.00, (per_diem_cap - e3_4_reduction)
```

## E-3.1 again — reimbursement up to the day's per diem

| EXP-POL E-3.1 (002-expense-reimbursement-policy.md:46)
|
| An Employee is entitled to reimbursement of meal expenses on each
| Travel Day up to the following amounts:
|
|   (a) Tier 1 city: £75 per Travel Day;
|   (b) Tier 2 city: £55 per Travel Day;
|   (c) Tier 3 city: £40 per Travel Day.

```catala
scope MealPerDiemForDay:
  label e3_1_reimbursement definition reimbursable_amount equals
    Money.min of meal_expense_incurred, adjusted_per_diem
```

## E-3.5 No meal expense incurred

| EXP-POL E-3.5 (002-expense-reimbursement-policy.md:64)
|
| No per diem is payable in respect of a Travel Day on which the Employee
| does not incur any meal expense.

```catala
scope MealPerDiemForDay:
  label e3_5 exception e3_1_reimbursement definition reimbursable_amount
    under condition meal_expense_incurred = $0.00
    consequence equals $0.00
```

## E-7.2 Critical Incident Response uplift

| EXP-POL E-7.2 (002-expense-reimbursement-policy.md:109)
|
| Where an Employee incurs expenses while engaged on Critical Incident
| Response within the meaning of Annex C, paragraph C-2.5, the per diem under E-3.1
| is increased by £20 per Travel Day and Pre-Approval is not required for that
| increase.

```catala
scope MealPerDiemForDay:
  label e7_2 exception e3_1 definition per_diem_under_e3_1
    under condition on_critical_incident_response
    consequence equals (tier_meal_per_diem of city_tier) + $20.00
```

| NOTE: E-7.2 is genuinely ambiguous where it meets E-3.2, and the ambiguity is
| worth £22.50 a day on a Tier 1 Client-Billable Travel Day for which the
| statement of work specifies no figure. The documents do not resolve it.
|
| Reading (A), adopted here: E-7.2 amends the quantum "under E-3.1" to £95, and
| E-3.2 then applies its multiplier to "the amount under E-3.1" as so amended,
| giving 1.5 x (75 + 20) = £142.50. The textual warrant is that E-7.2 operates
| on a named quantity rather than on the claim, and that E-3.2's fallback limb
| computes from that very quantity by name. On this reading the closing words of
| E-7.2 also do real work: absent them, a per diem above the £75 table figure
| would appear to need Pre-Approval under E-3.3.
|
| Reading (B), rejected: E-7.2 is a flat uplift to whatever per diem applies,
| giving 1.5 x 75 + 20 = £132.50. Its warrant is that on Client-Billable Travel
| the applicable per diem is not "under E-3.1" at all but under E-3.2, so
| E-7.2's stated target does not exist and the £20 can only attach to the figure
| that does apply. Reading (B) also keeps the uplift alive where E-3.3
| substitutes a Pre-Approved amount, which reading (A) does not — under reading
| (A) a Pre-Approved amount displaces the uplifted E-3.1 figure entirely, and
| the £20 survives only through E-3.3's "higher amount" test, which measures the
| approved figure against the uplifted £95.
|
| Reading (A) is adopted because it follows the words E-7.2 actually uses. The
| choice is pinned by `TestE7_2_ClientBillable_ReadingA` in
| `catala/tests/test_mealperdiem.catala_en`, which also records reading (B)'s
| number, so changing the reading changes a failing test rather than silently
| changing an answer.

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/mealperdiem.catala_en",
  "scopes": {
    "MealPerDiemForDay": {
      "input": [
        "city_tier",
        "client_billable",
        "pre_approval",
        "incurred_on",
        "meals_provided_free",
        "meal_expense_incurred",
        "on_critical_incident_response"
      ],
      "output": [
        "per_diem_cap",
        "adjusted_per_diem",
        "reimbursable_amount"
      ],
      "internal": [
        "per_diem_under_e3_1",
        "per_diem_apart_from_e3_3",
        "pre_approval_effective",
        "e3_4_reduction"
      ],
      "context": []
    }
  }
}