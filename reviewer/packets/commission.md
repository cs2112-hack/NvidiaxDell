# Review packet: catala

target: {"module": "commission", "path": "catala/modules/commission.catala_en"}

## Source document (authoritative)

### COMM-PLAN S-1.1 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-1 Introduction

This Plan sets out the basis on which commission is earned by Employees
in a quota-bearing sales role during the financial year commencing 1 October 2025
and ending 30 September 2026 (the "Plan Year").

### COMM-PLAN S-2.1 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-2 Definitions

"Quota" means the annual revenue target assigned to the Employee in
writing at the start of the Plan Year, or on joining if later.

### COMM-PLAN S-2.5 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-2 Definitions

"Base Commission Rate" means 8.0% of Net Booked Revenue.

### COMM-PLAN S-2.4 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-2 Definitions

"Churned Contract" means a contract which is terminated by the
customer, or which is not renewed at its first renewal date, in either case
within 180 days after its signature date.

### COMM-PLAN S-2.6 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-2 Definitions

"New Logo" means a customer which had no contract with the Company at
any time in the 24 months preceding the signature date.

### COMM-PLAN S-2.3 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-2 Definitions

"Net Booked Revenue" means the aggregate annual contract value of
contracts signed by the Employee during the Plan Year, less the annual contract
value of any Churned Contract.

### COMM-PLAN S-2.2 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-2 Definitions

"Attainment" means Net Booked Revenue expressed as a percentage of
Quota.

### COMM-PLAN S-3.1 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-3 Commission calculation

Commission is calculated as Net Booked Revenue multiplied by the Base
Commission Rate, multiplied by the accelerator determined under S-4.

### COMM-PLAN S-4.1 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-4 Accelerators

The accelerator is determined by Attainment as follows:

  (a) Attainment of less than 50%: accelerator of 0.0;
  (b) Attainment of 50% or more but less than 100%: accelerator of 1.0;
  (c) Attainment of 100% or more but less than 150%: accelerator of 1.5;
  (d) Attainment of 150% or more: accelerator of 2.0.

### COMM-PLAN S-4.2 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-4 Accelerators

The accelerator determined under S-4.1 applies to the whole of Net
Booked Revenue and not only to the portion above the relevant threshold.

### COMM-PLAN S-4.3 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-4 Accelerators

By way of exception to S-4.1, where more than 60% of Net Booked Revenue
is attributable to New Logo customers, the accelerator determined under S-4.1 is
increased by 0.25.

### COMM-PLAN S-4.4 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-4 Accelerators

By way of exception to S-4.1 and S-4.3, the accelerator applicable to an
Employee who commenced employment after the start of the Plan Year is capped at
1.5 for the Plan Year in which they commenced.

### COMM-PLAN S-5.1 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-5 Caps

Total commission payable to an Employee in respect of the Plan Year
must not exceed 250% of the Employee's annual base salary.

### COMM-PLAN S-5.3 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-5 Caps

By way of exception to S-5.1, where the Employee's Attainment exceeds
200%, the cap is 400% of annual base salary.

### COMM-PLAN S-5.2 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-5 Caps

The cap in S-5.1 is applied after the accelerator under S-4 and after
any adjustment under S-6.

### COMM-PLAN S-8.1 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-8 Leavers

An Employee who ceases employment is entitled to commission in respect
of contracts signed on or before their last day of employment, payable in the
ordinary payment cycle.

### COMM-PLAN S-8.2 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-8 Leavers

By way of exception to S-8.1, an Employee whose employment is terminated
for gross misconduct forfeits any commission not yet paid at the date of
termination.


## Artefact under review

```
# Sales Commission Plan — FY26: commission earned

> Module Commission

## Prologue — declarations

```catala-metadata
declaration enumeration TerminationReason:
  -- GrossMisconduct
  -- OtherReason

declaration structure Contract:
  data contract_id content integer
  data annual_contract_value content money
  data signature_date content date
  data terminated_by_customer_on content optional of date
  data not_renewed_at_first_renewal_on content optional of date
  data customer_contract_dates content list of date

declaration scope ChurnedContract:
  input contract content Contract
  output is_churned content boolean

declaration scope NewLogo:
  input signature_date content date
  input customer_contract_dates content list of date
  output window_start content date
  output is_new_logo content boolean

declaration scope AssignedQuota:
  input assigned_quota content money
  input commencement_date content date
  output quota_assignment_date content date
  output quota content money

declaration scope NetBookedRevenue:
  input contracts content list of Contract
  internal plan_year_contracts content list of Contract
  internal churned_contracts content list of Contract
  output gross_booked_revenue content money
  output churned_contract_value content money
  output net_booked_revenue content money
  output new_logo_revenue content money
  output new_logo_share content decimal

declaration scope Attainment:
  input net_booked_revenue content money
  input quota content money
  output attainment_pct content decimal

declaration scope Accelerator:
  input attainment_pct content decimal
  input new_logo_share content decimal
  input commencement_date content date
  output commenced_after_plan_year_start content boolean
  output s4_1_band content decimal
  output accelerator_before_joiner_cap content decimal
  output accelerator content decimal

declaration scope CommissionPayable:
  input net_booked_revenue content money
  input new_logo_share content decimal
  input quota content money
  input commencement_date content date
  input annual_base_salary content money
  output attainment_calc scope Attainment
  output accelerator_calc scope Accelerator
  output accelerator_base content money
  output commission_before_cap content money
  output cap content money
  output commission_payable content money

declaration scope LeaverCommission:
  input contracts content list of Contract
  input last_day_of_employment content date
  input termination_reason content TerminationReason
  input quota content money
  input commencement_date content date
  input annual_base_salary content money
  input commission_already_paid content money
  output qualifying_contracts content list of Contract
  output revenue scope NetBookedRevenue
  output entitlement scope CommissionPayable
  output commission_payable content money
  output overpaid_amount content money
```

## S-1 Introduction

| COMM-PLAN S-1.1 (003-sales-commission-plan.md:14)
|
| This Plan sets out the basis on which commission is earned by Employees
| in a quota-bearing sales role during the financial year commencing 1 October 2025
| and ending 30 September 2026 (the "Plan Year").

```catala-metadata
declaration plan_year_start content date equals |2025-10-01|
declaration plan_year_end content date equals |2026-09-30|

declaration signed_in_plan_year content boolean
  depends on c content Contract
  equals
    c.signature_date >= plan_year_start
    and c.signature_date <= plan_year_end
```

## S-2 Definitions

| COMM-PLAN S-2.1 (003-sales-commission-plan.md:26)
|
| "Quota" means the annual revenue target assigned to the Employee in
| writing at the start of the Plan Year, or on joining if later.

```catala
scope AssignedQuota:
  definition quota_assignment_date equals
    Date.max of plan_year_start, commencement_date

  definition quota equals assigned_quota
```

| COMM-PLAN S-2.5 (003-sales-commission-plan.md:40)
|
| "Base Commission Rate" means 8.0% of Net Booked Revenue.

```catala
declaration base_commission_rate content decimal equals 8.0%
```

| COMM-PLAN S-2.4 (003-sales-commission-plan.md:36)
|
| "Churned Contract" means a contract which is terminated by the
| customer, or which is not renewed at its first renewal date, in either case
| within 180 days after its signature date.

```catala
scope ChurnedContract:
  definition is_churned equals
    (match contract.terminated_by_customer_on with pattern
     -- Absent: false
     -- Present content d:
         d >= contract.signature_date
         and d - contract.signature_date <= 180 day)
    or
    (match contract.not_renewed_at_first_renewal_on with pattern
     -- Absent: false
     -- Present content d:
         d >= contract.signature_date
         and d - contract.signature_date <= 180 day)
```

| COMM-PLAN S-2.6 (003-sales-commission-plan.md:42)
|
| "New Logo" means a customer which had no contract with the Company at
| any time in the 24 months preceding the signature date.

```catala
scope NewLogo:
  date round down

  definition window_start equals signature_date - 24 month

  definition is_new_logo equals
    not (exists d among customer_contract_dates such that
           d >= window_start and d < signature_date)
```

| COMM-PLAN S-2.3 (003-sales-commission-plan.md:32)
|
| "Net Booked Revenue" means the aggregate annual contract value of
| contracts signed by the Employee during the Plan Year, less the annual contract
| value of any Churned Contract.

```catala
scope NetBookedRevenue:
  definition plan_year_contracts equals
    list of c among contracts such that signed_in_plan_year of c

  definition churned_contracts equals
    list of c among plan_year_contracts
    such that (output of ChurnedContract with { -- contract: c }).is_churned

  definition gross_booked_revenue equals
    Money.sum of (map each c among plan_year_contracts to c.annual_contract_value)

  definition churned_contract_value equals
    Money.sum of (map each c among churned_contracts to c.annual_contract_value)

  definition net_booked_revenue equals
    gross_booked_revenue - churned_contract_value
```

| COMM-PLAN S-2.2 (003-sales-commission-plan.md:29)
|
| "Attainment" means Net Booked Revenue expressed as a percentage of
| Quota.

```catala
scope Attainment:
  definition attainment_pct equals net_booked_revenue / quota

  assertion quota > $0
```

## S-3 Commission calculation

| COMM-PLAN S-3.1 (003-sales-commission-plan.md:47)
|
| Commission is calculated as Net Booked Revenue multiplied by the Base
| Commission Rate, multiplied by the accelerator determined under S-4.

```catala
scope CommissionPayable:
  definition commission_before_cap equals
    money of
      (decimal of accelerator_base
       * base_commission_rate
       * accelerator_calc.accelerator)
```

## S-4 Accelerators

| COMM-PLAN S-4.1 (003-sales-commission-plan.md:52)
|
| The accelerator is determined by Attainment as follows:
|
|   (a) Attainment of less than 50%: accelerator of 0.0;
|   (b) Attainment of 50% or more but less than 100%: accelerator of 1.0;
|   (c) Attainment of 100% or more but less than 150%: accelerator of 1.5;
|   (d) Attainment of 150% or more: accelerator of 2.0.

```catala
scope Accelerator:
  label s4_1_table definition s4_1_band
    under condition attainment_pct < 50%
    consequence equals 0.0

  label s4_1_table definition s4_1_band
    under condition attainment_pct >= 50% and attainment_pct < 100%
    consequence equals 1.0

  label s4_1_table definition s4_1_band
    under condition attainment_pct >= 100% and attainment_pct < 150%
    consequence equals 1.5

  label s4_1_table definition s4_1_band
    under condition attainment_pct >= 150%
    consequence equals 2.0

  label s4_1 definition accelerator equals s4_1_band
```

| COMM-PLAN S-4.2 (003-sales-commission-plan.md:59)
|
| The accelerator determined under S-4.1 applies to the whole of Net
| Booked Revenue and not only to the portion above the relevant threshold.

```catala
scope CommissionPayable:
  definition accelerator_base equals net_booked_revenue
```

| COMM-PLAN S-4.3 (003-sales-commission-plan.md:62)
|
| By way of exception to S-4.1, where more than 60% of Net Booked Revenue
| is attributable to New Logo customers, the accelerator determined under S-4.1 is
| increased by 0.25.

```catala
scope NetBookedRevenue:
  definition new_logo_revenue equals
    Money.sum of (map each c among plan_year_contracts
                  such that
                    not (output of ChurnedContract with { -- contract: c }).is_churned
                    and (output of NewLogo with {
                           -- signature_date: c.signature_date
                           -- customer_contract_dates: c.customer_contract_dates
                         }).is_new_logo
                  to c.annual_contract_value)

  definition new_logo_share equals
    if net_booked_revenue = $0 then 0.0
    else new_logo_revenue / net_booked_revenue
```

| COMM-PLAN S-4.3 (003-sales-commission-plan.md:62)
|
| By way of exception to S-4.1, where more than 60% of Net Booked Revenue
| is attributable to New Logo customers, the accelerator determined under S-4.1 is
| increased by 0.25.

```catala
scope Accelerator:
  definition accelerator_before_joiner_cap equals
    if new_logo_share > 60% then s4_1_band + 0.25 else s4_1_band

  label s4_3 exception s4_1 definition accelerator
    under condition new_logo_share > 60%
    consequence equals accelerator_before_joiner_cap
```

| COMM-PLAN S-4.4 (003-sales-commission-plan.md:66)
|
| By way of exception to S-4.1 and S-4.3, the accelerator applicable to an
| Employee who commenced employment after the start of the Plan Year is capped at
| 1.5 for the Plan Year in which they commenced.

```catala
scope Accelerator:
  definition commenced_after_plan_year_start equals
    commencement_date > plan_year_start

  label s4_4 exception s4_3 definition accelerator
    under condition commenced_after_plan_year_start
    consequence equals Decimal.min of accelerator_before_joiner_cap, 1.5
```

## S-5 Caps

| COMM-PLAN S-5.1 (003-sales-commission-plan.md:72)
|
| Total commission payable to an Employee in respect of the Plan Year
| must not exceed 250% of the Employee's annual base salary.

```catala
scope CommissionPayable:
  label s5_1 definition cap equals annual_base_salary * 250%

  assertion commission_payable <= cap
```

| COMM-PLAN S-5.3 (003-sales-commission-plan.md:78)
|
| By way of exception to S-5.1, where the Employee's Attainment exceeds
| 200%, the cap is 400% of annual base salary.

```catala
scope CommissionPayable:
  label s5_3 exception s5_1 definition cap
    under condition attainment_calc.attainment_pct > 200%
    consequence equals annual_base_salary * 400%
```

| COMM-PLAN S-5.2 (003-sales-commission-plan.md:75)
|
| The cap in S-5.1 is applied after the accelerator under S-4 and after
| any adjustment under S-6.

```catala
scope CommissionPayable:
  definition attainment_calc.net_booked_revenue equals net_booked_revenue
  definition attainment_calc.quota equals quota

  definition accelerator_calc.attainment_pct equals attainment_calc.attainment_pct
  definition accelerator_calc.new_logo_share equals new_logo_share
  definition accelerator_calc.commencement_date equals commencement_date

  definition commission_payable equals
    Money.min of commission_before_cap, cap
```

## S-8 Leavers

| COMM-PLAN S-8.1 (003-sales-commission-plan.md:106)
|
| An Employee who ceases employment is entitled to commission in respect
| of contracts signed on or before their last day of employment, payable in the
| ordinary payment cycle.

```catala
scope LeaverCommission:
  definition qualifying_contracts equals
    list of c among contracts
    such that c.signature_date <= last_day_of_employment

  definition revenue.contracts equals qualifying_contracts

  definition entitlement.net_booked_revenue equals revenue.net_booked_revenue
  definition entitlement.new_logo_share equals revenue.new_logo_share
  definition entitlement.quota equals quota
  definition entitlement.commencement_date equals commencement_date
  definition entitlement.annual_base_salary equals annual_base_salary

  label s8_1 definition commission_payable equals
    Money.positive of
      (entitlement.commission_payable - commission_already_paid)
```

| COMM-PLAN S-8.1 (003-sales-commission-plan.md:106)
|
| An Employee who ceases employment is entitled to commission in respect
| of contracts signed on or before their last day of employment, payable in the
| ordinary payment cycle.

```catala
scope LeaverCommission:
  definition overpaid_amount equals
    Money.positive of
      (commission_already_paid - entitlement.commission_payable)
```

| COMM-PLAN S-8.2 (003-sales-commission-plan.md:110)
|
| By way of exception to S-8.1, an Employee whose employment is terminated
| for gross misconduct forfeits any commission not yet paid at the date of
| termination.

```catala
scope LeaverCommission:
  label s8_2 exception s8_1 definition commission_payable
    under condition termination_reason with pattern GrossMisconduct
    consequence equals $0
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/commission.catala_en",
  "scopes": {
    "ChurnedContract": {
      "input": [
        "contract"
      ],
      "output": [
        "is_churned"
      ],
      "internal": [],
      "context": []
    },
    "NewLogo": {
      "input": [
        "signature_date",
        "customer_contract_dates"
      ],
      "output": [
        "window_start",
        "is_new_logo"
      ],
      "internal": [],
      "context": []
    },
    "AssignedQuota": {
      "input": [
        "assigned_quota",
        "commencement_date"
      ],
      "output": [
        "quota_assignment_date",
        "quota"
      ],
      "internal": [],
      "context": []
    },
    "NetBookedRevenue": {
      "input": [
        "contracts"
      ],
      "output": [
        "gross_booked_revenue",
        "churned_contract_value",
        "net_booked_revenue",
        "new_logo_revenue",
        "new_logo_share"
      ],
      "internal": [
        "plan_year_contracts",
        "churned_contracts"
      ],
      "context": []
    },
    "Attainment": {
      "input": [
        "net_booked_revenue",
        "quota"
      ],
      "output": [
        "attainment_pct"
      ],
      "internal": [],
      "context": []
    },
    "Accelerator": {
      "input": [
        "attainment_pct",
        "new_logo_share",
        "commencement_date"
      ],
      "output": [
        "commenced_after_plan_year_start",
        "s4_1_band",
        "accelerator_before_joiner_cap",
        "accelerator"
      ],
      "internal": [],
      "context": []
    },
    "CommissionPayable": {
      "input": [
        "net_booked_revenue",
        "new_logo_share",
        "quota",
        "commencement_date",
        "annual_base_salary"
      ],
      "output": [
        "accelerator_base",
        "commission_before_cap",
        "cap",
        "commission_payable"
      ],
      "internal": [],
      "context": []
    },
    "LeaverCommission": {
      "input": [
        "contracts",
        "last_day_of_employment",
        "termination_reason",
        "quota",
        "commencement_date",
        "annual_base_salary",
        "commission_already_paid"
      ],
      "output": [
        "qualifying_contracts",
        "commission_payable",
        "overpaid_amount"
      ],
      "internal": [],
      "context": []
    }
  }
}