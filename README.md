# Precedence

**Your contracts already contain the answers as arithmetic. We compile them so they can be run.**

Built at the Dell x NVIDIA Hackathon — *Local AI on Dell Pro Max with GB10* — Cornell eHub, September 12, 2026.

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

Precedence splits them. The rule-like clauses become typed, executable Catala scopes. Everything else is retrieved and cited. Answers are always labeled with which engine produced them, and the two are never silently blended.

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

## Running it

```bash
# toolchain (on the box)
opam install catala            # compiler + clerk
clerk typecheck catala/src/*.catala_en

# tests — the accumulated adversarial counterexamples
clerk test

# ingest the corpus
./precedence ingest corpus/

# ask
./precedence ask "liability cap, ordinary breach, $200k fees trailing 12 months"
```

## Repository layout

```
corpus/
  raw/real/          public EDGAR contracts, statutes, SLAs
  raw/templates/     CC-licensed standard agreements
  raw/synthetic/     authored documents, incl. planted conflicts
  manifest.csv       per-document source + license
catala/
  clerk.toml
  src/               scopes: liability, leave, service credits
  tests/             fact patterns + adversarial counterexamples
vector_store/        non-rule clauses, chunked, versioned with src
gold/
  qa_pairs.jsonl     question → expected answer → source → scope
  conflicts.jsonl    expected conflict pairs + correct resolution
```

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
