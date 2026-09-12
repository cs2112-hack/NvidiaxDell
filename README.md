# Ross

**Your contracts already contain the answers as arithmetic. We compile them so they can be run.**

Built at the Dell x NVIDIA Hackathon — *Local AI on Dell Pro Max with GB10* — Cornell eHub, September 12, 2026.


Ingests the company's legal documents, compiles the deterministic parts into an
executable [Catala](https://catala-lang.org) database, keeps the
non-deterministic parts in a local vector store, answers questions over both,
drafts new documents — and runs an agent fleet that continuously tries to sue
the company with them.

---

## The problem

A company's MSA caps liability at the fees paid in the trailing twelve months. An order form signed eight months later caps it at 3× annual fees. Both are in force. Nobody knows.

Every legal AI tool on the market treats this as a retrieval problem: find the clause, summarize it, hand it to a human. Retrieval cannot detect that contradiction, because it returns text *near* your question rather than the consequence *of* your documents. Ask about the cap and you get a fluent, confident, possibly wrong number.

## The insight

Contracts contain two kinds of sentences.

| Arithmetic in a suit | Judgment |
|---|---|
| Liability caps, PTO accrual rates, SLA credit tiers, notice periods, approval thresholds, payment terms | "Good faith", "material breach", "commercially reasonable efforts", governing law, recitals |
| → compiled to **Catala**, executed | → **local vector store**, quoted with citations |

Ross splits them. The rule-like clauses become typed, executable Catala scopes. Everything else is retrieved and cited. Answers are always labeled with which engine produced them, and the two are never silently blended.

Catala is the DSL used to compile French tax and social-benefit law into verified code. An MSA is not harder than the French tax code.

## What it does

- **Computes, doesn't retrieve.** Ask "what's our exposure on an ordinary breach where the customer paid $200k?" and the answer is the output of an executed scope, rendered with the clause text that produced it and the exception branch that fired. Change the facts, a different clause fires.
- **Knows what it doesn't know.** If a question is missing an input, the scope's type signature says exactly which one. No guessing.
- **Blocks conflicting documents at ingestion.** New documents are compiled and run against the accumulated fact-pattern suite. When two documents define the same value and neither is an exception to the other, Catala raises a conflict error naming both source locations. The merge is refused until a human declares the precedence.
- **Verifies its own drafts.** Generated Catala is rendered to English, re-encoded by a fresh agent that never saw the original, and the ASTs are diffed. Non-convergence is reported as an ambiguous draft, with the diff.
- **Attacks itself.** An adversarial reviewer agent sees the source clause and the artifact but never the implementer's reasoning. Its only job is to produce a fact pattern where the two disagree. Every counterexample it finds becomes a permanent test case.

## Architecture

```
corpus/ ──► triage ──┬──► Catala modules ──► typecheck ──► clerk test ──► executed
   (rule-like?)      │         ▲                                   │
                     │         └──── adversarial reviewer ◄────────┘
                     │
                     └──► vector store ──► retrieval + citation
                                   │
              query ──► router ──► [compute | cite] ──► answer + derivation + engine label
```

Every answer carries provenance: the value, the clause text, the branch taken, and which engine produced it.

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
./scripts/start.sh          # brings up everything, prints the URL
```

Then open **http://127.0.0.1:8765**. Stop with `./scripts/stop.sh`.

`start.sh` stages the Catala standard library if needed, starts MongoDB (and
falls back to the committed file index if Docker is unavailable), rebuilds the
vector index if it does not match the corpus, starts Ollama and checks the
model is present, builds the rule registry, and serves the interface. It says
what it did and what it could not do, line by line; `--no-model` skips the
local model, and everything except the model-backed features still works.

### The five views

| View | What it is for |
|---|---|
| **Ask** | a question; answers are labelled with the engine that produced them |
| **Rules** | pick a rule, fill in the facts, execute, and see which provision governed |
| **Clause** | any clause verbatim, how it was triaged, which rule encodes it |
| **Intake** | documents awaiting a decision, and recording how a conflict is resolved |
| **Exposure** | the corpus as one decision map, and what it will cost |
| **Verification** | the gate, every counterexample, and defects found in the documents |

In **Rules**, "Fill from a description" turns a sentence into the facts the
rule needs. Nothing is executed from that — the fields are filled in, marked so
you can see which you did not type, and anything the description did not state
is left for you rather than guessed.

### From the command line

```bash
. scripts/env.sh
$PY scripts/lks ask "what service credit do we owe at 98.5% uptime?"
$PY scripts/lks scopes --grep Credit  # what can answer this, and what it needs
$PY scripts/lks run ServiceCredits.ServiceCredit \
     '{"availability_percentage":98.50,"monthly_service_charge":10000.0,
       "arrears_days":0,"invoice_undisputed":true}'
$PY scripts/lks why Overtime.HourPremium multiplier   # the exception hierarchy
$PY scripts/lks cite EMP-ANNEX-C C-7.2                # a clause, verbatim
$PY scripts/lks convert path/to/contract.docx         # a real document
$PY scripts/lks check                                 # the full gate

$PY scripts/lks exposure operations --record          # what we did vs what we wrote
$PY scripts/lks exposure map Overtime.HourPremium     # the decision surface
$PY scripts/lks exposure fleet Overtime.HourPremium --rounds 40
$PY scripts/lks exposure queue                        # findings, worst first
$PY scripts/lks exposure show EXP-0003                # one finding, with its proof

$PY scripts/lks watch status                          # what the contradiction watch has seen
$PY scripts/lks watch results                         # the latest checks, with findings
$PY scripts/lks watch check EMP-ANNEX-C               # attack one document now
$PY scripts/lks watch run                             # the watch without the web server

$PY scripts/lks generate "an on-call allowance policy: £40 a shift, £80 on a public holiday"
$PY scripts/lks pdf EMP-ANNEX-C --out annex-c.pdf     # typeset a corpus document
```

### What is verified, and what is not

`$PY scripts/lks check` runs ten gates; the current state is 19 modules
typechecking, 504 in-source assertions, 31/31 counterexamples, 96/96 rule
clauses encoded. `docs/AGENT-RESULTS.md` gives the measured limits of the local
model — triage 88.8%, and HYBRID recall of 64%, which is the number to be
careful about. The adversarial review loop is built but not yet measured.

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
exposure/domains.yaml   which facts describe a person who actually exists
exposure/predicates.yaml what an opponent will say our documents mean
exposure/queue/         exposures found, each reproducible by re-execution
exposure/holdings/      rulings, formalised as constraints, awaiting a human
operations/decisions.jsonl what the company actually decided, to execute against
docs/EXPOSURE.md        the exposure engine, and the claims it makes
docs/GENERATION.md      document generation: the pipeline, its gates, and why
generated/<slug>/       one workspace per generation run (gitignored): PDF, report, evidence
eval/GENERATION-RESULTS.md generation measured against the corpus
```

The Precedence tree from `main` lives unchanged under `_precedence/`, so every
path in its own docs and tools is relative to that folder. The leading `_` is
load-bearing: clerk scans the whole project from the root and skips only `_` and
`.` directories, and the patched compiler's stdlib and `corpus/catala` would
otherwise collide with `catala/modules`.

```
_precedence/corpus/     public EDGAR contracts, CC-licensed templates, synthetic documents,
                        manifest, gold set, and its own clerk project in corpus/catala/
_precedence/pipeline/   ingest.py, chat.py, llm.py (run from _precedence/)
_precedence/catala/     the patched Catala compiler source (see PATCHES.md, ../catala-fixes.patch)
_precedence/DEMO.md, WRITEUP.md, CATALA_CHEATSHEET.md
```

## Verification

Run `$PY scripts/lks check`. Ten gates, and any one failing fails the run:

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
8. **exception-branch coverage** — branches per scope variable; a variable
   whose exception tree cannot be read fails
9. **exposure engine** — declared domains and predicates validate, and every
   exposure on the queue still reproduces when its scope is re-executed on its
   own recorded facts
10. **registry** — `catala/registry.yaml` is exactly what the modules declare
    today, so the router never answers from outputs that no longer exist

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
Before anything runs, the claim must also be executable as stated: the rule's
exact inputs with values of the right types, and an expected answer naming
results the rule produces. A misspelt input used to reach Catala, fail to
parse, and be recorded as a break.

Every attempt is reported in plain English, and an accepted break is written
up for a non-specialist in `reviewer/reports/<id>.md`: the situation, what the
document requires, what the system gives, and what happens next.

Confirmed breaks become permanent tests in `tests/counterexamples/` and are
re-run forever. They store the fact pattern and the clauses in tension, not
just the failing inputs, so a future maintainer can fix the encoding rather
than tune the number until the test passes.

## The adversarial legal exposure engine

Everything above answers *what do our documents say*. This answers *what will
they cost*. Full account in `docs/EXPOSURE.md`.

**Agents propose, the formal layer disposes.** A fleet of agents takes
adversarial roles — plaintiff's employment lawyer, customer's counsel,
regulator, auditor, departing contractor — and searches the corpus for the fact
pattern that extracts the most. Each proposal goes to the executor. It survives
only if executing the scope reaches a bad outcome under the company's own
encoded policy, on facts a declared file says describe somebody who exists.
Everything else dies without a human seeing it.

Six ways to land, five of which the Catala interpreter reaches on its own: the
documents **conflict** and establish no priority; they are **silent** about a
case that arises; the policy **refuses** to answer about a real person; two
scopes with authority over one question **contradict** each other; what the
company actually **diverged** from what its policy computes; or a declared,
cited exposure predicate fires on the executed outputs. Only the last needs
legal content beyond the corpus, it is written by a person, and the fleet is
never shown it — a role told what counts as a bad outcome would simply
reproduce it.

So the false positive rate is structurally zero rather than empirically low,
and the cost is entirely in recall: the engine is blind wherever the declared
domain is missing, which is 33 of 45 scopes today. `lks exposure doctor` names
them rather than letting the coverage look complete.

### The map

The corpus composes into one decision function, and the **Exposure** view draws
its equivalence classes. Regions are outcome classes, borders are the clauses
that separate them, and area is how many cases fall in each — real decisions
where the operations feed has any. Grey is where the corpus is silent, red is
where documents contradict each other, a single-case region is almost always an
accident.

The grid is not a sweep. `catala exceptions` reports the condition attached to
every definition, and those conditions *are* the borders: `ordinal > 40` is not
a plausible place to probe, it is the exact point at which C-4.1 begins to
govern. So every border is attributable to a clause by construction, and two
cells share a region because the interpreter took the same definitions for
them, which the trace reports.

Move a border in the interface and the map redraws — by applying the edit to
the real module, rebuilding, re-executing every mapped scope and every recorded
counterexample, and putting the module back. The counterfactual is measured,
not predicted, which is why it takes a moment.

### The watchers

The corpus is static, reality is not.

* **Our own operations.** `operations/decisions.jsonl` carries decisions the
  company actually made. The corpus is executed over each one and every
  divergence is a finding — with a date, a subject and two numbers. This is the
  one to run first: it needs no theory of liability, and the seeded feed shows
  payroll paying Grade 5+ overtime that the company's own reading of C-5.1 says
  is not payable.
* **Rulings.** A judgment is formalised as a constraint over the decision
  function and lands in `exposure/holdings/proposed/`, never live. The output is
  not "this may be relevant" — it is which regions just became indefensible,
  which clauses produce them, and how many decisions on record fall inside.
* **Inbound paper.** Conflicts between somebody else's document and ours,
  detected by `lks.ingest`, with a redline and a fallback for each.
* **New documents in MongoDB** (`lks.doc_watch`). Runs inside the web server
  `start.sh` brings up, around the clock. Every `LKS_WATCH_INTERVAL` seconds
  (default 30) it scans `lks.chunks` and the intake collection `lks.documents`,
  where anything may insert `{"text": ..., "doc_id"?: ..., "title"?: ...}`.
  A document is new when its key is unseen and changed when its clause hash
  moves, so the re-sync on every start wakes nothing. What is already there on
  the first scan is the baseline and is not attacked.
  Each new clause is scored against every clause of every other document, then
  put to two blind attackers on the local model with its nearest rivals. A
  finding survives only if both quotes are verbatim in clauses of two different
  documents. It is `corroborated` when both attackers raised the same pair.
  A check where an attacker gave no usable reply is `incomplete` and retried,
  never reported clean. Results go to `lks.contradiction_checks`, and each check
  runs as a server job, so it takes its turn on the model. `LKS_DOC_WATCH=0`
  turns the watch off.

## Document generation

`lks generate "<request>"` drafts a legal document from a plain-language request
and issues it as a PDF only if its executable encoding passes the Catala gate
and three independent reviewers fail to agree on a defect. Otherwise nothing is
issued, and `report.md` says which gate stopped it and on what input.

```
retrieve ─► draft ─► encode + G1–G4 ◄─┐ ─► screen ─► G5 roundtrip ─► PDF
                     (repair loop)    └── redraft from confirmed findings
```

- **Retrieve** — nearest clauses from the vector store, then the documents they
  came from and the Catala modules that encode them, so rule precedent is
  reached without indexing RULE clauses.
- **Catala gate** — G1 typecheck; G2 totality at every threshold and date
  boundary; G3 no provably dead branch; G4 every quotation in the encoding
  matches the document; G5, once, an independent re-encoding of the prose alone
  must converge with it.
- **Screen** — a logic reviewer whose findings are re-executed, a language
  reviewer and a consistency reviewer, run blind. A confirmed finding blocks on
  its own; a judgement blocks only when two reviewers raise it on the same
  clause; a lone judgement is printed in the PDF as an open question.
- **Output** — `generated/<slug>/document.pdf` with a provenance appendix, plus
  the document, its encoding and every model reply. A generated document never
  enters the corpus; that stays `lks ingest` / `lks merge`.

The gates were calibrated against the 19 committed modules before being
trusted, and three rules changed because they failed correct ones. G1, G3 and
G4 now pass 19/19. G2 fails 10: nine only because a scope takes a list or
structure input that no battery can be generated for, and one, availability, on
a real division by zero at `total_minutes = 0`. Design and decisions:
`docs/GENERATION.md` and D-11. Tests: `tests/test_generate.py` (no model).
Measurements: `scripts/eval_generate.py` → `eval/GENERATION-RESULTS.md`.

## Ingestion

```bash
$PY scripts/lks ingest path/to/new-document.md --candidate path/to/Module.catala_en
$PY scripts/lks proposals
$PY scripts/lks merge <proposal-id>     # refused while conflicts are unresolved
```

### Real documents

`ingest` accepts `.md`, `.txt`, `.docx`, `.pdf` and `.html`. A file already in
the house convention is handled exactly as before. Anything else is
**converted first, visibly**: `lks.extract` reads the format (a `.docx` with
`zipfile`, a `.pdf` with `pdftotext` or `pypdf` — whichever is installed),
`lks.structure` infers the document's own numbering scheme, and the result is
written to `ingest/converted/<doc-id>.md` in the house convention, with
`ingest/converted/<doc-id>.report.md` beside it. Everything downstream then
runs unchanged.

```bash
$PY scripts/lks convert --formats            # what this machine can read
$PY scripts/lks convert contract.pdf         # convert and inspect, merging nothing
$PY scripts/lks convert policy.docx --effective-date 2026-01-01
```

The report is the point. It accounts for **every** paragraph of the input,
states per clause whether the id came from the document or was **synthesised**
(a synthesised id is positional and cannot be cited), and lists every
paragraph that could not be confidently assigned, verbatim. A `doc_id`, title
or effective date the document does not state is never invented: the front
matter carries `NEEDS-HUMAN-INPUT`, ingestion raises a blocking conflict, and
`merge` refuses on that sentinel whatever resolutions are recorded — an
invented effective date would silently date a rule.

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

**D-7 to D-10** cover the exposure engine: what "the attack landed" is allowed
to mean, why realisability has to be declared, why the counterfactual edits the
real tree, and the measured reason the local model's roles do not reason first
— which also explains why the adversarial reviewer had never produced a
finding until now.

**D-3**: the corpus is synthetic. It was authored to exercise the clause shapes
that break naive encodings. It validates the machinery, not behaviour on the
company's real documents; those arrive through `lks ingest` like any other.

## Stack

Declared per hackathon rule 06.

| Layer | Choice |
|---|---|
| Hardware | Dell Pro Max with GB10 (all inference on-box) |
| Runtime agent | OpenClaw |
| Sandbox | OpenShell — compiled Catala and generated code execute under policy |
| Inference | Nemotron (local, served via vLLM) — **no remote LLM/API calls in the runtime path** |
| Rule engine | [Catala](https://catala-lang.org) 1.0.0~alpha + `clerk` |
| Vector store | file-backed, committed alongside the Catala source so any checkout has a matching index |
| Corpus | public EDGAR EX-10 contracts, CC BY 4.0 standard templates (Common Paper, Bonterms, oneNDA), public-domain state statutes, and synthetic documents authored for conflict coverage |

Open-source dependencies only. No third-party content that isn't cleared for use.

## Demo

1. **Compute.** Same question, two different claim types, two different clauses fire — with the derivation shown.
2. **Elicit.** A question missing inputs returns exactly which inputs are missing, from the type signature.
3. **Conflict.** A new order form is dropped into the ingest folder; the merge is refused, both clauses shown side by side. Add the order-of-precedence rule, re-ingest, merge succeeds.
4. **Adversary.** The counter: attacks attempted, disagreements found, all of them now permanent tests.

## Limitations

Honest ones, because they shape what's next.

- Conflict detection only covers clauses encoded as the same variable in the same scope. Semantic contradictions between two clauses sitting in the vector store need a separate NLI layer — not built today.
- Catala is alpha-stage. Toolchain and codegen paths are still stabilizing; the compiler version is pinned.
- The triage step uses a local model, so mis-encoding is possible. Three gates catch it: typecheck, the fact-pattern suite, and the adversarial reviewer. The literate source keeps the original clause directly above the code it encodes, so a lawyer reviews English, not code.
- The drafting roundtrip converges inconsistently on unseen documents. Non-convergence is surfaced rather than hidden.
- Corpus is demo-scale. Nothing here has been validated against a production contract set.

## Who this is for

Not lawyers. The sales rep signing a non-standard order form, the ops manager applying headquarters' PTO policy to an employee in a state with a higher statutory floor, the support lead issuing an SLA credit. None of them will ever get a legal-AI seat. All of them make binding decisions.

Every rule-dense corpus is the same shape: benefits plans, insurance policies, procurement rules, regulatory compliance.

## Team

*Ruben Hayrapetyan, Jason Pitchford, Sheikh Gaye, Nirajan Nair*
