# Review packet: catala

target: {"module": "LeaveAccrual", "path": "catala/modules/LeaveAccrual.catala_en"}

## Source document (authoritative)

### EMP-ANNEX-C C-9.1 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-9 Annual leave accrual

An Employee accrues annual leave at the rate of 2.0 days for each
completed calendar month of service.

### EMP-ANNEX-C C-9.2 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-9 Annual leave accrual

Where an Employee commences or terminates employment part-way through a
calendar month, accrual in respect of that month is pro-rated by reference to
the number of days of service in that month as a proportion of the number of
days in that month, rounded to the nearest half day, with an exact quarter
rounded upwards.

### EMP-ANNEX-C C-9.3 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-9 Annual leave accrual

An Employee may carry forward to the next leave year a maximum of 5
accrued but untaken days.

### EMP-ANNEX-C C-9.4 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-9 Annual leave accrual

By way of exception to C-9.3, an Employee who was prevented from taking
accrued leave by reason of long-term sickness absence may carry forward the
whole of their accrued but untaken balance.

### EMP-ANNEX-C C-9.5 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-9 Annual leave accrual

Accrued but untaken leave in excess of the amount permitted to be
carried forward is forfeited at the end of the leave year and is not payable on
termination.


## Artefact under review

```
#

> Module LeaveAccrual

##

```catala-metadata
declaration scope HalfDayRounding:
  input raw_days content decimal
  output half_steps content decimal
  output half_steps_below content decimal
  output excess_half_steps content decimal
  output is_exact_quarter content boolean
  output rounded_days content decimal

declaration scope MonthlyAccrual:
  input day_in_month content date
  input service_start content date
  input service_end content optional of date
  internal month_first_day content date
  internal month_last_day content date
  rounding scope HalfDayRounding
  output days_in_month content integer
  output days_of_service_in_month content integer
  output month_is_completed content boolean
  output pro_rated_days content decimal
  output days_accrued content decimal

declaration scope CarryForward:
  input accrued_untaken_days content decimal
  input prevented_from_taking_leave_by_long_term_sickness content boolean
  output permitted_carry_forward_days content decimal
  output carry_forward_days content decimal
  output forfeited_days content decimal
  output forfeited_days_payable_on_termination content boolean
```

##

| NO-CLAUSE

```catala
scope MonthlyAccrual:
  date round down

scope MonthlyAccrual:
  definition month_first_day equals Date.first_day_of_month of day_in_month
  definition month_last_day equals Date.last_day_of_month of day_in_month
```

## C-9.1

| EMP-ANNEX-C C-9.1 (001-employment-terms-annex-c.md:104)
|
| An Employee accrues annual leave at the rate of 2.0 days for each
| completed calendar month of service.

```catala
scope MonthlyAccrual:
  assertion
    match service_end with pattern
    -- Absent: true
    -- Present content ending: ending >= service_start

  definition month_is_completed equals
    service_start <= month_first_day
    and (match service_end with pattern
         -- Absent: true
         -- Present content ending: ending >= month_last_day)

  label c9_1 definition days_accrued
    under condition month_is_completed
    consequence equals 2.0
```

## C-9.2

| EMP-ANNEX-C C-9.2 (001-employment-terms-annex-c.md:107)
|
| Where an Employee commences or terminates employment part-way through a
| calendar month, accrual in respect of that month is pro-rated by reference
| to the number of days of service in that month as a proportion of the
| number of days in that month, rounded to the nearest half day, with an
| exact quarter rounded upwards.

```catala
scope MonthlyAccrual:
  definition days_in_month equals Date.get_day of month_last_day

  definition days_of_service_in_month equals
    let period_start equals Date.max of service_start, month_first_day in
    let period_end equals
      (match service_end with pattern
       -- Absent: month_last_day
       -- Present content ending: Date.min of ending, month_last_day) in
    if period_end < period_start then 0
    else integer of ((period_end - period_start) / (1 day)) + 1

  definition rounding.raw_days equals
    2.0 * (decimal of days_of_service_in_month)
        / (decimal of days_in_month)

  definition pro_rated_days equals rounding.rounded_days

  label c9_2 exception c9_1 definition days_accrued
    under condition not month_is_completed
    consequence equals pro_rated_days
```

### C-9.2

| EMP-ANNEX-C C-9.2 (001-employment-terms-annex-c.md:107)
|
| Where an Employee commences or terminates employment part-way through a
| calendar month, accrual in respect of that month is pro-rated by reference
| to the number of days of service in that month as a proportion of the
| number of days in that month, rounded to the nearest half day, with an
| exact quarter rounded upwards.

```catala
scope HalfDayRounding:
  definition half_steps equals raw_days * 2.0

  definition half_steps_below equals
    Decimal.round_by_default of half_steps

  definition excess_half_steps equals half_steps - half_steps_below

  definition is_exact_quarter equals excess_half_steps = 0.5

  label nearest_half definition rounded_days
    under condition excess_half_steps < 0.5
    consequence equals half_steps_below / 2.0

  label nearest_half definition rounded_days
    under condition excess_half_steps > 0.5
    consequence equals (half_steps_below + 1.0) / 2.0

  label exact_quarter_up exception nearest_half definition rounded_days
    under condition excess_half_steps = 0.5
    consequence equals (half_steps_below + 1.0) / 2.0
```

## C-9.3

| EMP-ANNEX-C C-9.3 (001-employment-terms-annex-c.md:113)
|
| An Employee may carry forward to the next leave year a maximum of 5
| accrued but untaken days.

```catala
scope CarryForward:
  label c9_3 definition permitted_carry_forward_days equals 5.0

  definition carry_forward_days equals
    Decimal.min of accrued_untaken_days, permitted_carry_forward_days
```

## C-9.4

| EMP-ANNEX-C C-9.4 (001-employment-terms-annex-c.md:116)
|
| By way of exception to C-9.3, an Employee who was prevented from taking
| accrued leave by reason of long-term sickness absence may carry forward
| the whole of their accrued but untaken balance.

```catala
scope CarryForward:
  label c9_4 exception c9_3 definition permitted_carry_forward_days
    under condition prevented_from_taking_leave_by_long_term_sickness
    consequence equals accrued_untaken_days
```

## C-9.5

| EMP-ANNEX-C C-9.5 (001-employment-terms-annex-c.md:120)
|
| Accrued but untaken leave in excess of the amount permitted to be carried
| forward is forfeited at the end of the leave year and is not payable on
| termination.

```catala
scope CarryForward:
  definition forfeited_days equals
    accrued_untaken_days - carry_forward_days

  definition forfeited_days_payable_on_termination equals false
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/LeaveAccrual.catala_en",
  "scopes": {
    "HalfDayRounding": {
      "input": [
        "raw_days"
      ],
      "output": [
        "half_steps",
        "half_steps_below",
        "excess_half_steps",
        "is_exact_quarter",
        "rounded_days"
      ],
      "internal": [],
      "context": []
    },
    "MonthlyAccrual": {
      "input": [
        "day_in_month",
        "service_start",
        "service_end"
      ],
      "output": [
        "days_in_month",
        "days_of_service_in_month",
        "month_is_completed",
        "pro_rated_days",
        "days_accrued"
      ],
      "internal": [
        "month_first_day",
        "month_last_day"
      ],
      "context": []
    },
    "CarryForward": {
      "input": [
        "accrued_untaken_days",
        "prevented_from_taking_leave_by_long_term_sickness"
      ],
      "output": [
        "permitted_carry_forward_days",
        "carry_forward_days",
        "forfeited_days",
        "forfeited_days_payable_on_termination"
      ],
      "internal": [],
      "context": []
    }
  }
}