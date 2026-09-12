# Review packet: catala

target: {"module": "ServiceCreditClaim", "path": "catala/modules/ServiceCreditClaim.catala_en"}

## Source document (authoritative)

### MSA-SCH4 L-5.1 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-5 Claiming

The Customer must claim a Service Credit in writing within 30 days after
the end of the Measurement Period to which it relates. A Service Credit not
claimed within that period is waived.

### MSA-SCH4 L-5.2 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-5 Claiming

A Service Credit is applied against the next invoice issued after the
claim is accepted. Where no further invoice will be issued, the Service Credit is
paid to the Customer within 30 days.


## Artefact under review

```
# MSA Schedule 4 — Claiming a Service Credit

> Module ServiceCreditClaim

## Prologue — declarations

```catala-metadata
declaration enumeration Settlement:
  -- AppliedAgainstNextInvoice
  -- PaidToCustomer

declaration scope CreditClaim:
  input measurement_period_end content date
  input claim_made content boolean
  input claim_date content date
  input claim_accepted_date content date
  input further_invoice_expected content boolean
  input next_invoice_date content date
  output claim_deadline content date
  output claim_in_time content boolean
  output waived content boolean
  output settlement content Settlement
  output settlement_due_date content date
```

## L-5.1 The claim window

| MSA-SCH4 L-5.1 (004-msa-sla-credits.md:92)
|
| The Customer must claim a Service Credit in writing within 30 days after
| the end of the Measurement Period to which it relates. A Service Credit not
| claimed within that period is waived.

```catala
scope CreditClaim:
  definition claim_deadline equals measurement_period_end + 30 day

  definition claim_in_time equals
    claim_made and (claim_date <= claim_deadline)

  definition waived equals not claim_in_time
```

## L-5.2 Settlement

| MSA-SCH4 L-5.2 (004-msa-sla-credits.md:96)
|
| A Service Credit is applied against the next invoice issued after the
| claim is accepted. Where no further invoice will be issued, the Service Credit is
| paid to the Customer within 30 days.

```catala
scope CreditClaim:
  label l5_2_setoff definition settlement equals AppliedAgainstNextInvoice

  label l5_2_payment exception l5_2_setoff definition settlement
    under condition not further_invoice_expected
    consequence equals PaidToCustomer

  label l5_2_setoff_date definition settlement_due_date equals next_invoice_date

  label l5_2_payment_date exception l5_2_setoff_date definition settlement_due_date
    under condition not further_invoice_expected
    consequence equals claim_accepted_date + 30 day
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/ServiceCreditClaim.catala_en",
  "scopes": {
    "CreditClaim": {
      "input": [
        "measurement_period_end",
        "claim_made",
        "claim_date",
        "claim_accepted_date",
        "further_invoice_expected",
        "next_invoice_date"
      ],
      "output": [
        "claim_deadline",
        "claim_in_time",
        "waived",
        "settlement",
        "settlement_due_date"
      ],
      "internal": [],
      "context": []
    }
  }
}