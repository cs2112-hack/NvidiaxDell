# What the local model can and cannot do

Measured on this host: NVIDIA GB10, aarch64, 121 GiB unified memory, Ollama
0.34.0, `qwen3.6:27b-q4_K_M` on CUDA compute 12.1 (`cuda_v13` runner).
Reproduce with `scripts/eval_agents.py all`.

Throughput is the binding constraint, not correctness: **~24 tok/s** without
reasoning, **~12 tok/s** with it, and Ollama serialises requests on this host,
so roles queue rather than run together. The figures below took 26 minutes of
wall clock for 134 classifications.

## Triage — 88.8% (119/134)

Ground truth is `triage/decisions.yaml`: 134 clauses adjudicated one at a time
with stated reasons.

```
              RULE   PROSE  HYBRID   ERROR
  RULE          77       5       3       0   n=85   recall 91%
  PROSE          2      35       1       0   n=38   recall 92%
  HYBRID         2       2       7       0   n=11   recall 64%
```

**Zero malformed replies.** The validator rejects a label that is not one of
the three, and a HYBRID that names no judgement input; it never had to. All 7
HYBRIDs it found named an input.

**HYBRID recall of 64% is the real weakness, and it is the class that matters
most.** A missed HYBRID is a clause whose judgement gets absorbed into a
computation — the system inventing law rather than asking a human. The two it
missed worst are exactly the consequential ones: `MSA-SCH4 L-2.5` (called
PROSE, though every service credit is downstream of its Force Majeure and
customer-fault attributions) and `DATA-RET R-4.2` (called PROSE, though its
"to the extent permitted by applicable data protection law" gates whether a
legal hold defeats an erasure request).

**Several disagreements are defensible, and a few are arguably better than the
ledger.** It called `COMM-PLAN S-7.1` HYBRID on "Churned Contract", `S-8.2`
HYBRID on "gross misconduct", and `NDA-MUT N-4.1` HYBRID on whether notice is
"lawful and practicable". A human adjudicator considered and rejected each,
but none is a misreading. The 88.8% therefore understates agreement on
substance and overstates it on the one class that needs recall.

**Verdict: usable as a first pass, not as an adjudicator.** It is a reasonable
proposer for a new document's clauses, with every HYBRID candidate reviewed by
a person — and its false-negative rate on HYBRID means a human must look at
the RULE pile too, not just the flagged ones.

## Slot filling — 2/3 exact, 0 invented facts

The property that matters here is not accuracy but restraint: an invented fact
silently changes a legal answer, whereas an omitted one gets asked for.

It extracted every stated fact in two cases exactly. In the third it omitted
`arrears_days` where the question said the customer "is not in arrears" —
declining to turn a prose assertion into the number 0. That is the correct
failure, and the validator additionally rejects any input name not in the
scope's own JSON Schema, so it cannot invent a field.

**Verdict: fit for purpose.** It prefills the fact form in the web interface
and a person sees and corrects every value before anything executes.

## Adversarial reviewer — not yet measured

Each round costs about 8 minutes of wall clock: reasoning is on, the budget is
6144 tokens, and it runs at ~12 tok/s. A round was two thirds of the way
through its deliberation when it had to be stopped to free the single model
slot for the triage run and the OpenShell install.

What is built and untested is `scripts/review_loop.py`. What is known is that
the harness will reject anything unsound regardless of what the model
produces: a finding is accepted only if re-executing the scope on the model's
own inputs contradicts the expected value it derived from the clause, and only
if it cited clauses. So the risk of running it unattended is wasted GPU time,
not a corrupted suite.

**Honest statement: the 31 counterexamples in the suite were found by stronger
reviewers than this one.** The local loop is there to extend the search
overnight, not to reproduce that work.

## Re-encoder — not measured

The drafting roundtrip converged once already, with a strong model. Whether a
27B quantised model can write Catala that typechecks and agrees on exception
structure is an open question; `eval_agents.py` has no case for it yet because
each attempt costs an 8192-token generation.

## A negative result worth keeping

`qwen3.6:27b-nvfp4` exists in the registry and the bundle's notes suggest it
for a Blackwell-class NVIDIA GPU. Ollama refuses it: *"this model requires MLX
support, but the MLX runtime is not available"*. MLX is Apple's framework, so
that tag is an Apple Silicon path despite NVFP4 being a Blackwell format. On
this stack `q4_K_M` at ~12 tok/s is the ceiling.

If throughput becomes the blocker, the options are a smaller model for triage
and slot filling — the bundle's notes name `qwen3.5:9b` — while keeping the 27B
for the reviewer, where reasoning quality is what the role is for.
