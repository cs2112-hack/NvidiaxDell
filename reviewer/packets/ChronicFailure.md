# Review packet: catala

target: {"module": "ChronicFailure", "path": "catala/modules/ChronicFailure.catala_en"}

## Source document (authoritative)

### MSA-SCH4 L-6.1 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-6 Chronic failure

Where the Availability Percentage is less than 99.90% in each of three
consecutive Measurement Periods, or in any four Measurement Periods in a rolling
twelve-month period, the Customer may terminate the Agreement in respect of the
affected Service on 30 days' written notice, without liability for early
termination charges.

### MSA-SCH4 L-6.2 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-6 Chronic failure

The right in L-6.1 must be exercised within 30 days after the end of the
Measurement Period which gives rise to it, failing which it lapses in respect of
that occurrence.


## Artefact under review

```
# MSA Schedule 4 — Chronic failure

> Module ChronicFailure

## Prologue — declarations

```catala-metadata
declaration structure PeriodAvailability:
  data period_start content date
  data period_end content date
  data availability_percentage content decimal

declaration scope ChronicFailureRight:
  input availability_history content list of PeriodAvailability
  input assessment_date content date
  input notice_given content boolean
  input notice_date content date
  output failing_periods content list of PeriodAvailability
  output consecutive_trigger content boolean
  output rolling_trigger content boolean
  output right_arisen content boolean
  output completing_periods content list of PeriodAvailability
  output trigger_period_end content optional of date
  output notice_period_days content integer
  output early_termination_charges_payable content boolean
  output exercise_deadline content optional of date
  output right_lapsed content boolean
  output right_exercisable content boolean
```

## L-6.1 The two triggers

| MSA-SCH4 L-6.1 (004-msa-sla-credits.md:102)
|
| Where the Availability Percentage is less than 99.90% in each of three
| consecutive Measurement Periods, or in any four Measurement Periods in a rolling
| twelve-month period, the Customer may terminate the Agreement in respect of the
| affected Service on 30 days' written notice, without liability for early
| termination charges.

```catala
scope ChronicFailureRight:
  definition failing_periods equals
    list of p among availability_history such that
      p.availability_percentage < 99.90

  definition consecutive_trigger equals
    number of (list of p among failing_periods such that
      ((exists q among failing_periods such that
          q.period_start = p.period_start - 1 month)
       and (exists r among failing_periods such that
          r.period_start = p.period_start - 2 month))) > 0

  definition rolling_trigger equals
    number of (list of p among failing_periods such that
      (number of (list of q among failing_periods such that
         ((q.period_start <= p.period_start)
          and (q.period_start >= p.period_start - 11 month)))) >= 4) > 0

  definition right_arisen equals consecutive_trigger or rolling_trigger

  definition completing_periods equals
    list of p among failing_periods such that
      (((exists q among failing_periods such that
           q.period_start = p.period_start - 1 month)
        and (exists r among failing_periods such that
           r.period_start = p.period_start - 2 month))
       or (number of (list of q among failing_periods such that
             ((q.period_start <= p.period_start)
              and (q.period_start >= p.period_start - 11 month)))) >= 4)

  definition notice_period_days equals 30

  definition early_termination_charges_payable equals false
```

## L-6.2 The exercise window

| MSA-SCH4 L-6.2 (004-msa-sla-credits.md:108)
|
| The right in L-6.1 must be exercised within 30 days after the end of the
| Measurement Period which gives rise to it, failing which it lapses in respect of
| that occurrence.

```catala
scope ChronicFailureRight:
  definition trigger_period_end equals
    if right_arisen then
      Present content
        (minimum of (map each p among completing_periods to p.period_end)
         or if list empty then assessment_date)
    else Absent

  definition exercise_deadline equals
    match trigger_period_end with pattern
    -- Absent: Absent
    -- Present content d: Present content (d + 30 day)

  label l6_2_not_lapsed definition right_lapsed equals false

  label l6_2_lapsed exception l6_2_not_lapsed definition right_lapsed
    under condition
      right_arisen
      and (match exercise_deadline with pattern
           -- Absent: false
           -- Present content d:
               (if notice_given then notice_date > d else assessment_date > d))
    consequence equals true

  definition right_exercisable equals right_arisen and (not right_lapsed)
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/ChronicFailure.catala_en",
  "scopes": {
    "ChronicFailureRight": {
      "input": [
        "availability_history",
        "assessment_date",
        "notice_given",
        "notice_date"
      ],
      "output": [
        "failing_periods",
        "consecutive_trigger",
        "rolling_trigger",
        "right_arisen",
        "completing_periods",
        "trigger_period_end",
        "notice_period_days",
        "early_termination_charges_payable",
        "exercise_deadline",
        "right_lapsed",
        "right_exercisable"
      ],
      "internal": [],
      "context": []
    }
  }
}