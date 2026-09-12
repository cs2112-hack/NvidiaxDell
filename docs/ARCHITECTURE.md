# Legal Knowledge System — architecture and component contracts

This document is the interface contract between components. It exists so that
components built independently compose without rework.

## The central invariant

**Two engines, never blended.**

| Engine | Answers by | Source of truth | Can it say "the answer is £412.50"? |
|---|---|---|---|
| `CATALA` | **executing** a scope | compiled Catala modules | yes |
| `VECTOR` | **quoting** a clause verbatim, with a citation | committed vector index | no — it may only quote |

Consequences that every component must honour:

1. The chat layer never paraphrases Catala source to answer a rule question.
   If it cannot execute, it says so. Reading the code and describing what it
   probably computes is the failure mode this whole design exists to prevent.
2. The vector engine never computes. It quotes. If a question needs arithmetic
   and no scope covers it, the answer is "no rule module covers this", not a
   best guess from prose.
3. Every answer is labelled per-part with the engine that produced it. A mixed
   answer is legal, silent mixing is not.

## Clause identity

Clause IDs come from the document's own numbering (`C-4.2`), never from
position. A clause carries a content hash (`lks.model.content_hash`,
whitespace-reflow-insensitive, semantically strict) so that a changed clause
invalidates the artefacts derived from it without changing its identity.

Canonical citation form: `DOC-ID CLAUSE-ID (filename:line)`.

## Triage: three labels

| Label | Destination | Rule |
|---|---|---|
| `RULE` | Catala only | reduces to computation with no judgement term |
| `PROSE` | vector store only | no computable content, or judgement-dependent |
| `HYBRID` | **both** | deterministic arithmetic gated on a non-computable predicate |

`HYBRID` is the load-bearing case. The non-computable predicate is lifted to an
explicit Catala scope **input** (a *judgement input*), so the module computes
the consequence *given* the judgement and never fabricates the judgement
itself. The same clause is indexed in the vector store with `judgement_inputs`
naming those inputs, so a reader is told what a human must decide.

Triage decisions live in `triage/decisions.yaml`, pinned to `clause_hash`.
Heuristics propose; `source: adjudicated` rows win and are authoritative.

## Catala source layout

```
catala/modules/<module>.catala_en
```

Each module is **literate**: the verbatim clause text sits directly above the
code that encodes it, in this shape:

```
## <Section heading mirroring the source document>

| EMP-ANNEX-C C-4.1 (001-employment-terms-annex-c.md:55)
|
| An Employee is entitled to an overtime payment in respect of each hour
| worked in excess of 40 hours in a Payroll Week, calculated at 1.25 times
| the Base Hourly Rate.

```catala
<code encoding exactly that clause and nothing else>
```
```

Rules:
- One clause per code block wherever the clause is separable. A code block
  encoding two clauses cannot be attributed to either.
- The `|`-gutter quotation is **verbatim** source text plus a citation line. It is
  machine-checked against the corpus by `lks.literate.check_fidelity`: a drifted
  quotation is a build failure, because a literate source whose prose no longer
  matches the law is worse than no prose at all. The gutter is `|` and not a
  markdown `>` blockquote because Catala reserves a leading `>` for its own
  directives.
- Exceptions in the document become exceptions in the code, at the same depth.
  `C-5.2` says "notwithstanding C-5.1" — so the encoding of C-5.2 must be an
  exception to the encoding of C-5.1, not a merged condition.

## Module registry

`catala/registry.yaml` maps each Catala scope to the clauses it encodes:

```yaml
modules:
  Overtime:
    path: catala/modules/overtime.catala_en
    encodes: ["EMP-ANNEX-C C-4.1", "EMP-ANNEX-C C-4.2", ...]
    scopes:
      OvertimePay:
        inputs:  [hours_worked, base_hourly_rate, grade, ...]
        outputs: [overtime_payment]
        judgement_inputs: []
```

This registry is the join between the two engines: a `PROSE` chunk's
`qualifies` field names modules here, and the chat layer uses it to attach
prose caveats to executed answers.

## Component contracts

### 1. Triage and conversion — `lks.triage`, `lks.literate`
- in: `corpus/*.md`
- out: `triage/decisions.yaml`, `catala/modules/*.catala_en`, `catala/registry.yaml`
- must: every `RULE`/`HYBRID` clause appears in exactly one module's `encodes`;
  every `PROSE`/`HYBRID` clause appears in the vector index.

### 2. Ingestion — `lks.ingest`
- in: a new document path
- out: a proposal under `ingest/proposals/<id>/` — never a direct merge
- must: run the same triage; generate candidate Catala; detect conflicts with
  existing modules; **refuse to merge while any conflict is unresolved**.
  Conflict classes: contradictory value for the same predicate, overlapping
  applicability with no stated precedence, redefinition of a defined term,
  and an exception targeting a clause that no longer exists.

### 3. Vector store — `lks.vector`
- committed under `vectorstore/`; static deterministic embeddings
- must: refuse to serve when the index does not match the corpus in the
  checkout, and localise the mismatch to the clause.

### 4. Chat — `lks.chat`
- must: route per sub-question; execute for `CATALA` parts; quote for `VECTOR`
  parts; label every part; never blend silently; state when no engine covers
  the question rather than guessing.

### 5. Drafting — `lks.draft`
- Catala → English → (fresh agent, no sight of the original) → Catala → AST diff
- loop until the two ASTs converge; report first-pass convergence separately,
  since that is the acceptance criterion.

## Verification

For every component, an adversarial reviewer sees **the source document and
the artefact, never the implementer's reasoning**. It does not approve. Its
only job is to produce a fact pattern where artefact and source disagree.

Every counterexample becomes a permanent test in `tests/counterexamples/` and
is re-run forever. A component is done when the reviewer has failed to break it
across a sustained run over the full corpus, the type checker is clean, every
scope has tests covering its exception branches, and the drafting roundtrip
converges first-pass on unseen documents.
