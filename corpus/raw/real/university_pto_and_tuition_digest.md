---
doc_id: REAL-009
title: Public university employee handbook rules — PTO accrual and tuition reimbursement (digest)
kind: real_digest
license: >
  Digest of numeric rules published openly by public institutions. Paraphrased, not
  verbatim. Cited for provenance of the accrual factors used in the synthetic handbook.
sources:
  - https://www.southalabama.edu/departments/hr/  (University of South Alabama / USA Health staff handbook, PTO accrual)
  - https://www.uth.edu/hoop/  (UT Health Houston Handbook of Operating Procedures, educational assistance)
  - https://www.hr.upenn.edu/policies-and-procedures  (Penn HR policy manual, PTO by years of service)
  - https://policy.ucop.edu/doc/4010393/PPSM-2.210  (University of California PPSM-2.210, absence from work)
retrieved: 2026-09-12
verbatim: false
scopes: [PtoAccrual_Acme, TuitionReimbursement_Reference]
---

# Public-institution HR rules (digest)

## PTO accrual by hours worked (University of South Alabama / USA Health pattern)

Accrual is computed from hours worked, capped at 40 hours per week, multiplied by an
accrual factor set by length of service:

| Completed service | Factor (PTO hours per hour worked) | Annual accrual at 40 h/week |
|---|---|---|
| Under 5 years | 0.0961 | 0.0961 × 40 × 52 ≈ 200 hours |
| 5–9 years | 0.1154 | ≈ 240 hours |
| 10+ years | 0.1346 | ≈ 280 hours |

The synthetic Acme handbook (SYN-005 § 4.1) uses this same factor structure, so the
Catala accrual module is validated against a real published formula.

## Tuition / educational assistance reimbursement (UT Health Houston pattern)

- Reimbursement is capped at **$5,250 per calendar year**, the amount excludable from
  income under 26 U.S.C. § 127.
- Eligibility is gated on grade: **C or better** for undergraduate coursework,
  **B or better** for graduate coursework.
- Coursework must be job-related or part of a degree program approved in advance.

Rule shape: `annual_reimbursement = min(eligible_cost, $5,250)` subject to a boolean
grade-eligibility gate — a clean two-input Catala scope, available if the demo needs a
fourth module.

## Penn / UC patterns (not encoded, recorded for reference)

- PTO accrual tiered by years of service with monthly accrual mechanics, and up to five
  days available on hire (Penn).
- Sick leave minimums and interaction with FMLA leave (UC PPSM-2.210).
