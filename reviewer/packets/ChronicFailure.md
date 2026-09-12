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

### Not quoted by the artefact

The clauses above cross-refer to the provisions below, or use terms they define.

### MSA-SCH4 L-2.1 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Measurement Period" means each calendar month.

### MSA-SCH4 L-2.3 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Availability Percentage" means, in respect of a Measurement Period,
the total number of minutes in the Measurement Period less Unavailable Minutes,
divided by the total number of minutes in the Measurement Period, expressed as a
percentage and rounded to two decimal places.


## Artefact under review

```
#

> Module ChronicFailure

##

```catala-metadata
declaration structure PeriodAvailability:
  data period_start content date
  data period_end content date
  data availability_percentage content decimal

declaration structure OccurrenceAssessment:
  data trigger_period_start content date
  data trigger_period_end content date
  data window_opens content date
  data exercise_deadline content date
  data arisen content boolean
  data window_open content boolean
  data exercised_by_notice content boolean
  data lapsed content boolean
  data exercisable content boolean

declaration scope ChronicFailureRight:
  input availability_history content list of PeriodAvailability
  input assessment_date content date
  input notice_given content boolean
  input notice_date content date
  output failing_periods content list of PeriodAvailability
  output failing_measurement_periods content list of PeriodAvailability
  output consecutive_trigger content boolean
  output rolling_trigger content boolean
  output right_arisen content boolean
  output completing_periods content list of PeriodAvailability
  output occurrences content list of OccurrenceAssessment
  output arisen_occurrences content list of OccurrenceAssessment
  output live_occurrences content list of OccurrenceAssessment
  output exercised_occurrences content list of OccurrenceAssessment
  output lapsed_occurrences content list of OccurrenceAssessment
  output governing_occurrences content list of OccurrenceAssessment
  output trigger_period_end content optional of date
  output notice_period_days content integer
  output early_termination_charges_payable content boolean
  output exercise_deadline content optional of date
  output notice_exercises_the_right content boolean
  output right_lapsed content boolean
  output right_exercisable content boolean
```

## L-6.1

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

  definition failing_measurement_periods equals
    combine all p among failing_periods in acc
    initially []
    with (if (exists q among acc such that q.period_start = p.period_start)
          then acc
          else acc ++ [p])

  definition consecutive_trigger equals
    number of (list of p among failing_measurement_periods such that
      ((exists q among failing_measurement_periods such that
          q.period_start = p.period_start - 1 month)
       and (exists r among failing_measurement_periods such that
          r.period_start = p.period_start - 2 month))) > 0

  definition rolling_trigger equals
    number of (list of p among failing_measurement_periods such that
      (number of (list of q among failing_measurement_periods such that
         ((q.period_start <= p.period_start)
          and (q.period_start >= p.period_start - 11 month)))) >= 4) > 0

  definition right_arisen equals consecutive_trigger or rolling_trigger

  definition completing_periods equals
    list of p among failing_measurement_periods such that
      (((exists q among failing_measurement_periods such that
           q.period_start = p.period_start - 1 month)
        and (exists r among failing_measurement_periods such that
           r.period_start = p.period_start - 2 month))
       or (number of (list of q among failing_measurement_periods such that
             ((q.period_start <= p.period_start)
              and (q.period_start >= p.period_start - 11 month)))) >= 4)

  definition notice_period_days equals 30

  definition early_termination_charges_payable equals false
```

## L-6.2

| MSA-SCH4 L-6.2 (004-msa-sla-credits.md:108)
|
| The right in L-6.1 must be exercised within 30 days after the end of the
| Measurement Period which gives rise to it, failing which it lapses in respect of
| that occurrence.

```catala
scope ChronicFailureRight:
  definition occurrences equals
    map each p among completing_periods to
      (let has_arisen equals p.period_end <= assessment_date in
       let deadline equals p.period_end + 30 day in
       let is_open equals has_arisen and (assessment_date <= deadline) in
       let exercised equals
         has_arisen
         and notice_given
         and (notice_date > p.period_end)
         and (notice_date <= deadline)
       in
       OccurrenceAssessment {
         -- trigger_period_start: p.period_start
         -- trigger_period_end: p.period_end
         -- window_opens: p.period_end + 1 day
         -- exercise_deadline: deadline
         -- arisen: has_arisen
         -- window_open: is_open
         -- exercised_by_notice: exercised
         -- lapsed: has_arisen and (not is_open) and (not exercised)
         -- exercisable: has_arisen and (is_open or exercised)
       })

  definition arisen_occurrences equals
    list of o among occurrences such that o.arisen

  definition live_occurrences equals
    list of o among occurrences such that o.window_open

  definition exercised_occurrences equals
    list of o among occurrences such that o.exercised_by_notice

  definition lapsed_occurrences equals
    list of o among occurrences such that o.lapsed

  definition governing_occurrences equals
    if (number of exercised_occurrences) > 0 then exercised_occurrences
    else if (number of live_occurrences) > 0 then live_occurrences
    else if (number of arisen_occurrences) > 0 then arisen_occurrences
    else occurrences

  definition trigger_period_end equals
    if right_arisen then
      Present content
        (maximum of
           (map each o among governing_occurrences to o.trigger_period_end)
         or if list empty then assessment_date)
    else Absent

  definition exercise_deadline equals
    match trigger_period_end with pattern
    -- Absent: Absent
    -- Present content d: Present content (d + 30 day)
```

## L-6.2

| MSA-SCH4 L-6.2 (004-msa-sla-credits.md:108)
|
| The right in L-6.1 must be exercised within 30 days after the end of the
| Measurement Period which gives rise to it, failing which it lapses in respect of
| that occurrence.

```catala
scope ChronicFailureRight:
  definition notice_exercises_the_right equals
    notice_given and ((number of exercised_occurrences) > 0)

  label l6_2_not_lapsed definition right_lapsed equals false

  label l6_2_lapsed exception l6_2_not_lapsed definition right_lapsed
    under condition
      right_arisen
      and ((number of arisen_occurrences) > 0)
      and ((number of lapsed_occurrences) = (number of arisen_occurrences))
    consequence equals true

  definition right_exercisable equals
    right_arisen
    and ((number of (list of o among occurrences such that o.exercisable)) > 0)
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
        "failing_measurement_periods",
        "consecutive_trigger",
        "rolling_trigger",
        "right_arisen",
        "completing_periods",
        "occurrences",
        "arisen_occurrences",
        "live_occurrences",
        "exercised_occurrences",
        "lapsed_occurrences",
        "governing_occurrences",
        "trigger_period_end",
        "notice_period_days",
        "early_termination_charges_payable",
        "exercise_deadline",
        "notice_exercises_the_right",
        "right_lapsed",
        "right_exercisable"
      ],
      "internal": [],
      "context": []
    }
  }
}