# Review packet: catala

target: {"module": "QuotaRelief", "path": "catala/modules/QuotaRelief.catala_en"}

## Source document (authoritative)

### COMM-PLAN S-6.1 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-6 Quota relief

Where an Employee is absent on statutory parental leave, adoption leave
or long-term sickness absence for a continuous period of 30 days or more, the
Employee's Quota is reduced by 1/365th of the annual Quota for each day of such
absence.

### COMM-PLAN S-6.2 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-6 Quota relief

Quota relief under S-6.1 is applied before Attainment is calculated.

### Not quoted by the artefact

The clauses above cross-refer to the provisions below, or use terms they define.

### COMM-PLAN S-2.1 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-2 Definitions

"Quota" means the annual revenue target assigned to the Employee in
writing at the start of the Plan Year, or on joining if later.

### COMM-PLAN S-2.2 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-2 Definitions

"Attainment" means Net Booked Revenue expressed as a percentage of
Quota.


## Artefact under review

```
#

> Module QuotaRelief

> Using Commission

##

```catala-metadata
declaration enumeration AbsenceCategory:
  -- StatutoryParentalLeave
  -- AdoptionLeave
  -- LongTermSicknessAbsence
  -- OtherAbsence

declaration structure AbsencePeriod:
  data category content AbsenceCategory
  data start_date content date
  data end_date content date

declaration scope QualifyingAbsence:
  input absence content AbsencePeriod
  output days_of_absence content integer
  output is_qualifying_category content boolean
  output is_qualifying content boolean
  output in_year_days_of_absence content integer

declaration scope RelievedQuota:
  input annual_quota content money
  input absence_periods content list of AbsencePeriod
  internal qualifying_category_absences content list of AbsencePeriod
  internal continuous_absence_windows content list of date
  output relief_days content integer
  output relief_exact content decimal
  output relief content money
  output relieved_quota_exact content decimal
  output relieved_quota content money
  output quota_for_attainment_exact content decimal
  output quota_for_attainment content money

declaration scope CommissionAfterRelief:
  input assigned_quota content money
  input absence_periods content list of AbsencePeriod
  input net_booked_revenue content money
  input new_logo_share content decimal
  input commencement_date content date
  input annual_base_salary content money
  output quota_relief scope RelievedQuota
  output commission scope Commission.CommissionPayable
  output quota_for_attainment content money
  output commission_payable content money
```

## S-6

| COMM-PLAN S-6.1 (003-sales-commission-plan.md:83)
|
| Where an Employee is absent on statutory parental leave, adoption leave
| or long-term sickness absence for a continuous period of 30 days or more, the
| Employee's Quota is reduced by 1/365th of the annual Quota for each day of such
| absence.

```catala
scope QualifyingAbsence:
  definition days_of_absence equals
    integer of ((absence.end_date - absence.start_date) / (1 day)) + 1

  definition is_qualifying_category equals
    absence.category with pattern StatutoryParentalLeave
    or absence.category with pattern AdoptionLeave
    or absence.category with pattern LongTermSicknessAbsence

  definition is_qualifying equals
    is_qualifying_category
    and days_of_absence >= continuous_period_length
```

## S-6.1

| COMM-PLAN S-6.1 (003-sales-commission-plan.md:83)
|
| Where an Employee is absent on statutory parental leave, adoption leave
| or long-term sickness absence for a continuous period of 30 days or more, the
| Employee's Quota is reduced by 1/365th of the annual Quota for each day of such
| absence.

```catala
scope QualifyingAbsence:
  definition in_year_days_of_absence equals
    let first_day equals
      Date.max of absence.start_date, Commission.plan_year_start
    in
    let last_day equals
      Date.min of absence.end_date, Commission.plan_year_end
    in
    if last_day < first_day then 0
    else integer of ((last_day - first_day) / (1 day)) + 1
```

## S-6.1

| COMM-PLAN S-6.1 (003-sales-commission-plan.md:83)
|
| Where an Employee is absent on statutory parental leave, adoption leave
| or long-term sickness absence for a continuous period of 30 days or more, the
| Employee's Quota is reduced by 1/365th of the annual Quota for each day of such
| absence.

```catala
declaration day_of_qualifying_absence content boolean
  depends on d content date, periods content list of AbsencePeriod
  equals
    exists p among periods such that p.start_date <= d and d <= p.end_date

declaration continuous_period_length content integer equals 30

declaration window_is_continuous_absence content boolean
  depends on w content date, periods content list of AbsencePeriod
  equals
    for all i among (List.sequence of 0, continuous_period_length) we have
      day_of_qualifying_absence of (w + i * (1 day)), periods

declaration continuity_window_starts content list of date equals
  let days_in_plan_year equals
    integer of
      ((Commission.plan_year_end - Commission.plan_year_start) / (1 day)) + 1
  in
  let n equals days_in_plan_year + continuous_period_length - 1 in
  map each i among (List.sequence of 0, n)
  to Commission.plan_year_start
     - (continuous_period_length - 1) * (1 day)
     + i * (1 day)

scope RelievedQuota:
  definition qualifying_category_absences equals
    list of a among absence_periods
    such that
      (output of QualifyingAbsence with { -- absence: a }).is_qualifying_category

  definition continuous_absence_windows equals
    list of w among continuity_window_starts
    such that window_is_continuous_absence of w, qualifying_category_absences
```

## S-6.1

| COMM-PLAN S-6.1 (003-sales-commission-plan.md:83)
|
| Where an Employee is absent on statutory parental leave, adoption leave
| or long-term sickness absence for a continuous period of 30 days or more, the
| Employee's Quota is reduced by 1/365th of the annual Quota for each day of such
| absence.

```catala
declaration plan_year_days content list of date equals
  let days_in_plan_year equals
    integer of
      ((Commission.plan_year_end - Commission.plan_year_start) / (1 day)) + 1
  in
  map each i among (List.sequence of 0, days_in_plan_year)
  to Commission.plan_year_start + i * (1 day)

scope RelievedQuota:
  definition relief_days equals
    number of (list of d among plan_year_days
               such that (exists w among continuous_absence_windows
                          such that w <= d
                                and d <= w
                                     + (continuous_period_length - 1)
                                       * (1 day)))
```

## S-6.1

| COMM-PLAN S-6.1 (003-sales-commission-plan.md:83)
|
| Where an Employee is absent on statutory parental leave, adoption leave
| or long-term sickness absence for a continuous period of 30 days or more, the
| Employee's Quota is reduced by 1/365th of the annual Quota for each day of such
| absence.

```catala
scope RelievedQuota:
  definition relief_exact equals
    (decimal of annual_quota) * (decimal of relief_days / 365.0)

  definition relieved_quota_exact equals
    (decimal of annual_quota) - relief_exact

  definition relief equals money of relief_exact

  definition relieved_quota equals money of relieved_quota_exact

  assertion number of plan_year_days = 365
  assertion relief_days <= 365
  assertion relieved_quota_exact >= 0.0
```

## S-6.2

| COMM-PLAN S-6.2 (003-sales-commission-plan.md:88)
|
| Quota relief under S-6.1 is applied before Attainment is calculated.

```catala
scope RelievedQuota:
  definition quota_for_attainment_exact equals relieved_quota_exact

  definition quota_for_attainment equals relieved_quota
```

| COMM-PLAN S-6.2 (003-sales-commission-plan.md:88)
|
| Quota relief under S-6.1 is applied before Attainment is calculated.

```catala
scope CommissionAfterRelief:
  definition quota_relief.annual_quota equals assigned_quota
  definition quota_relief.absence_periods equals absence_periods

  definition quota_for_attainment equals quota_relief.quota_for_attainment

  definition commission.quota equals quota_relief.quota_for_attainment_exact
  definition commission.net_booked_revenue equals net_booked_revenue
  definition commission.new_logo_share equals new_logo_share
  definition commission.commencement_date equals commencement_date
  definition commission.annual_base_salary equals annual_base_salary

  definition commission_payable equals commission.commission_payable
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/QuotaRelief.catala_en",
  "scopes": {
    "QualifyingAbsence": {
      "input": [
        "absence"
      ],
      "output": [
        "days_of_absence",
        "is_qualifying_category",
        "is_qualifying",
        "in_year_days_of_absence"
      ],
      "internal": [],
      "context": []
    },
    "RelievedQuota": {
      "input": [
        "annual_quota",
        "absence_periods"
      ],
      "output": [
        "relief_days",
        "relief_exact",
        "relief",
        "relieved_quota_exact",
        "relieved_quota",
        "quota_for_attainment_exact",
        "quota_for_attainment"
      ],
      "internal": [
        "qualifying_category_absences",
        "continuous_absence_windows"
      ],
      "context": []
    },
    "CommissionAfterRelief": {
      "input": [
        "assigned_quota",
        "absence_periods",
        "net_booked_revenue",
        "new_logo_share",
        "commencement_date",
        "annual_base_salary"
      ],
      "output": [
        "quota_for_attainment",
        "commission_payable"
      ],
      "internal": [],
      "context": []
    }
  }
}