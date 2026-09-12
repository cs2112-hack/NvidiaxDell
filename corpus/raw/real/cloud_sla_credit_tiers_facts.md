---
doc_id: REAL-008
title: Public cloud SLA uptime thresholds and service credit percentages (facts table)
kind: real_digest
license: >
  Uptime thresholds and credit percentages are uncopyrightable facts and are restated
  here. The vendors' SLA prose is copyrighted under their site terms and is NOT
  reproduced. For anything shown externally, use the CC BY 4.0 Bonterms SLA / oneSLA
  tiers instead (see ../templates/).
sources:
  - https://aws.amazon.com/ec2-sla/
  - https://aws.amazon.com/s3/sla/
  - https://aws.amazon.com/eks/sla/
  - https://www.microsoft.com/licensing/docs/view/Service-Level-Agreements-SLA-for-Online-Services
  - https://cloud.google.com/compute/sla
retrieved: 2026-09-12
verbatim: false
scopes: [ServiceCredit_Reference]
---

# Cloud SLA threshold → credit tables (facts only, paraphrased)

## AWS EC2 — region level (commitment: 99.99% Monthly Uptime Percentage)

| Monthly Uptime Percentage | Service credit |
|---|---|
| < 99.99% and ≥ 99.0% | 10% |
| < 99.0% and ≥ 95.0% | 30% |
| < 95.0% | 100% |

AWS EC2 — instance level: same credit tiers against a 99.5% commitment. AWS also does
not charge for a single EC2 instance that is unavailable for more than six minutes of
a clock hour.

## AWS S3 / RDS / Lambda / ECS-Fargate (commitment: 99.9%)

| Monthly Uptime Percentage | Service credit |
|---|---|
| < 99.9% and ≥ 99.0% | 10% |
| < 99.0% and ≥ 95.0% | 25% |
| < 95.0% | 100% |

**Note the 30% vs 25% middle tier.** This is a real, citable inconsistency between
services of the same vendor, and is the source pattern behind the synthetic
service-credit conflict (SYN-009 / SYN-010).

## AWS EKS control plane

Commitment 99.95% (standard) / 99.99% (provisioned); tiered credits of the same shape.

## Azure — Virtual Machines (commitment: 99.99% for two or more instances across
availability zones; 99.9% single instance with premium storage)

| Monthly Uptime Percentage | Service credit |
|---|---|
| < 99.99% | 10% |
| < 99.0% | 25% |
| < 95.0% | 100% |

## Google Cloud — Compute Engine (commitment: 99.99% for instances in multiple zones)

| Monthly Uptime Percentage | Service credit |
|---|---|
| ≥ 99.0% and < 99.99% | 10% |
| ≥ 95.0% and < 99.0% | 25% |
| < 95.0% | 100% |

## Shape of the rule (what the Catala module encodes)

`uptime (decimal) × monthly_fee (money) → credit_percentage (decimal), credit (money)`,
a descending chain of conditional definitions with a 100%-of-monthly-fee ceiling and a
30-day claim window. Identical structure across every vendor; only the numbers move.
