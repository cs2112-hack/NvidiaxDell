# Review packet: catala

target: {"module": "ErasureRequest", "path": "catala/modules/ErasureRequest.catala_en"}

## Source document (authoritative)

### DATA-RET R-5.1 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-5 Erasure requests

Where a data subject makes a valid request for erasure, the Company will
delete the relevant personal data within 30 days of verifying the request.

### DATA-RET R-5.2 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-5 Erasure requests

By way of exception to R-5.1, the Company will not delete personal data
where it is required to retain it to comply with a legal obligation, for the
establishment or exercise of legal claims, or where a Legal Hold applies.

### Not quoted by the artefact

The clauses above cross-refer to the provisions below, or use terms they define.

### DATA-RET R-2.3 — Data Retention and Deletion Standard (v5.1, effective 2025-06-01)
Section R-2 Definitions

"Legal Hold" means a documented instruction issued by the General
Counsel or their delegate to preserve specified records in connection with
actual or reasonably anticipated litigation, investigation or regulatory
proceedings.


## Artefact under review

```
# R-5

> Module ErasureRequest

##

```catala-metadata
declaration scope ErasureDeadline:
  input request_date content date
  input verification_date content optional of date
  input erasure_request_valid content boolean
  input retention_required_by_legal_obligation content boolean
  input retention_required_for_legal_claims content boolean
  input legal_hold_blocks_erasure content boolean
  output deletion_due_date content optional of date
  output deletion_refused content boolean
```

| NO-CLAUSE

```catala
scope ErasureDeadline:
  date round down
  assertion
    (match verification_date with pattern
     -- Absent: true
     -- Present content verified: verified >= request_date)
```

## R-5

| DATA-RET R-5.1 (006-data-retention-standard.md:81)
|
| Where a data subject makes a valid request for erasure, the Company will
| delete the relevant personal data within 30 days of verifying the request.

```catala
scope ErasureDeadline:
  label r5_1_none definition deletion_due_date equals Absent

  label r5_1 exception r5_1_none definition deletion_due_date
    under condition erasure_request_valid
    consequence equals
      match verification_date with pattern
      -- Absent: Absent
      -- Present content verified: Present content (verified + 30 day)

  label r5_1_not_refused definition deletion_refused equals false
```

| DATA-RET R-5.2 (006-data-retention-standard.md:84)
|
| By way of exception to R-5.1, the Company will not delete personal data
| where it is required to retain it to comply with a legal obligation, for
| the establishment or exercise of legal claims, or where a Legal Hold
| applies.

```catala
scope ErasureDeadline:
  label r5_2 exception r5_1 definition deletion_due_date
    under condition
      erasure_request_valid
      and (retention_required_by_legal_obligation
           or retention_required_for_legal_claims
           or legal_hold_blocks_erasure)
    consequence equals Absent

  label r5_2_refused exception r5_1_not_refused definition deletion_refused
    under condition
      erasure_request_valid
      and (retention_required_by_legal_obligation
           or retention_required_for_legal_claims
           or legal_hold_blocks_erasure)
    consequence equals true
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/ErasureRequest.catala_en",
  "scopes": {
    "ErasureDeadline": {
      "input": [
        "request_date",
        "verification_date",
        "erasure_request_valid",
        "retention_required_by_legal_obligation",
        "retention_required_for_legal_claims",
        "legal_hold_blocks_erasure"
      ],
      "output": [
        "deletion_due_date",
        "deletion_refused"
      ],
      "internal": [],
      "context": []
    }
  }
}