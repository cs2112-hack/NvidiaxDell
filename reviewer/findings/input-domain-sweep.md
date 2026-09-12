# Input-domain sweep — recommendations for the five modules under concurrent edit

Sweep of every `declaration scope` in `catala/modules/*.catala_en` for the
defect class recorded in `tests/counterexamples/CE-0011.yaml`: **an unguarded
input domain**, where a scope accepts a value the clause it comes from could
not possibly describe and computes a confident answer from it rather than
declining. In CE-0011 `mealperdiem.MealPerDiemForDay` took
`meals_provided_free: -10`, ran E-3.4's reduction backwards as an uplift, and
paid £190 against the £40 ceiling E-3.1(c) fixes — silently, unbounded in
magnitude, with no error raised.

The fix is an `assertion` on the input, placed against the clause whose
operative fact it protects. `assertion` raises `AssertionFailed`, which
`lks.catala_runner` now classifies as its own error class and which the system
surfaces as an error rather than an answer. For a legal module that is the
correct outcome: asked an impossible question it must decline, not compute.

Bounds were added directly in the fourteen modules not listed below. The five
modules here were being edited by other agents at the time of the sweep, so
each bound they need is written up as a concrete recommendation instead:
scope, input, bound, and the clause that compels it. Nothing in this file has
been applied to those files.

Each recommendation also states what the module answers *now*, unguarded,
because an impossible input that reaches money is a worse defect than one that
happens to be absorbed.

---

## 1. `availability.catala_en`

### 1.1 `AvailabilityPercentage.total_minutes` — must be strictly positive

- **Bound:** `total_minutes > 0`.
- **Clause:** L-2.3 divides by "the total number of minutes in the Measurement
  Period", and L-2.1 makes a Measurement Period "each calendar month" — the
  shortest of which has 40,320 minutes. A month with no minutes in it is not a
  Measurement Period, and the quotient the definition requires does not exist.
- **Now:** a division by zero inside `availability_percentage_unrounded`.
- **Suggested:** `assertion total_minutes > 0` in the L-2.3 block.
  Compare `commission.Attainment`, which already asserts `quota > $0` for
  exactly this reason.

### 1.2 `AvailabilityPercentage.minutes_not_available` — nought or more, and no more than the period

- **Bound:** `minutes_not_available >= 0 and minutes_not_available <= total_minutes`.
- **Clause:** L-2.4 counts "each whole minute during which the Service is not
  Available" — a count of minutes, and of minutes *of that Measurement
  Period*, since L-2.3 subtracts them from the period's own total.
- **Now:** a negative count produces an Availability Percentage above 100%; a
  count exceeding the period's minutes produces a negative one. Both are
  outside the scale L-2.3 constructs, and the first suppresses every Service
  Credit L-4.1 would otherwise give.

### 1.3 `AvailabilityPercentage.excluded_minutes` — nought or more, and a subset of the unavailable minutes

- **Bound:** `excluded_minutes >= 0 and excluded_minutes <= minutes_not_available`.
- **Clause:** L-2.5 defines Excluded Minutes as "minutes during which the
  Service is not Available by reason of" one of limbs (a) to (e). They are
  therefore a subset of the minutes counted under L-2.4, and cannot exceed
  them.
- **Now:** `unavailable_minutes` goes negative and the Availability Percentage
  rises above 100%, so the Service Level is reported met however long the
  outage. This is the highest-value bound in the module: it is the one that
  silently extinguishes the Customer's only financial remedy (L-1.2).

### 1.4 `AvailabilityPercentage.scheduled_maintenance_excess_minutes` — nought or more

- **Bound:** `scheduled_maintenance_excess_minutes >= 0`.
- **Clause:** L-4.5 speaks of the minutes by which Scheduled Maintenance
  "exceeds 8 hours in aggregate" — an excess is nought or more, and L-2.6's
  proviso is what makes it nought where the aggregate is within the cap.
- **Now:** a negative excess is subtracted twice by the `l2_4` base case and
  then added back by the `l4_5` exception; the exception's own guard
  (`> 0`) does not fire, so the figure lands in `unavailable_minutes`
  unremarked.

### 1.5 `ExcludedMinutes.minutes` and `Outage.minutes` — nought or more

- **Bound:** `minutes >= 0` in `ExcludedMinutes`, and the same for
  `Outage.minutes` as consumed by `ScheduledMaintenance.aggregate_minutes`.
- **Clause:** L-2.4, as above: the unit is a whole minute of unavailability.
- **Now:** a negative outage length passes straight through
  `excluded_minutes equals if is_excluded then minutes else 0` and reduces the
  aggregate that L-2.6's 8-hour proviso is measured against, so it can bring a
  maintenance window that did exceed 8 hours back inside the cap.

### 1.6 `ExcludedMinutes.emergency_notice_minutes` — nought or more

- **Bound:** `emergency_notice_minutes >= 0`.
- **Clause:** L-2.5(b) requires "an Emergency Maintenance event of which the
  Customer was given at least 60 minutes' notice". The quantity is a length of
  notice actually given; the least notice that can be given is none, which is
  nought.
- **Now:** absorbed — a negative figure fails the `>= 60` test, so limb (b)
  does not engage. Recommended anyway: the input describes no state of
  affairs, and the absorption depends on the direction of one comparison.

### 1.7 `ScheduledMaintenanceWindow` — the window cannot start before it was notified

- **Bound:** where `is_maintenance`, `window_start >= notified_at`.
- **Clause:** L-2.6 requires a window "notified to the Customer not less than
  5 Business Days **in advance**". Notice given after the window has begun is
  not notice in advance of it, and the clause's count of clear Business Days
  between the two dates presupposes the order.
- **Now:** absorbed — the count of business days strictly between the two
  dates is empty, so `business_days_notice` is 0 and the window is not
  Scheduled Maintenance. Worth asserting because the absorption is arithmetic
  accident: the same inverted pair would satisfy any encoding that took the
  absolute number of days between the dates.
- **Condition it on `is_maintenance`.** Where the outage is not maintenance at
  all, `notified_at` and `window_start` are filler and asserting on them would
  refuse a question L-2.6 answers perfectly well. (The same discipline is
  applied to `retention.RetentionEnd`'s statutory period, which is asserted
  only where `statutory_retention_applies`.)

### Considered and rejected for `availability.catala_en`

- **`MeasurementPeriod.period_start` must be the first day of a month.** L-2.1
  makes the Measurement Period a calendar month, and the scope derives
  `period_end` with `Date.last_day_of_month`, so a mid-month start yields a
  period shorter than the month. Tempting, but rejected: the module's own
  prose treats `period_start` as "any day in the month under examination" in
  the same way `LeaveAccrual.MonthlyAccrual` treats `day_in_month`, and
  `Date.last_day_of_month` is exactly the derivation that makes any day of the
  month do. A bound here would refuse a lawful call rather than an impossible
  fact. If the intended contract really is "the first day", that is a
  documentation fix, not an assertion.
- **`business_day_calendar` must be non-empty.** Nothing in L-2.6 says a
  Measurement Period contains a Business Day, and an empty calendar correctly
  yields no notice and hence no Scheduled Maintenance.
- **`RequestAvailable`'s three booleans.** A boolean has no domain to guard.

---

## 2. `ChronicFailure.catala_en`

### 2.1 `PeriodAvailability.availability_percentage` — 0.00 to 100.00

- **Bound:** for every element of `availability_history`,
  `availability_percentage >= 0.00 and availability_percentage <= 100.00`.
- **Clause:** L-2.3, as in 1.2 above: the figure is a proportion of the
  minutes in the Measurement Period expressed as a percentage, so it lies on
  the 0–100 scale. The module's own comment already says so ("on the 0-100
  scale"), which makes it a documented assumption with nothing enforcing it.
- **Now:** a figure of 200 reads as a period that met the Service Level and is
  dropped from `failing_periods`, so a real chronic failure stops triggering
  L-6.1; a figure of -500 adds a period that never failed. Either way the
  right to terminate turns on a number no Measurement Period could produce.
- **Note on form:** this is a bound on the elements of a list. Either
  `assertion (for all p among availability_history we have (p.availability_percentage >= 0.00 and p.availability_percentage <= 100.00))`
  in `ChronicFailureRight`, or — better, if `ServiceCredits.ServiceCredit`'s
  bound is any guide — a one-period sub-scope that both modules share.
  `ServiceCredits.ServiceCredit` now asserts the identical bound on the same
  defined term, so the two encodings should agree.

### 2.2 `PeriodAvailability.period_end` — not before `period_start`

- **Bound:** for every element, `period_end >= period_start`.
- **Clause:** L-2.1: a Measurement Period is "each calendar month", and the
  two fields are that month's first and last day. A month does not end before
  it begins.
- **Now:** the module orders and windows periods by these dates
  (`consecutive_trigger`, `rolling_trigger`, `trigger_period_end`), so an
  inverted pair silently reorders the history and can move the twelve-month
  rolling window and the L-6.2 exercise deadline computed from it.

### 2.3 `notice_date` — not before the right arose, where notice was given

- **Bound:** where `notice_given`, `notice_date` is not earlier than the end
  of the Measurement Period that gave rise to the right.
- **Clause:** L-6.2 requires the right to "be exercised within 30 days after
  the end of the Measurement Period which gives rise to it". A notice served
  before that period ended exercises a right that had not yet arisen.
- **Now:** such a notice satisfies the deadline test and can make the right
  `exercisable`.
- **Confidence:** lower than the two above, and flagged as such. The trigger
  period end is an *output* of this scope, not an input, so this is an
  internal-consistency assertion rather than a pure input-domain one, and it
  must be conditioned on `notice_given` (an ungiven notice has a filler date).
  Judgement for the module's owner.

### Considered and rejected for `ChronicFailure.catala_en`

- **`assessment_date` must not precede the history.** Asking the question
  early is a lawful question with a lawful answer (no right has arisen yet).
- **`availability_history` must be contiguous, or in date order, or twelve
  months long.** L-6.1 speaks of "three consecutive Measurement Periods" and
  "any four Measurement Periods in a rolling twelve-month period", which the
  module already tests on the dates it is given. A completeness requirement on
  the history is not stated anywhere in Schedule 4, and asserting one would
  refuse the ordinary case of a contract shorter than a year.

---

## 3. `QuotaRelief.catala_en`

### 3.1 `AbsencePeriod.end_date` — not before `start_date`

- **Bound:** for every element of `absence_periods`,
  `end_date >= start_date`.
- **Clause:** S-6.1 relieves quota for an absence "for a continuous period of
  30 days or more". A continuous period of absence runs from its first day to
  its last; it cannot end before it begins. The module's own comment fixes the
  inclusive reading ("an absence recorded as starting and ending on the same
  day is one day of absence"), which is the lawful floor — equality, not less.
- **Now:** absorbed twice over, and only by accident: `days_of_absence` comes
  out negative so the 30-day gateway fails, and `in_year_days_of_absence`
  clips to nought. Neither is a refusal, and the gateway's absorption would
  reverse the moment anyone read "30 days or more" off an absolute day count.
- **Suggested:** `assertion absence.end_date >= absence.start_date` in
  `QualifyingAbsence`, where the gateway is computed — the per-absence scope
  is the natural place, and `RelievedQuota` reaches it for every element of
  the list.

### 3.2 `RelievedQuota.annual_quota` — nought or more

- **Bound:** `annual_quota >= $0`.
- **Clause:** S-2.1 makes the Quota "the annual revenue target assigned to the
  Employee in writing". A revenue target is an amount of revenue to be booked;
  a negative target is not one. S-6.1 then reduces it "by 1/365th of the
  annual Quota for each day of such absence", and a reduction of a negative
  figure by a fraction of itself *increases* it — the same reversal of sign
  that CE-0011 found in E-3.4.
- **Now:** a negative Quota with qualifying absence yields a *larger*
  (less negative) relieved Quota, which is then the denominator of S-2.2's
  Attainment.
- **Note:** `commission.Attainment` already asserts `quota > $0`, so the
  composed path is guarded at the division. The recommendation here is the
  domain of *this* scope's own input, which S-6.1 operates on before the
  division is reached, and which is what `CommissionAfterRelief` takes from
  the caller as `assigned_quota`.

### Considered and rejected for `QuotaRelief.catala_en`

- **`relief_days <= 365` and `number of plan_year_days = 365`.** Already
  asserted in the module. Both are internal invariants of the day-counting
  rather than input domains, and they are correctly placed.
- **An upper bound on the number of absence records.** Nothing in S-6 limits
  it, and the day-counting makes overlapping records harmless.
- **`absence_periods` must fall inside the Plan Year.** Expressly rejected by
  the module's own reading, which CE-0012 and CE-0013 pin: an absence lying
  wholly outside the Plan Year is a lawful fact that relieves nothing, and an
  absence straddling the boundary passes the gateway on its full length while
  relieving only its in-year days. An assertion here would refuse both.

---

## 4. `commission.catala_en`

Two bounds are already asserted in this module — `quota > $0` in `Attainment`
(L-2.2's percentage presupposes a denominator) and `commission_payable <= cap`
in `CommissionPayable` (S-5.1) — so the technique is in use here and the
recommendations below are the gaps.

### 4.1 `CommissionPayable.annual_base_salary` / `LeaverCommission.annual_base_salary` — nought or more

- **Bound:** `annual_base_salary >= $0`.
- **Clause:** S-5.1 caps total commission at "250% of the Employee's annual
  base salary" and S-5.3 raises that to 400%. A base salary is a sum payable
  to the Employee.
- **Now:** a negative salary produces a negative cap, and the existing
  `assertion commission_payable <= cap` then forces the payable figure below
  nought — commission owed *by* the Employee, struck by the capping clause.

### 4.2 `Contract.annual_contract_value` — nought or more

- **Bound:** for every element of `contracts`, `annual_contract_value >= $0`.
- **Clause:** S-2.3 aggregates "the annual contract value of contracts signed
  by the Employee during the Plan Year, less the annual contract value of any
  Churned Contract". A contract's annual value is the revenue it books; a
  negative one is not a contract value, and S-2.3 already has its own
  mechanism — the churn subtraction — for taking value away.
- **Now:** a negative ACV reduces `gross_booked_revenue` silently, and where
  the contract is *also* churned it is subtracted a second time, which raises
  Net Booked Revenue above the gross. Both change the S-4.1 band.

### 4.3 `new_logo_share` — between 0 and 1

- **Bound:** `new_logo_share >= 0.0 and new_logo_share <= 1.0`.
- **Clause:** S-4.3 engages where "more than 60% of Net Booked Revenue is
  attributable to New Logo customers". A share of a quantity is a proportion
  of it, so it lies between nought and the whole.
- **Now:** `CommissionPayable` and `LeaverCommission` take the share as a free
  `decimal` input; `NetBookedRevenue` computes it correctly, but a caller that
  passes 5.0 or -1.0 directly gets S-4.3's +0.25 turned on or off with no
  complaint.
- **Note:** the same argument as 4.1/4.2 — the guard belongs where the clause
  makes the figure operative, i.e. in `Accelerator`, which is the scope S-4.3
  is encoded in and which every path reaches.

### 4.4 `LeaverCommission.commission_already_paid` — nought or more

- **Bound:** `commission_already_paid >= $0`.
- **Clause:** S-8.2 forfeits "any commission not yet paid at the date of
  termination", so the figure is a sum already paid out.
- **Now:** it is subtracted to reach the leaver's outstanding entitlement, so
  a negative figure inflates the payment. (CE-0016 shows how sensitive that
  subtraction is: it is the quantity that took `commission_payable` to
  -£60,000.)

### 4.5 `LeaverCommission.last_day_of_employment` — not before `commencement_date`

- **Bound:** `last_day_of_employment >= commencement_date`.
- **Clause:** S-8.1 entitles an Employee "who ceases employment" to commission
  on contracts "signed on or before their last day of employment"; S-4.4
  speaks of an Employee "who commenced employment after the start of the Plan
  Year". Employment cannot cease before it commences.
- **Now:** `qualifying_contracts` filters on the last day alone, so an
  inverted pair yields an empty contract set and a nil entitlement — an answer
  where the facts support none.

### Considered and rejected for `commission.catala_en`

- **`Contract.contract_id` bounds.** An opaque identifier; the Plan fixes
  nothing about it. (Same reasoning as `GazettedHoliday.place_of_work` in
  `WorkingTimeDefs`, where the integer is an opaque place-of-work code.)
- **`Contract.signature_date` must fall inside the Plan Year.** Rejected:
  S-2.3 selects contracts "signed ... during the Plan Year" by filtering, and
  S-2.6's New Logo test looks 24 months *before* the signature date. Contracts
  outside the window are lawful facts the module is meant to exclude, not
  refuse.
- **`terminated_by_customer_on` / `not_renewed_at_first_renewal_on` must
  follow `signature_date`.** Attractive, and S-2.4's "within 180 days after
  its signature date" does presuppose the order — but the churn test is a
  window test the module already applies, and a date before signature simply
  falls outside it. Left to the module's owner as a judgement call; it is the
  weakest of the date orderings in this file.
- **`attainment_pct` bounds in `Accelerator`.** Attainment is unbounded above
  by design — S-5.3 contemplates Attainment over 200% — and while a negative
  Attainment is impossible once `net_booked_revenue >= $0` and `quota > $0`
  hold, `Accelerator` receives it as a computed figure from `Attainment`
  rather than as a primary fact. Guard the operands, not the quotient.

---

## 5. `ServiceCreditClaim.catala_en`

### 5.1 `claim_accepted_date` — not before `claim_date`, where a claim was made

- **Bound:** where `claim_made`, `claim_accepted_date >= claim_date`.
- **Clause:** L-5.2 fixes settlement by reference to "the next invoice issued
  after **the claim is accepted**". A claim cannot be accepted before it is
  made.
- **Now:** the payment route computes `claim_accepted_date + 30 day`, so an
  acceptance date before the claim produces a settlement date that may precede
  the claim itself — an obligation dischargeable on no facts at all.
- **Condition it on `claim_made`:** where no claim was made, both dates are
  filler and L-5.1's waiver is the whole answer.

### 5.2 `claim_date` — not before `measurement_period_end`, where a claim was made

- **Bound:** where `claim_made`, `claim_date >= measurement_period_end`.
- **Clause:** L-5.1 gives the Customer 30 days "after the end of the
  Measurement Period to which it relates" to claim, and the credit itself
  cannot exist until the period's Availability Percentage is known, which
  L-2.3 computes over the whole period.
- **Now:** absorbed in the answer but not in the reasoning: an early claim is
  "in time" under `claim_date <= claim_deadline`, which is the right answer
  for the wrong reason.
- **Confidence:** medium. L-5.1's window is expressed as a deadline rather
  than as a two-sided window, so the floor is an inference from "after the end
  of the Measurement Period". Judgement for the module's owner.

### 5.3 `next_invoice_date` — not before the claim was accepted, where a further invoice is expected

- **Bound:** where `further_invoice_expected`,
  `next_invoice_date >= claim_accepted_date`.
- **Clause:** L-5.2's set-off route is against "the next invoice issued
  **after** the claim is accepted". An invoice issued before acceptance is not
  that invoice.
- **Now:** `settlement_due_date` is `next_invoice_date` outright on the
  set-off route, so the module reports a settlement date in the past.
- **Condition it on `further_invoice_expected`:** where no further invoice
  will be issued, L-5.2's second sentence governs and the date is filler.

### Considered and rejected for `ServiceCreditClaim.catala_en`

- **Bounds on the two booleans.** Nothing to guard.
- **`measurement_period_end` must be the last day of a month.** L-2.1 makes
  the Measurement Period a calendar month, so in principle its end is a
  month's last day — but the scope uses the date only as the start of a 30-day
  count, nothing in L-5 turns on which day of the month it is, and
  `availability.MeasurementPeriod` is the scope that owns the month boundary.
  Asserting it here would duplicate that ownership and could refuse a lawful
  question about a period defined elsewhere.

---

## Cross-cutting note for all five

Two disciplines from the fourteen modules already fixed are worth carrying
across, because both are ways an assertion can itself become a defect:

1. **Condition a bound on the judgement that makes its input operative.**
   Where an input is a paired judgement (`statutory_retention_applies` with
   `statutory_retention_period`, `claim_made` with `claim_date`,
   `notice_given` with `notice_date`, `is_maintenance` with `notified_at`), a
   filler value in the unused half is not an impossible fact — it is no fact
   at all. `retention.RetentionEnd` asserts its statutory period only where
   `statutory_retention_applies`, and CE-0020's own inputs
   (`statutory_retention_period: {years: 0}` with the flag false) are why.

2. **Do not compare durations against a bound.** Catala's days and months are
   incomparable, and `some_duration >= 0 day` raises "ambiguous comparison
   between durations in different units" on a lawful mixed-unit duration
   (verified: it holds for a pure-month or pure-day period and raises for
   "6 months and 15 days"). Where a duration must not run backwards, add it to
   the date it is measured from and compare the dates, as
   `retention.RetentionEnd` now does.
