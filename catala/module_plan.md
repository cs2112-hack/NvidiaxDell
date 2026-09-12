# Catala module decomposition

Derived from the adjudicated triage ledger (`triage/decisions.yaml`, all 134 rows
`source: adjudicated`). Every `RULE` and `HYBRID` clause appears in exactly one
module below; every `PROSE` clause's `qualifies` list names modules from this
file.

Nineteen modules. The unit of decomposition is **a computation a user would ask
about**, not a document section. Two modules (`WorkingTimeDefs`, `ExpenseDefs`)
exist because several sibling computations share the same defined quantities and
input types; they export types and constants and compute nothing on their own.

Exception hierarchies below are taken from the source text, at the depth the
text states. Where the documents leave a precedence genuinely unresolved it is
recorded under **Open precedence** rather than silently collapsed — those are
the places a reviewer should attack first.

---

## Working time (EMP-ANNEX-C)

### 1. `WorkingTimeDefs`
**Purpose.** The defined quantities and input types every Annex C computation
consumes. No entitlement is decided here.

**Encodes.** C-2.1, C-2.2, C-2.3, C-2.4, C-2.5, C-2.6, C-3.1.

**Exports**
| item | from | kind |
|---|---|---|
| `base_hourly_rate = annual_base_salary / 2080` | C-2.1 | computed quantity |
| `payroll_week` (Mon 00:00 → Sun 23:59:59) | C-2.2 | period constructor |
| `is_night_hour` (22:00–06:00) | C-2.3 | decidable predicate |
| `grade` | C-2.4 | **input type** — read from the HR system of record, as at the first day of the Payroll Week |
| `critical_incident` `{severity, declared_at, closed_at}` | C-2.5 | **input struct** — Severity 1 classification comes from the incident management standard |
| `gazetted_holidays` | C-2.6 | **input table** — per principal place of work |
| `ordinary_weekly_hours = 40` | C-3.1 | constant |

C-2.4/C-2.5/C-2.6 are RULE *as input declarations*: they contribute no
arithmetic but the C-5 and C-7 exceptions cannot be stated without them, and
each resolves to a record lookup rather than to a judgement.

### 2. `Overtime`
**Purpose.** Given an hour worked, what is paid for it (or accrued in lieu).

**Encodes.** C-4.1, C-4.2, C-5.1, C-5.2, C-6.1, C-6.2, C-7.1, C-7.2, C-8.1, C-8.2.

**Scopes**
- `PremiumMultiplier` — in: `hours_worked_in_week`, `hour_index`, `grade`,
  `is_public_holiday`, `is_critical_incident_response`; out: `multiplier`,
  `substitutive` (bool).
- `TimeOffInLieu` — in: `hours_worked_in_week`, `grade`; out: `toil_hours`.
- `NightPremium` — in: `is_night_hour`, `has_standing_shift_allowance`,
  `base_hourly_rate`; out: `night_premium_amount`.
- `OvertimePay` — in: the hour series for a Payroll Week, `base_hourly_rate`,
  `grade`; out: `overtime_payment`, `toil_hours`, `night_premium_total`.
- judgement inputs: **none**. Everything the module needs is a rate, a
  timestamp or a record.

**Exception hierarchy (as stated)**
```
C-4.1  base: hours > 40  -> 1.25 x BHR
  └─ C-4.2  exception: hours > 48 -> 1.5 x BHR
C-5.1  exception to the whole of C-4: Grade >= 5 -> no payment; TOIL 1:1 above 40
  └─ C-5.2  "notwithstanding C-5.1": Grade >= 5 on Critical Incident Response
            -> paid per C-4 (so the C-4.1/C-4.2 bands revive, at their own depth)
C-7.1  public holiday -> 2.0 x BHR, IN SUBSTITUTION FOR C-4
  └─ C-7.2  exception to C-7.1: holiday hour that is also > 48h in the week
            -> greater of (a) 2.0 x BHR and (b) C-4.2 rate + 0.25
C-8.1  precedence over C-4 / C-5 / C-7: one hour takes only the single
       highest applicable multiplier (this is why C-7.1 must be modelled as
       substitutive, not additive)
C-6.1  night premium 0.15 x BHR per Night Hour, EXPRESSLY CUMULATIVE with
       C-4, C-5.2 and C-7
  └─ C-6.2  exception: not payable where a standing shift allowance is paid
            under the individual contract
C-8.2  disapplies C-8.1 for C-6, confirming the night premium stacks
```
Encoding notes. C-5.2 must be an exception *to C-5.1*, not a condition merged
into C-4 — the text says "notwithstanding C-5.1". C-8.1 is a rule about the
other rules and needs its own definition (a maximum over the applicable
multipliers), not an inline `max`.

**Open precedence / reviewer targets**
- C-7.2 limb (b) evaluates to 1.5 + 0.25 = 1.75, which is always less than limb
  (a) 2.0. On the numbers as drafted the exception is inoperative: it can never
  change the answer. Encode it faithfully anyway (a later version of C-4.2 would
  make it bite) and test that it does not.
- C-5.2 restores "an overtime payment calculated in accordance with C-4" for
  Critical Incident Response hours. If such an hour also falls on a Gazetted
  Public Holiday, C-8.1 resolves it (highest multiplier: C-7.1's 2.0), but the
  text never says so expressly.

### 3. `LeaveAccrual`
**Purpose.** Annual leave accrued, carried forward and forfeited.

**Encodes.** C-9.1, C-9.2, C-9.3, C-9.4, C-9.5.

**Scopes**
- `MonthlyAccrual` — in: `service_start`, `service_end`, `month`; out:
  `days_accrued`.
- `CarryForward` — in: `accrued_untaken_days`; out: `carry_forward_days`,
  `forfeited_days`; **judgement input:
  `prevented_from_taking_leave_by_long_term_sickness`**.

**Exception hierarchy (as stated)**
```
C-9.1  base: 2.0 days per completed calendar month
  └─ C-9.2  part-month: pro-rate by days of service / days in month,
            round to nearest half day, exact quarter rounds UP
C-9.3  base cap on carry-forward: 5 accrued but untaken days
  └─ C-9.4  exception (HYBRID): prevented from taking leave by reason of
            long-term sickness absence -> carry forward the whole balance
C-9.5  consequence: the excess over the permitted carry-forward is forfeited
       at year end and is not payable on termination
```
C-9.5 must read the cap produced by C-9.3/C-9.4, not re-state 5 days — otherwise
the C-9.4 exception leaks.

**Qualified by (PROSE).** C-1.1, C-1.2 (statutory minimum prevails), C-10.1
(accrued entitlements survive variation), EXP-POL E-7.1.

---

## Expenses (EXP-POL)

### 4. `ExpenseDefs`
**Purpose.** Shared defined terms and input types for the three spend modules
and the claim window.

**Encodes.** E-2.1, E-2.2, E-2.3, E-2.4.

**Exports**
| item | from | kind |
|---|---|---|
| `city_tier` = Tier1 \| Tier2 \| Tier3 | E-2.1 | **input enum** (Finance intranet schedule) |
| `client_billable` `{is_client_billable, sow_per_diem?}` | E-2.2 | **input struct** (terms of an executed SOW) |
| `travel_days` — each calendar day or part day away overnight; departure and return each count | E-2.3 | counting rule |
| `pre_approval` `{in_writing, obtained_at, approver_authority_limit}` and `pre_approval_valid = in_writing ∧ obtained_at < incurred_at ∧ approver_authority_limit >= amount` | E-2.4 | struct + decidable predicate |

E-2.4 is the reason E-3.3 and E-5.2 are RULE rather than HYBRID: "Pre-Approval"
decomposes into a date comparison and a numeric comparison, both executable.

### 5. `MealPerDiem`
**Purpose.** The reimbursable meal amount for a Travel Day.

**Encodes.** E-3.1, E-3.2, E-3.3, E-3.4, E-3.5, E-7.2.

**Scope** `MealPerDiemForDay` — in: `city_tier`, `client_billable`,
`pre_approval`, `meals_provided_free`, `meal_expense_incurred`,
`on_critical_incident_response`; out: `per_diem_cap`, `reimbursable_amount`.
Judgement inputs: none.

**Exception hierarchy (as stated)**
```
E-3.1  base table: Tier 1 GBP 75 / Tier 2 GBP 55 / Tier 3 GBP 40 per Travel Day
  └─ E-3.2  exception to E-3.1: Client-Billable Travel -> the SOW amount, or if
            none specified 1.5 x the E-3.1 amount
      └─ E-3.3  exception to E-3.1 AND E-3.2: a Pre-Approved higher specified
                amount displaces both
E-3.4  adjustment to the day's per diem: minus GBP 15 per free meal provided
E-3.5  condition on entitlement: no meal expense incurred -> nil
E-7.2  uplift to the E-3.1 per diem: +GBP 20 per Travel Day for Critical
       Incident Response work, and no Pre-Approval needed for the uplift
       (cross-document dependency on EMP-ANNEX-C C-2.5)
```
**Open precedence / reviewer targets**
- E-7.2 increases "the per diem under E-3.1". On Client-Billable Travel, does
  E-3.2's 1.5x multiply the uplifted figure (1.5 × (75 + 20)) or the base
  (1.5 × 75 + 20)? The documents do not say. Model it explicitly, pick the
  reading, and record the choice as a test.
- E-3.4 is a reduction of the per diem, not of the claim. Where E-3.3 has
  substituted a Pre-Approved amount, the £15 deduction still applies to "the per
  diem for that Travel Day" — i.e. to the substituted figure.

### 6. `Accommodation`
**Purpose.** The reimbursable nightly accommodation amount.

**Encodes.** E-4.1, E-4.2, E-4.3.

**Scope** `NightlyAccommodation` — in: `city_tier`, `actual_cost`,
`local_taxes`, `mandatory_city_charge`, `parking_cost`; out: `applicable_cap`,
`reimbursable_amount`; **judgement input:
`no_compliant_accommodation_available_evidenced`**.

**Exception hierarchy (as stated)**
```
E-4.1  base caps: Tier 1 GBP 220 / Tier 2 GBP 160 / Tier 3 GBP 120 per night
  └─ E-4.2  exception (HYBRID): evidence that no compliant accommodation was
            available within the cap at the time of booking -> cap becomes
            1.4 x the applicable cap
E-4.3  defines the quantity compared against the cap: local taxes and any
       mandatory resort or city charge are INSIDE the cap; parking is OUTSIDE
       (so parking is reimbursed alongside, not against, the cap)
```

### 7. `GroundTransport`
**Purpose.** Rail, taxi and private hire reimbursement.

**Encodes.** E-5.1, E-5.2, E-5.3.

**Scopes**
- `RailFare` — in: `class_of_travel`, `ticket_cost`,
  `standard_class_fare_same_day`, `pre_approval`; out: `reimbursable_amount`.
- `TaxiFare` — in: `journey_cost`, `is_airport_journey`, `journey_time`; out:
  `reimbursable_amount`.
- judgement inputs: **none**. `standard_class_fare_same_day` is a
  counterfactual *price to be evidenced* — an ordinary input, like a salary —
  not a judgement anyone exercises. This is the distinction that keeps E-5.2 out
  of HYBRID.

**Exception hierarchy (as stated)**
```
E-5.1  standard class rail: actual cost, uncapped
E-5.2  first class rail: reimbursable ONLY IF ticket cost <= standard class
       fare for the same journey booked on the day of travel, OR Pre-Approval
       (E-2.4) obtained.  Two alternative gates, not a hierarchy.
E-5.3  private hire / taxi: actual cost up to GBP 60 per journey
  └─ "save that" exception: a journey to or from an airport departing before
     06:00 or after 22:00 is uncapped
```

### 8. `ClaimWindow`
**Purpose.** Is a claim admissible on time?

**Encodes.** E-6.1, E-6.2, E-6.3.

**Scope** `ClaimAdmissible` — in: `expense_incurred_date`, `submission_date`;
out: `admissible`, `days_elapsed`; **judgement input:
`delay_certified_for_good_reason`** (the line manager's certification).

**Exception hierarchy (as stated)**
```
E-6.1  base: submit within 60 days of the expense being incurred
  └─ E-6.2  exception (HYBRID): submitted after 60 but within 120 days MAY be
            reimbursed where the line manager certifies good reason
E-6.3  absolute long-stop: more than 120 days -> never reimbursed, "in any
       circumstances" (so E-6.3 bounds E-6.2 and admits no exception of its own)
```
Note that E-6.2 is permissive ("may be reimbursed"): with the certification the
scope should return *admissible*, leaving the discretionary refusal — if any —
visible as prose, not modelled as a second gate the module invents.

**Qualified by (PROSE).** E-1.1 ("properly incurred"), E-1.2 (Policy not
exhaustive), E-8.1 (false claims), E-7.1 (Annex C untouched).

---

## Commission (COMM-PLAN)

### 9. `Commission`
**Purpose.** Commission earned for the Plan Year.

**Encodes.** S-1.1, S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-3.1, S-4.1,
S-4.2, S-4.3, S-4.4, S-5.1, S-5.2, S-5.3, S-8.1, S-8.2.

**Scopes**
- `NetBookedRevenue` — in: `contracts` (ACV, signature date, churn events),
  `plan_year`; out: `net_booked_revenue`, `new_logo_share`.
- `Attainment` — in: `net_booked_revenue`, `quota` (after `QuotaRelief`); out:
  `attainment_pct`.
- `Accelerator` — in: `attainment_pct`, `new_logo_share`, `commenced_after_plan_year_start`; out: `accelerator`.
- `CommissionPayable` — in: all of the above, `annual_base_salary`; out:
  `commission_before_cap`, `cap`, `commission_payable`.
- `LeaverCommission` — in: `last_day_of_employment`, `termination_reason`; out:
  `commission_payable`.
- judgement inputs: **none**. The gross misconduct determination reaches the
  scope as a recorded termination reason, exactly as C-2.4's grade does.

**Exception hierarchy (as stated)**
```
S-1.1  Plan Year = 2025-10-01 .. 2026-09-30 (the only statement of the window)
S-2.5  Base Commission Rate = 8.0%
S-3.1  commission = Net Booked Revenue x Base Commission Rate x accelerator
S-4.1  base accelerator table on Attainment: <50% -> 0.0; 50-<100% -> 1.0;
       100-<150% -> 1.5; >=150% -> 2.0
S-4.2  the accelerator applies to the WHOLE of NBR, not marginally to the
       excess over the band threshold (a choice of formula, hence RULE)
  └─ S-4.3  exception to S-4.1: New Logo share > 60% -> accelerator + 0.25
      └─ S-4.4  exception to S-4.1 AND S-4.3: mid-year joiner -> accelerator
                capped at 1.5
S-5.1  base cap: 250% of annual base salary
  └─ S-5.3  exception: Attainment > 200% -> cap 400% of annual base salary
S-5.2  ordering: the cap is applied AFTER S-4 and AFTER any S-6 adjustment
S-8.1  leaver: entitled for contracts signed on or before the last day
  └─ S-8.2  exception: termination for gross misconduct -> unpaid commission
            forfeited
```
**Open precedence / reviewer targets**
- S-4.4 caps the accelerator at 1.5 for a mid-year joiner. Under S-4.1 alone a
  150%+ attainer would get 2.0 and under S-4.3 up to 2.25; the cap is a ceiling
  applied last among the S-4 rules, and must be encoded as an exception to both,
  not as a `min` inside S-4.1.
- S-5.3's trigger ("Attainment exceeds 200%") is strict; S-4.1(d)'s is "150% or
  more". Do not normalise the comparison operators.

### 10. `QuotaRelief`
**Purpose.** The Quota against which Attainment is measured, after absence
relief.

**Encodes.** S-6.1, S-6.2.

**Scope** `RelievedQuota` — in: `annual_quota`, `absence_periods` (type,
start, end); out: `relieved_quota`, `relief_days`. Judgement inputs: **none** —
the absence categories are legal statuses recorded by payroll, and the 30-day
continuous-period threshold supplies the content of "long-term".

**Hierarchy (as stated)**
```
S-6.1  qualifying absence (statutory parental / adoption leave / long-term
       sickness) of a CONTINUOUS period of 30 days or more
       -> Quota reduced by 1/365th of the annual Quota per day of absence
S-6.2  ordering: relief is applied BEFORE Attainment is calculated
       (so QuotaRelief is upstream of Commission.Attainment, and S-5.2 puts
       the S-5 cap downstream of both)
```
Reviewer target: is the 1/365th reduction applied to every day of the absence,
or only to days after the 30th? The text reduces "for each day of such absence"
once the 30-day gate is met — i.e. all days. Test the 30/31-day boundary.

### 11. `Clawback`
**Purpose.** How much paid commission is recoverable.

**Encodes.** S-7.1, S-7.2, S-7.3.

**Scope** `RecoverableCommission` — in: `contract`, `commission_paid`,
`payment_date`, `assessment_date`, `is_churned_contract`; out:
`recoverable_amount`; **judgement input:
`terminated_for_company_material_breach`**.

**Exception hierarchy (as stated)**
```
S-7.1  base: commission attributable to a contract that has become a Churned
       Contract (S-2.4) is recoverable, by deduction or as a debt
  └─ S-7.2  exception (HYBRID): NO recovery where the customer terminated by
            reason of a material breach BY THE COMPANY
S-7.3  long-stop: no recovery more than 24 months after the commission was paid
```
"The Company may recover" is an election about enforcement; the module computes
the recoverable quantum and does not model the election.

**Qualified by (PROSE).** S-1.2, S-1.3, S-9.1 (the CRO's determination on
interpretation and operation is final — every executed commission figure is
defeasible and must be served with that caveat).

---

## Service levels (MSA-SCH4)

### 12. `Availability`
**Purpose.** The Availability Percentage for a Measurement Period, and whether
the Service Level was met.

**Encodes.** L-2.1, L-2.2, L-2.3, L-2.4, L-2.5, L-2.6, L-3.1, L-4.5.

**Scopes**
- `ScheduledMaintenanceWindow` — in: `notified_at`, `window_start`,
  `window_end`, `business_day_calendar`; out: `is_scheduled_maintenance`,
  `aggregate_hours`, `excess_minutes`.
- `ExcludedMinutes` — in: per-outage minutes and cause codes; out:
  `excluded_minutes`; **judgement inputs: `force_majeure_event_certified`,
  `outage_attributable_to_customer`**.
- `AvailabilityPercentage` — in: `measurement_period`, `unavailable_minutes`,
  `excluded_minutes`; out: `availability_percentage` (2 dp),
  `meets_service_level`.

**Hierarchy and data flow (as stated)**
```
L-2.1  Measurement Period = each calendar month
L-2.2  "Available" = responds to a well-formed request at the documented
       endpoint within the applicable latency threshold  (INPUT PREDICATE;
       the threshold itself lives in the Agreement, not in this Schedule)
L-2.4  Unavailable Minutes = each whole minute not Available, LESS Excluded
       Minutes
L-2.5  Excluded Minutes (HYBRID) = minutes not Available by reason of
       (a) Scheduled Maintenance  -> computable via L-2.6
       (b) Emergency Maintenance notified >= 60 minutes in advance -> computable
       (c) a Force Majeure Event  -> JUDGEMENT
       (d) the Customer's own act or omission, or a Customer system/network
           failure -> JUDGEMENT (causal attribution)
       (e) suspension under clause 9 -> record of the Agreement
L-2.6  Scheduled Maintenance = window notified >= 5 Business Days in advance,
       PROVIDED THAT it must not exceed 8 hours in aggregate per period
  └─ L-4.5  consequence of breaching that proviso: the excess minutes cease to
            be Excluded Minutes and COUNT AS Unavailable Minutes
            (a feedback edge back into L-2.4)
L-2.3  Availability % = (total minutes - Unavailable Minutes) / total minutes,
       as a percentage rounded to 2 dp
L-3.1  Service Level = not less than 99.90% per Measurement Period
```
Encoding note: L-2.5 is the single most consequential HYBRID in this document.
Every credit in L-4 is downstream of it, so the two judgement inputs must be
visible on the credit answer as well, not swallowed inside `Availability`.

### 13. `ServiceCredits`
**Purpose.** The Service Credit for a Measurement Period.

**Encodes.** L-2.7, L-4.1, L-4.2, L-4.3, L-4.4.

**Scope** `ServiceCredit` — in: `availability_percentage`,
`monthly_service_charge`, `arrears_days`; out: `credit_percentage`,
`credit_cap_percentage`, `credit_amount`; **judgement input:
`invoice_undisputed`**.

**Exception hierarchy (as stated)**
```
L-2.7  credit base = charges for the Service for the Measurement Period, LESS
       one-off, professional services and pass-through charges
L-4.1  base band table on Availability Percentage:
       <99.90 & >=99.50 -> 5% ; <99.50 & >=99.00 -> 10% ;
       <99.00 & >=98.00 -> 20% ; <98.00 -> 30%
L-4.2  aggregate cap: 30% of the Monthly Service Charge per period
  └─ L-4.3  exception to L-4.2: availability < 95.00% -> cap 50% AND the
            Service Credit itself is 50%  (it overrides both L-4.2 and,
            in effect, L-4.1(d))
L-4.4  exception to L-4.1 (HYBRID): no credit at all for a period in which the
       Customer is in arrears of any UNDISPUTED invoice for more than 30 days
```
Reviewer target: L-4.3 sets both the cap and the credit to 50%, so it is an
exception at two levels at once. Encode it as an exception to the credit
definition *and* to the cap definition; do not fold L-4.3 into the L-4.1 table.

### 14. `ServiceCreditClaim`
**Purpose.** Is the credit still claimable, and how is it settled?

**Encodes.** L-5.1, L-5.2.

**Scope** `CreditClaim` — in: `measurement_period_end`, `claim_date`,
`claim_accepted_date`, `further_invoice_expected`; out: `claim_in_time`,
`waived`, `settlement_due_date`. Judgement inputs: none.

**Hierarchy (as stated)**
```
L-5.1  claim in writing within 30 days after the end of the Measurement Period;
       a credit not claimed within that period is WAIVED (determinate loss)
L-5.2  settlement: applied against the next invoice issued after the claim is
       accepted; if no further invoice will be issued, paid within 30 days
```

### 15. `ChronicFailure`
**Purpose.** Has a termination right arisen, and is it still exercisable?

**Encodes.** L-6.1, L-6.2.

**Scope** `ChronicFailureRight` — in: `availability_history` (per period),
`assessment_date`, `notice_date`; out: `right_arisen`, `trigger_period`,
`exercise_deadline`, `right_lapsed`, `notice_period_days`. Judgement inputs:
none.

**Hierarchy (as stated)**
```
L-6.1  right arises where availability < 99.90% in EITHER
       (i) each of three consecutive Measurement Periods, OR
       (ii) any four Measurement Periods in a rolling twelve-month period
       -> terminate the affected Service on 30 days' written notice, with no
          early termination charges
L-6.2  the right must be exercised within 30 days after the end of the
       Measurement Period GIVING RISE TO IT, failing which it lapses in
       respect of THAT OCCURRENCE (so the right is per-occurrence and can
       arise again later)
```
**Qualified by (PROSE).** L-1.1, L-1.2 (Service Credits are the sole financial
remedy save for a failure amounting to a material breach giving a right of
termination — an uncomputable carve-out that must accompany every credit
figure), L-1.3 (genuine pre-estimate recital).

---

## Confidentiality (NDA-MUT)

### 16. `NdaSurvival`
**Purpose.** Until when do the N-3 confidentiality obligations bind?

**Encodes.** N-5.1, N-5.2, N-5.3, N-5.4.

**Scope** `SurvivalEnd` — in: `effective_date`, `termination_notice_date`,
`actual_end_date`, `is_personal_data`, `date_ceased_to_hold`; out:
`agreement_end_date`, `survival_end_date` (optional — absent means indefinite);
**judgement inputs: `information_is_trade_secret`,
`information_remains_trade_secret`**.

**Exception hierarchy (as stated)**
```
N-5.1  term: Effective Date + 2 years, or earlier termination on 30 days'
       written notice -> yields the expiry/termination date
N-5.2  base survival: the N-3 obligations continue for 3 years from expiry or
       termination
  ├─ N-5.3  exception (HYBRID): trade secret -> obligations continue for so
  │         long as the information remains a trade secret (NO end date is
  │         produced; the 3-year arithmetic is disapplied)
  └─ N-5.4  exception: personal data -> obligations continue for so long as the
            Recipient holds that personal data (end date = the date it ceases
            to hold, an input event)
```
The two exceptions are siblings, each "by way of exception to N-5.2", and both
can apply to the same information — the longer survival governs.

Why N-5.3 is HYBRID but N-5.4 is RULE: "constitutes a trade secret" is a legal
characterisation the Agreement does not define and that must be re-made over
time, so the module must not assert an expiry; whether information is personal
data, and whether the Recipient still holds it, are records (processing
records, data inventory).

**Qualified by (PROSE).** N-1.2 (obligations run role-relatively in both
directions), N-2.1 (the "ought reasonably to be regarded as confidential"
gate), N-2.2, N-2.3 (four carve-outs), N-3.1–N-3.4 (the obligations themselves,
all qualitative), N-4.1 (compelled disclosure), N-6.1 (return/destruction on
request — the event that can end the N-5.4 period), N-6.2 (one retained copy
stays subject to the Agreement, so obligations can persist on it).

Nothing in N-1, N-2, N-3, N-4, N-6 to N-9 is computable: this document is
almost entirely vector-store material, and that is the correct outcome, not a
gap.

---

## Records (DATA-RET)

### 17. `Retention`
**Purpose.** When may (or must) a record be deleted, absent a hold?

**Encodes.** R-2.1, R-2.2, R-3.1, R-3.2, R-3.3, R-6.1.

**Scope** `RetentionEnd` — in: `data_class`, `retention_trigger_date`,
`talent_pool_consent_date`, `consent_renewals`; out: `retention_end_date`,
`backup_purge_deadline`; **judgement inputs: `statutory_retention_applies`,
`statutory_retention_period`**.

**Exception hierarchy (as stated)**
```
R-2.1  Data Class = closed enum: Employee Record | Customer Contract |
       Financial Record | Marketing Contact | Security Log | Candidate Record
R-2.2  Retention Trigger = the event the period runs from (input date)
R-3.1  base table, each limb with its own trigger:
       (a) Employee Record   6 years from end of employment
       (b) Customer Contract 7 years from expiry/termination
       (c) Financial Record  7 years from end of the financial year
       (d) Marketing Contact 24 months from the LATER OF collection and most
           recent engagement
       (e) Security Log      13 months from the logged event
       (f) Candidate Record  12 months from conclusion of the process
  ├─ R-3.2  exception to R-3.1(f): talent pool consent -> 24 months from the
  │         date of consent, RESTARTING on each renewal of consent
  └─ R-3.3  exception to R-3.1(a) (HYBRID): where pensions or payroll
            legislation requires longer, that longer period applies
            -> end date = LATER OF the R-3.1(a) date and the statutory date
R-6.1  backups: purged at expiry of the backup rotation cycle, which MUST NOT
       EXCEED 90 days -> the outer bound on any deletion date
```
Note the two exceptions are limb-specific: R-3.2 attaches to (f) and R-3.3 to
(a) only. Do not generalise either to the whole table.

### 18. `LegalHold`
**Purpose.** Does a hold suspend deletion, and when does deletion fall due
after release?

**Encodes.** R-2.3, R-4.1, R-4.2, R-4.3.

**Scope** `HoldEffect` — in: `hold_issued_date`, `hold_released_date`,
`retention_end_date` (from `Retention`), `assessment_date`; out:
`hold_in_force`, `deletion_due_date`, `erasure_blocked_by_hold`; **judgement
input: `retention_under_hold_permitted_by_data_protection_law`** (for R-4.2).

**Exception hierarchy (as stated)**
```
R-2.3  Legal Hold = a documented instruction by the General Counsel or delegate
       (the "reasonably anticipated litigation" judgement is exercised by the
       GC when issuing, so the module receives a record, not a judgement)
R-4.1  "Notwithstanding R-3": a held record MUST NOT be deleted while the hold
       is in force, irrespective of expiry of the retention period
       -> overrides the whole of Retention
R-4.2  (HYBRID) the hold takes precedence over a data subject's erasure
       request, but only "to the extent permitted by applicable data
       protection law" -> overrides ErasureRequest, gated on that judgement
R-4.3  on release: delete within 30 days if the retention period has already
       expired; otherwise on ordinary expiry
       -> deletion_due_date = max(release_date + 30 days IF already expired,
                                  retention_end_date)
```

### 19. `ErasureRequest`
**Purpose.** By when must personal data be deleted on a data subject request?

**Encodes.** R-5.1, R-5.2.

**Scope** `ErasureDeadline` — in: `request_date`, `verification_date`,
`legal_hold_in_force`; out: `deletion_due_date`, `deletion_refused`;
**judgement inputs: `erasure_request_valid`,
`retention_required_by_legal_obligation`,
`retention_required_for_legal_claims`** (and, via `LegalHold`, R-4.2's
`retention_under_hold_permitted_by_data_protection_law`).

**Exception hierarchy (as stated)**
```
R-5.1  base (HYBRID): a VALID request -> delete within 30 days of VERIFYING the
       request  (the 30-day arithmetic runs from verification, not from the
       request; validity is the judgement)
  └─ R-5.2  exception (HYBRID): no deletion where retention is required
            (i)  to comply with a legal obligation            -> JUDGEMENT
            (ii) for the establishment or exercise of legal claims -> JUDGEMENT
            (iii) a Legal Hold applies                        -> computable
                  via R-2.3/R-4.1, but subject to R-4.2's own judgement
```
This is the densest judgement cluster in the corpus: four judgement inputs
govern one 30-day arithmetic. Encoding it as plain RULE — which the heuristic
came close to doing — would have the system assert a deletion due date in
precisely the cases where the law forbids deletion.

**Qualified by (PROSE).** R-1.1 (all records in any form), R-1.2 (the periods
are maxima; delete earlier once the purpose is achieved — so a computed
retention date is a ceiling, never an instruction to keep), R-2.4 (Deletion
means destruction "not reconstructable by ordinary means" — a qualitative
standard governing when a computed deadline is actually met), R-7.1, R-7.2
(an approved Privacy Office departure displaces the Standard for that record).

---

## Cross-document edges

| edge | from | to | nature |
|---|---|---|---|
| Critical Incident Response | EMP-ANNEX-C C-2.5 | EXP-POL E-7.2 (`MealPerDiem`) | shared input type; the per diem uplift depends on an Annex C definition |
| Annex C precedence | EXP-POL E-7.1 (PROSE) | `Overtime`, `LeaveAccrual` | conflict rule: the Policy cannot cut down Annex C entitlements |
| statutory floor | EMP-ANNEX-C C-1.2 (PROSE) | `Overtime`, `LeaveAccrual` | external legal floor; deliberately NOT modelled as a judgement input (see the ledger reason) |
| Legal Hold over erasure | DATA-RET R-4.2 (`LegalHold`) | `ErasureRequest` | precedence, gated on a data-protection-law judgement |
| Scheduled Maintenance overrun | MSA-SCH4 L-4.5 (`Availability`) | L-2.4 Unavailable Minutes | feedback edge inside `Availability`; it must not be encoded in `ServiceCredits` |
| Business Day calendar | Agreement clause 1 (outside the corpus) | `Availability` (L-2.6) | undefined term imported by L-1.1; supply as an input table |
| latency threshold | Agreement / Service documentation (outside the corpus) | `Availability` (L-2.2) | the "applicable latency threshold" is not stated in Schedule 4 |

## Label counts

| label | count |
|---|---|
| RULE | 85 |
| HYBRID | 11 |
| PROSE | 38 |
| **total** | **134** |

The eleven HYBRID clauses, with their judgement inputs:

| clause | module | judgement inputs |
|---|---|---|
| EMP-ANNEX-C C-9.4 | LeaveAccrual | `prevented_from_taking_leave_by_long_term_sickness` |
| EXP-POL E-4.2 | Accommodation | `no_compliant_accommodation_available_evidenced` |
| EXP-POL E-6.2 | ClaimWindow | `delay_certified_for_good_reason` |
| COMM-PLAN S-7.2 | Clawback | `terminated_for_company_material_breach` |
| MSA-SCH4 L-2.5 | Availability | `force_majeure_event_certified`, `outage_attributable_to_customer` |
| MSA-SCH4 L-4.4 | ServiceCredits | `invoice_undisputed` |
| NDA-MUT N-5.3 | NdaSurvival | `information_is_trade_secret`, `information_remains_trade_secret` |
| DATA-RET R-3.3 | Retention | `statutory_retention_applies`, `statutory_retention_period` |
| DATA-RET R-4.2 | LegalHold | `retention_under_hold_permitted_by_data_protection_law` |
| DATA-RET R-5.1 | ErasureRequest | `erasure_request_valid` |
| DATA-RET R-5.2 | ErasureRequest | `retention_required_by_legal_obligation`, `retention_required_for_legal_claims` |
