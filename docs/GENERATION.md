# Document generation

`lks generate "<request>"` turns a plain-language request into a legal document
and issues it as a PDF, but only once the document's executable encoding has
passed the Catala gate and three independent reviewers have failed to agree on
any defect. Otherwise it issues nothing and says exactly why.

```
request
  │
  ▼
1 retrieve   nearest clauses from the vector store, then the full documents
  │          they came from, then the Catala modules that encode those documents
  ▼
2 draft      DRAFTER writes the document in the house convention
  │
  ▼
3 encode     ENCODER writes a literate Catala module            ◄──────┐
  │          G1 typecheck · G2 totality · G3 dead branch · G4 fidelity │
  │          fail → repair the encoding and retry                    │
  │          provably dead branch → the DOCUMENT is redrafted          │
  ▼                                                                  │
4 screen     logic · language · consistency, run blind               │
  │          blocked → redraft from the findings ─────────────────────┘
  ▼
5 roundtrip  G5: a fresh agent re-encodes the prose alone; the two
  │          encodings must agree on structure and behaviour
  ▼
6 issue      document.pdf, with a provenance appendix
```

Code: `src/lks/generate.py` (orchestrator), `gates.py` (G1–G5), `screen.py`
(reviewers and consensus), `render.py` + `pdf.py` (PDF), `mutate.py` (V1
evaluation). Roles are in `src/lks/agents.py`. Tests: `tests/test_generate.py`
(no model needed). Measurements: `scripts/eval_generate.py` →
`eval/GENERATION-RESULTS.md`.

## Using it

```bash
. scripts/env.sh
$PY scripts/lks generate "A remote working allowance policy: £300 one-off, £25 a month, \
  pro rata for part-time staff, repayable if the employee leaves within 12 months."

$PY scripts/lks generate - < request.txt              # a long request from a file
$PY scripts/lks generate "..." --no-roundtrip          # skip G5 (stamped on the PDF)
$PY scripts/lks generate "..." --emit-failed-pdf       # render even on failure, stamped
$PY scripts/lks pdf EMP-ANNEX-C --out annex-c.pdf      # typeset an existing corpus document
```

Exit status is 0 only when a PDF was issued. Expect 20–60 minutes on this host:
the local model serves one request at a time at ~12–24 tok/s
(`docs/AGENT-RESULTS.md`), and a run makes at least six model calls.

Each run gets a workspace under `generated/<slug>/` (gitignored). Nothing is
written anywhere else, and nothing in it is deleted, so `--workspace` must name
a new or empty directory:

| File | What it is |
|---|---|
| `document.md` | the document, in the house convention |
| `<Module>.catala_en` | its encoding, with verbatim quotations |
| `document.pdf` | only if every gate and the screen passed |
| `report.md`, `run.json` | every gate, finding, attempt and timing |
| `context.json` | what retrieval returned, with scores |
| `triage.yaml` | RULE / HYBRID / PROSE per clause, **derived** from the encoding |
| `packet-*.md` | exactly what each reviewer was shown |
| `attempts/` | every raw model reply, in order |
| `roundtrip/` | G5's independent re-encoding |

A generated document is **not** part of the corpus. Joining the knowledge base
stays a separate human act through `lks ingest` / `lks merge`.

## Decisions

### G-1. The PDF is the deliverable; the Catala module is the evidence

"Run it through Catala" is vacuous for prose alone, so every draft gets an
encoding of its computational clauses. The module is never handed to the user.
Gate G4 is what binds the two: every `|` quotation in the module must match the
document on the same content hash the corpus uses. Without it, the pipeline
could prove a rule sound and print different words.

The encoder does not quote. It writes `| ENCODES: K-3.1` and
`generate.assemble_module` expands that into the exact clause text. Copying
text verbatim is what a program does perfectly and a model does unreliably, and
each slip would have cost a full encoding attempt. G4 still runs on the result.

### G-2. The gate is a ladder: four cheap rungs every iteration, one expensive rung once

| Gate | Checks | Blocks on | Cost |
|---|---|---|---|
| G1 | `catala typecheck --check-invariants` | any error | ~1 s |
| G2 | every scope executed over a battery driven to both sides of and *onto* every threshold and date offset in its exception trees | `ScopeConflict`, `NoApplicableRule`, division by zero, other runtime errors; an ungeneratable battery; assertions admitting under 25% of the battery | ~1–20 s |
| G3 | every exception branch reached, by the interpreter's `--trace` | a **provably** dead branch | free (same pass as G2) |
| G4 | quotations match the document | any mismatch; code encoding no clause | free |
| G5 | a fresh re-encoding of the prose, compared by `compare_encodings` | structural or behavioural divergence | one reasoning call, ~10 min |

G5 is the only gate that tests what the user receives: whether the prose alone
is enough to reconstruct the rule. It runs last and once, with one retry on a
different seed. `--no-roundtrip` skips it, and the PDF says so.

### G-3. Calibrated against the committed modules, and changed where they proved it wrong

A gate that fails correct modules will fail correct drafts, and the repair loop
will spend its attempts on problems no redraft can fix. So G1–G4 were run over
all 19 committed modules before being trusted, and three things were changed
because of what that showed:

1. **An assertion is a domain statement, not a failure.** The battery probes 0
   for every integer; overtime guards `assertion ordinal >= 1`. The first
   version counted those 64 rejections as failures. Now they are dropped and
   counted — and a scope that admits under a quarter of its battery fails
   instead, so `assertion false` cannot pass vacuously.
2. **The admission floor is a fraction, not a count.** An absolute floor of 8
   vectors failed seven correct modules whose batteries held 1–6 vectors,
   every one admitted.
3. **G3 blocks only on proof.** An uncovered branch is *dead* only when every
   variable its conditions mention is an input the battery actually varied —
   because then the battery visited every cell of the partition those
   conditions define. A branch conditioned on a derived value (`days_elapsed`,
   a sub-scope's output, a `match` binding) cannot be aimed at by an input
   battery, and is reported as *not exercised*, loudly, without blocking.
   Blocking it failed 5 of 19 correct modules, and no redraft widens a battery.

Two gaps in the battery were also closed rather than reported around:
enumerations now contribute every constructor (they were refused outright), and
booleans or enums that the battery's cap pinned to one value are swept
afterwards, which is what took NdaSurvival from untested to 548/548 total.

G3 is a failure for a draft even though AMB-01 shows a faithfully encoded dead
branch in the committed corpus. There, fidelity to a defective source is the
higher duty. Here we are the author, and a clause that cannot change anything is
ours to fix.

### G-4. Three reviewers, and none of them votes on a fact

Every agent in this repo proposes and something that cannot be talked round
decides. The screen keeps that rule:

| Reviewer | Sees | Its findings are checked by |
|---|---|---|
| logic | document + stripped encoding + exact interface | re-executing the scope on its inputs |
| language | the document only | quote/clause validation; dangling references and definitions checked mechanically |
| consistency | document + nearest corpus clauses + interface | corpus references must exist; a CONTRADICTS_CORPUS with inputs is confirmed or refuted by execution; an UNDECIDED_CASE or INTERNAL_CONTRADICTION is confirmed only if the rule has no single answer either, and is never refuted by the rule's answer |

- A **confirmed** finding blocks on its own — one reviewer is enough, because a
  re-executed contradiction is a fact.
- A **judgement** finding blocks when a *different* reviewer raised one on an
  overlapping clause. Agreement is by clause id, never by wording.
- A lone judgement finding does **not** block. It is printed in the PDF under
  "Open questions for a lawyer".
- A finding citing a clause that does not exist, or quoting words its clause
  does not contain, is discarded before anything else happens.

That split mirrors the corpus: 31 confirmed breaks were fixed; 5 ambiguities no
encoding could settle were recorded for a lawyer.

The reviewers run blind, with no deliberation round, and all three must return
a usable reply (one retry each). An incomplete screen blocks: passing on two
reviewers out of three would report a search that did not happen as a search
that found nothing. It does not redraft, though: if nothing blocked, a reviewer
that never replied says nothing about the draft, so the same draft is screened
again on new seeds, and that round counts towards the limit.

All three run `think=False` in JSON mode. The logic reviewer would benefit from
reasoning, but on this runtime `think=True` with JSON mode returns an empty
completion (`lks.fleet`, `lks.agents.REVIEWER`).

### G-5. Retrieval ranks on the committed index, and reaches RULE precedent through documents

The vector store holds PROSE and HYBRID clauses only, so the chat layer cannot
quote code as law. A drafter of rules needs rule precedent, so the nearest
chunks are used to find *documents*, and the full text of those documents and
their Catala modules (via the registry) join the context. The index is
untouched, so `corpus_aggregate` and the router's calibration are too.

Scores come from the committed file index (cosine), not MongoDB's
`$vectorSearch`, which reports `(1 + cosine) / 2` — the reason documented in
`lks.chat.Chat._pick_ranker`. If MongoDB is live it is opened, which runs its
three-way staleness check, and the result is recorded in `context.json`.

If the index is stale, generation refuses: retrieval is a required step, not
an enhancement.

### G-6. A stdlib PDF writer

No PDF tooling exists on the host except LibreOffice, and the repo runs offline
from a cold checkout. `lks.pdf` writes base-14 fonts with WinAnsi encoding,
justifies with `Tw`, keeps a clause marker with its first line, and is
byte-deterministic: `/CreationDate` comes from the document's effective date,
never the clock, so a PDF's SHA-256 can be pinned in `report.md`. Metadata is
written as UTF-16BE, because WinAnsi's em dash is Scaron in PDFDocEncoding.
Characters outside WinAnsi are transliterated where unambiguous and otherwise
replaced *and reported*.

### G-7. Out-of-tree modules run through `catala interpret`

`clerk run` rejects a target outside `clerk.toml`'s `include_dirs`. Staging a
draft into `catala/modules` would have let an unproven module be indexed by
`build_registry`, `check_fidelity` and `coverage` while still being repaired.
So `catala_runner` now gives an out-of-tree target `-I` flags for the project's
modules, the standard library and its own directory, and executes it with
`catala interpret`. An in-tree target is byte-for-byte unchanged. A draft whose
module name collides with a committed module is renamed with a `Gen` prefix.

### G-8. On failure, no PDF

The default is to issue nothing. `--emit-failed-pdf` renders anyway, with
`FAILED GATE — NOT FOR EXECUTION` in the running header of every page, not
only the first, because pages get separated.

Limits: 3 encodings per document version, 2 redrafts for a dead branch (each
with its own 3 encodings), 2 screen rounds, 2 drafts for parse failures, 2
roundtrip attempts.

### G-9. The model never dates a rule

An effective date decides which facts a rule governs. The first real run's
drafter, given a request with no date, wrote `2024-06-01`. So the date is
settled by `generate.settle_effective_date`, not requested: `--effective-date`
(or the API's `effective_date`) wins; otherwise the drafter's date survives only
if that exact string appears in the request, and anything else becomes
`"TO BE CONFIRMED"`, which the PDF prints. This matches `lks convert`, which
already refuses to guess a real document's date.

### G-10. The encoding roles do not reason first, because the first real run measured it

The first real run (a remote working allowance policy) drafted successfully with
`think=True` — a 4.4 KB document in about 12 minutes. The encoder, also
`think=True` with 8,192 tokens, spent the entire budget deliberating and returned
no module: `the token budget (8192) was consumed by reasoning and no answer was
produced`. Its two remaining attempts had identical settings, so the run was
stopped rather than left to fail twice more at ~14 minutes each.

The budget could not simply grow. The encoder's prompt — the document, the
Catala reference and a precedent module — already takes about half of the
16,384-token context, and at this model's ~12 tok/s a longer deliberation costs
25 minutes or more per attempt. So `ENCODER`, and `REENCODER` as G5 uses it, run
with `think=False`. That is the same measured trade `docs/DECISIONS.md` D-10
records for the reviewer roles: an unreasoned module is rougher, but G1–G4 and
the repair loop exist to catch rough, and nothing can repair a reply that never
arrives.

The second run then measured the drafter doing the same thing. It had fit its
budget in the first run; with the same prompt shape and seed, but different
precedent from retrieval, it deliberated through all 8,192 tokens and produced
no document. So reasoning is not a property a role reliably has or lacks on this
model. The drafter keeps `think=True`, and `generate` now repeats any attempt
whose budget was consumed by reasoning, immediately and without it. Reasoning is
used where it fits; a run no longer loses its draft to a deliberation that did
not finish. Both calls appear in `attempts/` and the report.

### G-11. What the encoder is told about Catala syntax is what the compiler accepted

The first real encoding to reach the compiler (run 3) was structurally sound — a
labelled exception hierarchy per clause, inclusive and exclusive boundaries as
drafted — and failed G1 with nine errors, all from one construct written three
times: `Date.add_round_down(payment_date, 6 month)`. The Catala reference the
encoder is given names `Date.add_round_down` but never shows how to apply it,
and its only `of` examples are casts. The committed modules all write
`Date.max of a, b`.

So `agents.CATALA_SYNTAX_NOTES` now states it for both encoding roles, and each
line was settled with a probe module first: application with `of` compiles and
computes 31 January + 6 months as 31 July; `date round down` inside the scope
body compiles; `date round decreasing` is rejected. The repair loop would
eventually have fed the diagnostic back, but at several minutes an attempt, a
gap in the prompt is cheaper to close than to rediscover.

### G-12. An audit of the pipeline, and what it changed

Each of these let the pipeline claim something it had not established, or
blamed the model for a fault of the harness. Every one has a test in
`tests/test_generate.py`.

1. **A later round passing did not resolve an earlier finding.** Reviewers run
   blind on new seeds, so round 2 not probing round 1's inputs proved nothing,
   yet the PDF printed "Resolved by redrafting" for a confirmed break whose
   clause and code were unchanged. `generate.replay_blocked` now re-verifies
   every finding that blocked a round against the final draft. A confirmed
   finding that still reproduces on unchanged clauses blocks issue. The PDF
   records each outcome, and says when a finding was a judgement that two
   reviewers shared rather than a confirmed break.
2. **"G3 passed" is not "every branch fired".** G3 passes with branches it could
   not aim at, and the screen used that to refute INOPERATIVE_CLAUSE findings
   on exactly those branches. The refutation now requires
   `gates.exercised_clauses`: the trace shows every rung quoting the clause was
   taken.
3. **G5 compared only the scopes it could pair.** A module scope with no
   counterpart in the re-encoding was skipped, and G5 passed on the rest. Such a
   scope now blocks as UNTESTED, unless it is only ever called as a sub-scope.
   Outputs that the prose yielded but the module does not encode are printed.
   They do not block.
4. **A dead base case the encoder wrote went to the drafter.** An unconditional
   base that exhaustive exceptions always override comes from the encoding, not
   the document. `ENCODER` is told to write one. It is now repaired as an
   encoding failure instead of costing a redraft.
5. **Workspaces.** A reused workspace kept an earlier run's `document.pdf`,
   which the API then served for a run that failed. It also restarted attempt
   numbering at 01 over the old files. A non-empty workspace is now refused
   (`WorkspaceInUse`), and `eval_generate.py regenerate` uses a fresh directory
   per run. Before, a rerun that failed before encoding was scored against the
   previous run's module. Workspaces are also resolved to absolute paths, since
   Catala runs from the repository root.
6. **The drafter's prompt was never fitted.** It began with the request, which is
   the part Ollama drops. A redraft of a long document overflowed the context.
   The prompt now puts precedent first and sheds it to fit.
7. **Triage.** "in good faith" was matched by its first word. Any input
   containing "in", such as `termination_date` in run 4, made a date rule
   HYBRID. Judgement inputs are now boolean inputs matched on the whole stem.
8. **Date batteries never straddled a date offset.** The compiler prints
   durations as `[6 months]`, but `lks.draft.OFFSET_RE` matched only the
   singular. `collect_date_offsets` therefore returned nothing for every
   committed module and every draft. On top of that, `date_pool` cut its pool to
   9 dates, which could drop the exact anniversary, and G3 read `months` as a
   variable, so it called an unreached date branch "derived". Run 4's "repay
   half between 6 and 12 months" was never executed by G2, G3 or G5. With all
   three fixed, run 4's battery grows from 41 to 137 vectors and G3 goes from
   7/8 to 8/8 branches exercised. Over the committed modules, no gate outcome
   changes. NdaSurvival's battery goes from 1,000 to 968 vectors,
   all total, because its pinning shifted. It now reports one branch as not
   exercised, on a `match` binding. **The V1 numbers in
   `eval/GENERATION-RESULTS.md` predate this fix and need re-running.**
9. **Smaller fixes.** When G1 fails, G2 and G3 are *skipped*, so the repair
   brief carries only the compiler's diagnostic. Leave-one-out retrieval no
   longer offers a module that also encodes the held-out document. A redraft
   after a dead branch updates the recorded title and id. A lower-case module
   name is capitalised in the file as well as in its filename.

### G-13. A second audit

Each of these has a test in `tests/test_generate.py`.

1. **An incomplete screen redrafted a draft nobody had faulted.** A reviewer
   that timed out left the screen incomplete with nothing blocking, and the
   pipeline redrafted from `repair_brief()`, which then held a heading and no
   items. The same draft is now screened again, and a last round that is still
   incomplete fails as `incomplete`, not `blocked`.
2. **The rule's answer refuted claims about the words.** An UNDECIDED_CASE or
   INTERNAL_CONTRADICTION with inputs was executed like a BREAK, so when the
   encoding happened to give the reviewer's guessed answer the finding was
   REFUTED and dropped, not even kept as an open question. Run 4 lost "no rule
   for exactly 37.5 hours" that way. Those kinds are now confirmed only when
   the rule has no single answer either (a Conflict, no applicable rule), and
   otherwise stay judgements. CONTRADICTS_CORPUS, whose `expected` is the
   existing clause's answer, is still settled by execution.
3. **A redraft after a dead branch inherited the old draft's attempts.** The
   rewritten document got whatever encodings were left, so a run could fail
   "after 3 attempts" having tried it once. Attempts now restart per document
   version, and `Limits.dead_branch_redrafts` (2) bounds the redrafts.
4. **Executions raced clerk's rebuilds.** Out-of-tree modules load compiled
   objects from `_build`, which `clerk` rewrites, and the generation job does
   not hold the API's EXEC_LOCK. The committed accommodation module, executed
   during a rebuild, failed every vector with "implementation mismatch on
   Stdlib_en". `catala_runner.BUILD_LOCK` is now held exclusively by every
   clerk invocation (including `lks.remedy.rebuild`, `check.sh` and
   `start.sh`) and shared by every catala invocation. A process that does not
   take the lock (one started before it existed, or clerk run by hand) can
   still leave `_build` inconsistent, so an execution that fails with the
   loader's "implementation mismatch" is rebuilt with `clerk build` under the
   exclusive lock and retried once.

## Evaluation against the corpus

`scripts/eval_generate.py` — method in its docstring, numbers in
`eval/GENERATION-RESULTS.md`.

- **V1, planted defects.** Mutation operators modelled on the defects this
  corpus actually produced are applied to the 19 correct modules. Each mutant
  is classified stillborn / untestable / equivalent / live against the original
  by `compare_encodings`; only live mutants count. Kill rates are reported
  separately for the cheap gates (no model) and the logic reviewer, whose kill
  only counts when its confirmed finding lands where the mutant and the
  original disagree. The live count is also the ceiling for G5.
- **V2, leave-one-out regeneration.** A corpus document is removed from
  retrieval entirely, the pipeline gets a realistic brief of it, and the result
  is compared by `compare_encodings` against each committed module that
  encodes it. Per-clause triage agreement is not reported, because a
  regenerated document numbers its own clauses.

## Known limits

- The cheap gates cannot see a rule that is well-formed, total and fully
  reachable but computes the wrong number. That is what the logic reviewer and
  G5 are for, and V1 measures how often they manage it.
- Scope inputs are restricted to booleans, numbers, money, dates and
  payload-free enumerations, because only those can be driven to boundaries
  without inventing values. A document whose rules genuinely need a list
  input cannot currently pass G2.
- `triage.yaml` is the encoder's claim, marked `source: derived`. It is not an
  adjudication.
- Reviewer isolation is `prompt` unless OpenShell has proven a sandbox
  (`lks.agents.Enforcement`); the PDF's reviewer table states which.
