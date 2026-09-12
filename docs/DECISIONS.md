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
