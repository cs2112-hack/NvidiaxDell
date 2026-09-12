---
doc_id: TPL-007
title: Bonterms Standard SLA and PSA — field map (digest)
kind: template_digest
license: >
  Bonterms standard agreements are released under CC BY 4.0 (unless otherwise noted).
  The download centre is JavaScript-driven, so the automated fetch found no direct
  asset links (see tools/fetch_templates.py, bonterms_download_links.txt). Download the
  PDFs by hand from https://bonterms.com/download-center/ if the demo needs the full
  text; attribution required.
source: https://bonterms.com/download-center/
retrieved: 2026-09-12
verbatim: false
scopes: [ServiceCredit_Reference]
---

# Bonterms — field map

Bonterms publishes neutral standard forms (NDA, Cloud Terms, Professional Services
Agreement, DPA, SLA) drafted by a standing committee of 120+ lawyers, with a Cover Page
of negotiated variables in front of fixed standard terms — the same "variables in front,
fixed terms behind" shape as Common Paper.

## Bonterms Standard SLA v1.0 — the variables a Catala module needs

| Cover Page variable | Type | Feeds |
|---|---|---|
| Uptime Commitment (%) | decimal | `ServiceCredit.commitment` |
| Service Credit table (uptime band → % of fee) | tier list | `ServiceCredit.credit_percentage` |
| Credit cap per month (% of monthly fee) | decimal | ceiling on `ServiceCredit.credit` |
| Credit request window (days after month end) | duration | eligibility gate |
| Support response targets by severity | duration per tier | out of scope for this demo |
| Measurement window (calendar month) | date range | selects the governing period |

The Bonterms SLA states that Service Credits constitute liquidated damages and are not
a penalty, and are the customer's exclusive remedy for a missed uptime commitment —
the same exclusive-remedy structure used in SYN-002 § A.4.3.

## Why it is in this corpus

It is the license-clean stand-in for the AWS/Azure/GCP SLA prose (see
../real/cloud_sla_credit_tiers_facts.md): the vendor tier *numbers* are facts and safe
to encode, but their prose is not redistributable. Anything shown outside the company
should quote Bonterms or oneSaaS, not the vendor pages.
