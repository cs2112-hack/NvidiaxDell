# Review packet: catala

target: {"module": "overtime", "path": "catala/modules/overtime.catala_en"}

## Source document (authoritative)

### EMP-ANNEX-C C-3.1 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-3 Ordinary hours

The ordinary working week is 40 hours.

### EMP-ANNEX-C C-4.1 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-4 Overtime — general entitlement

An Employee is entitled to an overtime payment in respect of each hour
worked in excess of 40 hours in a Payroll Week, calculated at 1.25 times the
Base Hourly Rate.

### EMP-ANNEX-C C-4.2 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-4 Overtime — general entitlement

By way of exception to C-4.1, each hour worked in excess of 48 hours in
a Payroll Week is calculated at 1.5 times the Base Hourly Rate.

### EMP-ANNEX-C C-5.1 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-5 Overtime — grade exceptions

By way of exception to C-4, an Employee of Grade 5 or above is not
entitled to an overtime payment. Such an Employee accrues time off in lieu at
the rate of one hour for each hour worked in excess of 40 hours in a Payroll
Week.

### EMP-ANNEX-C C-5.2 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-5 Overtime — grade exceptions

Notwithstanding C-5.1, an Employee of Grade 5 or above is entitled to
an overtime payment calculated in accordance with C-4 in respect of hours worked
on Critical Incident Response.

### EMP-ANNEX-C C-7.1 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-7 Public holidays

Each hour worked on a Gazetted Public Holiday is calculated at 2.0
times the Base Hourly Rate. This rate is payable in substitution for, and not in
addition to, any amount otherwise payable under C-4.

### EMP-ANNEX-C C-7.2 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-7 Public holidays

By way of exception to C-7.1, where hours worked on a Gazetted Public
Holiday are also hours worked in excess of 48 hours in the Payroll Week, the
applicable rate is the greater of (a) 2.0 times the Base Hourly Rate and (b) the
rate determined under C-4.2 plus 0.25.

### EMP-ANNEX-C C-6.1 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-6 Night work premium

A premium of 0.15 times the Base Hourly Rate is payable in respect of
each Night Hour worked. This premium is payable in addition to, and not in
substitution for, any amount payable under C-4, C-5.2 or C-7.

### EMP-ANNEX-C C-6.2 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-6 Night work premium

By way of exception to C-6.1, the night premium is not payable in
respect of an Employee who receives a standing shift allowance under their
individual contract of employment.

### EMP-ANNEX-C C-8.1 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-8 Precedence

Where two or more provisions of C-4, C-5 or C-7 would apply to the same
hour, only the single highest applicable multiplier applies to that hour.

### EMP-ANNEX-C C-8.2 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-8 Precedence

C-8.1 does not apply to the night premium under C-6, which is
cumulative with any multiplier determined under C-4, C-5.2 or C-7.


## Artefact under review

```
# Employment Terms — Annex C: Working Time and Overtime

> Module Overtime

## Prologue — declarations

```catala-metadata
declaration structure HourWorked:
  data ordinal content integer
  data is_public_holiday content boolean
  data is_critical_incident content boolean
  data is_night content boolean

declaration scope HourPremium:
  input ordinal content integer
  input is_public_holiday content boolean
  input is_critical_incident content boolean
  input is_night content boolean
  input grade content integer
  input has_standing_shift_allowance content boolean
  output multiplier content decimal
  output night_premium content decimal
  output total_rate content decimal
  output accrues_toil content boolean

declaration scope WeeklyOvertime:
  input hours content list of HourWorked
  input base_hourly_rate content money
  input grade content integer
  input has_standing_shift_allowance content boolean
  output overtime_payment content money
  output toil_hours content decimal
```

## C-3 Ordinary hours

| EMP-ANNEX-C C-3.1 (001-employment-terms-annex-c.md:51)
|
| The ordinary working week is 40 hours.

```catala
scope HourPremium:
  label ordinary definition multiplier equals 0.0
```

## C-4 Overtime — general entitlement

| EMP-ANNEX-C C-4.1 (001-employment-terms-annex-c.md:55)
|
| An Employee is entitled to an overtime payment in respect of each hour
| worked in excess of 40 hours in a Payroll Week, calculated at 1.25 times the
| Base Hourly Rate.

```catala
scope HourPremium:
  label c4_1 exception ordinary definition multiplier
    under condition ordinal > 40
    consequence equals 1.25
```

| EMP-ANNEX-C C-4.2 (001-employment-terms-annex-c.md:59)
|
| By way of exception to C-4.1, each hour worked in excess of 48 hours in a
| Payroll Week is calculated at 1.5 times the Base Hourly Rate.

```catala
scope HourPremium:
  label c4_2 exception c4_1 definition multiplier
    under condition ordinal > 48
    consequence equals 1.5
```

## C-5 Overtime — grade exceptions

| EMP-ANNEX-C C-5.1 (001-employment-terms-annex-c.md:64)
|
| By way of exception to C-4, an Employee of Grade 5 or above is not entitled
| to an overtime payment. Such an Employee accrues time off in lieu at the
| rate of one hour for each hour worked in excess of 40 hours in a Payroll
| Week.

```catala
scope HourPremium:
  label c5_1 exception c4_2 definition multiplier
    under condition grade >= 5 and ordinal > 40
    consequence equals 0.0
```

| EMP-ANNEX-C C-5.2 (001-employment-terms-annex-c.md:69)
|
| Notwithstanding C-5.1, an Employee of Grade 5 or above is entitled to an
| overtime payment calculated in accordance with C-4 in respect of hours
| worked on Critical Incident Response.

```catala
scope HourPremium:
  label c5_2 exception c5_1 definition multiplier
    under condition
      grade >= 5 and ordinal > 40 and is_critical_incident
    consequence equals
      if ordinal > 48 then 1.5 else 1.25
```

## C-7 Public holidays

| EMP-ANNEX-C C-7.1 (001-employment-terms-annex-c.md:85)
|
| Each hour worked on a Gazetted Public Holiday is calculated at 2.0 times the
| Base Hourly Rate. This rate is payable in substitution for, and not in
| addition to, any amount otherwise payable under C-4.

```catala
scope HourPremium:
  label c7_1 exception c5_2 definition multiplier
    under condition is_public_holiday
    consequence equals 2.0
```

| EMP-ANNEX-C C-7.2 (001-employment-terms-annex-c.md:89)
|
| By way of exception to C-7.1, where hours worked on a Gazetted Public
| Holiday are also hours worked in excess of 48 hours in the Payroll Week, the
| applicable rate is the greater of (a) 2.0 times the Base Hourly Rate and (b)
| the rate determined under C-4.2 plus 0.25.

```catala
scope HourPremium:
  label c7_2 exception c7_1 definition multiplier
    under condition is_public_holiday and ordinal > 48
    consequence equals
      if 2.0 > (1.5 + 0.25) then 2.0 else (1.5 + 0.25)
```

## C-6 Night work premium

| EMP-ANNEX-C C-6.1 (001-employment-terms-annex-c.md:75)
|
| A premium of 0.15 times the Base Hourly Rate is payable in respect of each
| Night Hour worked. This premium is payable in addition to, and not in
| substitution for, any amount payable under C-4, C-5.2 or C-7.

```catala
scope HourPremium:
  label c6_1 definition night_premium
    equals if is_night then 0.15 else 0.0
```

| EMP-ANNEX-C C-6.2 (001-employment-terms-annex-c.md:79)
|
| By way of exception to C-6.1, the night premium is not payable in respect of
| an Employee who receives a standing shift allowance under their individual
| contract of employment.

```catala
scope HourPremium:
  label c6_2 exception c6_1 definition night_premium
    under condition has_standing_shift_allowance
    consequence equals 0.0
```

## C-8 Precedence

| EMP-ANNEX-C C-8.1 (001-employment-terms-annex-c.md:96)
|
| Where two or more provisions of C-4, C-5 or C-7 would apply to the same
| hour, only the single highest applicable multiplier applies to that hour.

| EMP-ANNEX-C C-8.2 (001-employment-terms-annex-c.md:99)
|
| C-8.1 does not apply to the night premium under C-6, which is cumulative
| with any multiplier determined under C-4, C-5.2 or C-7.

```catala
scope HourPremium:
  definition total_rate equals multiplier + night_premium
```

| EMP-ANNEX-C C-5.1 (001-employment-terms-annex-c.md:64)
|
| By way of exception to C-4, an Employee of Grade 5 or above is not entitled
| to an overtime payment. Such an Employee accrues time off in lieu at the
| rate of one hour for each hour worked in excess of 40 hours in a Payroll
| Week.

```catala
scope HourPremium:
  label toil_base definition accrues_toil equals false

  label toil_c5_1 exception toil_base definition accrues_toil
    under condition grade >= 5 and ordinal > 40
    consequence equals true

  label toil_c5_2 exception toil_c5_1 definition accrues_toil
    under condition
      grade >= 5 and ordinal > 40
      and (is_critical_incident or is_public_holiday)
    consequence equals false
```

## Weekly aggregation

| NO-CLAUSE: Annex C states no weekly aggregation rule. Summing the per-hour
| entitlements that C-4 to C-8 confer adds no law of its own; the legal content
| is entirely in HourPremium above.

```catala
scope WeeklyOvertime:
  definition overtime_payment equals
    Money.sum of (map each h among hours to
      (output of HourPremium with {
         -- ordinal: h.ordinal
         -- is_public_holiday: h.is_public_holiday
         -- is_critical_incident: h.is_critical_incident
         -- is_night: h.is_night
         -- grade: grade
         -- has_standing_shift_allowance: has_standing_shift_allowance
       }).total_rate * base_hourly_rate)

  definition toil_hours equals
    Decimal.sum of (map each h among hours to
      (if (output of HourPremium with {
             -- ordinal: h.ordinal
             -- is_public_holiday: h.is_public_holiday
             -- is_critical_incident: h.is_critical_incident
             -- is_night: h.is_night
             -- grade: grade
             -- has_standing_shift_allowance: has_standing_shift_allowance
           }).accrues_toil
       then 1.0 else 0.0))
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/overtime.catala_en",
  "scopes": {
    "HourPremium": {
      "input": [
        "ordinal",
        "is_public_holiday",
        "is_critical_incident",
        "is_night",
        "grade",
        "has_standing_shift_allowance"
      ],
      "output": [
        "multiplier",
        "night_premium",
        "total_rate",
        "accrues_toil"
      ],
      "internal": [],
      "context": []
    },
    "WeeklyOvertime": {
      "input": [
        "hours",
        "base_hourly_rate",
        "grade",
        "has_standing_shift_allowance"
      ],
      "output": [
        "overtime_payment",
        "toil_hours"
      ],
      "internal": [],
      "context": []
    }
  }
}