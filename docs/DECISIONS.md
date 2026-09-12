# Design decisions, including where this deviates from the brief

## D-1. "Diff the two ASTs" — the capability does not exist in Catala 1.2.1

**The brief asks for:** draft Catala → render to English → re-encode with a
fresh agent → **diff the two ASTs** → loop until they converge.

**The problem:** Catala 1.2.1 has no structured serialisation of any of its
ASTs. Verified by reading every command implementation in `compiler/driver.ml`
at tag `1.2.1` and grepping the tree for `json`/`sexp`/`serial`. The
`scopelang`, `dcalc`, `lcalc` and `scalc` commands are documented as printing
a "debugging verbatim" and call a pretty-printer producing Catala-ish concrete
syntax. There is no `--emit-ast=json`.

Diffing the pretty-printed text directly would be the obvious move and it is
the wrong one: it compares *surface form*, so it reports a difference for a
renamed local, a reordered-but-equivalent definition, or different whitespace —
none of which mean the two encodings disagree about the law. A roundtrip that
"fails to converge" because an agent chose the variable name `rate` instead of
`tax_rate` tells us nothing, and a loop driven by that signal never terminates.

**What is implemented instead** — three comparisons, ordered by how directly
they bear on legal meaning, all three required to converge:

1. **Exception-tree equivalence** (`catala exceptions -s S -v V -F json`).
   This *is* structured JSON, and it is the legally load-bearing structure: it
   says which definition is an exception to which, under what condition, at
   what depth. Compared after canonicalisation (normalise generated labels,
   sort sibling nodes by condition text, drop absolute file paths and line
   numbers). Two encodings that agree here agree about the exception hierarchy,
   which is the thing the brief actually cares about.
2. **Behavioural equivalence** over a generated input battery. Inputs are
   derived from the scope's own JSON Schema (`catala json-schema`) and driven
   to the boundaries of every condition appearing in the exception tree —
   which is exactly where two encodings that differ will differ. Executed via
   `catala interpret -F json`.
3. **Normalised structural diff** of the `scopelang` dump, alpha-renaming
   locals and sorting commutative groupings. This is the weakest signal and is
   reported but not gating, because its false-positive rate is the reason (1)
   and (2) exist.

**Consequence for the acceptance criterion:** "the drafting roundtrip converges
on the first pass" is measured as (1) and (2) both converging on the first
attempt, with (3) reported. This is a faithful reading of the intent — the two
encodings mean the same thing — and it is strictly stronger than a textual AST
diff would have been, since (2) tests meaning rather than form.

This is the one place the implementation does not do what the brief literally
says, because what the brief literally says is not available. Flagged for
approval.

## D-2. Triage is three-way, not two-way

The brief says "classify each clause as rule-like or not". Implemented as
`RULE` / `PROSE` / `HYBRID`. Rationale in `src/lks/triage.py`'s module
docstring: the clauses that matter most carry deterministic arithmetic gated on
a non-computable predicate, and a two-way split forces either inventing law or
discarding verifiable arithmetic. `HYBRID` sends the clause to Catala with the
judgement lifted to an explicit scope input, and to the vector store with that
input named.

This is an extension of the brief, not a departure from it: a `HYBRID` clause
does go to Catala *and* the vector store, which is what a "rule-like clause
that does not fully reduce to a rule" requires.

## D-3. The corpus is synthetic, and that is a stated limitation

There was no corpus on this machine. Six documents were authored to exercise
the clause shapes that break naive encodings (greater-of, notwithstanding-
precedence, cumulative-vs-substitutive premiums, caps applied after
accelerators, inclusive/exclusive date boundaries, pro-rating with a specified
rounding direction, cross-document defined-term borrowing).

What this validates: the machinery, end to end, against text with the right
adversarial structure.

What it does not validate: behaviour on the company's real documents, their
formatting, their scanning artefacts, or their genuine internal
inconsistencies. Real documents arrive through `lks.ingest`, which runs the
same triage and refuses to merge on conflict — so the swap is a normal
ingestion, not a rebuild.

## D-4. Static embeddings rather than a transformer encoder

The brief requires a git checkout of any commit to give the matching index.
Static embeddings (model2vec, a token→vector lookup plus pooled average) are
bit-identical across machines and runs; a transformer encoder is not, because
of nondeterministic kernels and library drift. Recall is somewhat lower. For a
legal system where an index must be provably the index for the text at that
commit, reproducibility wins, and the store refuses to serve a mismatched
index rather than degrading silently.

The model weights (30MB) are committed so a checkout is self-contained with no
network and no model-registry drift.

## D-5. The vector store does not index RULE clauses

Indexing them would let the chat layer retrieve and quote a rule as prose,
which is precisely the silent blending the brief forbids. A rule question that
finds no vector hit is the correct outcome: it must be routed to execution, or
answered "no rule module covers this".

## D-6. MongoDB is the query engine; the committed files remain the source of truth

**Requested:** use a local MongoDB for the vector store.

**The tension:** the brief also requires that a git checkout of any commit
gives you the matching index. A mongod data directory cannot be git-versioned
in any useful way — it is a binary, mutable, machine-local blob.

**Resolution — two layers, one of them authoritative:**

| Layer | Role | Versioned? |
|---|---|---|
| `vectorstore/index/{chunks.jsonl,embeddings.npy,manifest.json}` | canonical, deterministic | yes, committed |
| MongoDB `lks.chunks` | materialised, queryable view | no — rebuilt by `scripts/sync_mongo.py` |

Every Mongo document is stamped with the `corpus_aggregate` hash of the
manifest it was loaded from, and `MongoVectorStore.verify()` requires three-way
agreement between the live corpus, the committed manifest, and what Mongo
actually holds. Checking out an older commit and querying therefore *fails
loudly* and names which pair diverged, rather than silently answering from a
newer index. In a legal system that silent case is the one that matters: an
answer about text the commit does not contain, delivered with citations that
look valid.

**Vector search:** `mongodb/mongodb-atlas-local` bundles `mongot`, so
`$vectorSearch` runs locally. Plain MongoDB Community has no `mongot` and no
`$vectorSearch`, so the store detects its absence and falls back to exact
brute-force cosine over the stored vectors. At this corpus size the fallback is
exhaustive and therefore strictly *more* accurate than an approximate ANN
index — only slower. `status()` always reports which path is live, so the
difference is never invisible.

Embeddings remain the static model2vec vectors of D-4: moving the storage layer
does not change what is embedded or make it non-deterministic.

### D-6 addendum — verified behaviour

Running against `mongodb/mongodb-atlas-local`, `mongot` is present and
`$vectorSearch` is the live path (`status()` reports which). The Mongo-backed
store and the file-backed store return **identical rankings** on every test
query, which is what you would expect given both read the same committed,
deterministic vectors — and is worth asserting, because a divergence would mean
the materialised view had drifted from the artefact it was built from.

All three staleness paths are verified to refuse rather than degrade:

| Change | Result |
|---|---|
| a corpus clause edited (i.e. a different commit checked out) | refused, naming the manifest and corpus hashes |
| a triage label changed with the corpus untouched | refused, naming the triage hashes |
| Mongo holding fewer chunks than its own metadata claims | refused |

The second case is the one that matters most and the one a corpus-hash-only
design misses entirely: relabelling a clause changes *what may be quoted at
all*, without changing a byte of the text.

## D-7. "The attack landed" is an execution outcome, never a judgement

**The exposure engine's brief asks for:** an agent fleet that searches for
exposures, and a formal executor that "only lets through the attacks that
actually land", making the false positive rate "structurally zero rather than
empirically low".

**The problem:** "landed" has to be defined before it can be enforced, and the
obvious definitions all smuggle a judgement back in. "The model is confident"
is a judgement. "Two clauses look inconsistent" is a judgement. "The number
seems wrong" is a judgement about what the number should be, which is the whole
question.

**What is implemented** — six outcome classes, five of which the Catala
interpreter reaches on its own:

`CONFLICT` (the compiler refuses to choose between two applicable
definitions), `SILENCE` (no definition applies), `REFUSAL` (an assertion
rejected the facts), `CONTRADICTION` (two scopes asserted to answer one
question, executed, disagree), `DIVERGENCE` (the corpus executed over a
decision actually made produces something else), and `ADVERSE` (a declared
predicate fires on the executed outputs).

Only `ADVERSE` requires legal content beyond the corpus, and that content is
authored by a person in `exposure/predicates.yaml`, must cite, and is never
shown to the fleet — a role told what counts as a bad outcome would reproduce
it and a survivor would mean nothing.

**What this buys and what it costs.** A finding is emitted only when the
interpreter reached a bad-outcome state on facts the declared domain admits.
Both halves are checkable by a reader, and `scripts/check.sh` gate 9
re-adjudicates the whole queue on every run. The cost is entirely in recall:
the engine is blind wherever `exposure/domains.yaml` is wrong or absent, which
is presently 33 of 45 scopes. `lks exposure doctor` names them rather than
letting the coverage look complete. That is the direction a queue a lawyer
works must fail in.

## D-8. Realisability is declared, because the documents cannot state it

A policy is silent, conflicted or refusing across most of its arithmetic space,
and nearly all of that is correct: there is no 700th hour of a payroll week, so
nothing needs to say what it pays. An engine that reported those would bury the
few places where a policy is silent about somebody who is really there.

The documents say what follows from a fact. They never say which facts occur.
So `exposure/domains.yaml` declares the realisable range of every mapped input,
and every narrowing carries a `note` saying what makes it true — because a
narrowing *suppresses findings*, and an unexplained one is indistinguishable
from a convenience that happens to make the queue shorter. `validate_domains`
fails a narrowing with no note.

Per-input ranges turned out to be insufficient. A claim submitted on 15 January
is realisable and an expense incurred on 31 December is realisable, and the
pair is impossible; without a cross-input constraint the grid manufactured six
regions of "the policy refuses to answer" that were the map's own fault. So
domains also carry `coherence` — one relation between two terms, no disjunction
and no arithmetic. A constraint elaborate enough to need those is usually not a
fact about the world but an argument about the policy, and an argument belongs
in a predicate where it is visible and costed, not in the grid where it
silently deletes cases.

## D-9. The counterfactual edits the real module, and must

"The diff of everything else that edit changes" is computed by applying the
edit, rebuilding, re-executing every mapped scope and every counterexample, and
restoring the file.

The obvious alternative — copy the module to a temporary directory and evaluate
it there — does not work and fails in the worst available way. Catala resolves
a module's dependencies through compiled objects under `_build`, keyed by the
module's path *in the source tree*. A module evaluated from elsewhere either
cannot find its dependencies, or finds the objects built from the **unedited**
source and reports that the edit changed nothing. A counterfactual that
silently answers "nothing moved" is worse than one that fails.

So `lks.remedy.edited` writes to the real file, restores in a `finally`,
verifies the restoration by content hash, and raises `RestoreFailed` rather
than returning a diff if the tree is not as it was.
`tests/test_exposure.py` asserts restoration both for a good edit and for one
that does not compile.

Discovered while building this: `run_scope_traced` pointed `--bin` at the
standard library's own object directory, which resolves only modules that
depend on nothing. Every module with a `> Using` failed to trace, so "which
provision governed this answer" worked for 8 of 19 modules and errored for the
rest, including everything in the expenses policy. Fixed in
`lks.catala_runner`.

## D-10. The local model's roles do not reason first, and this was measured

`lks.agents.REVIEWER` was configured with `think=True` and JSON output and had
never produced a single finding. The cause is not the prompt:

| configuration | result on qwen3.6:27b, this Ollama build |
|---|---|
| `think=True`, `format: json` | empty completion, `done_reason: stop`, 0 tokens |
| `think=True`, no JSON mode | 6,144-token budget consumed by deliberation, no answer |
| `think=False`, `format: json` | a well-formed object in ~50s |

Both failures were reproduced directly against the model, once through
`llm.generate` and once through `agents.propose_attack` itself. Until the
runtime stops swallowing the answer, `REVIEWER` and every fleet archetype run
with `think=False` and reason in their output rather than before it.

The fleet's budget is 1,600 tokens rather than 3,072 for a related reason: in
JSON mode this model spends whatever it is given, and at 3,072 it wrote
2,000-token narratives and took three minutes a round. The object a round
returns is a fact vector, a paragraph and a list of citations. A tighter budget
quadruples what an overnight run covers, and coverage is what a fleet is for.

Neither choice affects what reaches a human. The harness re-executes every
proposal regardless of what the model was thinking.

## D-11. A generated document is issued as a PDF only when execution and three reviewers let it through

**Requested:** generate legal documents from natural-language requests: compare
against similar documents in the vector store, draft, run the draft through
Catala until it passes, have three agents find holes and reach a consensus, and
output a PDF only when both the Catala check and the agents pass.

**Resolution:** `lks generate` (`src/lks/generate.py`). The full record of the
design is `docs/GENERATION.md`, decisions G-1 to G-8; the load-bearing ones:

- **The PDF is the deliverable and a Catala module is the evidence.** Every
  draft gets an encoding, and gate G4 requires its quotations to match the
  document on the corpus's own content hash, so the logic that was verified is
  the logic that is printed.
- **"Passes Catala" is a ladder, calibrated against the 19 committed modules
  before being trusted.** Typecheck; totality over a boundary battery; no
  *provably* dead branch; quotation fidelity; and, once at the end, convergence
  with an independent re-encoding of the prose. The calibration run changed
  three rules — assertions are domain statements, the admission floor is a
  fraction, and a branch conditioned on a derived value is reported rather than
  blocked — because each original rule failed correct committed modules.
- **The three agents never vote on a fact.** A finding that execution confirms
  blocks on its own; a judgement blocks only when a different reviewer raised
  one on the same clause; a lone judgement becomes an open question printed in
  the PDF. That is the same split the corpus already draws between its fixed
  counterexamples and its recorded ambiguities.
- **Nothing is issued on failure,** and a draft never enters `catala/modules`:
  `catala_runner` gained out-of-tree execution through `catala interpret` with
  `-I` flags, leaving in-tree execution byte-identical, so an unproven module is
  never seen by the registry, fidelity or coverage checks.

Two shared modules changed as a result and are recorded here because other
features depend on them: `lks.draft.generate_battery` now enumerates
payload-free enumerations instead of refusing them, and `lks.catala_runner`
resolves out-of-tree targets as above.

Measured against the corpus by `scripts/eval_generate.py` — planted-defect kill
rates (V1) and leave-one-out regeneration (V2) — in `eval/GENERATION-RESULTS.md`.
