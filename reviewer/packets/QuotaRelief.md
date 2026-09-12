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


## Artefact under review

```
# Sales Commission Plan — FY26: quota relief

> Module QuotaRelief

## Prologue — declarations

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
  output relief_days content integer

declaration scope RelievedQuota:
  input annual_quota content money
  input absence_periods content list of AbsencePeriod
  output relief_days content integer
  output relief content money
  output relieved_quota content money
  output quota_for_attainment content money
```

## S-6 Quota relief

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
    is_qualifying_category and days_of_absence >= 30

  definition relief_days equals
    if is_qualifying then days_of_absence else 0

scope RelievedQuota:
  definition relief_days equals
    Integer.sum of (map each a among absence_periods to
      (output of QualifyingAbsence with { -- absence: a }).relief_days)

  definition relief equals
    annual_quota * (decimal of relief_days / 365.0)

  definition relieved_quota equals
    annual_quota - relief
```

| COMM-PLAN S-6.2 (003-sales-commission-plan.md:88)
|
| Quota relief under S-6.1 is applied before Attainment is calculated.

```catala
scope RelievedQuota:
  definition quota_for_attainment equals relieved_quota
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
        "relief_days"
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
        "relief",
        "relieved_quota",
        "quota_for_attainment"
      ],
      "internal": [],
      "context": []
    }
  }
}