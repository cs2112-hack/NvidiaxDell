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


## Artefact under review

```
# Data Retention and Deletion Standard — R-5 Erasure requests

> Module ErasureRequest

## Prologue — declarations

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

| NO-CLAUSE: the rounding mode is a compiler directive, not a rule. Only the
| 30 days of R-5.1 are added in this scope, which as a day duration is never
| ambiguous, but the mode is declared so the arithmetic is total by
| construction; `date round down` matches the other DATA-RET modules.
|
| The assertion is a well-formedness invariant on the inputs, not a rule of
| the Standard: a request cannot be verified before it is made. It is what
| `request_date` is for. R-5.1 measures its 30 days from verification and
| not from the request, so the request date enters no computed date at all,
| and holding both dates is how a reader can see that.

```catala
scope ErasureDeadline:
  date round down
  assertion
    (match verification_date with pattern
     -- Absent: true
     -- Present content verified: verified >= request_date)
```

## R-5 Erasure requests

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