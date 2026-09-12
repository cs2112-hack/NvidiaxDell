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


## Artefact under review

```
# Data Retention and Deletion Standard — R-4 Legal Hold

> Module LegalHold

## Prologue — declarations

```catala-metadata
declaration scope HoldEffect:
  input hold_issued_date content optional of date
  input hold_released_date content optional of date
  input retention_end_date content date
  input assessment_date content date
  input retention_under_hold_permitted_by_data_protection_law content boolean
  output hold_in_force content boolean
  output deletion_due_date content optional of date
  output erasure_blocked_by_hold content boolean
```

| NO-CLAUSE: the rounding mode is a compiler directive, not a rule. Only day
| durations are added in this scope (R-4.3's 30 days), which are never
| ambiguous, but the mode is declared so that the scope's arithmetic is
| total by construction rather than by inspection. `date round down` matches
| `Retention`, whose R-3 result this scope consumes, so a single record
| cannot be rounded one way here and the other way there.

```catala
scope HoldEffect:
  date round down
```

## R-2 Definitions

| DATA-RET R-2.3 (006-data-retention-standard.md:34)
|
| "Legal Hold" means a documented instruction issued by the General Counsel
| or their delegate to preserve specified records in connection with actual
| or reasonably anticipated litigation, investigation or regulatory
| proceedings.

```catala
scope HoldEffect:
  label r2_3 definition hold_in_force equals
    match hold_issued_date with pattern
    -- Absent: false
    -- Present content issued:
         assessment_date >= issued
         and (match hold_released_date with pattern
              -- Absent: true
              -- Present content released: assessment_date < released)
```

## R-4 Legal Hold

| NO-CLAUSE: the base case of the deletion decision is the ordinary retention
| expiry that R-3 fixes, and R-3 is encoded in `Retention`, not here — this
| block adds no law of its own, it only names that module's result as the
| base that R-4.3 and R-4.1 are exceptions to. Encoding R-3 again here would
| put one clause in two modules.

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
        "erasure_blocked_by_hold"
      ],
      "internal": [],
      "context": []
    }
  }
}