# Review packet: catala

target: {"module": "LegalHold", "path": "catala/modules/LegalHold.catala_en"}

## Source document (authoritative)

### DATA-RET R-2.3 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-2 Definitions

"Legal Hold" means a documented instruction issued by the General
Counsel or their delegate to preserve specified records in connection with
actual or reasonably anticipated litigation, investigation or regulatory
proceedings.

### DATA-RET R-4.3 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-4 Legal Hold

On release of a Legal Hold, the record is deleted within 30 days if its
retention period has already expired, and otherwise on the ordinary expiry of
its retention period.

### DATA-RET R-4.1 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-4 Legal Hold

Notwithstanding R-3, a record subject to a Legal Hold must not be
deleted while the Legal Hold remains in force, irrespective of the expiry of the
applicable retention period.

### DATA-RET R-4.2 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-4 Legal Hold

A Legal Hold takes precedence over any request for erasure made by a
data subject, to the extent permitted by applicable data protection law.

### DATA-RET R-6.1 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-6 Deletion of backups

Records deleted from production systems are deleted from backups at the
expiry of the backup rotation cycle, which must not exceed 90 days.

### Not quoted by the artefact

The clauses above cross-refer to the provisions below, or use terms they define.

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
# R-4

> Module LegalHold

##

```catala-metadata
declaration scope HoldEffect:
  input hold_issued_date content optional of date
  input hold_released_date content optional of date
  input retention_end_date content date
  input assessment_date content date
  input retention_under_hold_permitted_by_data_protection_law content boolean
  output hold_in_force content boolean
  output deletion_due_date content optional of date
  output backup_purge_deadline content optional of date
  output erasure_blocked_by_hold content boolean
```

| NO-CLAUSE

```catala
scope HoldEffect:
  date round down
```

## R-2

| DATA-RET R-2.3 (006-data-retention-standard.md:34)
|
| "Legal Hold" means a documented instruction issued by the General Counsel
| or their delegate to preserve specified records in connection with actual
| or reasonably anticipated litigation, investigation or regulatory
| proceedings.

```catala
scope HoldEffect:
  assertion
    match hold_released_date with pattern
    -- Absent: true
    -- Present content released:
         (match hold_issued_date with pattern
          -- Absent: false
          -- Present content issued: released >= issued)

  label r2_3 definition hold_in_force equals
    match hold_issued_date with pattern
    -- Absent: false
    -- Present content issued:
         assessment_date >= issued
         and (match hold_released_date with pattern
              -- Absent: true
              -- Present content released: assessment_date < released)
```

## R-4

| NO-CLAUSE

```catala
scope HoldEffect:
  label r3_ordinary definition deletion_due_date
    equals Present content retention_end_date
```

| DATA-RET R-4.3 (006-data-retention-standard.md:75)
|
| On release of a Legal Hold, the record is deleted within 30 days if its
| retention period has already expired, and otherwise on the ordinary expiry
| of its retention period.

```catala
scope HoldEffect:
  label r4_3 exception r3_ordinary definition deletion_due_date
    under condition
      (hold_issued_date with pattern Present)
      and (hold_released_date with pattern Present)
    consequence equals
      match hold_released_date with pattern
      -- Absent: Present content retention_end_date
      -- Present content released:
           if retention_end_date < released
           then Present content (released + 30 day)
           else Present content retention_end_date
```

| DATA-RET R-4.1 (006-data-retention-standard.md:68)
|
| Notwithstanding R-3, a record subject to a Legal Hold must not be deleted
| while the Legal Hold remains in force, irrespective of the expiry of the
| applicable retention period.

```catala
scope HoldEffect:
  label r4_1 exception r4_3 definition deletion_due_date
    under condition hold_in_force
    consequence equals Absent
```

| DATA-RET R-4.2 (006-data-retention-standard.md:72)
|
| A Legal Hold takes precedence over any request for erasure made by a data
| subject, to the extent permitted by applicable data protection law.

```catala
scope HoldEffect:
  label r4_2 definition erasure_blocked_by_hold equals hold_in_force

  label r4_2_permitted exception r4_2 definition erasure_blocked_by_hold
    under condition
      hold_in_force
      and not retention_under_hold_permitted_by_data_protection_law
    consequence equals false
```

## R-6

| DATA-RET R-6.1 (006-data-retention-standard.md:90)
|
| Records deleted from production systems are deleted from backups at the
| expiry of the backup rotation cycle, which must not exceed 90 days.

```catala
scope HoldEffect:
  label r6_1 definition backup_purge_deadline equals
    match deletion_due_date with pattern
    -- Absent: Absent
    -- Present content deleted_from_production:
         Present content (deleted_from_production + 90 day)
```


```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/LegalHold.catala_en",
  "scopes": {
    "HoldEffect": {
      "input": [
        "hold_issued_date",
        "hold_released_date",
        "retention_end_date",
        "assessment_date",
        "retention_under_hold_permitted_by_data_protection_law"
      ],
      "output": [
        "hold_in_force",
        "deletion_due_date",
        "backup_purge_deadline",
        "erasure_blocked_by_hold"
      ],
      "internal": [],
      "context": []
    }
  }
}