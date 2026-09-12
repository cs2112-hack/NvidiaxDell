# Review packet: catala

target: {"module": "retention", "path": "catala/modules/retention.catala_en"}

## Source document (authoritative)

### DATA-RET R-2.1 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-2 Definitions

"Data Class" means the classification assigned to a record under the
Company's information classification policy, being one of: Employee Record,
Customer Contract, Financial Record, Marketing Contact, Security Log, or
Candidate Record.

### DATA-RET R-2.2 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-2 Definitions

"Retention Trigger" means the event from which the retention period is
measured, as specified in R-3.

### DATA-RET R-3.1 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-3 Retention periods

Records are retained for the following periods measured from the
applicable Retention Trigger:

  (a) Employee Record: 6 years from the end of employment;
  (b) Customer Contract: 7 years from the expiry or termination of the contract;
  (c) Financial Record: 7 years from the end of the financial year to which it
      relates;
  (d) Marketing Contact: 24 months from the later of the date of collection and
      the date of the most recent engagement by the contact;
  (e) Security Log: 13 months from the date of the logged event;
  (f) Candidate Record: 12 months from the date on which the recruitment process
      concluded.

### DATA-RET R-3.2 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-3 Retention periods

By way of exception to R-3.1(f), where the candidate has given consent
to be retained on a talent pool, the Candidate Record is retained for 24 months
from the date of consent, and the period restarts on each renewal of consent.

### DATA-RET R-3.3 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-3 Retention periods

By way of exception to R-3.1(a), records required to be retained for a
longer period under pensions or payroll legislation are retained for the period
so required.


## Artefact under review

```
# Data Retention and Deletion Standard — R-2 and R-3 retention periods

> Module Retention

## Prologue — declarations

| DATA-RET R-2.1 (006-data-retention-standard.md:26)
|
| "Data Class" means the classification assigned to a record under the
| Company's information classification policy, being one of: Employee
| Record, Customer Contract, Financial Record, Marketing Contact, Security
| Log, or Candidate Record.

| DATA-RET R-2.2 (006-data-retention-standard.md:31)
|
| "Retention Trigger" means the event from which the retention period is
| measured, as specified in R-3.

```catala-metadata
declaration enumeration DataClass:
  -- EmployeeRecord
  -- CustomerContract
  -- FinancialRecord
  -- MarketingContact
  -- SecurityLog
  -- CandidateRecord

declaration scope RetentionEnd:
  input data_class content DataClass
  input retention_trigger_date content date
  input most_recent_engagement_date content optional of date
  input talent_pool_consent_date content optional of date
  input consent_renewal_dates content list of date
  input statutory_retention_applies content boolean
  input statutory_retention_period content duration
  output retention_end_date content date
```

| NO-CLAUSE: the rounding mode is a compiler directive, not a rule. Catala
| date addition raises on an ambiguous result, and this scope adds month and
| year durations to arbitrary trigger dates (6 years from 29 February, 13
| months from 31 January), so a mode must be declared. `date round down` is
| chosen because R-1.2 states that where this Standard specifies a maximum
| period business units are expected to delete earlier: the period is a
| ceiling, so resolving an ambiguous month-end to the previous existing day
| never authorises retention for a day the Standard does not allow.
| Rounding up would extend every such period by a day.
|
| That justification is about the Standard's own periods, and R-3.3's period
| is not one of them: it is imposed by pensions or payroll legislation, and a
| statutory retention period is a floor rather than a maximum. Rounding it
| down would have the Company destroy a record a day before the legislation
| allows — a breach, where rounding an R-1.2 maximum down is at worst early
| deletion the Standard positively encourages. So R-3.3 rounds the other way,
| by calling `Date.add_round_up` explicitly rather than relying on the scope
| mode, exactly as `NdaSurvival` rounds its periods of protection up
| throughout. The scope mode stays `date round down` because every other
| period in this scope is an R-1.2 maximum, and limb (a)'s own six years
| keep rounding down even where R-3.3 displaces the result.

```catala
scope RetentionEnd:
  date round down
```

## R-3 Retention periods

| DATA-RET R-3.1 (006-data-retention-standard.md:45)
|
| Records are retained for the following periods measured from the
| applicable Retention Trigger:
|
|   (a) Employee Record: 6 years from the end of employment;
|   (b) Customer Contract: 7 years from the expiry or termination of the
|       contract;
|   (c) Financial Record: 7 years from the end of the financial year to
|       which it relates;
|   (d) Marketing Contact: 24 months from the later of the date of
|       collection and the date of the most recent engagement by the
|       contact;
|   (e) Security Log: 13 months from the date of the logged event;
|   (f) Candidate Record: 12 months from the date on which the recruitment
|       process concluded.

```catala
scope RetentionEnd:
  label r3_1_a definition retention_end_date
    under condition data_class with pattern EmployeeRecord
    consequence equals retention_trigger_date + 6 year

  label r3_1_b definition retention_end_date
    under condition data_class with pattern CustomerContract
    consequence equals retention_trigger_date + 7 year

  label r3_1_c definition retention_end_date
    under condition data_class with pattern FinancialRecord
    consequence equals retention_trigger_date + 7 year

  label r3_1_d definition retention_end_date
    under condition data_class with pattern MarketingContact
    consequence equals
      (match most_recent_engagement_date with pattern
       -- Absent: retention_trigger_date
       -- Present content engaged:
            Date.max of retention_trigger_date, engaged)
      + 24 month

  label r3_1_e definition retention_end_date
    under condition data_class with pattern SecurityLog
    consequence equals retention_trigger_date + 13 month

  label r3_1_f definition retention_end_date
    under condition data_class with pattern CandidateRecord
    consequence equals retention_trigger_date + 12 month
```

| DATA-RET R-3.2 (006-data-retention-standard.md:58)
|
| By way of exception to R-3.1(f), where the candidate has given consent to
| be retained on a talent pool, the Candidate Record is retained for 24
| months from the date of consent, and the period restarts on each renewal
| of consent.

```catala
scope RetentionEnd:
  label r3_2 exception r3_1_f definition retention_end_date
    under condition
      (data_class with pattern CandidateRecord)
      and (talent_pool_consent_date with pattern Present)
    consequence equals
      (match talent_pool_consent_date with pattern
       -- Absent: retention_trigger_date
       -- Present content consented:
            Date.max of
              consented,
              (maximum of consent_renewal_dates
               or if list empty then consented))
      + 24 month
```

| DATA-RET R-3.3 (006-data-retention-standard.md:62)
|
| By way of exception to R-3.1(a), records required to be retained for a
| longer period under pensions or payroll legislation are retained for the
| period so required.

```catala
scope RetentionEnd:
  assertion
    if statutory_retention_applies
    then (Date.add_round_up of
            retention_trigger_date, statutory_retention_period)
         >= retention_trigger_date
    else true

  label r3_3 exception r3_1_a definition retention_end_date
    under condition
      (data_class with pattern EmployeeRecord)
      and statutory_retention_applies
    consequence equals
      Date.max of
        (retention_trigger_date + 6 year),
        (Date.add_round_up of
           retention_trigger_date, statutory_retention_period)
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/retention.catala_en",
  "scopes": {
    "RetentionEnd": {
      "input": [
        "data_class",
        "retention_trigger_date",
        "most_recent_engagement_date",
        "talent_pool_consent_date",
        "consent_renewal_dates",
        "statutory_retention_applies",
        "statutory_retention_period"
      ],
      "output": [
        "retention_end_date"
      ],
      "internal": [],
      "context": []
    }
  }
}