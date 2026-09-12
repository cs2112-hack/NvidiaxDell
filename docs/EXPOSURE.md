# The adversarial legal exposure engine

An agent fleet that continuously tries to sue this company, and a formal
executor that only lets through the attacks that actually land.

Every other part of this system answers *what do our documents say*. This part
answers *what will they cost*.

## The claim, and where it is cashed

**Agents propose. The formal layer disposes.**

Agents do open-ended creative search over fact patterns, which they are good
at. Catala adjudicates, which agents are catastrophically bad at. No unverified
agent output reaches a human: an attack either reaches a bad outcome under the
company's own encoded policy, or it dies silently in a subprocess.

That is not a claim about how well the model performs. It is a claim about what
is on the path between the model and the queue, and it is worth stating
precisely because "low false positive rate" is what every tool in this space
says.

A finding is emitted only when **the interpreter reached a bad-outcome state on
facts the declared domain admits**. Both halves are checkable by a reader:

* the state is reproducible — `lks exposure show EXP-0003` prints the facts,
  and re-running the scope on them gives the same answer, forever;
* the domain is a file of stated claims about the world, `exposure/domains.yaml`,
  where every narrowing carries a note saying what makes it true.

There is no step at which something is *probably* a problem. `scripts/check.sh`
gate 9 re-adjudicates every entry on the queue on every run, so an entry that
stopped reproducing fails the build rather than sitting in a lawyer's inbox.

### What that costs

Recall, and only recall. The engine is blind wherever the declared domain is
wrong or missing. Right now 12 of the 45 registered scopes have a declared
population; the other 33 are invisible to it, and `lks exposure doctor` lists
them by name rather than letting the coverage look complete. A narrowing that
is too tight silently suppresses findings.

That is the direction a queue a General Counsel works has to fail in. A missed
exposure is a gap. A fabricated one, twice, is a queue nobody opens again.

## The six ways an attack lands

Five of these the Catala interpreter reaches on its own. Only the sixth needs
legal content that is not already in the corpus.

| Class | What happened | Decided by |
|---|---|---|
| `CONFLICT` | two definitions apply and the documents establish no priority | the compiler |
| `SILENCE` | no definition applies to a case that can arise | the compiler |
| `REFUSAL` | an assertion rejected facts that are realisable | the compiler |
| `CONTRADICTION` | two scopes with authority over one question answer differently | executing both |
| `DIVERGENCE` | what the company did differs from what its policy computes | executing the corpus over the record |
| `ADVERSE` | a declared exposure predicate fires on the outputs | the predicate, on executed values |

`CONFLICT`, `SILENCE` and `REFUSAL` are the findings hardest to argue with,
because nobody wrote them down: they are the company's own compiler declining
to say what the company's own policy is. `DIVERGENCE` is the one that goes
quiet in a room, because it is not a reading of a document — it is a list of
decisions with dates and two numbers each.

## What a human writes, and what is computed

Exactly three files are authored by a person. Everything else is derived.

### `exposure/domains.yaml` — which facts describe somebody who exists

The documents say what follows from a fact. They never say which facts occur.
`ordinal = -3` is not an hour of anyone's payroll week, and reporting that the
corpus is silent there would bury the handful of places where it is silent
about someone who is really there.

So each scope declares the realisable range of each input, a `note` justifying
every narrowing, and `coherence` constraints between inputs — a claim cannot be
submitted before the expense it claims for, and neither date alone is
impossible. Without coherence the grid manufactures cases nobody is in, the
scope correctly refuses them, and the map reports six regions of "the policy
refuses to answer" that are the map's own fault. That is exactly the false
positive this engine is not allowed to have.

Also declared here: the `population` a scope decides about ("an employee-hour",
"a customer service-month"), so a finding reads as a person rather than a
record, and the `measure` output that carries the money.

### `exposure/predicates.yaml` — what an opponent will say

A predicate is a theory of liability held by somebody outside this company: the
reading of our own documents they will advance, the outcome it produces, and
the provisions that make the reading arguable. It is a conjunction over the
values the scope actually produced and the facts it ran on — no disjunction,
because two ways of being exposed are two predicates, which costs four lines
and buys a queue where each entry names one theory and can be closed by one
amendment.

Every predicate must cite. Uncited, it is an opinion with a currency symbol
attached, which is the same discipline `record_counterexample` already applies
to the adversarial reviewer's findings.

**The fleet cannot add to this file, and is never shown it.** A role that knew
what counted as a bad outcome would reproduce it, and a survivor would mean
nothing.

### `operations/decisions.jsonl` — what we actually did

One decision per line: the facts, the outcome recorded in the system of record,
who it was about, when, and where the export came from. The watcher executes
the corpus over each and reports every divergence.

This is also the only thing that can put a real population on a region of the
map, which is what turns *the corpus is silent here* into *the corpus is silent
about these eleven people*.

## The map is derived, not swept

The corpus composes into one decision function. Its regions are recovered by
executing it densely enough to see the shape — and the grid comes from **the
compiler's own exception conditions**, not from anyone's guess about
interesting inputs.

`catala exceptions -s S -v V -F json` reports, for every output, the hierarchy
of definitions and the condition attached to each. Those conditions *are* the
borders. `ordinal > 40` is not a plausible place to probe; it is the exact
point at which C-4.1 begins to govern. The probe set for an input is every
literal some clause tests it against, each with the value one step either side
at the literal's own precision, plus the ends of the realisable domain.

Three things follow, and they are the reason the map is worth looking at:

* a border is always attributable to a clause, because it came from a clause's
  condition in the first place;
* a region cannot be an artefact of sampling — two cells are in the same region
  because the interpreter *took the same definitions* for them, which the trace
  reports, not because their outputs happened to look alike;
* adding a clause redraws the map without anyone updating a sweep.

Dates and enumerations have no numeric neighbourhood, so their probe values are
declared — and are reported as declared, in `axis_reasons`, because only a
derived axis carries the argument above.

Where the product of the probe values would exceed the cell ceiling, dimensions
are dropped least-discriminating-first and `Surface.elided` names each one and
the value it was pinned to. A map with a dimension flattened is a map of a
slice; saying which slice is the difference between a limitation and a lie.

## The figure

No number in this system is bare, and none is estimated.

* `ADVERSE` and `CONTRADICTION` are a difference between two values that were
  both produced by executing something.
* `CONFLICT`, `SILENCE` and `REFUSAL` have no value at all — that is what makes
  them findings — so what is quantified is the **spread**: the range the
  measured output takes across cells one fact away, which is the amount the
  corpus leaves undetermined and the range over which the argument will
  actually be conducted.
* A scope that declares no measure produces findings with `amount: null` and an
  `amount_basis` saying why. An invented number would be worse than no number.

Every amount ships with the sentence that produced it. `lks exposure show`
prints it under `basis:`.

## The counterfactual is applied, not simulated

`lks exposure close EXP-0009` asks a role for the smallest edit that closes an
exposure — the amendment to the document, and the corresponding edit to the
encoding so the consequences can be computed before anyone signs anything.

The edit is worth nothing on its own. What is worth something is the diff of
everything else it changes, and that is not proposed by anybody:

1. the edit is written to the **real module file**,
2. the project is rebuilt (`clerk test`, about a second),
3. every mapped scope is re-executed over its whole grid,
4. every recorded counterexample is re-run,
5. the module is restored, verified by content hash, and the project rebuilt
   again.

It has to be the real tree. Catala resolves a module's dependencies through
compiled objects keyed by the module's path in the source tree, so a module
evaluated from a temporary directory either fails to find its dependencies or —
far worse — silently loads objects built from the *unedited* source and reports
that the edit changed nothing. The restore is in a `finally`, and a failure to
restore raises rather than returning a diff.

The same machinery drives the **Exposure** view's "move a border" box. Edit a
clause, the borders redraw, and what comes back is measured. It takes tens of
seconds, and that is the point: a map that redrew itself by guessing would be
the one thing on that screen nobody could rely on.

### A worked example, from the first run

`lks exposure close EXP-0009` asked the remedy role to close the C-8.1
exposure. It proposed a clause amendment that reads well:

> Where two or more provisions of C-4, C-5 or C-7 would apply to the same hour,
> only the single highest applicable multiplier applies to that hour, **provided
> that no provision expressly excludes the entitlement to an overtime payment.**

and, to go with it, a Catala edit that adds a second exception with the same
condition and the same consequence as the one above it. Applied and measured:

```
typecheck: ok
in-source assertions: FAILED
closes the exposure: NO
counterexamples broken: CE-0001, CE-0002
Overtime.HourPremium: 180/640 cells moved (180 computed -> conflict); regions 22 -> 13
```

It compiles. It does not close the exposure it was proposed for. It breaks two
recorded counterexamples, and it turns 180 cases that the policy used to decide
into cases where Catala refuses to choose between two applicable definitions --
which is to say, the "fix" would have introduced the very class of defect the
engine exists to find, in a fifth of the decision space, for every senior
employee working overtime.

A reviewer reading that diff would very likely have approved it. That is the
argument for measuring a remedy rather than reading one, and it is the same
argument as the rest of this document applied to the engine's own output: the
model proposes, and something that cannot be talked round disposes.

(The clause amendment, meanwhile, may well be right. It is a question for a
lawyer, and it is the half of the output that reaches one.)

## The watchers

The corpus is static. Nothing else is.

**Rulings.** A judgment lands in `exposure/holdings/incoming/`. A role
formalises its ratio as a constraint over one scope's decision function, and
the result goes to `exposure/holdings/proposed/` — never to
`exposure/predicates.yaml`. It can be *executed* from there, so "what would
this ruling do to us" is answerable in seconds, but it does not bind until a
person moves it across. Same discipline as `ingest/proposals/`, same reason: an
agent reading a judgment is proposing legal content.

The output is not "this may be relevant". It is which regions of the decision
space become indefensible, which clauses produce them, how many decisions
already on record fall inside, and whether the constraint is **degenerate** —
requiring the outcome those cases already have. That last check catches the
commonest way a formalised holding is wrong, the direction of the contention
copied from the wrong side, and it is caught by execution because it cannot be
caught by reading.

**Inbound paper.** `lks.ingest` already detects conflicts between an incoming
document and the corpus mechanically. `lks exposure inbound` adds the
negotiating half — a redline and a fallback with the condition that makes it
acceptable — so the output is a position rather than a list of differences.
Where the counterparty's clauses have been encoded as candidate modules, the
counterfactual will show exactly which regions of our decision space move if we
sign; where they have not, this is a textual comparison with a position
attached, and it says so.

**Our own operations.** Described above. It is the one to run first.

## Proof underneath everything

Every region, exposure and redline carries the execution trace: which scope
ran, which definition the interpreter took for each output, the line it sits
on, and the clause quoted above that line in the literate source. The clause
chain on a finding is not a reconstruction — `catala interpret --trace` reports
the definition it applied and `lks.literate` resolves the line to the
quotation.

It costs almost nothing once scopes execute, and it is what stops the whole
thing reading as a language model with confidence.

## Running it

```bash
lks exposure doctor                      # the declared content, and what is invisible
lks exposure operations --record         # what we did vs what the policy says
lks exposure map Overtime.HourPremium    # the decision surface, as regions
lks exposure fleet Overtime.HourPremium --rounds 40
lks exposure queue                       # worst first; divergences above arguments
lks exposure show EXP-0003               # one finding with its whole proof
lks exposure letter EXP-0003             # how it will actually arrive
lks exposure close EXP-0003              # the fix, and everything it moves
lks exposure holding <ruling.md> Overtime.HourPremium
lks exposure impact HOLD-0001
lks exposure contradictions
```

Or the **Exposure** view at http://127.0.0.1:8765, which has four panes:

| Pane | What you can do there |
|---|---|
| **The map** | pick a scope, click a region for its clause chain, move a border and watch the counterfactual measured |
| **Probe a fact pattern** | a form built from the scope's own schema, with each field's realisable range and note beside it; put a case to the executor and see it land or die, with the reason, the outputs and the provisions that decided them |
| **Findings** | the queue, filterable; open one for its whole proof, then draft the letter or propose and measure a fix |
| **Watchers** | divergences between the record and the policy, proposed rulings and what they would do, declared rivalries, and a fleet run you can watch round by round |

The probe form shows the realisable domain and does not enforce it. That is
deliberate: putting in somebody who cannot exist, and watching the executor
refuse with the reason quoted from `domains.yaml`, is one of the things the pane
is for.

The model-backed actions — fleet, letter, fix — run as background jobs, one at
a time, refused rather than queued when one is already running, because Ollama
serves this hardware one request at a time and a second job would only wait.
Preconditions are checked before a job starts: a letter for a divergence that
favoured the other side is refused immediately with the reason, rather than
starting a job that fails a minute later. Every endpoint that executes Catala
holds one lock, because the counterfactual writes to the real module while it
measures (D-9), and a map request in that window would otherwise draw the
borders of a policy nobody adopted.

## Known limits

* **33 of 45 scopes are invisible**, for want of a declared population. `lks
  exposure doctor` names them.
* **The fleet's roles do not reason first.** On qwen3.6 under this Ollama
  build, `think=True` with JSON mode returns an empty completion; without JSON
  mode the whole token budget goes into deliberation. Both verified directly.
  The same configuration is why `lks.agents.REVIEWER` had never produced a
  finding, which is fixed in the same way. See `docs/DECISIONS.md` D-10.
* **Inbound paper is compared textually** unless the counterparty's clauses
  have been encoded. Composing two contracts properly means executing both, and
  we can only execute ours.
* **Arrays and durations are not mapped.** Scopes taking a list of absence
  periods or a timesheet have no grid, so `ChronicFailure` and
  `Overtime.WeeklyOvertime` are outside the map even though their scalar
  neighbours are inside it.
* **The corpus and the operations feed are synthetic**, as is the sample
  ruling. `docs/DECISIONS.md` D-3 applies to all three. The machinery does not
  change when they are replaced; the numbers do.
