# The corpus

44 documents for an internal legal knowledge system that compiles rule-like clauses to
Catala and keeps everything else in a local vector store. Hybrid by design: real public
documents for credibility and messiness, CC-licensed templates for clean structure, and
an authored set where the conflicts are planted on purpose.

```
corpus/
  manifest.csv          doc_id -> path, licence, rule-dense?, modules, conflicts     (generated)
  SCOPES.md             the scope contract every Catala module must satisfy          (hand-written)
  raw/
    synthetic/   15 authored documents  -- the controlled half; all conflicts live here
    real/         9 statutes and digests -- CA/WA/CO/FLSA verbatim + 3 fact digests
    real/edgar/   5 EX-10 exhibits       -- real contracts from SEC full-text search
    templates/    8 standard forms       -- Common Paper, oneNDA, oneSaaS, YC SAFE, Bonterms
    discovery/    7 litigation PDFs      -- FTC v. Facebook, Six4Three internal emails,
                                            multistate AG complaint, Kadrey v. Meta (+ .txt)
  pdf/           21 typeset PDFs of the authored documents and digests               (generated)
  gold/          fact_patterns.jsonl (64), qa_pairs.jsonl (20), conflicts.jsonl (12) (generated)
  vector_store/  chunks.jsonl — 1,291 non-rule clauses with module pointers
  catala/        12 modules, 96 tests, clerk test green (see catala/README.md)
  tools/         fetchers, renderer, gold builder, manifest builder, self-check
```

## Regenerating

```bash
python3 tools/fetch_real.py        # public statutes            (idempotent; CO returns 403)
python3 tools/fetch_edgar.py       # EX-10 exhibits from EDGAR  (10 req/s cap, UA required)
python3 tools/fetch_templates.py   # CC BY / CC BY-ND templates
python3 tools/fetch_discovery.py   # litigation PDFs + extracted text
python3 tools/build_gold.py        # gold set (expected values are computed, not typed)
python3 tools/build_manifest.py    # manifest.csv
python3 tools/render_pdfs.py       # pdf/ via pandoc + headless Chrome
python3 tools/check_corpus.py      # must exit 0
```

`check_corpus.py` is the one test that matters here: it fails if a gold record points at
a doc_id that does not exist, if a module in the gold set is not declared in `SCOPES.md`,
if front matter drifts from the manifest, if a module with carve-outs has fewer than two
branches covered, or if a `resolve` conflict has no precedence clause.

## The conflict map

Every conflict is between two documents that define the **same variable in the same
agreement's scope**. Ingestion must block the first two, resolve the next six by encoding
an `exception`, and stay silent on the last four.

| | Documents | Variable | Values | Resolution |
|---|---|---|---|---|
| **block** | SYN-003 / SYN-004 | ordinary liability cap | $720,000 vs $500,000 | none — SYN-004 § 3.2 puts the Amendment and the Order Form at equal rank |
| **block** | SYN-009 / SYN-010 | Globex credit % | 10/25 vs 20/40 | none — SYN-010 § 4.1 states no precedence was agreed |
| resolve | SYN-001 / SYN-003 | liability cap | 1× vs 300% | MSA § 14.3 — Order Form controls |
| resolve | SYN-002 / SYN-003 | credit % | 10/25 vs 15/30 | MSA § 14.3, date-scoped from 2026-07-01 |
| resolve | SYN-005 / REAL-001 | sick accrual (CA) | 1:40 vs 1:30 | handbook § 4.6 — statutory floor |
| resolve | SYN-005 / REAL-002 | sick accrual + cap (CO) | 1:40, 40h vs 1:30, 48h | handbook § 4.6 |
| resolve | SYN-005 / REAL-003 | final paycheck date (CA) | next payday vs immediately | handbook § 9.5 |
| resolve | SYN-006 / SYN-007 | approval level at $6,000 | VP vs Director | handbook § 7.1 — more restrictive controls |
| no conflict | SYN-008 / SYN-003 | liability cap | 150% vs 300% | different agreement and counterparty |
| no conflict | SYN-001 / SYN-011 | confidentiality survival | 3 vs 5 years | different agreements |
| no conflict | SYN-005 / REAL-006 | sick accrual (WA) | 1:40 vs 1:40 | same variable, same value |
| no conflict | SYN-013 / SYN-001 | cap tied to fees | SOW fees vs 1× fees | different agreement, opposite roles |

Three of the resolvable conflicts are employer-policy-below-statutory-floor, which is the
realistic compliance bug: headquarters policy applied to employees in a stricter state.

The four calibration pairs exist to measure false positives. A detector that keys on
"two documents mention a liability cap" fires on all four and is wrong four times.

## Rule-shaped vs not

18 modules are declared in `SCOPES.md`, covering liability caps, SLA credits, sick-leave
and PTO accrual, expense approval, final-paycheck dates, consulting fee ceilings,
confidentiality duration, breach notice, bonus targets, FLSA overtime, and tuition
reimbursement. Each produces a number, a date, a money amount, or a boolean from typed
inputs.

Everything else is vector-store material and is marked as such in the manifest
(`rule_dense = no|partial`): governing law and venue, force majeure, warranty
disclaimers, the definition of Confidential Information, narrative security obligations,
all of SYN-015 (Code of Conduct — deliberately zero rule-shaped clauses), all of
`raw/discovery/`, and the two 79k-character EDGAR exhibits kept purely as retrieval
messiness anchors.

Deliberate calibration documents: **SYN-008** (same variables, different agreement),
**SYN-015** (no rules at all), **REAL-006** (identical value to the handbook), and
**EDGAR-004/005** (real, long, and not worth encoding).

## Licence hygiene

`manifest.csv` carries a per-document licence column; nothing is in the corpus without
one. The four rules that shaped what got collected:

1. **Statutes are public domain** under the government-edicts doctrine (*Georgia v.
   Public.Resource.Org*, 590 U.S. 255 (2020)), so CA/WA/US Code text is stored verbatim.
2. **Vendor SLA prose is not redistributable, but the numbers are facts.** REAL-008
   restates AWS/Azure/GCP thresholds and credit percentages and reproduces none of their
   prose. Anything shown outside the company should quote Bonterms or oneSaaS instead.
3. **CC BY 4.0 templates are stored verbatim with attribution** (Common Paper, oneNDA,
   oneSaaS). **The YC SAFE is CC BY-ND**: stored as-is, never modified, and deliberately
   *not* encoded into Catala even though its conversion arithmetic is the most
   Catala-shaped text in the corpus.
4. **Discovery PDFs are public court records.** The underlying internal Facebook/Meta
   material is third-party; it is here as retrieval and demo material, and each file has
   a `.source.txt` sidecar with its origin.

## Known gaps

- **REAL-002 (Colorado) is a paraphrased digest, not verbatim text** — every automated
  source for C.R.S. § 8-13.3-403 returned HTTP 403. The numbers (1:30, 48-hour cap) are
  the operative facts and are marked `verify_before_external_use: true`.
- **TPL-007 (Bonterms) is a field map, not the agreement** — the download centre is
  JavaScript-driven, so no direct asset link was found. Download by hand if needed.
- **oneSLA is not published** at the documented URL (404); oneSaaS stands in.
- **No local PDF text extraction on this box** (no poppler, no pypdf). The discovery PDFs
  ship with DocumentCloud's extracted `.txt` alongside; `ftc_v_facebook…pdf` has no text
  sidecar, so ingestion needs a PDF parser to reach it.
- **Statute currency:** fetched 2026-09-12. Sick-leave law moves fast (California went to
  40 hours in 2024, AB 406 expanded permissible uses from 2025-10-01) and many local
  ordinances exceed state law. Internal consistency matters more than currency for the
  demo, but cite the retrieval date.
- **44 documents is above the 25–40 target.** The encodable core is 26 (15 synthetic +
  9 real + 2 EDGAR); the other 18 are retrieval-only. If ingestion plus compile runs
  long, drop DISC-004/005/006/007 and EDGAR-004/005 first — nothing in the gold set
  except QA-018 depends on them.

## What the corpus found once it was executable

Two corrections came out of encoding the documents rather than from re-reading them, and
both are now enforced by tests:

- **CONF-008** — the ingestion pre-check flagged Cal. Lab. Code § 202 against handbook
  § 9.3 (the 72-hour rule for quitting without notice). The hand-written conflict set had
  only the § 201 discharge branch. Added, with fact pattern FP-046.
- **FP-054** — the gold set claimed a 2026-07-05 invoice was out of time under SOW § 5.3.
  April ends on 2026-04-30, so the deadline is 2026-07-29. The pattern now uses 2026-08-15.

This is the point of writing the gold values as expressions in `tools/build_gold.py` and
mirroring them into `clerk test` assertions: the compiler gets a vote on the ground truth.
