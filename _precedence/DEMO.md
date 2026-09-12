# Demo — runs end to end on the box

Nine steps, about six minutes. Every number below was produced by the commands as written.

## 0. Setup

```bash
eval "$(opam env --switch=catala)"      # catala/clerk 1.2.1 live in an opam switch
catala --version                        # 1.2.1
echo "$LOCAL_LLM_URL"                   # unset here -> the tools fall back to --offline
```

`$LOCAL_LLM_URL` is the only inference endpoint the runtime is allowed to use. It is unset
on this box, so `ingest.py` and `chat.py` print one warning and run their deterministic
paths: keyword clause classification, keyword+IDF retrieval, regex input extraction. Set it
and the same commands route classification, embedding, and input extraction to the local
Nemotron instead — nothing else changes.

## 1. The corpus checks itself

```bash
python3 corpus/tools/check_corpus.py
```

```
44 documents, 18 modules, 65 fact patterns, 20 Q/A pairs, 14 conflict records

0 errors, 0 warnings
```

Fails on a gold record that points at a missing document, a module no scope contract
declares, front matter that drifted from the manifest, a module with carve-outs whose
exception branches are untested, or a "resolvable" conflict with no precedence clause.

## 2. Typecheck is clean and every scope has tests

```bash
cd corpus/catala && clerk test && cd ../..
```

```
                     FAILED     PASSED      TOTAL      RATIO
   files                  0         12         12      100 %
   tests                  0         96         96      100 %
```

96 tests over 12 files: 10 modules plus the two modules whose expected result *is* a
conflict error.

## 3. A computed answer, with the clause and the branch

```bash
python3 pipeline/chat.py ask \
  "What is our liability cap if Northwind brings a confidentiality claim in September 2026?" \
  --given annual_fees=240000 fees_paid_last_12mo=240000
```

```
[catala] LiabilityCap
  cap = $1,200,000.00
  uncapped = false

  cap decided by: SYN-003 § 5.2 — the Order Form's supercap for Excluded Claims
    “Notwithstanding Section 9.3 of the MSA, each party's aggregate liability for Excluded
     Claims shall not exceed five hundred percent (500%) of the Annual Fees stated above.”

  exception chain for cap (* = the branch that fired):
      msa_9_2  (base definition)
        SYN-001 § 9.2 — the base cap
        carve_out  when uncapped_claim
        * of_5_2  when order_form_applies && excluded_claim
        of_5_1  when order_form_applies && (not excluded_claim) && not uncapped_claim
        msa_9_3  when excluded_claim && not order_form_applies
```

The value comes from `clerk run --input=<json>`, the branch from `catala interpret --trace`,
and the tree from `clerk exceptions --output-format=json`. No model is in this path at all:
the clause text is read out of the literate source that sits above the code.

## 4. Missing facts are elicited, never guessed

```bash
python3 pipeline/chat.py ask "What's our liability cap on the Northwind contract?"
```

```
[catala] LiabilityCap — cannot answer yet, 4 input(s) missing

  needed: fees_paid_last_12mo          (money)
  needed: annual_fees                  (money)
  needed: claim_type                   (Legal_types.ClaimType)  one of: Ordinary,
          Confidentiality, IpIndemnity, GrossNegligence, UnpaidFees, BodilyInjury
  needed: claim_date                   (date)

  (signature of scope LiabilityCap, from `clerk json-schema`)
```

The list is the compiler's own JSON schema for the scope, so it cannot drift from the code.
The date matters because the Order Form only governs claims from 2026-07-01.

## 5. A conflicting document is blocked at ingest

```bash
python3 pipeline/ingest.py add corpus/raw/synthetic/globex_sla_addendum_2026.md; echo "exit $?"
```

```
re-scanning 43 documents to check SYN-010 against the corpus

BLOCKED: SYN-010 conflicts with no stated precedence

  module ServiceCredit_AcmeGlobex
  --- SYN-009 2.1 (2. Service Credits) ---
      | Less than 99.9% but equal to or greater than 99.0% | 10% | …
  --- SYN-010 2.1 (2. Enhanced Service Credits) ---
      | Less than 99.9% but equal to or greater than 99.0% | 20% | …

exit 1
```

Both clauses are surfaced and the merge fails. The gate also runs the full fact-pattern
suite, so a document that quietly changes an existing answer is blocked the same way.

## 6. The same conflict, from the engine that has to execute it

```bash
python3 pipeline/chat.py ask "Does Globex get a service credit for a 99.4% month in July 2026?" \
  --given monthly_service_fee=30000
```

```
[catala] GlobexServiceCredit — BLOCKED: conflicting definitions, no answer

│  During evaluation: conflict between multiple valid consequences for
│  assigning the same variable.
├─➤ src/conflict_service_credit_acme_globex.catala_en:39.24-27:
│ 39 │     consequence equals 10%
├─ SYN-009 § 2.1 — Service Level Exhibit tier table
├─➤ src/conflict_service_credit_acme_globex.catala_en:59.24-27:
│ 59 │     consequence equals 20%
└─ SYN-010 § 2.1 — Service Level Addendum tier table

  Two clauses define this variable and neither is an exception to the other.
  The documents state no order of precedence, so there is no single answer to give.
```

Catala's own error carries both source positions *and* both clause headings, because the
literate source keeps the clause text above the code. Compare
`corpus/catala/src/liability_cap_acme_northwind.catala_en`, where the same collision is
resolved because MSA § 14.3 states an order of precedence — that module answers in step 3.

## 7. A quotation answer, labelled as such

```bash
python3 pipeline/chat.py ask "What counts as Confidential Information under the Vertex NDA, and what doesn't?"
```

```
[vector] SYN-011 2.1 — Acme/Vertex Mutual NDA
         (2. Confidential Information)
         “2.1 "Confidential Information" means non-public information disclosed by one
          party to the other, in any form, that is marked confidential or that a reasonable
          person would understand to be confidential in the circumstances of disclosure.”
…
  no computation was performed: these clauses state standards, not rules
```

Questions that ask what a document *says* never reach the Catala engine, and a computed
answer that has qualifying prose prints it under its own `[vector]` label — the two engines
are never merged into one paragraph.

## 8. The adversarial counter: two things the corpus had wrong

Both were found by the machinery, not by re-reading the documents.

```bash
python3 pipeline/ingest.py verify | tail -3
```

```
LABEL CONF-010 SYN-001/SYN-004: predicted block, gold says resolve

pre-check vs gold: tp=10 fp=0 fn=0 precision=1.00 recall=1.00  block/resolve label correct on 9/10
```

1. **A conflict the hand-written gold set missed.** The pre-check flagged
   REAL-004 (Cal. Lab. Code § 202) against SYN-005 § 9.3 — the 72-hour rule for an employee
   who quits without notice, a second branch of the same statutory-floor pattern. It is now
   `CONF-008`, and the corresponding branch is a passing Catala test (FP-046).
2. **A wrong date in the gold set.** FP-054 asserted that an invoice dated 2026-07-05 was
   out of time under SOW § 5.3. Encoding the clause showed April ends on 2026-04-30, so the
   deadline is 2026-07-29 and the invoice was in time. The fact pattern now uses 2026-08-15,
   and the corrected expectation is enforced by `clerk test`.

The one remaining label mismatch is recorded in the gold set with its reason: SYN-004's
"equal rank" disclaimer is scoped to Order Forms, which a keyword pre-check cannot see, so
it conservatively says *block* where the correct answer is *resolve*. Catala's own check is
the authority, and it resolves once the exception is declared.

## 9. Scoreboard

```bash
python3 pipeline/chat.py gold | tail -3
python3 pipeline/ingest.py verify | tail -2
cd corpus/catala && clerk test | tail -4
```

| Measure | Result |
|---|---|
| Catala tests (`clerk test`) | **96/96**, 12 files, typecheck clean |
| Modules encoded | 10 + 2 expected-conflict modules |
| Gold Q/A pairs | **18 pass, 1 fail, 1 skipped** of 20 |
| Conflict detection vs gold | precision **1.00**, recall **1.00** (10 positives, 4 calibration negatives) |
| Conflict block/resolve labels | 9/10 (one documented conservative mislabel) |
| Vector store | 1,291 chunks from 43 documents, 150+ with a module pointer |
| Offline input extraction | 18/36 values without the local model |
| Counterexamples found and fixed | 2 (CONF-008, FP-054) |

The failing pair, QA-015, asks "if a labor strike stops Acme from delivering, are we
excused?" and wants SYN-001 § 15.1 (force majeure). Keyword retrieval cannot bridge
strike → labor dispute → force majeure; this is the one gold pair that needs the local
model's embeddings, and it is left failing rather than hand-tuned away. The skipped pair,
QA-003, is a follow-up question ("and if the same claim is for breach of confidentiality?")
that needs conversation state the chat component does not keep.
