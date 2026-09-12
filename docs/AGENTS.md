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
| `reviewer` | a fact pattern where code and clause disagree | re-executing the scope; `expected != observed`; citations required | yes |
| `reencoder` | Catala from an English spec | typecheck, exception-tree and behavioural equivalence | yes |
| `slotfill` | machine inputs from a question in prose | a person sees every extracted fact before anything runs | no |

So a weak model costs little. Bad labels show up as a low score against ground
truth. Bad fact patterns are rejected by the harness. Bad Catala fails to
typecheck. What survives is worth keeping.

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

The loop stops a module when it has gone `--quiet-rounds` consecutive rounds
without producing a finding the harness accepts, and records how many rounds
that took — so "survived a sustained run" finally has a number behind it.
Attacks already tried are fed back into each round so the search moves on, and
the log persists between runs so stopping and resuming does not reset it.

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
