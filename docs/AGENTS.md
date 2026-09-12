# The agentic layer

## What was missing

Every judgement this system needed from an agent used to come from outside it.
A Claude session adjudicated the triage, wrote the Catala, played the
adversarial reviewer, and re-encoded the English for the drafting roundtrip.
That made the verification loop — the part the brief says matters more than the
code — an *activity someone performs* rather than a *property the system has*.
No component could honestly be called done, because "the reviewer has run out
of attacks" only ever meant "the person driving it stopped".

A local model fixes that specific thing. It does not make the system smarter;
it makes the loop able to run unattended, on the user's own hardware, for as
long as it takes.

## The stack

| Piece | Source | Role here |
|---|---|---|
| Ollama 0.34.0 | `ollama-linux-arm64.tar.zst`, installed rootless in `~/.local` | serves the model over HTTP |
| qwen3.6:27b-q4_K_M | staged from `~/Downloads/Hackathon/bundle/models/ollama`, all blobs SHA-256 verified | the model |
| OpenShell | NVIDIA/OpenShell, via the NemoClaw installer | sandbox runtime — **this is what makes role isolation real** |
| NemoClaw | NVIDIA/NemoClaw, pinned to the bundle's reviewed commit | installs OpenShell and onboards an agent |

Hardware actually present: NVIDIA **GB10**, aarch64, 20 cores, 121 GiB unified
memory. Ollama loads the model on CUDA (compute 12.1, `cuda_v13` — the `cuda_v12`
runner skips this card as its compute capability is not in that build's
architecture list). Measured throughput on this host: **~24 tok/s** without
reasoning, **~11 tok/s** with it.

## Why a model can be trusted with these jobs

It cannot decide a legal question, and it is never asked to. Every figure still
comes from executing Catala and every quotation still comes from the corpus
verbatim. All four roles are *proposal* roles whose output is checked by
something that cannot be talked round:

| Role | Proposes | Checked by | Reasoning on? |
|---|---|---|---|
| `triage` | a clause's label, and why | accuracy against the 134 adjudicated labels | no |
| `reviewer` | a fact pattern where code and clause disagree | re-executing the scope; `expected != observed`; citations required | no (D-10) |
| `reencoder` | Catala from an English spec | typecheck, exception-tree and behavioural equivalence | yes |
| `slotfill` | machine inputs from a question in prose | a person sees every extracted fact before anything runs | no |
| `fleet-*` (five archetypes) | a fact pattern that extracts money from us | executing the scope; a bad outcome under the declared domain, or nothing | no (D-10) |
| `holding` | a ruling's ratio, as a constraint over one scope | lands in `proposed/`, never live; degeneracy caught by execution | no |
| `remedy` | the smallest edit that closes an exposure | applied for real: typecheck, `clerk test`, every counterexample, every map | no |
| `redline` | a negotiating position on an inbound conflict | nothing — it is prose about a conflict found mechanically, and is labelled | no |
| `fleet-demand` | the letter before action | nothing — run only on a finding already accepted, and forbidden to produce a figure | no |

So a weak model costs little. Bad labels show up as a low score against ground
truth. Bad fact patterns are rejected by the harness. Bad Catala fails to
typecheck. What survives is worth keeping.

### The exposure fleet, and the one thing it is not shown

`lks.fleet` adds five adversarial archetypes — a claimant's employment lawyer,
a customer's commercial counsel, a regulator, an auditor, and a departing
worker chasing what they are owed. Each is given the clauses, one scope's
machine interface, and a description of which facts describe a person who
actually exists, and asked for the fact pattern that extracts the most.

They are **not** given `exposure/predicates.yaml`. A role shown what counts as a
bad outcome would reproduce it, and a survivor would mean nothing. The search
has to be blind for the result to carry information, so `exposure/` is on every
archetype's `forbidden` list and the packet is assembled from the corpus and
the commentary-stripped module, exactly as the reviewer's is.

Their output is adjudicated by `lks.exposure.adjudicate`, which executes the
scope and reports the interpreter's outcome. A role's narrative, contention and
citations ride along on a finding that already survived; none of them is
consulted in deciding whether it survives. The last two roles in the table
above are the only places in this system where a model writes prose that
reaches a human unchecked, and both are constrained to a finding the formal
layer has already accepted — the demand letter is explicitly forbidden to
produce a number, because every figure in it was computed.

Observed on the first eight-round run against `Overtime.HourPremium`: two
proposals died because the scope computed an answer and no predicate fired, one
landed on EXP-P001 and became EXP-0009. The archetype that found it was the
customer's counsel, which had wandered well outside its brief — which is fine,
and is why the queue attributes a finding to the party whose theory it is
rather than to the role that stumbled into it.

## Isolation is enforced, not requested

`reviewer/PROTOCOL.md` tells the adversarial reviewer not to read the
implementer's reasoning. **An instruction is not a control.** Every role
therefore declares the paths it may read and the paths whose absence is the
point:

```
reviewer   may read : reviewer/PROTOCOL.md, reviewer/packets, corpus, scripts/probe.py
           must not : docs, src, catala, tests, triage, reviewer/findings
```

With OpenShell present, exactly the `reads` paths are mounted read-only and
nothing else exists, so `docs/` is not forbidden to the reviewer — it is absent
from its filesystem. `lks agent status` reports which mode is live, and the
review loop says so in its header, because claiming enforced isolation when
only the prompt is doing the work is the one lie this layer must not tell.

`agents.audit_isolation` checks each policy for the mistake that would quietly
void it: a `reads` entry that *contains* a `forbidden` one. Mounting `.` so a
reviewer can see the corpus would hand it `docs/` as well, and the policy would
look strict while enforcing nothing.

## Measured results

Run them yourself: `scripts/eval_agents.py all`.

Two roles have ground truth in the repository, which is why they are measured
rather than asserted: 134 clause labels adjudicated one at a time with stated
reasons, and 31 counterexamples whose expected values were derived from clause
text and confirmed by re-execution.

The numbers, and the caveats, are in `docs/AGENT-RESULTS.md`.

## Running the loop

```bash
~/.local/bin/ollama serve &                    # once
python scripts/lks agent status                # model, isolation, role policies
python scripts/eval_agents.py all              # measure before trusting
python scripts/review_loop.py --module overtime --rounds 40 --quiet-rounds 25
python scripts/review_loop.py --status
```

The loop stops a module when it has gone `--quiet-rounds` consecutive attempts
without a defect, and records how many that took — so "survived a sustained
run" finally has a number behind it. Only an attempt that ran the rule
counts: an earlier version counted every round, so a module where the model
never answered "went quiet" after fifteen failures, and the next still counted
`NO_BREAK_FOUND` and `AMBIGUITY`, which run nothing, so a model proposing
nothing could sign a module off. Three unanswered attempts in a row abandon the
module instead. Attacks already tried are fed back into each round so the
search moves on, and the log persists between runs so stopping and resuming
does not reset it.

A sign-off belongs to the version of the rule it was earned on
(`reviewer.rule_fingerprint`: the review packet plus the code of every module
it uses). When the rule or a clause it quotes changes, the clean run starts
again; a defect found later withdraws it. Rewording implementer commentary does
neither, because the reviewer never saw it.

A claimed break is recorded only if its inputs and expected values have the
types the rule declares, every citation is a clause that exists in the corpus, and
re-running the rule disagrees. That makes it a well-formed claim, not a proven
one: nothing checks the reviewer's reading or that its inputs describe its fact
pattern, so a person confirms each report before the rule is changed.

Each attempt prints one labelled outcome — `DEFECT FOUND`, `NO PROBLEM`,
`NOTHING TO TEST`, `DOCUMENT UNCLEAR`, `CLAIM DID NOT CHECK OUT` or `NO ANSWER` — with a sentence
saying what it means. A defect is written up in `reviewer/reports/<id>.md`;
every attempt, with its technical detail, goes to `reviewer/rounds/<module>.jsonl`;
`--details` prints the inputs and values as well.

### Why every round used to die

Both reviewer rounds on record took 8m41s and 8m45s and were discarded as
malformed. In JSON mode this model does not stop when its object is complete:
it spent the whole 6,144-token budget and the reply was cut off mid-string.
Separately, the client's 900-second timeout counted time spent queued behind
other jobs, and the socket timeout it raised escaped every handler.

The client now streams and stops reading the moment a complete JSON value has
arrived, treats the timeout as the longest silence allowed, and reports a
timeout as one failed attempt. The reviewer and the fleet pass a JSON Schema
built from the scope's own interface, with field lengths the runtime enforces
— verified on this Ollama: asked for 500 words in a field capped at 40
characters, it stopped at 40 with valid JSON — so a reply fits in about 800
tokens and cannot name an input the rule lacks.

## Limits worth stating

- **Throughput bounds the loop, not correctness.** At ~11 tok/s with reasoning,
  a reviewer round costs minutes. A sustained run is an overnight job, not an
  interactive one.
- **The model is a reasoning model.** With thinking enabled it deliberates
  before answering, and an early version of the client read the resulting empty
  `response` field as a failure. Roles that do not need deliberation have it
  off, which roughly doubles their speed.
- **`--defer-onboarding` forced the agent choice.** NemoClaw's deferred install
  supports `hermes` only, and only with NVIDIA-hosted inference; with
  `provider=ollama` onboarding must run inline. OpenClaw's own path needs
  NVIDIA inference credentials this host does not have. OpenShell — the part
  the isolation depends on — is the same either way.
- **A local model does not replace the adversarial review already done.** The
  31 counterexamples in the suite were found by stronger reviewers. The local
  loop extends that search; it does not reproduce it.
