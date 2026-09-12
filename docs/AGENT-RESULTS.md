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

## Adversarial reviewer — run, not signed off

Only `overtime` has been attacked: five attempts on record, about three minutes
each (171 s logged), with the role at `think=False` and a 1,400-token reply
bound by a schema (see the comments on `agents.REVIEWER`). No module has reached
the clean run of 15 attempts that `scripts/review_loop.py` signs off on.

One break was accepted, CE-0032, and has been withdrawn as false
(`tests/counterexamples/CE-0032.withdrawn`). Its inputs were a one-row
timesheet, which `WeeklyOvertime` counts as hour 1 of the week, not the 50th
hour its fact pattern described; the rule's 115 was right for those inputs and
the expected 150 followed from none of the reasoning.

This page used to say the harness "will reject anything unsound regardless of
what the model produces". It will not. Re-executing the scope proves the rule
disagrees with the model's number, not that the model's number is the
document's. The harness now also turns away expected values of the wrong type,
citations of clauses that do not exist and findings about other components, and
only an attempt that ran the rule counts towards sign-off. A well-formed wrong
reading still gets through, which is why every report asks a person to confirm
the reading before the rule is changed, and why running the loop unattended can
cost a false entry in the suite, not only GPU time.

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
