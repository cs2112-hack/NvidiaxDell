# Scope contract — what each Catala module must expose

The ingestion component writes the Catala; this file is the contract it has to satisfy,
and the gold set in `gold/` is written against these names. Nothing here is Catala
source: the modules are generated then repaired against `catala typecheck`.

Naming: `<Rule>_<Counterparty|Org>`. One module per agreement, never one module per
clause type. **This is what keeps conflict detection honest** — two documents only
collide when they define the same variable in the same agreement's scope, so the Helios
agreement's 150% cap cannot be mistaken for a conflict with the Northwind 300% cap.

## Shared types

```
enum ClaimType     = Ordinary | Confidentiality | IpIndemnity | GrossNegligence | UnpaidFees | BodilyInjury
enum WorkState     = CA | CO | WA | NY | TX | Other
enum Department    = Engineering | Sales | Marketing | Finance | Other
enum ApprovalLevel = Manager | Director | VicePresident | ChiefFinancialOfficer
enum Separation    = InvoluntaryTermination | QuitWithNotice | QuitWithoutNotice
struct Money       -- Catala's native `money`; never a float
```

## Modules

| Module | Inputs | Outputs | Base definition | Exceptions (in priority order) |
|---|---|---|---|---|
| `LiabilityCap_AcmeNorthwind` | `fees_paid_last_12mo: money`, `annual_fees: money`, `claim_type: ClaimType`, `claim_date: date` | `cap: money`, `uncapped: boolean` | SYN-001 § 9.2 — 1× fees in the 12 months before the claim | SYN-001 § 9.3 (Excluded Claims → 200%); SYN-001 § 9.4 (gross negligence, unpaid fees, bodily injury → `uncapped`); SYN-003 § 5.1–5.2 for claims on or after 2026-07-01 (→ 300% / 500% of annual fees) **by authority of SYN-001 § 14.3** |
| `LiabilityCap_AcmeHelios` | `fees_paid_last_12mo: money`, `claim_type: ClaimType` | `cap: money`, `uncapped: boolean` | SYN-008 § 8.1 — 150% of trailing 12-month fees | SYN-008 § 8.2 (gross negligence, unpaid fees → `uncapped`) |
| `LiabilityCap_PinnacleAcme` | `sow_fees_paid_or_payable: money`, `claim_type: ClaimType` | `cap: money`, `uncapped: boolean` | SYN-013 § 6.1 — total fees under the SOW | SYN-013 § 6.2 (gross negligence, wilful misconduct, confidentiality → `uncapped`) |
| `LiabilityCap_InhibitorMsa` | `sow_fees_paid_or_payable: money`, `claim_type: ClaimType` | `cap: money`, `uncapped: boolean` | EDGAR-001 § 16 — fees paid or payable under the relevant SOW | indemnity obligations under § 15 are carved out of the cap |
| `ServiceCredit_AcmeNorthwind` | `month: date`, `monthly_uptime: decimal`, `monthly_service_fee: money`, `request_date: date` | `credit_percentage: decimal`, `credit: money`, `eligible: boolean` | SYN-002 § A.3 — 10% / 25% / 100% against a 99.9% commitment | SYN-003 § 4.2 for months beginning on or after 2026-07-01 (→ 15% / 30% / 100%) **by authority of SYN-001 § 14.3 and SYN-003 § 6.1**; SYN-002 § A.4.1 (request later than 30 days after month end → `eligible = false`, credit $0); § A.4.2 ceiling of 100% of the monthly fee |
| `ServiceCredit_AcmeGlobex` | same as above | same as above | SYN-009 § 2.1 — 10% / 25% / 100% | **none available** — SYN-010 § 2.1 defines the same tiers as 20% / 40% / 100% and § 4.1 refuses an order of precedence. Catala must report conflicting definitions; ingestion must block. |
| `SickLeaveAccrual_Acme` | `hours_worked: decimal`, `weeks_worked: integer`, `work_state: WorkState`, `days_employed: integer`, `exempt: boolean` | `accrual_rate: decimal` (hours per hour worked), `accrued_hours: decimal`, `annual_use_cap: decimal`, `usable: boolean` | SYN-005 § 4.2 — 1 hour per 40 worked, 40-hour annual use cap, usable on day 90 | REAL-001 (Cal. Lab. Code § 246(b)(1)) for `work_state = CA` → 1 per 30, 80-hour accrual cap, 40-hour use cap; REAL-002 (C.R.S. § 8-13.3-403) for `work_state = CO` → 1 per 30, 48-hour cap, usable as accrued. Both **by authority of SYN-005 § 4.6**. Cal. Lab. Code § 246(b)(2): an exempt employee is deemed to work 40 hours per week. |
| `PtoAccrual_Acme` | `hours_worked_in_period: decimal`, `years_of_service: integer`, `balance_at_year_end: decimal` | `accrued_hours: decimal`, `carryover_hours: decimal`, `forfeited_hours: decimal` | SYN-005 § 4.1.2 — factor 0.0961 under 5 years, hours above 40/week not counted | § 4.1.2 tiers (5–9 years → 0.1154; 10+ → 0.1346); § 4.1.4 carryover ceiling of 1.5× approximate annual accrual |
| `ExpenseApproval_Acme` | `amount: money`, `department: Department` | `approval_level: ApprovalLevel`, `receipt_required: boolean` | SYN-006 § 2.1 — Manager ≤ $1,000; Director ≤ $5,000; VP ≤ $25,000; CFO above | SYN-007 § 2.1–2.2 for `department = Engineering`, resolved **by SYN-005 § 7.1**: the more restrictive of the two levels controls (`Manager < Director < VicePresident < ChiefFinancialOfficer`); SYN-006 § 3.1 (`receipt_required` above $75) |
| `FinalPaycheckDeadline_Acme` | `last_day: date`, `separation: Separation`, `work_state: WorkState`, `next_regular_payroll_date: date` | `due_date: date`, `pto_payout_required: boolean` | SYN-005 § 9.3 — next regular payroll date; § 9.4 — no PTO payout | REAL-003 (Cal. Lab. Code § 201) for `work_state = CA` + involuntary → due on `last_day`; REAL-004 (§ 202) for `QuitWithoutNotice` → `last_day + 72 hours`; REAL-002 (C.R.S. § 8-4-109, in the same digest) for CO → due on `last_day`; REAL-005 (§ 227.3) → `pto_payout_required` in CA. All **by authority of SYN-005 § 9.5**. |
| `ConsultingFees_PinnacleAcme` | `hours: decimal`, `month_of_service: date`, `invoice_date: date` | `fees: money`, `fees_payable: money`, `change_order_required: boolean`, `eighty_percent_notice_required: boolean`, `invoice_payable: boolean` | SYN-013 § 3.1 — $225/hour | § 3.2 ceiling of $112,500 (500 hours) → `change_order_required`; § 3.3 notice at 80% ($90,000); § 5.3 invoice later than 90 days after month end → `invoice_payable = false` |
| `ConfidentialityDuration` | `disclosure_date: date`, `agreement_id: text`, `is_trade_secret: boolean` | `obligation_end: date`, `indefinite: boolean` | SYN-001 § 11.2 — 3 years from disclosure (ACME-NW-MSA-2026) | SYN-011 § 4.2 — 5 years (ACME-VTX-NDA-2026); trade secrets → `indefinite` under both |
| `BreachNotice_AcmeNorthwind` | `awareness_date: date` | `notify_by: date` | SYN-012 § 5.1 — 72 hours after awareness | none |
| `IncentiveComp_Acme` | `annual_base_salary: money`, `target_bonus_rate: decimal` | `target_bonus: money` | SYN-014 § 2.2 — 15% of base | EDGAR-002 (DXC side letter) is the same shape at 135% of an $800,000 base, encoded as its own module below |
| `IncentiveComp_DxcSideLetter` | `annual_base_salary: money`, `target_bonus_rate: decimal` | `target_bonus: money` | EDGAR-002 paras 2–3 — 135% of an $800,000 base, effective 2025-04-01 | none; the Good Reason definition in the same letter is vector-only |
| `Overtime_Flsa` | `hours_worked_in_week: decimal`, `regular_rate: money` | `total_pay: money`, `overtime_pay: money` | REAL-007 (29 U.S.C. § 207(a)) — 1.5× over 40 hours in a workweek | none encoded |
| `ServiceCredit_Reference` | `monthly_uptime: decimal`, `monthly_service_fee: money`, `vendor: text` | `credit_percentage: decimal` | REAL-008 / TPL-007 — the public vendor tier tables, kept as provenance for the tier *shape* | not executed in the demo; it exists so the SLA numbers have a citable public origin |
| `TuitionReimbursement_Reference` | `eligible_cost: money`, `grade: text`, `level: text` | `reimbursement: money`, `eligible: boolean` | REAL-009 — lesser of cost and $5,250 per calendar year | grade gate: C or better undergraduate, B or better graduate → otherwise $0 |

## Vector-store-only material (no module, pointer only)

Governing law and venue (SYN-001 § 13, SYN-008 § 12, SYN-011 § 7.1), force majeure
(SYN-001 § 15), warranty disclaimers (SYN-001 § 12), the definition of Confidential
Information (SYN-001 § 11.3, SYN-011 § 2.1–2.2), narrative security obligations
(SYN-012 § 4, § 8), everything in SYN-015 (Code of Conduct), all of `raw/discovery/`,
and the long EDGAR exhibits in `raw/real/edgar/` other than the clauses named above.

Each vector-store chunk carries `qualifies_module` — the module whose computed answer
it should be quoted alongside — or `null` when it stands alone.

## Implemented modules (Catala 1.2.1, `clerk test` green)

Scope names inside the modules are short (`LiabilityCap`, `ServiceCredit`, …) and are
addressed through the module alias, e.g. `L.LiabilityCap`.

| Module file | Scope | Tests | Status |
|---|---|---|---|
| `catala/src/legal_types.catala_en` | — (shared enums) | — | typechecks |
| `catala/src/liability_cap_acme_northwind.catala_en` | `LiabilityCap` | `tests/test_liability_cap_acme_northwind.catala_en` (FP-001…005) | green |
| `catala/src/service_credit_acme_northwind.catala_en` | `ServiceCredit` | `tests/test_service_credit_acme_northwind.catala_en` (FP-009…016) | green |
| `catala/src/sick_leave_accrual_acme.catala_en` | `SickLeaveAccrual` | `tests/test_sick_leave_accrual_acme.catala_en` (FP-019…027) | green |
| `catala/src/expense_approval_acme.catala_en` | `ExpenseApproval` | `tests/test_expense_approval_acme.catala_en` | green |
| `catala/src/final_paycheck_acme.catala_en` | `FinalPaycheck` | `tests/test_final_paycheck_acme.catala_en` | green |
| `catala/src/conflict_service_credit_acme_globex.catala_en` | `GlobexServiceCredit` | `tests/test_conflict_service_credit_acme_globex.catala_en` | **expected conflict**, locked in |
| `catala/src/conflict_liability_cap_with_amendment.catala_en` | `AmendedLiabilityCap` | `tests/test_conflict_liability_cap_with_amendment.catala_en` | **expected conflict**, locked in |

Not yet encoded (gold patterns exist, modules do not): `LiabilityCap_AcmeHelios`,
`LiabilityCap_PinnacleAcme`, `LiabilityCap_InhibitorMsa`, `PtoAccrual_Acme`,
`ConsultingFees_PinnacleAcme`, `ConfidentialityDuration`, `BreachNotice_AcmeNorthwind`,
`IncentiveComp_Acme`, `IncentiveComp_DxcSideLetter`, `Overtime_Flsa`,
`TuitionReimbursement_Reference`.

Two conventions the compiler enforced, recorded here so the next module gets them right:
a module only exports declarations that sit in a ```` ```catala-metadata ```` block, and
`#[test]` means "this scope must evaluate cleanly" — a test whose expected result is a
conflict error carries only the `catala-test-cli` block.
