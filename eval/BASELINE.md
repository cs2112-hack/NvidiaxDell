# Routing baseline — measured before any router change

Recorded so that every later change can be shown to be an improvement rather
than a different set of mistakes. Do not edit these numbers; add a new section
to `eval/RESULTS.md` instead.

```
commit state   src/lks/chat.py as found, plus the diagnostic `AnswerPart.candidates`
               field (no behavioural change)
command        $PY scripts/eval_routing.py
eval set       eval/routing.yaml, 155 cases
settings       CATALA_THRESHOLD=0.40  ROUTE_MARGIN=0.06  VECTOR_THRESHOLD=0.30
backend        files (the committed vector index)
```

## Headline

| metric | baseline |
|---|---|
| overall pass rate | **8.4 %** (13/155) |
| engine accuracy | **11.6 %** (18/155) |
| scope accuracy | **77.3 %** (85/110 cases with a labelled scope) |
| citation precision | **17.4 %** (tp 141, fp 669) |
| citation recall | **91.0 %** (fn 14) |

The gap between scope accuracy (77 %) and engine accuracy (12 %) is the whole
story of this baseline: the router picks the right *scope* most of the time and
then the answer is buried under prose the question never asked for.

## Per expected engine

| expected | n | engine accuracy | pass rate |
|---|---|---|---|
| CATALA | 102 | 4.9 % | 2.0 % |
| VECTOR | 33 | 12.1 % | 12.1 % |
| BOTH | 8 | 100.0 % | 75.0 % |
| NONE | 12 | 8.3 % | 8.3 % |

## Confusion matrix (rows expected, columns routed)

| | CATALA | VECTOR | BOTH | NONE | total |
|---|---|---|---|---|---|
| **CATALA** | 5 | 2 | **94** | 1 | 102 |
| **VECTOR** | . | 4 | **28** | 1 | 33 |
| **BOTH** | . | . | 8 | . | 8 |
| **NONE** | 1 | 3 | **7** | 1 | 12 |

`BOTH` scores 100 % engine accuracy only because the router answers almost
everything with both engines. 129 of 155 cases came back as BOTH; only 8 should
have. This is one hand-set number doing the damage: `VECTOR_THRESHOLD = 0.30`
admits any chunk scoring above 0.30, and on this static embedding model
unrelated prose routinely scores 0.31–0.50. So "What is the Base Hourly Rate on
a salary of 52000?" returns the correct scope at 0.8336 and then quotes C-1.1
(the Annex's scope-of-application clause) at 0.3863 and E-1.1 (the expense
policy's scope clause) at 0.3170. 669 of the 810 clause refs the system cited
across the run were not refs a correct answer needed.

## Ambiguity handling

| | baseline |
|---|---|
| cases labelled ambiguous | 12 |
| of those, named one scope anyway (over-confident) | 5 (41.7 %) |
| cases labelled with a single scope | 98 |
| of those, reported candidates instead (under-confident) | 16 (16.3 %) |

`ROUTE_MARGIN` is an **absolute** 0.06. Its docstring records the failure it was
built to prevent, at scores of 0.446 vs 0.430 — and at that level 0.06 is a
sensible gap. But most correct routes on this eval score 0.55–0.85, and at 0.77
a runner-up 0.06 behind is not a real rival. So the absolute margin mostly
misfires high: C-019 ("Is leave forfeited at the end of the leave year payable
on termination?") scored 0.7729 and was still refused as ambiguous.

## Judgement safety

| | baseline |
|---|---|
| judgement cases | 15 |
| **silently computed anyway** | **0** |
| judgement input named to the user | 11/15 |
| fully safe | 11/15 |

No invariant violation: the system never computed a judgement question. The
four misses are all routing failures upstream — the question never reached the
scope that would have named the input (J-007 got no coverage at all; J-011,
J-013 and A-010 stopped at an ambiguous route).

## Near-miss pairs

11 pairs, 1 passing (`DECIDES`). Four collapsed — both halves routed to the
same engine and scope, so the router cannot tell them apart at all:
`CHURN` (C-050/J-009), `LEAVE` (C-047/C-048), `SURVIVE` (C-066/J-005),
and by engine `PENSION`, `REMEDY`, `TIER1`, `PH`, `HOLD`, `NOTICE`, `DEADLINE`
all failed for other reasons.

## Pass rate by category

| category | baseline |
|---|---|
| computational | 2/76 (2.6 %) |
| prose | 4/33 (12.1 %) |
| judgement | 0/15 (0.0 %) |
| ambiguous | 0/12 (0.0 %) |
| no-coverage | 1/12 (8.3 %) |
| both | 6/8 (75.0 %) |
| definition | 1/10 (10.0 %) |
| prose-no-computation | 0/4 (0.0 %) |

## What the numbers say is worst, in order

1. **Prose padding.** 129/155 answers are BOTH. Not a routing decision at all:
   `answer()` appends every vector hit above 0.30 to any CATALA part of kind
   needs-input / error / ambiguous-route. Biggest single cause of both the
   engine-accuracy and the citation-precision failure.
2. **The absolute route margin.** 16 under-confident refusals at scores up to
   0.77, and 5 over-confident claims where candidates were required. A single
   absolute number cannot serve both the 0.44-vs-0.43 case the docstring
   records and the 0.77-vs-0.72 case this eval finds.
3. **Route index vocabulary.** The index embeds `section_title :: clause_body`
   only. Two scopes (`Overtime.WeeklyOvertime`, `Availability.ServiceAvailability`)
   have empty `encodes` and are therefore *unreachable by construction* — no
   question can ever route to them. Questions phrased in the vocabulary of the
   answer ("which grade applies", "when is the Service Available") miss: C-015
   and C-058 fell below `CATALA_THRESHOLD` entirely.
4. **No lexical signal.** A question naming "£75", "99.90 %", "Tier 1" or a
   clause id is handing over an exact key, and the router throws it away.
