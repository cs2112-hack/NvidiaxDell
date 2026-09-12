# Routing results — each change, measured separately

`eval/BASELINE.md` is the starting point and is not edited. This file records
what changed, what each change was worth on its own, and the curve behind every
number now in `lks.chat` and `lks.vector`.

Reproduce any row with `$PY scripts/eval_routing.py` and the flags shown.
All figures are over the 155 cases of `eval/routing.yaml`. Since change K the
two backends give identical numbers, so `--backend files` and `--backend mongo`
are interchangeable; the harness defaults to `files` because that is the index
`scripts/check.sh` gate 7 verifies against the corpus.

```
$PY scripts/eval_routing.py                    # the report, plus every failure
$PY scripts/eval_routing.py --json             # machine-readable
$PY scripts/eval_routing.py --only judgement   # one tag, or one case id
$PY scripts/eval_routing.py --sweep margin     # a curve for any constant
$PY scripts/eval_routing.py --margin 0.03      # one-off override, no edit
```

## Headline

| metric | baseline | now | change |
|---|---|---|---|
| overall pass rate | 8.4 % | **83.9 %** | +75.5 |
| engine accuracy | 11.6 % | **92.9 %** | +81.3 |
| scope accuracy | 77.3 % | **88.2 %** | +10.9 |
| citation precision | 17.4 % | **58.5 %** | +41.1 |
| citation recall | 91.0 % | **89.0 %** | −2.0 |
| over-confident (of 12 ambiguous) | 5 | **6** | +1 |
| under-confident (of 98 single-scope) | 16 | **3** | −13 |
| judgement questions fully safe | 11/15 | **15/15** | +4 |
| two-engine invariant | held | **held** | — |

Confusion matrix now (rows expected, columns routed):

| | CATALA | VECTOR | BOTH | NONE | total |
|---|---|---|---|---|---|
| **CATALA** | 100 | . | . | 2 | 102 |
| **VECTOR** | . | 30 | 2 | 1 | 33 |
| **BOTH** | . | . | 7 | 1 | 8 |
| **NONE** | 2 | 1 | 2 | 7 | 12 |

## The changes, in the order they were made

Each row is the *incremental* effect: the change applied on top of everything
above it, everything below it absent.

| # | change | pass | engine | scope | cit P | cit R |
|---|---|---|---|---|---|---|
| — | baseline | 8.4 | 11.6 | 77.3 | 17.4 | 91.0 |
| A | cross-engine arbitration (`ENGINE_MARGIN`), HYBRID de-duplication, `VECTOR_THRESHOLD` 0.30→0.45, `VECTOR_SPREAD` | **66.5** | 81.9 | 76.4 | 32.8 | 86.5 |
| C | `ROUTE_MARGIN` 0.06 → 0.02 | **69.7** | 80.0 | 82.7 | 34.5 | 85.8 |
| B | route index: scope row (output names) | 69.7 | 80.0 | 82.7 | 35.8 | 85.8 |
| D | lexical signal, `LEXICAL_WEIGHT` 0.20 | **76.8** | 85.2 | 83.6 | 40.4 | 88.4 |
| E+F | co-encoder ambiguity detection **and** per-sub-question routing | **81.9** | 88.4 | 88.2 | 39.8 | 91.0 |
| G | adaptive supporting citations (`SUPPORT_SPREAD`) | **83.9** | 92.9 | 88.2 | 57.3 | 89.0 |
| H | citations for aggregate scopes | 83.9 | 92.9 | 88.2 | 58.2 | 89.0 |
| I | a scope's own clauses are not quotable against it | 83.9 | 92.9 | 88.2 | **58.5** | 89.0 |
| J | caveat de-duplicated against quotations | 83.9 | 92.9 | 88.2 | 58.5 | 89.0 |
| K | ranking calibrated to one score scale | 83.9 | 92.9 | 88.2 | 58.5 | 89.0 |

J and K are both exactly neutral on the eval set, and both fix a defect the
eval set cannot see. They are in the table so that the final numbers are
reproducible from it, not because they moved a metric.

E and F were applied together, so only their joint effect was measured at that
point in the sequence. Each is separately measured further down by switching it
off at the final configuration, which is the only clean way to attribute them.

A speedup was also applied first, before any measurement: `lks.vector._load_model`
was re-reading 30 MB of model weights on every call, and the caveat ranker
re-encoded every qualifying clause per question. Memoising both took a full run
from ~4 s to 0.44 s. A static model returns bit-identical vectors from a shared
instance, so this changed no score — confirmed by re-running the baseline and
getting byte-identical numbers, which is why it was safe to do mid-evaluation.

---

### A — cross-engine arbitration (+58.1 pass, the single biggest change)

**What was wrong.** The old rule was: if the CATALA part is anything other than
a clean computation, append every vector hit scoring above 0.30. That is not a
routing decision. It produced a two-engine answer for 129 of 155 cases when 8
were correct, and 669 of the 810 clause refs cited across the baseline run were
refs no correct answer needed.

**The signal, measured not guessed.** The difference `best route score − best
prose score` separates the two engines far better than either absolute score:

| expected engine | n | p10 | median | p90 |
|---|---|---|---|---|
| CATALA | 102 | +0.043 | +0.226 | +0.378 |
| VECTOR | 33 | −0.300 | −0.194 | −0.051 |
| BOTH | 8 | −0.194 | −0.008 | +0.237 |
| NONE | 12 | −0.136 | +0.011 | +0.155 |

So whichever engine wins by `ENGINE_MARGIN` answers alone; if neither does,
both answer and both stay labelled; if neither clears its floor, no-coverage.

**HYBRID de-duplication.** A HYBRID clause is in *both* stores by design, so a
judgement question matched the rule index and the prose index at nearly the
same score (J-004 +0.002, J-005 +0.017, J-014 +0.014). Quoting the very clause
the rule route already cites reports one clause twice under two engine labels,
and it also skews the engine margin with a competitor that is not a competitor.
Excluding clauses the CATALA part already cited fixes both.

**`ENGINE_MARGIN` curve** (`--sweep engine-margin`, at the final configuration):

```
   value    pass  engine   scope   cit P   cit R
   0.000   85.2%   93.5%   88.2%   44.0%   89.7%   <- curve maximum
   0.020   84.5%   92.9%   88.2%   43.2%   89.7%
   0.040   84.5%   92.9%   88.2%   42.6%   90.3%   <- chosen
   0.060   82.6%   91.0%   88.2%   41.4%   90.3%
   0.080   81.9%   89.7%   88.2%   40.8%   90.3%
   0.100   81.9%   88.4%   88.2%   40.9%   91.0%
   0.150   76.8%   82.6%   88.2%   39.0%   91.6%
   0.200   71.0%   76.1%   88.2%   36.4%   91.6%
   0.300   57.4%   62.6%   88.2%   32.2%   91.6%
```

**0.04, not the curve's maximum of 0.00.** 0.00 is better by one case, and
means a 0.001 difference in cosine decides the whole answer with no band in
which both engines are engaged. 0.04 keeps that band, is within one case of the
maximum, and has the better citation recall of the two. Compound questions —
the real reason an answer needs both engines — are handled by change F rather
than by widening this margin, which is why the curve slopes down past 0.04.

**`VECTOR_THRESHOLD` curve** (`--sweep vector-threshold`):

```
   0.200   80.0%   89.0%   88.2%   39.4%   91.0%
   0.300   81.3%   89.7%   88.2%   40.4%   91.0%
   0.400   84.5%   92.9%   88.2%   42.0%   90.3%
   0.450   84.5%   92.9%   88.2%   42.6%   90.3%   <- chosen
   0.500   82.6%   91.0%   88.2%   42.6%   87.7%
   0.600   76.8%   85.2%   88.2%   40.0%   81.3%
   0.650   72.9%   81.3%   88.2%   38.0%   77.4%
```

0.40 and 0.45 tie on pass rate; 0.45 has the better citation precision. Above
0.50 citation recall collapses as genuine prose answers fall below the floor.

**`VECTOR_SPREAD` curve** (`--sweep vector-spread`) is flat on pass rate from
0.06 to 0.30 and monotonic on citation precision (0.06 → 40.9 %, 0.12 → 39.8 %,
0.30 → 37.6 %), so **0.06** is the lowest value at the pass-rate plateau.

### C — `ROUTE_MARGIN` 0.06 → 0.02 (+3.2 pass, +6.3 scope accuracy)

`--sweep margin`, at the final configuration:

```
   value    pass  engine   scope   cit P   cit R  over  under
   0.000   79.4%   88.4%   84.5%   40.7%   90.3%    12     0
   0.010   81.9%   88.4%   88.2%   40.1%   91.0%     6     2
   0.020   81.9%   88.4%   88.2%   39.8%   91.0%     6     3   <- chosen
   0.030   80.6%   88.4%   86.4%   39.9%   91.6%     6     6
   0.040   79.4%   89.0%   85.5%   39.0%   91.6%     5     8
   0.060   78.7%   90.3%   82.7%   38.9%   91.6%     5    11   <- was
   0.100   76.1%   90.3%   79.1%   36.2%   91.0%     4    16
   0.150   63.2%   90.3%   59.1%   30.5%   91.0%     2    40
   0.200   54.2%   90.3%   46.4%   26.4%   90.3%     0    56
```

0.01 and 0.02 are identical on every metric. **0.02 is chosen because it is the
smaller of the two that still catches the misroute the old docstring records**
(0.446 against 0.430, a gap of 0.016); 0.01 would let that one through. At 0.00
the test stops working: all 12 genuinely-ambiguous questions get a confidently
named scope.

0.06 was set from that single example, and at 0.446 it is a sensible gap. The
eval shows why one number cannot serve: correct routes here mostly score
0.55–0.85, and at 0.77 a runner-up 0.06 behind is not a rival. 16 questions
with exactly one right answer were being refused, including C-019 at 0.7729
against 0.7310.

### B — route index enrichment (+1.9 pass, but only once D exists)

Measured every way, because most of it made things worse.

At `LEXICAL_WEIGHT=0`, `ROUTE_MARGIN=0.02`:

| embedded row content | pass | engine | scope | cit P | under |
|---|---|---|---|---|---|
| clause text only (as found) | 69.7 | 80.0 | 82.7 | 34.5 | 4 |
| + scope row, output names only | 69.7 | 80.0 | 82.7 | 35.8 | 2 |
| + scope row, scope + outputs | 67.1 | 80.0 | 79.1 | 36.2 | 4 |
| + scope row, module + scope + outputs | 65.8 | 80.0 | 77.3 | 36.0 | 7 |
| + scope row, module/scope/title/outputs/inputs | 63.9 | 77.4 | 76.4 | 35.3 | 7 |
| + document title on clause rows | 63.9 | 74.2 | 81.8 | 31.6 | 5 |

At `LEXICAL_WEIGHT=0.20`, `ROUTE_MARGIN=0.02`:

| embedded row content | pass | engine | scope | cit P |
|---|---|---|---|---|
| clause text only | 74.8 | 84.5 | 81.8 | 39.0 |
| + scope row, output names only | **76.8** | **85.2** | **83.6** | 40.4 |
| + scope row, module + scope + outputs | 74.8 | 84.5 | 80.9 | 41.4 |
| + document title on clause rows | 74.8 | 83.9 | 82.7 | 39.0 |

**The brief's suggestion is right about where the vocabulary belongs and wrong
about which index should carry it.** Putting module names, scope names, input
names or the document title into the *embedded* text makes routing worse, and
the mechanism is visible in the `under` column: every scope signature is made
of the same business vocabulary ("Commission", "Service", "Credit",
"Availability"), so those tokens pull all scopes toward each other, the
runner-up closes on the leader, and the margin test refuses to name either.
The document title helps the prose store, where it distinguishes *whose*
governing law you retrieved, and hurts here, where RULE clauses are already
specific and all six titles share generic words.

Output variable names are the exception, because they are the one part of a
signature that names the thing the user asked for rather than the document it
lives in. Kept, and the *lexical* row (change D) carries the full signature,
where a rare token only affects the rows that actually contain it.

Independent of the score: two scopes, `Overtime.WeeklyOvertime` and
`Availability.ServiceAvailability`, encode no clause of their own (they
aggregate other scopes) and so had **no rows in the route index at all**. No
question could route to them at any threshold. The scope row fixes that; the
eval only partly credits it, because on the cases where they are the natural
answer the aggregating alternative is also an accepted label.

### D — lexical signal (+7.1 pass, +4.6 citation precision)

An IDF-weighted token overlap, blended with the dense score as
`(1−w)·cos + w·lex`. IDF is computed over the rows being searched, so the
weighting is derived rather than hand-tuned: a figure, a clause id or a rare
defined term dominates, and a token every clause contains contributes nothing,
with no stopword list to maintain. Numbers are kept as single tokens (`99.90%`,
`75`) because that is the whole point.

`--sweep lexical-weight`:

```
   value    pass  engine   scope   cit P   cit R  over
   0.000   76.1%   87.7%   84.5%   38.7%   85.2%     6
   0.050   78.1%   89.0%   85.5%   39.3%   87.7%     6
   0.100   78.1%   89.0%   85.5%   39.8%   89.0%     6
   0.150   81.9%   91.0%   86.4%   41.5%   89.7%     6
   0.200   84.5%   92.9%   88.2%   42.6%   90.3%     6   <- chosen
   0.250   84.5%   93.5%   87.3%   43.3%   90.3%     6
   0.300   81.9%   91.6%   84.5%   42.8%   89.7%     7
   0.400   79.4%   90.3%   81.8%   42.7%   87.1%     9
   0.600   76.8%   87.7%   80.9%   42.7%   85.2%     8
```

0.20 and 0.25 tie on pass rate; 0.20 has the better scope accuracy and one
fewer under-confident refusal, and is the smaller departure from the committed
dense index. Past 0.30 the sparse signal starts dominating and over-confident
routes climb.

The lexical index adds no artefact: it is derived from chunk text already in
the committed index, so there is nothing new to pin in `manifest.json` and no
new way for the index to go stale. `search(lexical_weight=0.0)` reproduces the
old dense-only ranking exactly, which is what made the blend measurable rather
than merely different.

It also fixes the exact misroute in the old `ROUTE_MARGIN` docstring:
"can the General Counsel stop a record being deleted" now ranks
`LegalHold.HoldEffect` **first** (0.3977 vs `Commission.LeaverCommission`
0.3627), because R-2.3 is the clause containing the words "General Counsel"
and a dense average cannot see that. It lands 0.0023 below `CATALA_THRESHOLD`
and so returns no-coverage — still not the right answer, but no longer a
confidently asserted wrong scope.

### E — co-encoder ambiguity detection (+2.0 pass, +2.7 scope, over-confident 9 → 6)

Measured by switching it off at the final configuration:

| | pass | engine | scope | cit P | over | under |
|---|---|---|---|---|---|---|
| on (`ROUTE_COENCODER_MARGIN=0.04`) | **83.9** | 92.9 | **88.2** | 57.3 | **6** | 3 |
| off (`--coencoder-margin 0.0`) | 81.9 | 92.9 | 85.5 | 58.0 | 9 | 3 |

(measured before change H, hence the 57.3.)

The insight the eval handed over: the questions labelled genuinely ambiguous
are not ambiguous because two scores happen to be close. They are ambiguous
because **the clause that put the leader on top is a clause the runner-up also
encodes** — `registry.encodes` says so. C-9.2's rounding proviso lives in
`LeaveAccrual.HalfDayRounding` and is applied by `LeaveAccrual.MonthlyAccrual`,
so a question about rounding matches identical text in both.

The gaps separate cleanly, which is why this earns a wider margin than the
plain thin-margin test:

| | co-encoder gap |
|---|---|
| A-001 … A-006 (labelled ambiguous) | 0.031, 0.018, 0.002, 0.025, 0.024, 0.000 |
| correctly-routed single-scope questions, plain runner-up gap | 0.070 and up |

`--sweep coencoder-margin` plateaus from 0.04 to 0.08 (81.9 % pass, 88.2 %
scope, 6 over-confident) and degrades below 0.03 and above 0.10. **0.04** is
the lowest value on the plateau.

Cost: it fires on C-046 and C-060, whose labels accept either co-encoding scope
without asking for candidates, so those two now count as under-confident. Six
ambiguous cases fixed against two single-scope cases lost.

### F — per-sub-question routing (+3.9 pass, +3.9 engine, BOTH 1/8 → 7/8)

`ARCHITECTURE.md` §4 requires the chat layer to "route per sub-question". It
never did: a compound question was routed as one string, so whichever engine
won the whole thing answered both halves. Measured by switching it off at the
final configuration:

| | pass | engine | cit R | BOTH cases |
|---|---|---|---|---|
| on | **83.9** | **92.9** | **89.0** | **7/8** |
| off | 80.0 | 89.0 | 85.8 | 1/8 |

The splitter is deliberately narrow: only a comma followed by a conjunction, a
semicolon, or a mid-string question mark counts as a boundary; both sides must
carry four words; never more than two parts. A bare "and" joins two halves of
one condition at least as often as two questions ("hours on a public holiday
and above 48 in the week"), and splitting that would route each half to a scope
that answers neither.

**A caveat on this evidence, stated because the number overstates the
confidence.** All eight BOTH cases in the eval happen to use ", and ". The eval
can therefore show this rule helping and cannot show what it costs on a
compound question phrased some other way. That is the argument for keeping the
rule narrow rather than clever; it is not an argument that the rule is
well-validated.

### G — adaptive supporting citations (+14.7 citation precision, −0.6 pass)

A route cites the scope's clauses that its rows matched best, capped at three.
`Overtime.HourPremium` encodes eleven clauses, so the cap silently dropped the
clause a question was actually about: "is the night premium added on top of the
public holiday rate" routes correctly and then cites C-4.1, C-5.1 and C-5.2
rather than C-8.2, which is the clause that answers it.

Raising the cap is not the fix — it buys recall with precision, one for one
(`--sweep support-citations`):

```
   value    pass   cit P   cit R      F1
       1   76.1%   70.8%   78.1%    74.3
       2   82.6%   49.8%   85.8%    63.0
       3   84.5%   42.6%   90.3%    57.9
       6   85.8%   33.5%   92.3%    49.2
      11   85.8%   29.6%   92.3%    44.9
```

Note that pass rate rises monotonically with the cap while citation F1 falls:
the pass criterion only requires *one* labelled clause to be cited, so it
rewards citing more. Pass rate is the wrong number to pick this value on, which
is why the cap was left at 3 rather than tuned to it.

Gating on score spread instead makes the support set adaptive — a question that
plainly matches one clause cites that clause alone — and improves both sides
(`--sweep support-spread`, cap 3):

```
   value    pass   cit P   cit R      F1
   0.000   76.1%   70.8%   78.1%    74.3
   0.040   80.0%   64.3%   82.6%    72.3
   0.060   81.3%   59.8%   84.5%    70.1
   0.080   81.9%   58.1%   85.2%    69.1
   0.100   83.9%   57.3%   89.0%    69.7   <- chosen
   0.150   83.9%   53.3%   89.7%    66.9
   0.200   84.5%   50.0%   90.3%    64.5
   1.000   84.5%   42.6%   90.3%    58.0   (no gate: the old behaviour)
```

**0.10** is the joint optimum: the highest citation F1 among values whose pass
rate is within one case of the maximum.

### `CATALA_THRESHOLD` — unchanged at 0.40, now measured

It was a hand-set number. `--sweep catala-threshold`:

```
   value    pass  engine   scope   cit P   cit R
   0.200   80.6%   90.3%   88.2%   39.0%   91.6%
   0.300   81.3%   91.0%   88.2%   39.8%   91.6%
   0.350   82.6%   91.0%   88.2%   41.5%   91.0%
   0.400   84.5%   92.9%   88.2%   42.6%   90.3%   <- peak, kept
   0.450   82.6%   91.0%   85.5%   42.8%   88.4%
   0.500   80.0%   88.4%   81.8%   43.0%   85.8%
   0.600   70.3%   76.1%   64.5%   44.2%   74.2%
   0.700   52.9%   54.2%   40.0%   46.4%   58.7%
```

A real trade-off rather than a free optimum. Lowering it recovers questions the
corpus does answer but whose clause the embedding matches weakly (C-015 ranks
its scope correctly at 0.388); raising it protects the twelve questions the
corpus does *not* answer, which sit semantically next to real clauses.

### H — citations for aggregate scopes (+0.9 citation precision, and a defect fixed)

Not a threshold. Found by reading an answer rather than a number: a route to
`Overtime.WeeklyOvertime` printed *"WeeklyOvertime decides it, from the clauses
cited below"* and then cited nothing at all, because an aggregate scope encodes
no clause of its own and the fallback was `entry.encodes[:4]` — also empty. An
answer that claims authority and names none is worse than the metrics could
show; no eval case caught it, because the two aggregate scopes are only ever an
accepted alternative in a scope list.

It now cites the clauses of the sibling scopes within its own module, ranked by
how well they match the question — restricted to the module because crossing
modules would attribute a rule to a unit that does not claim it, which
`registry.coverage()` already treats as a conflict.

The same change stopped the scope row being credited to `encodes[0]`. That row
matches on *names*, so attributing it to a clause cited an arbitrary provision
as the authority for a name match. It is now attributed to no clause, which
raised citation precision from 57.3 % to 58.2 %.

### I — a routed scope's own clauses are not quotable against it (+0.3 citation precision, and a documented example fixed)

Change A excluded from quotation the clauses the CATALA part had *cited*. That
is too narrow: what matters is the clauses the routed scope **encodes**. If the
scope encodes the clause, its content is reachable by executing the scope, and
quoting it instead is precisely the substitution the architecture forbids.

Found from the README's own flagship example, not from the eval: "what service
credit do we owe at 98.5% uptime?" routed correctly to
`ServiceCredits.ServiceCredit` at 0.5348 and was then answered by quoting
L-4.4 at 0.5491 — one of that scope's own HYBRID clauses, in both stores by
design, beating its own scope in the arbitration.

Also in this change: `lex_tokens` now normalises `98.50` to `98.5`, because a
figure written to different precision is the same figure and the old
tokenisation turned an exact key into a *penalty* — "98.5" appears nowhere in
the corpus, so it counted against the question's total IDF mass and dragged
down the clause it identifies. Measured exactly neutral on this eval set (no
labelled question happens to differ from its clause in trailing zeros); kept
because it is correct on the semantics and costs nothing.

### J — a quoted clause is not also shown as a caveat (neutral; removes a visible duplication)

Two paths can legitimately reach the same clause: L-1.2 qualifies every
`ServiceCredits` computation *and* answers "are the Service Credits the
Customer's only financial remedy?". A compound question hits both and rendered
the clause twice in one answer, once as a quotation and once as a caveat.

The quotation wins: a caveat says "this also bears on the figure above", a
quotation says "this answers what you asked", and having said the second the
first is only length. Filtering such clauses out of *retrieval* instead was
measured and cost 8.4 points — see the rejected changes below.

### K — one score scale for the arbitration (Mongo: 34.8 % → 83.9 %)

`ENGINE_MARGIN` compares a prose score against a route score directly, so a
store that returns a differently *scaled* score does not merely rank
differently — it invalidates the arbitration. `MongoVectorStore` is that case:
Atlas `$vectorSearch` reports `(1 + cosine) / 2`. Verified numerically rather
than assumed — "which law governs the mutual NDA?" returns N-9.1 at cosine
0.5070 from the committed index and 0.7535 from Atlas, and
`(1 + 0.5070) / 2 = 0.7535` exactly.

The consequence is that Atlas's floor is 0.5, so `VECTOR_THRESHOLD = 0.45`
admits *every chunk in the corpus* and the prose engine wins nearly every
arbitration:

| backend | pass | engine | scope | cit P | cit R |
|---|---|---|---|---|---|
| files, before | 83.9 | 92.9 | 88.2 | 58.5 | 89.0 |
| **mongo, before** | **34.8** | **35.5** | **42.7** | **21.7** | **59.4** |
| files, after | 83.9 | 92.9 | 88.2 | 58.5 | 89.0 |
| **mongo, after** | **83.9** | **92.9** | **88.2** | **58.5** | **89.0** |

This was latent before any of these changes — `VECTOR_THRESHOLD = 0.30` was
equally mis-scaled against a floor of 0.5 — but `ENGINE_MARGIN` made it decide
the answer, and `Chat.open(backend="auto")` prefers Mongo, so it was the
default path for `lks ask`.

Ranking now always goes through the committed file index, which
`scripts/check.sh` gate 7 proves matches both the corpus and the triage
decisions in this checkout. The injected store keeps everything it is
authoritative for: `qualifying()`, `by_ref()`, and the chunk text that gets
quoted. Rescaling Atlas's score back to cosine was the alternative, and was
rejected because the transform depends on the similarity function the search
index was created with, which the chat layer cannot see — and a wrong guess
there fails silently, which is the failure mode this whole system is built to
avoid.

## Four things tried and rejected by measurement

Recorded because a negative result is the part of an evaluation that stops the
same idea being tried again.

**Per-engine confidence instead of raw best score.** The arbitration compares
one engine's best score with the other's, and those scores come from rows with
different vocabulary and length, so they are not obviously calibrated against
each other. The alternative: favour the engine that is internally more
confident, scoring `best + k·(best − second)` on each side. Monotonically worse
(`--sweep confidence-bonus`): pass rate 83.9 % at k=0, 83.2 % at 0.5, 80.6 % at
1.0, 76.8 % at 3.0. The internal gap turns out to measure how many clauses of
one document resemble the question, not how well the best one answers it.

**Excluding a module's qualifying prose from quotation.** A clause whose triage
`qualifies` names the routed module already reaches the reader as a caveat, so
letting it also compete as a free-standing quotation looked like double
counting — and it does produce visible duplication, the same clause rendered
twice in one answer under two kinds. Suppressing it cost 8.4 points of pass
rate (83.9 % → 75.5 %) and 8.4 of citation recall. The reason is that the same
clause can be both a qualification of a module and the direct answer to a
question about it: L-1.2 qualifies `ServiceCredits` *and* is the whole answer to
"are the Service Credits the Customer's only financial remedy?" (case P-013).
Discounting it in the arbitration only, leaving it quotable, was no better
(75.5 %) for the same reason. The duplication is real and is fixed by
de-duplicating the assembled parts instead — change J.

**Enriching the embedded route rows with scope and module vocabulary.** Six
variants, all measured, all worse than clause text alone except output names;
the table is under change B. The brief suggested this direction and it is right
about *where* the vocabulary belongs and wrong about *which index* should carry
it: shared vocabulary in a dense average pulls every scope toward every other,
while in a sparse IDF index a shared token cannot.

**Carrying context into split sub-questions.** The trailing half of a compound
question often refers back with a pronoun — "is that credit our only remedy?"
loses the words "Service Credit" and retrieves N-8.1 ("damages alone may not be
an adequate remedy") instead of L-1.2 ("sole financial remedy"). Two repairs
were tried: appending the whole question to each part, and appending the first
part. Both were worse, and both specifically damaged the cases they were meant
to help — BOTH cases passing went 7/8 to 4/8 and 3/8. Re-adding the context
re-creates the single diffuse query that splitting exists to avoid. The
pronoun problem is real and needs coreference, not more query text.

## Every value, and where it sits on its curve

`$PY scripts/eval_routing.py --sweep <name>` for any row.

| constant | value | at its curve maximum? |
|---|---|---|
| `CATALA_THRESHOLD` | 0.40 | yes, sole maximum |
| `VECTOR_THRESHOLD` | 0.45 | yes (ties 0.40; 0.45 has better citation precision) |
| `ENGINE_MARGIN` | 0.04 | no — 1 case below the maximum at 0.00, by choice |
| `VECTOR_SPREAD` | 0.06 | yes (lowest of a wide plateau; best precision on it) |
| `ROUTE_MARGIN` | 0.02 | yes (ties 0.01; 0.02 catches the recorded misroute) |
| `ROUTE_COENCODER_MARGIN` | 0.04 | yes (lowest of the 0.04–0.08 plateau) |
| `SUPPORT_SPREAD` | 0.10 | no — 1 case below the maximum, best citation F1 |
| `SUPPORT_CITATIONS` | 3 | unchanged; pass rate is the wrong metric for it |
| `LEXICAL_WEIGHT` | 0.20 | yes (ties 0.25; 0.20 has better scope accuracy) |

The two deliberate departures from a maximum are each worth one case out of
155 and each buys something the pass rate does not measure: a band in which
both engines stay engaged rather than a coin-flip at 0.001 (`ENGINE_MARGIN`),
and citation precision that the pass criterion is too weak to reward
(`SUPPORT_SPREAD`).

## What still fails, and what the failures have in common

25 failures. They fall into five groups, and the first four are each one
mechanism rather than 25 separate problems.

**1. Ambiguity labels the router disagrees with (6 cases: A-007 … A-012).**
All over-confident: the router named one scope where the label asks for
candidates. Three of these labels I believe are wrong; see below. The other
three are real. A-010's two scopes are 0.035 apart. A-011 is the most
interesting failure in the set — the router picks
`WorkingTimeDefs.CriticalIncidentResponse` (0.609), the scope that *defines*
Critical Incident Response, over `MealPerDiem.MealPerDiemForDay` (0.463), the
scope that answers the per-diem question actually asked. Definition scopes win
on questions that merely *mention* the term they define. That is a systematic
bias worth a change, and it is not a threshold.

**2. The corpus is adjacent to the question but does not answer it (5 cases:
N-001, N-002, N-003, N-007, N-012).** The most stubborn group. "How many days
of paid sick leave does an employee get?" scores 0.541 against
`QuotaRelief.QualifyingAbsence`, because S-6.1 is about long-term sickness
absence — it just says nothing about sick pay. Same shape for parental leave
(S-6.1 again), notice on resignation (S-8.1 is about leavers), invoice interest
(L-4.4 mentions 30 days' arrears), and user counts (the MSA schedule in the
corpus is about availability). **The topic is present and the entitlement is
not**, and no similarity measure over clause text can see that difference:
every one of these would be fixed by lowering nothing and raising
`CATALA_THRESHOLD`, and the curve shows that costs more than it saves. Fixing
them needs a different signal — whether the *quantity the question asks for*
appears among any scope's outputs — not a better threshold.

**3. Just below the floor (4 cases: C-015, C-058, P-027, B-008).** No-coverage
where the corpus does answer. C-015's scope ranks correctly at 0.388 against a
0.40 floor; P-027's clause (N-9.2, variation in writing) scores 0.267 dense
because "exchange of emails" shares no vocabulary with "in writing and signed
by both parties"; B-008's second sub-question ("who decides if we disagree
about it") is too vague for either index. The same trade-off as group 2, from
the other side — which is why group 2 and group 3 cannot both be fixed by
moving `CATALA_THRESHOLD`.

**4. Co-encoder rule firing where the label accepts either scope (3 cases:
C-046, C-059, C-060).** Under-confident. C-046 and C-060 are the measured cost
of change E. C-059 is different and is a genuine regression risk introduced by
change B: "emergency maintenance was notified 30 minutes in advance, are those
minutes excluded?" now puts `Availability.ServiceAvailability` top at 0.622 —
an aggregate scope reachable only through its new scope row — ahead of
`Availability.ExcludedMinutes`, which is the scope that answers it. The scope
row buys reachability for aggregate scopes and pays for it by letting them
outrank the specific scope on a question about one limb.

**5. Right engine and scope, wrong clause (4 cases: C-011, C-074, J-001,
P-001).** Routing correct, the cited clause not the labelled one. C-011 wants
C-8.2 out of `Overtime.HourPremium`'s eleven clauses; C-074 wants R-2.3 out of
`LegalHold.HoldEffect`'s five; J-001 cites E-6.3 (the 120-day bar) where the
label wants E-6.2 (the 60-to-120-day certification) — adjacent clauses of one
exception chain. This is the residue of change G and the one group where more
citations would help and would cost precision.

**6. Three that do not fit a group (C-057, P-008, P-014).** C-057 "what is a
Measurement Period?" routes to `Availability.AvailabilityPercentage` instead of
`Availability.MeasurementPeriod` — the definition-scope bias of group 1, in the
opposite direction. P-008 and P-014 are prose questions where a rule scope also
matched within `ENGINE_MARGIN`, so both engines answered; in both the labelled
prose clause *was* quoted (E-8.1, L-1.3), and the failure is the extra CATALA
part, not a missing answer.

Two structural notes the eval surfaced that are not failures of the router:

* Six RULE clauses (`COMM-PLAN S-1.1`, `S-2.5`, `DATA-RET R-2.1`, `R-2.2`,
  `EXP-POL E-2.1`, `E-2.2`) appear in no scope's `encodes` — they are encoded
  as type declarations in a module prologue. Being RULE they are also absent
  from the quotable store. **No engine can answer a question about them**: "what
  is the Base Commission Rate?" (S-2.5, 8.0 %) has no route and no quotation.
  No eval case was written for these, because the honest label is unclear and a
  wrong label is worse than a missing case. It is a coverage hole in the
  artefacts, not in the router, and belongs in `docs/DOCUMENT-DEFECTS.md` or a
  registry change.
* `answer()` attaches 210 caveat parts across the 155 cases, all determined by
  triage's `qualifies` field rather than by routing. They are excluded from
  engine and citation scoring for that reason, and reported as their own count.

## Eval labels I believe are wrong

Recorded rather than changed, so the router is not being scored against a
moving target. Each is a case the router currently fails.

**A-007** — "What was the Availability Percentage for the month?", labelled
`ambiguous` between `Availability.AvailabilityPercentage` and
`Availability.ServiceAvailability`. I think this is not ambiguous.
`AvailabilityPercentage` encodes L-2.3, which *is* the definition of the
Availability Percentage, and outputs it directly. `ServiceAvailability` encodes
no clause at all; it is a wrapper that computes the same figure from raw
outages. Two scopes that produce the same value from different inputs are a
pipeline, not a pair of rival authorities, and naming the one that encodes the
defining clause is a correct answer rather than a guess. Correct label:
`engine: CATALA`, `scope: [Availability.AvailabilityPercentage,
Availability.ServiceAvailability]`, no `ambiguous`.

**A-008** — "What overtime is payable for hours worked above 40 in a payroll
week?", labelled `ambiguous` between `Overtime.HourPremium` and
`Overtime.WeeklyOvertime`. Same reasoning. C-4.1 states a per-hour
entitlement — "an overtime payment in respect of each hour worked in excess of
40 hours" — and `HourPremium` is the scope that encodes it;
`WeeklyOvertime` aggregates `HourPremium` over the hours of a week and encodes
no clause. The router's gap here is 0.189, not a tie.

**A-009** — "When is a record that is subject to a Legal Hold finally
deleted?", labelled `ambiguous` between `LegalHold.HoldEffect` and
`Retention.RetentionEnd`. R-4.3 is the clause that answers it ("on release of
a Legal Hold, the record is deleted within 30 days if its retention period has
already expired, and otherwise on the ordinary expiry of its retention
period"), and R-4.3 is encoded in `HoldEffect`, which outputs
`deletion_due_date` and takes `retention_end_date` as an *input*.
`Retention.RetentionEnd` computes that input. The registry states the
dependency; the question has one answering scope.

The common error in all three is mine: I treated "another scope is involved in
producing the answer" as ambiguity. It is not. Ambiguity is two scopes that
could each be *the* answer — which is exactly the co-encoding case change E
now detects, and A-001 … A-006 are the cases that really have it.

I am **not** claiming C-046, C-059 or C-060 are mislabelled. Those name two
acceptable scopes without asking for candidates, which is a coherent and
different statement, and the router reporting candidates there is a genuine if
minor loss.

## Note on the verification gate

`./scripts/check.sh` reports **`GATE: PASS`** with every change here in place.
The passing run is saved verbatim as `eval/gate-pass.txt`, timestamped after
the last edit to any file changed here (`src/lks/chat.py` 16:07:50,
`src/lks/vector.py` 16:03:11; `src/lks/registry.py` was not modified at all).

It flapped to `GATE: FAIL` twice while this work was finishing, in gates 2
(literate fidelity), 4 (`clerk test`) and 5 (counterexample regression), from
concurrent in-progress edits under `catala/modules/` and `catala/tests/` by
another worker — the diagnostics named only `retention.catala_en`,
`test_quota_relief.catala_en` and counterexamples over `QuotaRelief`,
`Retention` and `NdaSurvival`, and each failure appeared within seconds of one
of those files changing.

That attribution is checked, not asserted: the failing gates' code paths do not
load a single file changed here.

```
$ python -c "import lks.literate, lks.reviewer, lks.counterexample, lks.catala_runner"
lks modules loaded: catala_runner, counterexample, literate, model, reviewer, segment
  lks.chat:     not loaded
  lks.vector:   not loaded
  lks.registry: not loaded
```

Gate 7, the only gate that exercises `lks.vector`, passed throughout, on both
the file index and Mongo. `scripts/build_index.py` also reproduces
`manifest.json`, `embeddings.npy` and `chunks.jsonl` **byte-identically** after
these changes: the lexical index is derived at query time from chunk text
already in the committed index, so it adds no artefact, changes no pinned hash,
and cannot make the index disagree with the corpus or the triage decisions.
