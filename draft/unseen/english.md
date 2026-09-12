# Referral bonus rules — plain English specification

This document specifies, in English, the bonus payable when a worker refers a
candidate whom the organisation later employs. It is a complete specification:
an implementer should need nothing else.

## Facts supplied

- **grade** — a whole number, the job grade at which the referred person is
  employed.
- **is_hard_to_fill** — true or false, whether the role was designated
  hard-to-fill.
- **agency_introduced** — true or false, whether a recruitment agency had
  introduced the same candidate to the organisation.
- **agency_introduction_date** — a date, when that agency introduction
  happened.
- **referral_date** — a date, when the worker submitted the referral.
- **first_day_of_employment** — a date, the referred person's first day.
- **has_ceased** — true or false, whether the referred person has stopped
  working for the organisation.
- **cessation_date** — a date, when they stopped.
- **cessation_by_redundancy** — true or false, whether they stopped by reason
  of redundancy.
- **bonus_already_paid_this_year** — an amount of money already paid to this
  worker under these rules in the current financial year.

## Values to determine

- **qualifying_period_end** — a date, the last day of the qualifying period.
- **entitlement** — an amount of money, the bonus the referral earns before
  any question of payability or the annual limit.
- **payable** — true or false, whether the bonus is payable at all.
- **bonus_before_cap** — an amount of money: the entitlement if payable, nil
  otherwise.
- **bonus_payable** — an amount of money, the final figure after the annual
  limit.

## The qualifying period

The qualifying period is 180 days **beginning on** the referred person's first
day of employment. The first day is therefore day 1, and the period ends on the
179th day after it.

## Rules for the entitlement

These rules form a hierarchy of general rules and exceptions. Where more than
one could apply, the rule stated as an exception to the others governs. The
hierarchy runs, from most general to most specific: the standard rule, then the
senior-grade rule, then the hard-to-fill rule, then the agency rule, then the
stale-agency rule. Each is an exception to the one before it.

1. **Standard rule.** By default the entitlement is 1,000.

2. **Senior-grade rule.** As an exception to the standard rule: where the grade
   is 5 or more, the entitlement is 2,500.

3. **Hard-to-fill rule.** As an exception to the senior-grade rule: where the
   role is hard-to-fill, the entitlement is 4,000.

4. **Agency rule.** As an exception to the hard-to-fill rule: where an agency
   introduced the candidate AND that introduction happened strictly before the
   referral was submitted, the entitlement is nil.

5. **Stale-agency rule.** As an exception to the agency rule: where an agency
   introduced the candidate, that introduction happened strictly before the
   referral was submitted, AND the introduction was more than 12 months before
   the referral, the entitlement is re-derived as it would have been under
   rules 1 to 3 — that is, 4,000 if the role is hard-to-fill, otherwise 2,500
   if the grade is 5 or more, otherwise 1,000.

   "More than 12 months before" means the introduction date advanced by 12
   months is strictly earlier than the referral date. Where advancing a date by
   12 months would land on a day that does not exist, round the result down to
   the last day of the month.

## Rules for payability

These form their own hierarchy of three levels.

1. **Payable rule.** By default the bonus is payable.

2. **Early-cessation rule.** As an exception to the payable rule: where the
   referred person has ceased employment and the cessation date is on or before
   the last day of the qualifying period, the bonus is not payable.

3. **Redundancy rule.** As an exception to the early-cessation rule: where the
   referred person has ceased employment on or before the last day of the
   qualifying period AND the cessation was by reason of redundancy, the bonus
   is payable.

## Combining entitlement and payability

`bonus_before_cap` is the entitlement where the bonus is payable, and nil
otherwise. There is no exception to this.

## The annual limit

The total payable to a worker under these rules in any financial year must not
exceed 10,000, and that limit is applied **after** everything above. So the
amount available for this referral is 10,000 less what has already been paid
this year; if that headroom is nil or negative the figure is nil, if
`bonus_before_cap` exceeds the headroom the figure is the headroom, and
otherwise the figure is `bonus_before_cap`.

## A note on building the exception hierarchy

An exception does not inherit the condition of the rule it is an exception to.
Each rule above states its own condition in full, and an implementation must
reproduce each condition exactly as stated rather than relying on the parent's
condition still holding. Note in particular that rules 4 and 5 both restate the
agency conditions.
