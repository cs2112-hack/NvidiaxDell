# Hourly premium rules — plain English specification

This document specifies, in English, the rules for determining the premium
payable in respect of a single hour of work, and whether that hour instead
accrues time off in lieu. It is a complete specification: an implementer
should need nothing else.

## Facts supplied about the hour

- **ordinal** — a whole number, the position of this hour within the working
  week (the 41st hour worked in the week has ordinal 41).
- **grade** — a whole number, the worker's job grade.
- **is_public_holiday** — true or false, whether the hour was worked on a
  public holiday.
- **is_critical_incident** — true or false, whether the hour was worked in
  response to a declared critical incident.
- **is_night** — true or false, whether the hour falls in the night period.
- **has_standing_shift_allowance** — true or false, whether the worker
  already receives a standing shift allowance under their contract.

## Values to determine

- **multiplier** — a decimal, the multiple of the base hourly rate payable for
  this hour by reason of when or how it was worked.
- **night_premium** — a decimal, an additional multiple of the base hourly
  rate payable because the hour falls at night.
- **total_rate** — a decimal, the two added together.
- **accrues_toil** — true or false, whether this hour accrues an hour of time
  off in lieu instead of a payment.

## Rules for the multiplier

These rules are arranged as a hierarchy of general rules and exceptions. Where
more than one rule could apply to the same hour, the rule that is stated as an
exception to the others is the one that governs. The hierarchy is, from most
general to most specific: the ordinary rule, then the over-40 rule, then the
over-48 rule, then the senior-grade rule, then the critical-incident rule,
then the public-holiday rule, then the long-public-holiday rule. Each is an
exception to the one before it.

1. **Ordinary rule.** By default, an hour attracts a multiplier of 0.

2. **Over-40 rule.** As an exception to the ordinary rule: where the ordinal is
   greater than 40, the multiplier is 1.25.

3. **Over-48 rule.** As an exception to the over-40 rule: where the ordinal is
   greater than 48, the multiplier is 1.5.

4. **Senior-grade rule.** As an exception to the over-48 rule: where the grade
   is 5 or more and the ordinal is greater than 40, the multiplier is 0.

5. **Critical-incident rule.** As an exception to the senior-grade rule: where
   the grade is 5 or more, the ordinal is greater than 40, and the hour was
   worked in response to a critical incident, the multiplier is 1.5 if the
   ordinal is greater than 48, and otherwise 1.25.

6. **Public-holiday rule.** As an exception to the critical-incident rule:
   where the hour was worked on a public holiday, the multiplier is 2.0. Note
   that this rule has no condition on the ordinal, so it applies to a public
   holiday hour even within the first 40 hours of the week.

7. **Long-public-holiday rule.** As an exception to the public-holiday rule:
   where the hour was worked on a public holiday and the ordinal is greater
   than 48, the multiplier is the greater of 2.0 and (1.5 plus 0.25).

## Rules for the night premium

1. **Night rule.** By default, the night premium is 0.15 if the hour falls at
   night, and 0 otherwise.

2. **Shift-allowance rule.** As an exception to the night rule: where the
   worker has a standing shift allowance, the night premium is 0.

## Rule for the total rate

The total rate is the multiplier plus the night premium. There is no exception
to this; the night premium is always cumulative with the multiplier, including
where the multiplier was determined by the public-holiday rule.

## Rules for time off in lieu

These rules form their own hierarchy of two levels.

1. **No-accrual rule.** By default, an hour does not accrue time off in lieu.

2. **Senior-grade accrual rule.** As an exception to the no-accrual rule: where
   the grade is 5 or more and the ordinal is greater than 40, the hour accrues
   time off in lieu.

3. **Critical-incident accrual rule.** As an exception to the senior-grade
   accrual rule: where the grade is 5 or more, the ordinal is greater than 40,
   and the hour was worked in response to a critical incident, the hour does
   not accrue time off in lieu.

Note that working on a public holiday does **not** prevent the accrual of time
off in lieu. A senior-grade hour beyond 40 worked on a public holiday both
attracts the public-holiday multiplier and accrues time off in lieu.

## A note on how the exception hierarchy must be built

An exception does not inherit the condition of the rule it is an exception to.
Each rule above states its own condition in full, and an implementation must
reproduce each condition exactly as stated rather than relying on the parent's
condition still holding.
