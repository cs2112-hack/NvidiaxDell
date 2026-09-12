# Review packet: catala

target: {"module": "availability", "path": "catala/modules/availability.catala_en"}

## Source document (authoritative)

### MSA-SCH4 L-2.1 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Measurement Period" means each calendar month.

### MSA-SCH4 L-2.2 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Available" means the Service responds to a well-formed request at the
Service's documented endpoint within the applicable latency threshold.

### MSA-SCH4 L-2.6 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Scheduled Maintenance" means maintenance carried out within a window
notified to the Customer not less than 5 Business Days in advance, provided that
Scheduled Maintenance must not exceed 8 hours in aggregate in any Measurement
Period.

### MSA-SCH4 L-2.5 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Excluded Minutes" means minutes during which the Service is not
Available by reason of:

  (a) Scheduled Maintenance;
  (b) an Emergency Maintenance event of which the Customer was given at least 60
      minutes' notice;
  (c) a Force Majeure Event;
  (d) the Customer's own act or omission, or the failure of any Customer
      system or network; or
  (e) suspension of the Service in accordance with clause 9 of the Agreement.

### MSA-SCH4 L-4.5 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-4 Service Credits

Where Scheduled Maintenance exceeds 8 hours in aggregate in a
Measurement Period, the excess minutes are not Excluded Minutes and count as
Unavailable Minutes.

### MSA-SCH4 L-2.4 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Unavailable Minutes" means each whole minute during which the Service
is not Available, excluding Excluded Minutes.

### MSA-SCH4 L-2.3 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-2 Definitions

"Availability Percentage" means, in respect of a Measurement Period,
the total number of minutes in the Measurement Period less Unavailable Minutes,
divided by the total number of minutes in the Measurement Period, expressed as a
percentage and rounded to two decimal places.

### MSA-SCH4 L-3.1 — Master Services Agreement — Schedule 4: Service Levels and Service Credits (v2.1, effective 2025-01-15)
Section L-3 Service Level

The Supplier will ensure that the Availability Percentage in each
Measurement Period is not less than 99.90%.


## Artefact under review

```
# MSA Schedule 4 — Service Levels (availability measurement)

> Module Availability

## Prologue — declarations

```catala-metadata
declaration structure Outage:
  data minutes content integer
  data is_maintenance content boolean
  data notified_at content date
  data window_start content date
  data is_emergency_maintenance content boolean
  data emergency_notice_minutes content integer
  data force_majeure_event_certified content boolean
  data outage_attributable_to_customer content boolean
  data is_suspension_under_clause_9 content boolean

declaration scope MeasurementPeriod:
  input period_start content date
  output period_end content date
  output total_minutes content integer

declaration scope RequestAvailable:
  input is_well_formed content boolean
  input responded_at_documented_endpoint content boolean
  input responded_within_latency_threshold content boolean
  output is_available content boolean

declaration scope ScheduledMaintenanceWindow:
  input is_maintenance content boolean
  input notified_at content date
  input window_start content date
  input business_day_calendar content list of date
  output business_days_notice content integer
  output is_scheduled_maintenance content boolean

declaration scope ExcludedMinutes:
  input minutes content integer
  input is_scheduled_maintenance content boolean
  input is_emergency_maintenance content boolean
  input emergency_notice_minutes content integer
  input force_majeure_event_certified content boolean
  input outage_attributable_to_customer content boolean
  input is_suspension_under_clause_9 content boolean
  output limb_a_scheduled_maintenance content boolean
  output limb_b_emergency_maintenance content boolean
  output limb_c_force_majeure content boolean
  output limb_d_customer_act content boolean
  output limb_e_suspension content boolean
  output is_excluded content boolean
  output excluded_minutes content integer

declaration scope ScheduledMaintenance:
  input outages content list of Outage
  input business_day_calendar content list of date
  output aggregate_minutes content integer
  output cap_minutes content integer
  output excess_minutes content integer
  output excluded_minutes content integer

declaration scope AvailabilityPercentage:
  input total_minutes content integer
  input minutes_not_available content integer
  input excluded_minutes content integer
  input scheduled_maintenance_excess_minutes content integer
  output excluded_minutes_before_l4_5 content integer
  output unavailable_minutes content integer
  output availability_percentage_unrounded content decimal
  output availability_percentage content decimal
  output meets_service_level content boolean

declaration scope ServiceAvailability:
  input period_start content date
  input outages content list of Outage
  input business_day_calendar content list of date
  output total_minutes content integer
  output minutes_not_available content integer
  output excluded_minutes_before_l4_5 content integer
  output excluded_minutes content integer
  output scheduled_maintenance_aggregate_minutes content integer
  output scheduled_maintenance_excess_minutes content integer
  output unavailable_minutes content integer
  output availability_percentage content decimal
  output meets_service_level content boolean
```

## L-2.1 Measurement Period

| MSA-SCH4 L-2.1 (004-msa-sla-credits.md:26)
|
| "Measurement Period" means each calendar month.

```catala
scope MeasurementPeriod:
  definition period_end equals Date.last_day_of_month of period_start

  definition total_minutes equals
    (Date.get_day of (Date.last_day_of_month of period_start)) * 1440
```

## L-2.2 Available

| MSA-SCH4 L-2.2 (004-msa-sla-credits.md:28)
|
| "Available" means the Service responds to a well-formed request at the
| Service's documented endpoint within the applicable latency threshold.

```catala
scope RequestAvailable:
  definition is_available equals
    is_well_formed
    and responded_at_documented_endpoint
    and responded_within_latency_threshold
```

## L-2.6 Scheduled Maintenance — the notice limb

| MSA-SCH4 L-2.6 (004-msa-sla-credits.md:50)
|
| "Scheduled Maintenance" means maintenance carried out within a window
| notified to the Customer not less than 5 Business Days in advance, provided that
| Scheduled Maintenance must not exceed 8 hours in aggregate in any Measurement
| Period.

```catala
scope ScheduledMaintenanceWindow:
  definition business_days_notice equals
    number of (list of d among business_day_calendar such that
      ((d > notified_at) and (d < window_start)))

  definition is_scheduled_maintenance equals
    is_maintenance and (business_days_notice >= 5)
```

## L-2.5 Excluded Minutes

| MSA-SCH4 L-2.5 (004-msa-sla-credits.md:39)
|
| "Excluded Minutes" means minutes during which the Service is not
| Available by reason of:
|
|   (a) Scheduled Maintenance;
|   (b) an Emergency Maintenance event of which the Customer was given at least 60
|       minutes' notice;
|   (c) a Force Majeure Event;
|   (d) the Customer's own act or omission, or the failure of any Customer
|       system or network; or
|   (e) suspension of the Service in accordance with clause 9 of the Agreement.

```catala
scope ExcludedMinutes:
  definition limb_a_scheduled_maintenance equals is_scheduled_maintenance

  definition limb_b_emergency_maintenance equals
    is_emergency_maintenance and (emergency_notice_minutes >= 60)

  definition limb_c_force_majeure equals force_majeure_event_certified

  definition limb_d_customer_act equals outage_attributable_to_customer

  definition limb_e_suspension equals is_suspension_under_clause_9

  definition is_excluded equals
    limb_a_scheduled_maintenance
    or limb_b_emergency_maintenance
    or limb_c_force_majeure
    or limb_d_customer_act
    or limb_e_suspension

  definition excluded_minutes equals if is_excluded then minutes else 0
```

## L-2.6 Scheduled Maintenance — the 8-hour aggregate proviso

| MSA-SCH4 L-2.6 (004-msa-sla-credits.md:50)
|
| "Scheduled Maintenance" means maintenance carried out within a window
| notified to the Customer not less than 5 Business Days in advance, provided that
| Scheduled Maintenance must not exceed 8 hours in aggregate in any Measurement
| Period.

```catala
scope ScheduledMaintenance:
  definition cap_minutes equals 8 * 60

  definition aggregate_minutes equals
    Integer.sum of (map each o among outages to
      (if (output of ScheduledMaintenanceWindow with {
             -- is_maintenance: o.is_maintenance
             -- notified_at: o.notified_at
             -- window_start: o.window_start
             -- business_day_calendar: business_day_calendar
           }).is_scheduled_maintenance
       then o.minutes else 0))
```

## L-4.5 Scheduled Maintenance overrun

| MSA-SCH4 L-4.5 (004-msa-sla-credits.md:86)
|
| Where Scheduled Maintenance exceeds 8 hours in aggregate in a
| Measurement Period, the excess minutes are not Excluded Minutes and count as
| Unavailable Minutes.

```catala
scope ScheduledMaintenance:
  label l4_5_no_excess definition excess_minutes equals 0

  label l4_5_excess exception l4_5_no_excess definition excess_minutes
    under condition aggregate_minutes > cap_minutes
    consequence equals aggregate_minutes - cap_minutes

  definition excluded_minutes equals aggregate_minutes - excess_minutes
```

## L-2.4 Unavailable Minutes

| MSA-SCH4 L-2.4 (004-msa-sla-credits.md:36)
|
| "Unavailable Minutes" means each whole minute during which the Service
| is not Available, excluding Excluded Minutes.

| MSA-SCH4 L-4.5 (004-msa-sla-credits.md:86)
|
| Where Scheduled Maintenance exceeds 8 hours in aggregate in a
| Measurement Period, the excess minutes are not Excluded Minutes and count as
| Unavailable Minutes.

```catala
scope AvailabilityPercentage:
  definition excluded_minutes_before_l4_5 equals
    excluded_minutes + scheduled_maintenance_excess_minutes

  label l2_4 definition unavailable_minutes equals
    minutes_not_available - excluded_minutes_before_l4_5

  label l4_5 exception l2_4 definition unavailable_minutes
    under condition scheduled_maintenance_excess_minutes > 0
    consequence equals
      minutes_not_available - excluded_minutes_before_l4_5
      + scheduled_maintenance_excess_minutes
```

## L-2.3 Availability Percentage

| MSA-SCH4 L-2.3 (004-msa-sla-credits.md:31)
|
| "Availability Percentage" means, in respect of a Measurement Period,
| the total number of minutes in the Measurement Period less Unavailable Minutes,
| divided by the total number of minutes in the Measurement Period, expressed as a
| percentage and rounded to two decimal places.

```catala
scope AvailabilityPercentage:
  definition availability_percentage_unrounded equals
    ((total_minutes - unavailable_minutes) / total_minutes) * 100.0

  definition availability_percentage equals
    Decimal.round_to_decimal of availability_percentage_unrounded, 2
```

## L-3.1 The Service Level

| MSA-SCH4 L-3.1 (004-msa-sla-credits.md:61)
|
| The Supplier will ensure that the Availability Percentage in each
| Measurement Period is not less than 99.90%.

```catala
scope AvailabilityPercentage:
  definition meets_service_level equals availability_percentage >= 99.90
```

## Period aggregation

| NO-CLAUSE: Schedule 4 states no aggregation rule of its own. This scope only
| wires the clause-level scopes above together in the order the Schedule
| requires — L-2.1 for the denominator, L-2.5 per outage for the numerator's
| deduction, L-2.6 and L-4.5 for the maintenance overrun, then L-2.4 and L-2.3
| — so that a caller cannot get the L-4.5 feedback edge wrong. All the legal
| content is in the clause blocks above.

```catala
scope ServiceAvailability:
  definition total_minutes equals
    (output of MeasurementPeriod with { -- period_start: period_start }).total_minutes

  definition minutes_not_available equals
    Integer.sum of (map each o among outages to o.minutes)

  definition scheduled_maintenance_aggregate_minutes equals
    (output of ScheduledMaintenance with {
       -- outages: outages
       -- business_day_calendar: business_day_calendar
     }).aggregate_minutes

  definition scheduled_maintenance_excess_minutes equals
    (output of ScheduledMaintenance with {
       -- outages: outages
       -- business_day_calendar: business_day_calendar
     }).excess_minutes

  definition excluded_minutes_before_l4_5 equals
    Integer.sum of (map each o among outages to
      (output of ExcludedMinutes with {
         -- minutes: o.minutes
         -- is_scheduled_maintenance:
             (output of ScheduledMaintenanceWindow with {
                -- is_maintenance: o.is_maintenance
                -- notified_at: o.notified_at
                -- window_start: o.window_start
                -- business_day_calendar: business_day_calendar
              }).is_scheduled_maintenance
         -- is_emergency_maintenance: o.is_emergency_maintenance
         -- emergency_notice_minutes: o.emergency_notice_minutes
         -- force_majeure_event_certified: o.force_majeure_event_certified
         -- outage_attributable_to_customer: o.outage_attributable_to_customer
         -- is_suspension_under_clause_9: o.is_suspension_under_clause_9
       }).excluded_minutes)

  definition excluded_minutes equals
    excluded_minutes_before_l4_5 - scheduled_maintenance_excess_minutes

  definition unavailable_minutes equals
    (output of AvailabilityPercentage with {
       -- total_minutes: total_minutes
       -- minutes_not_available: minutes_not_available
       -- excluded_minutes: excluded_minutes
       -- scheduled_maintenance_excess_minutes: scheduled_maintenance_excess_minutes
     }).unavailable_minutes

  definition availability_percentage equals
    (output of AvailabilityPercentage with {
       -- total_minutes: total_minutes
       -- minutes_not_available: minutes_not_available
       -- excluded_minutes: excluded_minutes
       -- scheduled_maintenance_excess_minutes: scheduled_maintenance_excess_minutes
     }).availability_percentage

  definition meets_service_level equals
    (output of AvailabilityPercentage with {
       -- total_minutes: total_minutes
       -- minutes_not_available: minutes_not_available
       -- excluded_minutes: excluded_minutes
       -- scheduled_maintenance_excess_minutes: scheduled_maintenance_excess_minutes
     }).meets_service_level
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/availability.catala_en",
  "scopes": {
    "MeasurementPeriod": {
      "input": [
        "period_start"
      ],
      "output": [
        "period_end",
        "total_minutes"
      ],
      "internal": [],
      "context": []
    },
    "RequestAvailable": {
      "input": [
        "is_well_formed",
        "responded_at_documented_endpoint",
        "responded_within_latency_threshold"
      ],
      "output": [
        "is_available"
      ],
      "internal": [],
      "context": []
    },
    "ScheduledMaintenanceWindow": {
      "input": [
        "is_maintenance",
        "notified_at",
        "window_start",
        "business_day_calendar"
      ],
      "output": [
        "business_days_notice",
        "is_scheduled_maintenance"
      ],
      "internal": [],
      "context": []
    },
    "ExcludedMinutes": {
      "input": [
        "minutes",
        "is_scheduled_maintenance",
        "is_emergency_maintenance",
        "emergency_notice_minutes",
        "force_majeure_event_certified",
        "outage_attributable_to_customer",
        "is_suspension_under_clause_9"
      ],
      "output": [
        "limb_a_scheduled_maintenance",
        "limb_b_emergency_maintenance",
        "limb_c_force_majeure",
        "limb_d_customer_act",
        "limb_e_suspension",
        "is_excluded",
        "excluded_minutes"
      ],
      "internal": [],
      "context": []
    },
    "ScheduledMaintenance": {
      "input": [
        "outages",
        "business_day_calendar"
      ],
      "output": [
        "aggregate_minutes",
        "cap_minutes",
        "excess_minutes",
        "excluded_minutes"
      ],
      "internal": [],
      "context": []
    },
    "AvailabilityPercentage": {
      "input": [
        "total_minutes",
        "minutes_not_available",
        "excluded_minutes",
        "scheduled_maintenance_excess_minutes"
      ],
      "output": [
        "excluded_minutes_before_l4_5",
        "unavailable_minutes",
        "availability_percentage_unrounded",
        "availability_percentage",
        "meets_service_level"
      ],
      "internal": [],
      "context": []
    },
    "ServiceAvailability": {
      "input": [
        "period_start",
        "outages",
        "business_day_calendar"
      ],
      "output": [
        "total_minutes",
        "minutes_not_available",
        "excluded_minutes_before_l4_5",
        "excluded_minutes",
        "scheduled_maintenance_aggregate_minutes",
        "scheduled_maintenance_excess_minutes",
        "unavailable_minutes",
        "availability_percentage",
        "meets_service_level"
      ],
      "internal": [],
      "context": []
    }
  }
}