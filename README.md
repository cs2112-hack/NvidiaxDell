# Legal Knowledge System

Ingests the company's legal documents, compiles the deterministic parts into an
executable [Catala](https://catala-lang.org) database, keeps the
non-deterministic parts in a local vector store, answers questions over both,
and drafts new documents.

## The one thing to understand first

**Two engines, and they are never blended.**

| Engine | Answers by | Can it state a figure? |
|---|---|---|
| `CATALA` | **executing** a scope | yes |
| `VECTOR` | **quoting** a clause verbatim, with a citation | no — only quotes |

Every answer is labelled per part with the engine that produced it. A mixed
answer is fine; silent mixing is not. Nothing in this system reads Catala
source to predict what it computes — `lks.catala_runner` exposes execution as
the only affordance, so "answer by executing the scope" is enforced rather than
intended.

A rule question with no inputs supplied is answered by naming the scope and the
inputs it needs, never by guessing. Where a clause turns on a judgement the
documents do not define — "good reason", "material breach", whether a thing is
a trade secret — that judgement is an explicit scope **input** a human must
supply. The module computes the consequence *given* the judgement and never
makes it.

## Quick start

```bash
. scripts/env.sh                      # catala, clerk, $PY on PATH

$PY scripts/lks ask "what service credit do we owe at 98.5% uptime?"
$PY scripts/lks scopes --grep Credit  # what can answer this, and what it needs
$PY scripts/lks run ServiceCredits.ServiceCredit \
     '{"availability_percentage":98.50,"monthly_service_charge":10000.0,
       "arrears_days":0,"invoice_undisputed":true}'
$PY scripts/lks why Overtime.HourPremium multiplier   # the exception hierarchy
$PY scripts/lks cite EMP-ANNEX-C C-7.2                # a clause, verbatim
$PY scripts/lks check                                 # the full gate
```

## Layout

```
corpus/                 the source documents — the only authority
triage/decisions.yaml   per-clause RULE / PROSE / HYBRID, pinned to clause hashes
catala/modules/         literate Catala: each clause quoted above the code encoding it
catala/tests/           #[test] scopes asserting the clauses
catala/registry.yaml    derived: which scope answers what, and what it needs
vectorstore/index/      the committed, deterministic vector index
tests/counterexamples/  every way this system has been wrong, re-run forever
reviewer/               the adversarial protocol, blind packets, and findings
docs/DOCUMENT-DEFECTS.md defects found in the SOURCE TEXT, for a lawyer to read
docs/DECISIONS.md       design decisions, including where this deviates from the brief
ingest/proposals/       incoming documents awaiting human conflict resolution
```

## Verification

Run `$PY scripts/lks check`. Eight gates:

1. `catala typecheck --check-invariants` on every module
2. **literate fidelity** — every quoted clause matches the corpus by content
   hash. A drifted quotation fails the build, because a literate source whose
   prose no longer matches the law is worse than no prose
3. **coverage** — every RULE and HYBRID clause encoded, in exactly one module;
   no PROSE clause encoded
4. `clerk test` — the in-source assertions
5. **counterexample regression** — every defect ever found, re-run
6. **executability** — no scope has an enum-typed output, which Catala 1.2.1
   cannot serialise, and which would make the scope silently unanswerable
7. **vector store** — index pinned to both the corpus text and the triage
   decisions, on files and in MongoDB
8. **exception-branch coverage** — branches per scope variable

### The adversarial loop

An adversarial reviewer sees the source document and the artefact, and **never
the implementer's reasoning**. That is enforced, not requested:
`scripts/make_packet.py` reduces a module to law plus code, stripping every
explanatory paragraph and comment, and the packet is written to a file so you
can check for yourself that no rationale leaked.

The reviewer does not approve anything. Its only output is a discrepancy, with
the expected answer derived from the clause text and the clauses that compel it.
A claimed break is **re-executed** before being accepted, so a reviewer that
mis-executed cannot plant a false test — and a finding whose expected value
equals the observed value is rejected outright, because an expectation
harvested from the code under test cannot detect that the code is wrong.

Confirmed breaks become permanent tests in `tests/counterexamples/` and are
re-run forever. They store the fact pattern and the clauses in tension, not
just the failing inputs, so a future maintainer can fix the encoding rather
than tune the number until the test passes.

## Ingestion

```bash
$PY scripts/lks ingest path/to/new-document.md --candidate path/to/Module.catala_en
$PY scripts/lks proposals
$PY scripts/lks merge <proposal-id>     # refused while conflicts are unresolved
```

A new document becomes a **proposal**, never a merge. `merge` raises unless
every blocking conflict carries an explicit human `resolution` and
`resolved_by`. Six detectors run; five are text heuristics, and the sixth asks
Catala itself — staging the candidate and comparing exception-tree **roots**
before and after, which catches an amendment bolted on outside the existing
hierarchy even though it typechecks and executes fine on most inputs.

## Known deviations from the brief

See `docs/DECISIONS.md`. The substantive one is **D-1**: Catala 1.2.1 has no
structured AST serialisation, so the drafting roundtrip converges on
exception-tree equivalence plus behavioural equivalence over boundary-driven
input batteries, rather than a textual AST diff — which would fire on a renamed
local and never terminate.

**D-3**: the corpus is synthetic. It was authored to exercise the clause shapes
that break naive encodings. It validates the machinery, not behaviour on the
company's real documents; those arrive through `lks ingest` like any other.
