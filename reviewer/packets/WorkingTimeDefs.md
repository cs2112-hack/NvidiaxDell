# Review packet: catala

target: {"module": "WorkingTimeDefs", "path": "catala/modules/WorkingTimeDefs.catala_en"}

## Source document (authoritative)

### EMP-ANNEX-C C-2.1 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-2 Definitions

"Base Hourly Rate" means the Employee's annual base salary divided by
2,080.

### EMP-ANNEX-C C-2.2 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-2 Definitions

"Payroll Week" means the period of seven consecutive days commencing
at 00:00 on Monday and ending at 23:59:59 on the following Sunday.

### EMP-ANNEX-C C-2.3 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-2 Definitions

"Night Hours" means hours worked between 22:00 on any day and 06:00 on
the following day.

### EMP-ANNEX-C C-2.4 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-2 Definitions

"Grade" means the Employee's job grade as recorded in the Company's
human resources system of record at the first day of the Payroll Week in
question.

### EMP-ANNEX-C C-2.5 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-2 Definitions

"Critical Incident Response" means work performed in response to an
incident classified as Severity 1 under the Company's incident management
standard, during the period from declaration of the incident until its formal
closure.

### EMP-ANNEX-C C-2.6 — Employment Terms — Annex C: Working Time, Overtime and Leave (v4.2, effective 2024-01-01)
Section C-2 Definitions

"Gazetted Public Holiday" means a day designated as a bank or public
holiday in the Employee's principal place of work.


## Artefact under review

```
#

> Module WorkingTimeDefs

##

```catala-metadata
declaration structure PayrollWeek:
  data first_day content date
  data last_day content date

declaration structure GradeRecord:
  data grade content integer
  data recorded_from content date

declaration structure CriticalIncident:
  data severity content integer
  data declared_at content date
  data closed_at content optional of date

declaration structure GazettedHoliday:
  data place_of_work content integer
  data holiday_date content date

declaration scope BaseHourlyRate:
  input annual_base_salary content money
  output base_hourly_rate content decimal
  output base_hourly_rate_to_the_penny content money

declaration scope PayrollWeekOf:
  input calendar_day content date
  output days_since_monday content integer
  output payroll_week content PayrollWeek

declaration scope NightHours:
  input hour_beginning content integer
  output is_night_hour content boolean

declaration scope GradeForPayrollWeek:
  input grade_records content list of GradeRecord
  input payroll_week content PayrollWeek
  internal records_in_force content list of GradeRecord
  output grade content optional of integer

declaration scope CriticalIncidentResponse:
  input incident content CriticalIncident
  input work_performed_at content date
  input performed_in_response_to_incident content boolean
  output within_incident_period content boolean
  output is_critical_incident_response content boolean

declaration scope GazettedPublicHolidays:
  input gazette content list of GazettedHoliday
  input principal_place_of_work content integer
  input calendar_day content date
  output is_gazetted_public_holiday content boolean
```

## C-2.1

| EMP-ANNEX-C C-2.1 (001-employment-terms-annex-c.md:28)
|
| "Base Hourly Rate" means the Employee's annual base salary divided by
| 2,080.

```catala
scope BaseHourlyRate:
  assertion annual_base_salary >= $0.00

  definition base_hourly_rate equals
    (annual_base_salary / $1.00) / 2080.0

  definition base_hourly_rate_to_the_penny equals
    money of base_hourly_rate
```

## C-2.2

| EMP-ANNEX-C C-2.2 (001-employment-terms-annex-c.md:31)
|
| "Payroll Week" means the period of seven consecutive days commencing at
| 00:00 on Monday and ending at 23:59:59 on the following Sunday.

```catala
scope PayrollWeekOf:
  date round down

scope PayrollWeekOf:
  definition days_since_monday equals
    match (Date.day_of_week of calendar_day) with pattern
    -- Monday: 0
    -- Tuesday: 1
    -- Wednesday: 2
    -- Thursday: 3
    -- Friday: 4
    -- Saturday: 5
    -- Sunday: 6

  definition payroll_week equals
    PayrollWeek {
      -- first_day: calendar_day - (days_since_monday * (1 day))
      -- last_day: calendar_day + ((6 - days_since_monday) * (1 day))
    }
```

## C-2.3

| EMP-ANNEX-C C-2.3 (001-employment-terms-annex-c.md:34)
|
| "Night Hours" means hours worked between 22:00 on any day and 06:00 on the
| following day.

```catala
scope NightHours:
  assertion hour_beginning >= 0 and hour_beginning <= 23

  definition is_night_hour equals
    hour_beginning >= 22 or hour_beginning < 6
```

## C-2.4

| EMP-ANNEX-C C-2.4 (001-employment-terms-annex-c.md:37)
|
| "Grade" means the Employee's job grade as recorded in the Company's human
| resources system of record at the first day of the Payroll Week in
| question.

```catala
scope GradeForPayrollWeek:
  definition records_in_force equals
    list of r among grade_records
      such that r.recorded_from <= payroll_week.first_day

  definition grade equals
    if number of records_in_force = 0 then Absent
    else Present content
      (content of r among records_in_force
         such that r.recorded_from is maximum
         or if list empty then
           GradeRecord {
             -- grade: 0
             -- recorded_from: payroll_week.first_day
           }
      ).grade
```

## C-2.5

| EMP-ANNEX-C C-2.5 (001-employment-terms-annex-c.md:41)
|
| "Critical Incident Response" means work performed in response to an
| incident classified as Severity 1 under the Company's incident management
| standard, during the period from declaration of the incident until its
| formal closure.

```catala
scope CriticalIncidentResponse:
  definition within_incident_period equals
    work_performed_at >= incident.declared_at
    and (match incident.closed_at with pattern
         -- Absent: true
         -- Present content closure: work_performed_at <= closure)

  definition is_critical_incident_response equals
    performed_in_response_to_incident
    and incident.severity = 1
    and within_incident_period
```

## C-2.6

| EMP-ANNEX-C C-2.6 (001-employment-terms-annex-c.md:46)
|
| "Gazetted Public Holiday" means a day designated as a bank or public
| holiday in the Employee's principal place of work.

```catala
scope GazettedPublicHolidays:
  definition is_gazetted_public_holiday equals
    exists h among gazette such that
      (h.place_of_work = principal_place_of_work
       and h.holiday_date = calendar_day)
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/WorkingTimeDefs.catala_en",
  "scopes": {
    "BaseHourlyRate": {
      "input": [
        "annual_base_salary"
      ],
      "output": [
        "base_hourly_rate",
        "base_hourly_rate_to_the_penny"
      ],
      "internal": [],
      "context": []
    },
    "PayrollWeekOf": {
      "input": [
        "calendar_day"
      ],
      "output": [
        "days_since_monday",
        "payroll_week"
      ],
      "internal": [],
      "context": []
    },
    "NightHours": {
      "input": [
        "hour_beginning"
      ],
      "output": [
        "is_night_hour"
      ],
      "internal": [],
      "context": []
    },
    "GradeForPayrollWeek": {
      "input": [
        "grade_records",
        "payroll_week"
      ],
      "output": [
        "grade"
      ],
      "internal": [
        "records_in_force"
      ],
      "context": []
    },
    "CriticalIncidentResponse": {
      "input": [
        "incident",
        "work_performed_at",
        "performed_in_response_to_incident"
      ],
      "output": [
        "within_incident_period",
        "is_critical_incident_response"
      ],
      "internal": [],
      "context": []
    },
    "GazettedPublicHolidays": {
      "input": [
        "gazette",
        "principal_place_of_work",
        "calendar_day"
      ],
      "output": [
        "is_gazetted_public_holiday"
      ],
      "internal": [],
      "context": []
    }
  }
}