# Review packet: catala

target: {"module": "clawback", "path": "catala/modules/clawback.catala_en"}

## Source document (authoritative)

### COMM-PLAN S-7.1 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-7 Clawback

Where commission has been paid in respect of a contract which
subsequently becomes a Churned Contract, the Company may recover the commission
attributable to that contract by deduction from future commission payments or,
where no such payments fall due, as a debt.

### COMM-PLAN S-7.2 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-7 Clawback

By way of exception to S-7.1, no recovery will be made where the
contract was terminated by the customer by reason of a material breach by the
Company.

### COMM-PLAN S-7.3 — Sales Commission Plan — FY26 (v1.3, effective 2025-10-01)
Section S-7 Clawback

No recovery will be made more than 24 months after the date on which
the commission was paid.


## Artefact under review

```
# Sales Commission Plan — FY26: clawback

> Module Clawback

## Prologue — declarations

```catala-metadata
declaration scope RecoverableCommission:
  input commission_paid content money
  input payment_date content date
  input assessment_date content date
  input is_churned_contract content boolean
  input terminated_for_company_material_breach content boolean
  output long_stop_date content date
  output recovery_time_barred content boolean
  output recoverable_amount content money
```

## S-7 Clawback

| COMM-PLAN S-7.1 (003-sales-commission-plan.md:92)
|
| Where commission has been paid in respect of a contract which
| subsequently becomes a Churned Contract, the Company may recover the commission
| attributable to that contract by deduction from future commission payments or,
| where no such payments fall due, as a debt.

```catala
scope RecoverableCommission:
  assertion commission_paid >= $0

  label no_recovery definition recoverable_amount equals $0

  label s7_1 exception no_recovery definition recoverable_amount
    under condition is_churned_contract
    consequence equals commission_paid
```

| COMM-PLAN S-7.2 (003-sales-commission-plan.md:97)
|
| By way of exception to S-7.1, no recovery will be made where the
| contract was terminated by the customer by reason of a material breach by the
| Company.

```catala
scope RecoverableCommission:
  label s7_2 exception s7_1 definition recoverable_amount
    under condition
      is_churned_contract and terminated_for_company_material_breach
    consequence equals $0
```

| COMM-PLAN S-7.3 (003-sales-commission-plan.md:101)
|
| No recovery will be made more than 24 months after the date on which
| the commission was paid.

```catala
scope RecoverableCommission:
  date round down

  definition long_stop_date equals payment_date + 24 month

  definition recovery_time_barred equals assessment_date > long_stop_date

  label s7_3 exception s7_2 definition recoverable_amount
    under condition is_churned_contract and recovery_time_barred
    consequence equals $0
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/clawback.catala_en",
  "scopes": {
    "RecoverableCommission": {
      "input": [
        "commission_paid",
        "payment_date",
        "assessment_date",
        "is_churned_contract",
        "terminated_for_company_material_breach"
      ],
      "output": [
        "long_stop_date",
        "recovery_time_barred",
        "recoverable_amount"
      ],
      "internal": [],
      "context": []
    }
  }
}