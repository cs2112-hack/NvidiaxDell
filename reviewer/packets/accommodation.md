# Review packet: catala

target: {"module": "accommodation", "path": "catala/modules/accommodation.catala_en"}

## Source document (authoritative)

### EXP-POL E-4.1 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-4 Accommodation

Accommodation is reimbursable at actual cost up to £220 per night in a
Tier 1 city, £160 per night in a Tier 2 city and £120 per night in a Tier 3
city.

### EXP-POL E-4.2 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-4 Accommodation

By way of exception to E-4.1, where the Employee provides evidence that
no compliant accommodation was available within the applicable cap at the time
of booking, the actual cost is reimbursable up to 1.4 times the applicable cap.

### EXP-POL E-4.3 — Expense Reimbursement Policy (v7.0, effective 2025-04-01)
Section E-4 Accommodation

The caps in E-4.1 are inclusive of local taxes and of any mandatory
resort or city charge, and exclusive of the cost of parking.


## Artefact under review

```
#

> Module Accommodation

> Using ExpenseDefs

##

```catala-metadata
declaration scope NightlyAccommodation:
  input city_tier content ExpenseDefs.CityTier
  input room_cost content money
  input local_taxes content money
  input mandatory_resort_or_city_charge content money
  input parking_cost content money
  input no_compliant_accommodation_available_evidenced content boolean
  internal base_cap content money
  output applicable_cap content money
  output cost_against_cap content money
  output accommodation_reimbursable content money
  output parking_reimbursable content money
  output reimbursable_amount content money
```

## E-4.1

| EXP-POL E-4.1 (002-expense-reimbursement-policy.md:69)
|
| Accommodation is reimbursable at actual cost up to £220 per night in a
| Tier 1 city, £160 per night in a Tier 2 city and £120 per night in a Tier 3
| city.

```catala
declaration tier_night_cap content money
  depends on tier content ExpenseDefs.CityTier
  equals
    match tier with pattern
    -- Tier1: $220.00
    -- Tier2: $160.00
    -- Tier3: $120.00

scope NightlyAccommodation:
  assertion room_cost >= $0.00

  definition base_cap equals tier_night_cap of city_tier

  label e4_1 definition applicable_cap equals base_cap

  definition accommodation_reimbursable equals
    Money.min of cost_against_cap, applicable_cap
```

## E-4.2

| EXP-POL E-4.2 (002-expense-reimbursement-policy.md:73)
|
| By way of exception to E-4.1, where the Employee provides evidence that
| no compliant accommodation was available within the applicable cap at the time
| of booking, the actual cost is reimbursable up to 1.4 times the applicable cap.

```catala
scope NightlyAccommodation:
  label e4_2 exception e4_1 definition applicable_cap
    under condition no_compliant_accommodation_available_evidenced
    consequence equals base_cap * 1.4
```

## E-4.3

| EXP-POL E-4.3 (002-expense-reimbursement-policy.md:77)
|
| The caps in E-4.1 are inclusive of local taxes and of any mandatory
| resort or city charge, and exclusive of the cost of parking.

```catala
scope NightlyAccommodation:
  assertion local_taxes >= $0.00

  assertion mandatory_resort_or_city_charge >= $0.00

  assertion parking_cost >= $0.00

  definition cost_against_cap equals
    room_cost + local_taxes + mandatory_resort_or_city_charge

  definition parking_reimbursable equals parking_cost

  definition reimbursable_amount equals
    accommodation_reimbursable + parking_reimbursable
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/accommodation.catala_en",
  "scopes": {
    "NightlyAccommodation": {
      "input": [
        "city_tier",
        "room_cost",
        "local_taxes",
        "mandatory_resort_or_city_charge",
        "parking_cost",
        "no_compliant_accommodation_available_evidenced"
      ],
      "output": [
        "applicable_cap",
        "cost_against_cap",
        "accommodation_reimbursable",
        "parking_reimbursable",
        "reimbursable_amount"
      ],
      "internal": [
        "base_cap"
      ],
      "context": []
    }
  }
}