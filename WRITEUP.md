# Legal knowledge system — writeup

An internal legal knowledge system over `./corpus`. The rule-like parts of each document
are compiled to Catala and **executed, never read**; everything else lives in a
file-backed vector store; questions are answered over both with the engine labelled;
conflicts are detected at ingestion and block the merge.

## Status

| Component | State |
|---|---|
| Step 0 — `CATALA_CHEATSHEET.md` | **done** — syntax, three golden examples from the official repo, 30 compiler errors with fixes, the runtime command surface, and the modelling rules the compiler taught us |
| Corpus (`./corpus`) | **done** — 44 documents, 18 declared scopes, gold set, conflict map, self-check green |
| 1. Ingestion (`pipeline/ingest.py`) | **done** — batch + incremental, clause classification, vector store, conflict pre-check (precision 1.00 / recall 1.00 vs gold), merge gate |
| Catala modules (`corpus/catala`) | **done** — 10 modules + 2 expected-conflict modules, typecheck clean, **96/96 `clerk test`** |
| 2. Chat (`pipeline/chat.py`) | **done** — executes scopes, elicits missing facts from the compiler's own schema, renders result + clause + branch, quotes the vector store with citations, labels every engine; **18/20 gold pairs** |
| 3. Drafting (stretch) | **not started** — needs generation, so it needs `$LOCAL_LLM_URL` |
| `DEMO.md` | **done** — nine steps, verified output |

## Stack

**Runtime (hard constraints):**

- All inference goes to the local model at `$LOCAL_LLM_URL` (Nemotron via vLLM), reached
  through `pipeline/llm.py` — one file, two functions (`complete`, `embed`), an
  OpenAI-compatible `/v1/chat/completions` and `/v1/embeddings` call each. There is no
  remote fallback anywhere: if the endpoint is missing the client raises `LlmUnavailable`.
- Compiled Catala executes inside an OpenShell sandbox; the product runtime is an OpenClaw
  agent. In this session the same commands run directly (`clerk run`, `catala interpret`),
  which is what the sandbox wraps.
- The vector store is a JSONL file in git. No vector database, no hosted embedding API.

**Toolchain, as installed here:**

- **catala 1.2.1 and clerk 1.2.1**, built in an opam switch (`opam switch catala`, OCaml
  5.2.1). `eval "$(opam env --switch=catala)"` before any clerk command; the Python tools
  find the binaries under `~/.opam/catala/bin` and put that directory on `PATH` for their
  own subprocesses, so they work without the eval.
- **Python 3 standard library only.** No third-party packages are installed or needed:
  HTML→text is `html.parser`, `.docx`→text is `zipfile`, HTTP is `urllib.request`.
- **pandoc + headless Chrome** for the 21 typeset PDFs in `corpus/pdf/`.
- Network was used only at corpus build time, against public endpoints (`efts.sec.gov`,
  `leginfo.legislature.ca.gov`, `app.leg.wa.gov`, `law.cornell.edu`,
  `api.www.documentcloud.org`, `commonpaper.com`, `lawinsider.com`,
  `bookface-static.ycombinator.com`, `ftc.gov`). No LLM generated any corpus document.

**How the model's absence was handled.** `$LOCAL_LLM_URL` is unset on this box, so both
tools print one warning and take a deterministic path: keyword clause classification,
regex input extraction, IDF keyword retrieval, and no embeddings. Every LLM call site has
an offline counterpart behind the same interface, and `--offline` forces it even when the
model is up. What the model would change is listed under *Measured gaps* below.

**The one rule that was bent, and why.** Catala is supposed to be written by
generate → `catala typecheck` → repair. Generation needs the local model, which is not
reachable here, so the session wrote the modules and the compiler verified every one of
them: `catala typecheck` clean, `clerk test` green, and the two conflict modules failing
exactly as recorded. `pipeline/ingest.py` contains the real loop (`generate_module()`:
generate with the cheatsheet in the system prompt, typecheck with
`--message-format=gnu`, feed the parsed errors back, up to N attempts) and it runs as soon
as the endpoint exists.

## Integration with the NvidiaxDell `main` branch

`main` vendors a patched Catala at the same upstream commit this work was written against
(`b7623302`, `nightly-137`), plus seven fixes recorded in `catala-fixes.patch`. Two of them
bear directly on this branch:

- **Fence labels.** Stock nightly-137 silently treats any label other than
  ```` ```catala ````/```` ```catala-metadata ```` as prose, so a mislabelled block
  disappears while every gate still reports success. The patched lexer warns and names the
  label. Every module here uses the two accepted labels, but the generator in
  `pipeline/ingest.py` should run against the patched compiler so a mislabelled generation
  is caught rather than silently dropped.
- **`catala proof --fail-on-unproven`.** Static conflict and gap detection with
  counterexamples, and a non-zero exit that can gate CI. That is strictly better than what
  this branch does today: the conflict modules here are caught by the interpreter at
  evaluation time (and by the compile-time "Multiple conflicting definitions" warning), and
  the "no applicable rule" gaps are only covered where a fact pattern happens to hit them.
  The obvious next step is to add `catala proof --fail-on-unproven` to the ingestion gate in
  `run_tests()` beside `clerk test`.

**Everything on this branch was verified against stock catala/clerk 1.2.1** from opam, not
against the vendored tree — `clerk test` 96/96 and `catala typecheck` clean there. It should
be re-run against the patched compiler before the two are treated as one pipeline; the
`_opam` switch build in `catala/PATCHES.md` is the documented path.

## How it works

**Ingestion** (`pipeline/ingest.py batch | add <file> | verify | show <doc_id>`)

1. Splits each document into clauses. Line-based on purpose: fetched statute text puts a
   subsection per line with no blank lines, markdown uses blank-line blocks, and tables
   attach to the clause above them.
2. Classifies each clause rule-like or prose — the local model when available, otherwise a
   keyword heuristic over money, percentages, durations, threshold language, and
   open-textured standards.
3. Rule-like clauses go to the Catala generator with the scope contract from `SCOPES.md`;
   prose clauses go to `corpus/vector_store/chunks.jsonl` with a `qualifies_module`
   pointer to the module they qualify.
4. Runs the **conflict pre-check** before generation: for every pair of documents that
   declare the same module, compare the numeric fingerprints of their cue-matching rule
   clauses dimension by dimension (money and accrual ratios must be disjoint to count;
   percentage tier tables and deadline phrases must differ; a percentage cap against a
   flat dollar cap is a unit mismatch). Pairs are skipped when the documents belong to
   different agreements, or when both are statutes — a statute never conflicts with
   another statute here, it only overrides policy. Precedence language may come from a
   third governing document; a "no order of precedence" disclaimer only counts in the
   documents that contain it.
5. `add` re-scans the corpus, blocks the merge on any unresolved conflict while printing
   both clauses, then runs the whole fact-pattern suite. Exit code 1 means do not merge.

**Chat** (`pipeline/chat.py ask <question> [--given k=v] | gold | scopes`)

- Routes on three signals: does the question name a module's subject, does it name a
  counterparty (only used to disambiguate between modules on the same subject), and does
  it actually ask for a computation. A question that asks what a document *says* never
  reaches the Catala engine.
- Missing inputs come from `clerk json-schema --scope=…`, so the elicitation list is the
  compiler's own signature and cannot drift from the code.
- Executes with `clerk run --input=<json>`, takes the fired branch from
  `catala interpret --trace`, and the exception tree from
  `clerk exceptions --output-format=json`. The clause text quoted next to the number is
  read out of the literate source above the code. No model touches this path.
- A conflict is an answer: the engine prints Catala's error with both clause headings and
  says why there is no single number.
- Vector answers are quotations with `doc_id`/locator citations and an explicit note that
  nothing was computed. When a computed answer has qualifying prose, it is printed under
  its own `[vector]` label — the engines are never merged into one paragraph.

## Numbers

| Measure | Result |
|---|---|
| Catala | 12 files, **96/96 tests**, typecheck clean (1.2.1) |
| Modules | 10 executable + 2 expected-conflict |
| Exception branches | every module with a carve-out has ≥2 branches under test (enforced by `check_corpus.py`) |
| Conflict detection vs gold | precision **1.00**, recall **1.00** (10 positives, 4 calibration negatives) |
| Conflict block/resolve label | 9/10 |
| Gold Q/A | **18 pass, 1 fail, 1 skip** of 20 |
| Vector store | 1,291 chunks, 43 documents |
| Offline input extraction | 18/36 values without the model |
| Counterexamples found → fixed | 2 → 2 (CONF-008, FP-054) |

Both counterexamples came from the machinery, not from re-reading documents: the pre-check
found a conflict pair the hand-written gold set had missed (Cal. Lab. Code § 202 vs
handbook § 9.3), and encoding SOW § 5.3 showed a gold fact pattern had the wrong date
(April ends on the 30th, so the 90-day deadline is 2026-07-29, not before 2026-07-05).
Both are now permanent `clerk test` cases.

## Measured gaps

- **QA-015 fails**: "if a labor strike stops Acme from delivering, are we excused?" wants
  the force-majeure clause. Keyword retrieval cannot bridge strike → labor dispute → force
  majeure. Left failing rather than hand-tuned; this is the clearest single measure of what
  the local model's embeddings would add.
- **QA-003 is skipped**: a follow-up question ("and if the same claim is for breach of
  confidentiality?") needs conversation state the chat component does not keep.
- **Offline input extraction finds half the values** (18/36). The rest are the ones a model
  is good at — "1,200 hours so far this year" → `days_employed`, `weeks_worked`.
- **Drafting is not started.** It is the one component that cannot be faked without a
  generator: write Catala, render to English, re-encode with a fresh agent, diff the ASTs,
  loop up to three times, report "ambiguous draft" with the diff on non-convergence.
- **7 of 18 declared scopes are not encoded** (Helios and Pinnacle liability caps, PTO
  accrual, breach notice, tuition reimbursement, the reference SLA tiers). Their gold fact
  patterns exist; `SCOPES.md` lists them.
- **catala 1.2.1 cannot emit JSON for enum-valued outputs** (`Json_encoding.string_enum`,
  fixed upstream in PR #1058). `chat.py` parses the human `RESULT` block as a fallback;
  that code can go when the pinned compiler moves.
- **Corpus gaps** (detail in `corpus/CORPUS.md`): the Colorado statute is a paraphrased
  digest because every source returned HTTP 403, the Bonterms form is a field map rather
  than the text, oneSLA is unpublished at the documented URL, and no PDF text extractor is
  installed (the discovery PDFs ship with DocumentCloud's extracted text alongside).

## Layout

```
CATALA_CHEATSHEET.md     step 0: loaded by every agent and every runtime prompt
DEMO.md                  nine-step demo with verified output
WRITEUP.md               this file
pipeline/llm.py          the only outbound inference call; local endpoint or nothing
pipeline/ingest.py       batch + incremental ingestion, conflict pre-check, merge gate
pipeline/chat.py         both engines, labelled; elicitation from the compiler's schema
corpus/                  44 documents, SCOPES.md, gold/, manifest.csv, pdf/, tools/
corpus/catala/           12 modules, 96 tests, clerk.toml
corpus/vector_store/     chunks.jsonl (committed)
```
