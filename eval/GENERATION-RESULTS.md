# Document generation — measured against the corpus

Reproduce with `scripts/eval_generate.py`. Method and definitions are in that script's docstring and `docs/GENERATION.md`.

## V1 — planted defects

96 mutants of the committed modules (seed 20260912, up to 6 per module), 1852s.

| class | count | meaning |
|---|---:|---|
| stillborn | 10 | does not compile; excluded |
| untestable | 21 | mutated scope has no battery; excluded |
| equivalent | 28 | no behavioural difference; excluded |
| **live** | **37** | the denominator, and the ceiling for G5 |

| of the live mutants | count | rate |
|---|---:|---:|
| killed by G2/G3 (no model) | 10 | 27% |
| sent to the logic reviewer | 0 | |
| killed by the logic reviewer | 0 | — |
| survived everything run on them | 27 | 73% |

| operator | stillborn | untestable | equivalent | live | killed by gates |
|---|---:|---:|---:|---:|---:|
| and_to_or | 3 | 2 | 6 | 5 | 3 |
| change_constant | 0 | 4 | 6 | 4 | 0 |
| drop_exception | 7 | 0 | 0 | 12 | 0 |
| flip_comparison | 0 | 7 | 6 | 9 | 4 |
| negate_condition | 0 | 5 | 4 | 6 | 3 |
| shift_threshold | 0 | 3 | 6 | 1 | 0 |

### Live mutants the cheap gates did not catch

These are what the gates cannot see: a well-formed, total rule with every branch reachable that computes the wrong thing. Catching them is the logic reviewer's job and G5's.

- `ChronicFailure:360:drop_exception:0` — drop_exception at line 360: 'label l6_2_lapsed exception l6_2_not_lapsed definition right_lapsed\n    under condition\n      right_arisen\n      and ((number of arisen_occurrences) > 0)\n      and ((number of lapsed_occurrences) = (number of arisen_occurrences))\n    consequence equals true' -> '(deleted)'
- `ErasureRequest:133:negate_condition:1` — negate_condition at line 133: 'erasure_request_valid\n      and (retention_required_by_legal_obligation\n           or retention_required_for_legal_claims\n           or legal_hold_blocks_erasure)' -> 'not (\n      erasure_request_valid\n      and (retention_required_by_legal_obligation\n           or retention_required_for_legal_claims\n           or legal_hold_blocks_erasure)\n      )'
- `ErasureRequest:135:and_to_or:0` — and_to_or at line 135: 'and (retention_required_by_legal_obligation' -> 'or (retention_required_by_legal_obligation'
- `ErasureRequest:124:drop_exception:0` — drop_exception at line 124: 'label r5_2 exception r5_1 definition deletion_due_date\n    under condition\n      erasure_request_valid\n      and (retention_required_by_legal_obligation\n           or retention_required_for_legal_claims\n           or legal_hold_blocks_erasure)\n    consequence equals Absent' -> '(deleted)'
- `LeaveAccrual:127:change_constant:0` — change_constant at line 127: 'consequence equals 2.0' -> 'consequence equals 2.25'
- `LeaveAccrual:242:drop_exception:0` — drop_exception at line 242: 'label exact_quarter_up exception nearest_half definition rounded_days\n    under condition excess_half_steps = 0.5\n    consequence equals (half_steps_below + 1.0) / 2.0' -> '(deleted)'
- `LeaveAccrual:169:flip_comparison:0` — flip_comparison at line 169: 'if period_end < period_start then 0' -> 'if period_end <= period_start then 0'
- `LeaveAccrual:290:drop_exception:0` — drop_exception at line 290: 'scope CarryForward:\n  label c9_4 exception c9_3 definition permitted_carry_forward_days\n    under condition prevented_from_taking_leave_by_long_term_sickness\n    consequence equals accrued_untaken_days' -> '(deleted)'
- `LegalHold:159:flip_comparison:0` — flip_comparison at line 159: 'if retention_end_date < released' -> 'if retention_end_date <= released'
- `LegalHold:152:negate_condition:1` — negate_condition at line 152: '(hold_issued_date with pattern Present)\n      and (hold_released_date with pattern Present)' -> 'not (\n      (hold_issued_date with pattern Present)\n      and (hold_released_date with pattern Present)\n      )'
- `LegalHold:216:negate_condition:1` — negate_condition at line 216: 'hold_in_force\n      and not retention_under_hold_permitted_by_data_protection_law' -> 'not (\n      hold_in_force\n      and not retention_under_hold_permitted_by_data_protection_law\n      )'
- `LegalHold:218:and_to_or:0` — and_to_or at line 218: 'and not retention_under_hold_permitted_by_data_protection_law' -> 'or not retention_under_hold_permitted_by_data_protection_law'
- `NdaSurvival:278:drop_exception:0` — drop_exception at line 278: 'label n5_3 exception n5_2 definition survival_end_date\n    under condition\n      information_is_trade_secret and not is_personal_data\n    consequence equals Absent' -> '(deleted)'
- `NdaSurvival:212:flip_comparison:0` — flip_comparison at line 212: 'notice >= effective_date' -> 'notice > effective_date'
- `ServiceCreditClaim:177:drop_exception:0` — drop_exception at line 177: 'label l5_1_waived_date exception l5_2_payment_date definition settlement_due_date\n    under condition waived\n    consequence equals Absent' -> '(deleted)'
- `ServiceCredits:257:flip_comparison:0` — flip_comparison at line 257: 'and (arrears_days > 30)' -> 'and (arrears_days >= 30)'
- `ServiceCredits:257:shift_threshold:1` — shift_threshold at line 257: 'and (arrears_days > 30)' -> 'and (arrears_days > 31)'
- `ServiceCredits:139:drop_exception:0` — drop_exception at line 139: 'label l4_1 exception l4_1_base definition credit_percentage\n    under condition\n      (availability_percentage < 99.90) and (availability_percentage >= 99.50)\n    consequence equals 5%' -> '(deleted)'
- `ServiceCredits:141:flip_comparison:1` — flip_comparison at line 141: '(availability_percentage < 99.90) and (availability_percentage >= 99.50)' -> '(availability_percentage < 99.90) and (availability_percentage > 99.50)'
- `accommodation:103:change_constant:0` — change_constant at line 103: 'consequence equals base_cap * 1.4' -> 'consequence equals base_cap * 1.65'
- `accommodation:101:drop_exception:0` — drop_exception at line 101: 'scope NightlyAccommodation:\n  label e4_2 exception e4_1 definition applicable_cap\n    under condition no_compliant_accommodation_available_evidenced\n    consequence equals base_cap * 1.4' -> '(deleted)'
- `availability:298:drop_exception:0` — drop_exception at line 298: 'label l4_5_excess exception l4_5_no_excess definition excess_minutes\n    under condition aggregate_minutes > cap_minutes\n    consequence equals aggregate_minutes - cap_minutes' -> '(deleted)'
- `clawback:68:change_constant:100` — change_constant at line 68: 'label no_recovery definition recoverable_amount equals $0' -> 'label no_recovery definition recoverable_amount equals $1'
- `clawback:98:change_constant:100` — change_constant at line 98: 'consequence equals $0' -> 'consequence equals $1'
- `commission:544:drop_exception:0` — drop_exception at line 544: 'scope CommissionPayable:\n  label s5_3 exception s5_1 definition cap\n    under condition attainment_calc.attainment_pct > 200%\n    consequence equals annual_base_salary * 400%' -> '(deleted)'
- `groundtransport:190:drop_exception:0` — drop_exception at line 190: 'label e5_3_airport exception e5_3 definition reimbursable_amount\n    under condition\n      is_airport_journey\n      and (minutes_after_midnight < 6 * 60\n           or minutes_after_midnight > 22 * 60)\n    consequence equals journey_cost' -> '(deleted)'
- `retention:195:drop_exception:0` — drop_exception at line 195: 'scope RetentionEnd:\n  label r3_2 exception r3_1_f definition retention_end_date\n    under condition\n      (data_class with pattern CandidateRecord)\n      and (talent_pool_consent_date with pattern Present)\n    consequence equals\n      (match talent_pool_consent_date with pattern\n       -- Absent: retention_trigger_date\n       -- Present content consented:\n            Date.max of\n              consented,\n              (maximum of consent_renewal_dates\n               or if list empty then consented))\n      + 24 month' -> '(deleted)'

