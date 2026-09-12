# Review packet: catala

target: {"module": "ServiceCredits", "path": "catala/modules/ServiceCredits.catala_en"}

## Source document (authoritative)

### MSA-SCH4 L-2.7 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Monthly Service Charge" means the charges payable by the Customer in
respect of the Service for the Measurement Period in question, excluding
one-off charges, professional services charges and pass-through charges.

### MSA-SCH4 L-4.1 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-4 Service Credits

Where the Availability Percentage in a Measurement Period is less than
99.90%, the Customer is entitled to a Service Credit calculated as a percentage
of the Monthly Service Charge as follows:

  (a) less than 99.90% but not less than 99.50%: 5%;
  (b) less than 99.50% but not less than 99.00%: 10%;
  (c) less than 99.00% but not less than 98.00%: 20%;
  (d) less than 98.00%: 30%.

### MSA-SCH4 L-4.2 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-4 Service Credits

The maximum aggregate Service Credit in respect of any Measurement
Period is 30% of the Monthly Service Charge.

### MSA-SCH4 L-4.3 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-4 Service Credits

By way of exception to L-4.2, where the Availability Percentage is less
than 95.00%, the maximum aggregate Service Credit in respect of that Measurement
Period is 50% of the Monthly Service Charge and the Service Credit is 50%.

### MSA-SCH4 L-4.4 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-4 Service Credits

By way of exception to L-4.1, no Service Credit is payable in respect of
a Measurement Period in which the Customer is in arrears of any undisputed
invoice for more than 30 days.

### Not quoted by the artefact

The clauses above cross-refer to the provisions below, or use terms they define.

### MSA-SCH4 L-2.1 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Measurement Period" means each calendar month.

### MSA-SCH4 L-2.3 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Availability Percentage" means, in respect of a Measurement Period,
the total number of minutes in the Measurement Period less Unavailable Minutes,
divided by the total number of minutes in the Measurement Period, expressed as a
percentage and rounded to two decimal places.


## Artefact under review

```
#

> Module ServiceCredits

##

```catala-metadata
declaration scope MonthlyServiceCharge:
  input charges_for_the_service content money
  input one_off_charges content money
  input professional_services_charges content money
  input pass_through_charges content money
  output monthly_service_charge content money

declaration scope ServiceCredit:
  input availability_percentage content decimal
  input monthly_service_charge content money
  input arrears_days content integer
  input invoice_undisputed content boolean
  output credit_percentage content decimal
  output credit_cap_percentage content decimal
  output credit_payable_percentage content decimal
  output credit_amount content money
```

## L-2.7

| MSA-SCH4 L-2.7 (004-msa-sla-credits.md:55)
|
| "Monthly Service Charge" means the charges payable by the Customer in
| respect of the Service for the Measurement Period in question, excluding
| one-off charges, professional services charges and pass-through charges.

```catala
scope MonthlyServiceCharge:
  assertion charges_for_the_service >= $0.00

  assertion one_off_charges >= $0.00

  assertion professional_services_charges >= $0.00

  assertion pass_through_charges >= $0.00

  assertion
    one_off_charges + professional_services_charges + pass_through_charges
      <= charges_for_the_service

  definition monthly_service_charge equals
    charges_for_the_service
    - one_off_charges
    - professional_services_charges
    - pass_through_charges
```

## L-4.1

| MSA-SCH4 L-4.1 (004-msa-sla-credits.md:66)
|
| Where the Availability Percentage in a Measurement Period is less than
| 99.90%, the Customer is entitled to a Service Credit calculated as a percentage
| of the Monthly Service Charge as follows:
|
|   (a) less than 99.90% but not less than 99.50%: 5%;
|   (b) less than 99.50% but not less than 99.00%: 10%;
|   (c) less than 99.00% but not less than 98.00%: 20%;
|   (d) less than 98.00%: 30%.

```catala
scope ServiceCredit:
  assertion
    availability_percentage >= 0.00 and availability_percentage <= 100.00

  label l4_1_base definition credit_percentage equals 0%

  label l4_1 exception l4_1_base definition credit_percentage
    under condition
      (availability_percentage < 99.90) and (availability_percentage >= 99.50)
    consequence equals 5%

  label l4_1 exception l4_1_base definition credit_percentage
    under condition
      (availability_percentage < 99.50) and (availability_percentage >= 99.00)
    consequence equals 10%

  label l4_1 exception l4_1_base definition credit_percentage
    under condition
      (availability_percentage < 99.00) and (availability_percentage >= 98.00)
    consequence equals 20%

  label l4_1 exception l4_1_base definition credit_percentage
    under condition availability_percentage < 98.00
    consequence equals 30%

  definition credit_amount equals
    monthly_service_charge * credit_payable_percentage
```

## L-4.2

| MSA-SCH4 L-4.2 (004-msa-sla-credits.md:75)
|
| The maximum aggregate Service Credit in respect of any Measurement
| Period is 30% of the Monthly Service Charge.

```catala
scope ServiceCredit:
  label l4_2 definition credit_cap_percentage equals 30%

  definition credit_payable_percentage equals
    if credit_percentage > credit_cap_percentage
    then credit_cap_percentage
    else credit_percentage
```

## L-4.3

| MSA-SCH4 L-4.3 (004-msa-sla-credits.md:78)
|
| By way of exception to L-4.2, where the Availability Percentage is less
| than 95.00%, the maximum aggregate Service Credit in respect of that Measurement
| Period is 50% of the Monthly Service Charge and the Service Credit is 50%.

```catala
scope ServiceCredit:
  label l4_3_cap exception l4_2 definition credit_cap_percentage
    under condition availability_percentage < 95.00
    consequence equals 50%

  label l4_3_credit exception l4_1 definition credit_percentage
    under condition availability_percentage < 95.00
    consequence equals 50%
```

## L-4.4

| MSA-SCH4 L-4.4 (004-msa-sla-credits.md:82)
|
| By way of exception to L-4.1, no Service Credit is payable in respect of
| a Measurement Period in which the Customer is in arrears of any undisputed
| invoice for more than 30 days.

```catala
scope ServiceCredit:
  assertion arrears_days >= 0

  label l4_4 exception l4_3_credit definition credit_percentage
    under condition
      (availability_percentage < 99.90)
      and invoice_undisputed
      and (arrears_days > 30)
    consequence equals 0%
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/ServiceCredits.catala_en",
  "scopes": {
    "MonthlyServiceCharge": {
      "input": [
        "charges_for_the_service",
        "one_off_charges",
        "professional_services_charges",
        "pass_through_charges"
      ],
      "output": [
        "monthly_service_charge"
      ],
      "internal": [],
      "context": []
    },
    "ServiceCredit": {
      "input": [
        "availability_percentage",
        "monthly_service_charge",
        "arrears_days",
        "invoice_undisputed"
      ],
      "output": [
        "credit_percentage",
        "credit_cap_percentage",
        "credit_payable_percentage",
        "credit_amount"
      ],
      "internal": [],
      "context": []
    }
  }
}