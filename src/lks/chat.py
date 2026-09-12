"""Question answering over both engines, with the boundary kept visible.

The brief's constraint: answer from Catala by *executing* the scope, answer
from the vector store by *quoting* with citations, never blend the two
silently, and label which engine produced which part.

Three design consequences worth stating, because each rules out an easier
implementation that would look fine in a demo:

1. **Routing needs its own index.** To send a rule question to the right scope
   you must match the question against rule clause text -- but rule clauses are
   deliberately absent from the quotable vector store (see DECISIONS.md D-5),
   precisely so they cannot be quoted as prose. So routing gets a separate
   in-memory index whose payload is a *scope name*, never text. Nothing
   retrieved by the router is ever shown to the user.

2. **A rule answer without inputs is not an answer.** "How much overtime am I
   owed?" has no value until someone supplies hours and grade. The honest
   response is to name the scope that decides it and the inputs it requires,
   not to execute with invented defaults. `answer()` therefore returns a
   NEEDS_INPUT part rather than guessing, and executes only when the caller
   supplies them.

3. **Prose caveats travel with executed answers.** A computed figure that
   ignores C-1.2 ("where this Annex would produce a result less favourable
   than the statutory minimum, the statutory minimum prevails") is misleading
   even when the arithmetic is right. Prose clauses whose `qualifies` names the
   executed module are attached as a separate, VECTOR-labelled part -- adjacent
   to the number, never merged into it.

4. **Which engine answers is a comparison, not two thresholds.** Each engine's
   index reports how well it matches the question; the one that wins by
   ENGINE_MARGIN answers alone. Before this was measured, the rule was "if the
   CATALA part is anything but a clean computation, append every prose hit
   above 0.30", which produced a two-engine answer for 129 of the 155 cases in
   eval/routing.yaml when 8 were correct, and filled the other 121 with prose
   nobody asked for. It was not a routing decision at all.

Everything the router does with a number is measured against
**eval/routing.yaml** by `scripts/eval_routing.py`. eval/BASELINE.md records
where this module started; eval/RESULTS.md records each change, its measured
effect in isolation, and the sweep curve behind every value below. A threshold
in this file without a curve behind it is a bug.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from .catala_runner import (
    AssertionFailed,
    CatalaError,
    NoApplicableRule,
    ScopeConflict,
    run_scope,
)
from .registry import ScopeEntry, build_registry, load_registry
from .segment import load_corpus
from .triage import Label, load_ledger
from . import vector as _vector
from .vector import Chunk, Hit, LexicalIndex, VectorStore, _load_model


class Engine:
    CATALA = "CATALA"
    VECTOR = "VECTOR"
    NONE = "NONE"
    MODEL = "MODEL"
    """The local model's reading of the closest clauses. Never produced by
    `Chat.answer`, which only computes or quotes: `lks.api.ep_ask` adds it after
    a no-coverage answer, as a separate part labelled as the model's reading."""


@lru_cache(maxsize=2048)
def _embed_query(question: str) -> np.ndarray:
    """The question as a unit vector, cached.

    Cached because the router, the caveat ranker and any threshold sweep all
    embed the same question string, and the model is static so the vector can
    never differ between calls. The array is marked read-only: a cached vector
    handed out by reference must not be mutable by a caller.
    """
    q = np.asarray(_load_model().encode([question]), dtype=np.float32)[0]
    n = float(np.linalg.norm(q))
    if n:
        q = q / n
    q.setflags(write=False)
    return q


@dataclass
class AnswerPart:
    engine: str
    kind: str                 # "computed" | "needs-input" | "quotation" | "caveat" | "no-coverage" | "error" | "general" | "general-failed"
    text: str
    citations: list[str] = field(default_factory=list)
    scope: str | None = None
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    score: float | None = None
    reported: list[str] = field(default_factory=list)
    """On an `ambiguous-route` part, the scopes actually named to the user."""

    candidates: list[tuple[str, float]] = field(default_factory=list)
    """Scopes the router considered, best first, with their scores.

    Populated on every CATALA part, not only the ambiguous one. It exists so
    that a routing evaluation (`scripts/eval_routing.py`) can read the router's
    decision structurally instead of scraping the rendered prose, and so that a
    thin-margin answer can be checked against the candidates it actually named.
    It is diagnostic only: the rendered answer still shows candidates solely in
    the `ambiguous-route` case, because listing runners-up beside a confident
    answer invites the reader to treat a rejected scope as authority.
    """

    def render(self) -> str:
        head = f"[{self.engine} — {self.kind}"
        if self.scope:
            head += f": executed {self.scope}" if self.kind == "computed" else f": {self.scope}"
        head += "]"
        body = self.text
        if self.citations:
            body += "\n    cites: " + ", ".join(self.citations)
        return f"{head}\n{body}"


@dataclass
class Answer:
    question: str
    parts: list[AnswerPart] = field(default_factory=list)

    @property
    def engines_used(self) -> list[str]:
        seen: list[str] = []
        for p in self.parts:
            if p.engine not in seen:
                seen.append(p.engine)
        return seen

    def render(self) -> str:
        out = [f"Q: {self.question}", ""]
        for p in self.parts:
            out.append(p.render())
            out.append("")
        if len(self.engines_used) > 1:
            out.append(
                "Note: this answer combines two engines. The CATALA parts were "
                "computed by executing the named scope; the VECTOR parts are "
                "verbatim quotations. They are reported separately and have not "
                "been merged."
            )
        return "\n".join(out).rstrip() + "\n"


# --- routing ---------------------------------------------------------------


def _words(identifier: str) -> str:
    """`ServiceCreditClaim` -> `Service Credit Claim`; `credit_amount` -> `credit amount`.

    Scope, module and variable names are the vocabulary of the *answer*, and a
    user asks in that vocabulary ("what service credit do we owe") rather than
    in the clause's ("the Customer is entitled to a Service Credit calculated
    as a percentage of..."). Feeding the identifiers to a word-level embedding
    model requires splitting them into words first: `CreditClaim` is one
    unknown token, `Credit Claim` is two known ones.
    """
    out = re.sub(r"[_.]+", " ", identifier)
    out = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", out)
    out = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", out)
    return re.sub(r"\s+", " ", out).strip()


@dataclass
class RouteIndex:
    """Maps a question to candidate Catala scopes.

    Built over the text of the RULE and HYBRID clauses each scope encodes,
    plus, for each scope, one synthetic row naming the scope itself. The
    payload is the scope key; nothing in this index is ever returned for
    display, so it cannot become a back door for quoting rule text as prose.

    Rows, per scope:

    1. **Clause rows** -- one per RULE/HYBRID clause, as `section_title ::
       clause_body`, exactly as the document words it. Adding the document
       title here measured worse, unlike in the prose store where
       `Chunk.embed_text` needs it: RULE clauses are already specific, and all
       six corpus titles share the same generic business words.

    2. **One scope row** -- the scope's output variable names, split into
       words ("computes credit percentage, credit amount"). A user asks for the
       thing they want computed, and that phrase is in the scope's signature,
       not in the clause text. Only the *outputs* go in: module names, scope
       names, input names and document titles all measured worse here (see
       eval/RESULTS.md, change B), because they are shared vocabulary that
       pulls every scope toward every other. The full signature does go into
       the lexical rows, where a shared token cannot do that damage.

       This row is also the only reason two scopes in this registry are
       reachable at all. `Overtime.WeeklyOvertime` and
       `Availability.ServiceAvailability` encode no clause of their own -- they
       aggregate other scopes -- so before it existed no question could route
       to them at any threshold.

    A scope's score is the max over its rows, so the scope row can only raise a
    scope's score; it never suppresses a clause match. Its cost is visible in
    eval case C-059: an aggregate scope can now outrank the specific scope on a
    question about one limb of a rule.
    """

    keys: list[str]
    refs: list[str]
    vectors: np.ndarray
    lexical: LexicalIndex | None = None
    """Sparse companion to `vectors`, over a deliberately *richer* row text.

    The dense row is the clause as the document words it, because measurement
    showed that adding scope and module names to the embedded text made routing
    worse: every scope signature shares the same business vocabulary, so it
    compressed the score gaps and drove the router into reporting candidates
    (see eval/RESULTS.md, change B). The same names are pure gain on the
    lexical side, where a rare token contributes only to the rows that actually
    contain it -- "which *grade* applies" matches `GradeForPayrollWeek` on the
    token, not on a topic.
    """

    @classmethod
    def build(cls, registry: dict[str, ScopeEntry] | None = None) -> "RouteIndex":
        reg = registry if registry is not None else (load_registry() or build_registry())
        ledger = load_ledger()
        clauses = {c.ref: c for d in load_corpus("corpus") for c in d.clauses}

        texts: list[str] = []
        lex_texts: list[str] = []
        keys: list[str] = []
        refs: list[str] = []
        for key, e in sorted(reg.items()):
            signature = " ".join([
                _words(e.module), _words(e.scope),
                " ".join(_words(o) for o in e.outputs),
                " ".join(_words(i) for i in e.inputs),
                " ".join(e.law_headings),
            ])
            for ref in e.encodes:
                d = ledger.get(ref)
                if d is None or d.label is Label.PROSE:
                    continue
                c = clauses.get(ref)
                if c is None:
                    continue
                texts.append(f"{c.section_title} :: {c.body}")
                lex_texts.append(f"{ref} {signature} {c.section_title} {c.body}")
                keys.append(key)
                refs.append(ref)

            # The scope row: what this scope answers, in the words of its own
            # signature rather than of the clauses behind it. Outputs only --
            # see the class docstring for what else was tried and measured.
            sig = ("computes " + ", ".join(_words(o) for o in e.outputs)
                   if e.outputs else "")
            if sig.strip():
                texts.append(sig)
                lex_texts.append(f"{signature} {' '.join(e.encodes)}")
                keys.append(key)
                # The scope row supports no particular clause, so it is
                # attributed to none: crediting it to `encodes[0]` would cite an
                # arbitrary clause as the authority for a name match.
                refs.append("")

        if not texts:
            return cls(keys=[], refs=[], vectors=np.zeros((0, 1), dtype=np.float32))
        v = np.asarray(_load_model().encode(texts), dtype=np.float32)
        n = np.linalg.norm(v, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return cls(keys=keys, refs=refs, vectors=v / n,
                   lexical=LexicalIndex(lex_texts))

    def candidates(self, question: str, k: int = 3) -> list[tuple[str, float, list[str]]]:
        """(scope_key, best_score, supporting_clause_refs), best first."""
        return self._ranked(question, k)

    def best_refs(self, question: str, allowed: set[str], k: int) -> list[str]:
        """The `allowed` clause refs this question matches best, best first.

        Used for an aggregate scope, which encodes no clause of its own and so
        has nothing to cite. `Overtime.WeeklyOvertime` sums the per-hour
        premiums that `Overtime.HourPremium` computes; its authority is that
        module's clauses, and which of them to cite depends on the question.
        Without this, a route to an aggregate scope printed "from the clauses
        cited below" and then cited nothing.
        """
        if not self.keys or not allowed:
            return []
        q = _embed_query(question)
        scores = self.vectors @ q
        w = _vector.LEXICAL_WEIGHT
        if w and self.lexical is not None:
            scores = (1.0 - w) * scores + w * self.lexical.score(question)
        best: dict[str, float] = {}
        for i, ref in enumerate(self.refs):
            if ref in allowed and float(scores[i]) > best.get(ref, -1.0):
                best[ref] = float(scores[i])
        return [r for r, _ in sorted(best.items(), key=lambda kv: -kv[1])[:k]]

    def _ranked(self, question: str, k: int = 3) -> list[tuple[str, float, list[str]]]:
        if not self.keys:
            return []
        q = _embed_query(question)
        scores = self.vectors @ q
        w = _vector.LEXICAL_WEIGHT
        if w and self.lexical is not None:
            scores = (1.0 - w) * scores + w * self.lexical.score(question)
        best: dict[str, float] = {}
        support: dict[str, list[tuple[float, str]]] = {}
        for i, key in enumerate(self.keys):
            s = float(scores[i])
            if s > best.get(key, -1.0):
                best[key] = s
            support.setdefault(key, []).append((s, self.refs[i]))
        ranked = sorted(best.items(), key=lambda kv: -kv[1])[:k]
        out = []
        for key, s in ranked:
            ranked_sup = sorted(support[key], key=lambda t: -t[0])
            top = ranked_sup[0][0] if ranked_sup else 0.0
            sup = [
                r for s2, r in ranked_sup[:SUPPORT_CITATIONS]
                if r and top - s2 <= SUPPORT_SPREAD
            ]
            out.append((key, s, sup))
        return out


# --- the answerer ----------------------------------------------------------

CATALA_THRESHOLD = 0.40
"""Floor below which the rule index has not matched the question at all.

Unchanged in value, but it was a hand-set number and is now **measured against
eval/routing.yaml** (sweep `catala-threshold` in eval/RESULTS.md), where 0.40
is the peak of the curve: pass rate 0.30 -> 81.3%, 0.40 -> 83.9%, 0.50 -> 80.0%.

The curve has a real trade-off in it rather than a free optimum. Lowering the
floor recovers questions the corpus does answer but whose clause the embedding
matches weakly ("which grade applies if an employee is regraded mid-week?" ranks
its scope correctly but at 0.388); raising it protects the twelve eval questions
the corpus does *not* answer, which are semantically adjacent to real clauses
("how many days of paid sick leave?" against C-9.4's long-term sickness). 0.40
is where those two costs balance.
"""

VECTOR_THRESHOLD = 0.45
"""Floor below which a prose chunk is not a quotable answer.

Was 0.30. Measured against eval/routing.yaml (sweep `vector-threshold`): at
0.30 this static model admits unrelated prose -- "What is the Base Hourly Rate
on a salary of 52000?" came back with the Annex's scope-of-application clause
at 0.386 and the expense policy's at 0.317 quoted beside it. 669 of the 810
clause refs cited across the baseline run were refs no correct answer needed.
See eval/RESULTS.md for the curve and why the chosen value is not simply the
curve's maximum.
"""

ENGINE_MARGIN = 0.04
"""How far one engine's best match must beat the other's to answer alone.

This is the router's main decision, and it replaces the previous rule -- "if
the CATALA part is anything other than a clean computation, append every vector
hit" -- which was not a routing decision at all and produced BOTH for 129 of
155 eval cases when 8 were correct.

The signal it uses was measured, not guessed. On eval/routing.yaml the
difference `best route score - best prose score` separates the two engines far
better than either absolute score does:

    expected engine   n    p10      median   p90
    CATALA          102   +0.043   +0.226   +0.378
    VECTOR           33   -0.300   -0.194   -0.051

A rule question matches the rule index better than any prose clause, and a
prose question the reverse, by a wide and consistent margin. So: whichever
engine wins by at least ENGINE_MARGIN answers alone; if neither wins by that
much, both are engaged and both are reported, separately labelled, because a
question that matches both indexes about equally well genuinely may have a part
for each. Neither clearing its floor is the no-coverage case.

Note that this is a margin between ENGINES, not between scopes. ROUTE_MARGIN
below is the within-CATALA test and does a different job.
"""

VECTOR_SPREAD = 0.06
"""How far behind the best prose hit a further quotation may be and still show.

The store returns a ranked list; quoting three clauses when only the first
answers the question is the citation-precision half of the padding problem.
Additional quotations must be within this of the best hit, so a clear single
answer is quoted alone and a genuine tie quotes both. Measured against
eval/routing.yaml; see eval/RESULTS.md.
"""

ROUTE_MARGIN = 0.02
"""How far the best-matching scope must beat the runner-up before the router
will name it.

Absolute score alone is a bad test. The failure this exists to prevent was a
*wrong* route scraping in at 0.446 against 0.430 -- sending "can the General
Counsel stop a record being deleted" to the sales-commission leaver scope, one
place above the legal-hold scope that actually answers it. Both scores clear
any threshold you could set without also excluding good routes, so the margin
is what separates them. When the margin is thin the honest output is the
candidate list, not a confident wrong scope: a legal answer attributed to the
wrong rule is worse than an answer that says which rules might govern.

Was 0.06, set from that one example. Now **0.02, measured against
eval/routing.yaml** (155 labelled cases; sweep `margin` in eval/RESULTS.md).
0.06 was far too wide: most correct routes on the eval score 0.55-0.85, and at
0.77 a runner-up 0.06 behind is not a rival, so the margin was refusing to
name a scope on 16 questions that had exactly one right answer -- including
"is leave forfeited at the end of the leave year payable on termination?" at
0.7729 against 0.7310. Scope accuracy over the curve: 0.02 -> 88.2%,
0.06 -> 82.7%, 0.12 -> 71.8%. 0.01 scores identically to 0.02 on every metric;
0.02 is chosen because it is the smaller value that still catches the 0.016 gap
of the recorded misroute above, and 0.01 does not.

Below 0.02 the test stops doing its job: at 0.00 every one of the eval's 12
genuinely-ambiguous questions gets a confidently-named scope.

Two notes on how this interacts with the rest of the router:
  * The recorded example is no longer a near-miss at all. With the lexical
    signal (`lks.vector.LEXICAL_WEIGHT`) the legal-hold scope now ranks *first*
    on that question, at 0.3977 against 0.3627, because R-2.3 is the clause
    that contains the words "General Counsel". It falls below
    CATALA_THRESHOLD and so returns no-coverage rather than a route -- still
    not the right answer, but no longer the wrong scope asserted confidently.
  * `Chat._indistinguishable` applies a second, wider test for the structural
    case where two scopes encode the same clause. That is what keeps the
    ambiguous questions ambiguous while this margin stays narrow.
"""

SUPPORT_SPREAD = 0.10
"""How far behind the best-matching clause a supporting citation may be.

Makes the support set adaptive rather than a fixed count: a question that
plainly matches one clause cites that clause alone, and a question that matches
three of a scope's clauses equally well cites all three. Preferred to simply
raising SUPPORT_CITATIONS because a bigger fixed cap buys citation recall with
citation precision, one for one, whereas the spread gate improves both --
measured; see eval/RESULTS.md, change G.
"""

SUPPORT_CITATIONS = 3
"""Upper bound on the support set that SUPPORT_SPREAD selects.

A scope's route score is the best of its clause rows; these are the next-best
rows, and they are what the answer cites as the law it rests on.
`Overtime.HourPremium` encodes eleven clauses, so a cap of three silently drops
the clause a question was actually about -- "is the night premium added on top
of the public holiday rate" routes correctly and then cites C-4.1, C-5.1 and
C-5.2 rather than C-8.2, which is the clause that answers it. Swept against
eval/routing.yaml; see eval/RESULTS.md, change G.
"""

ROUTE_COENCODER_MARGIN = 0.04
"""Margin for the co-encoding ambiguity test in `Chat._indistinguishable`.

Wider than ROUTE_MARGIN because the evidence is stronger: the registry says
both scopes encode the clause the question matched. Measured against
eval/routing.yaml; see eval/RESULTS.md, change E.
"""

MAX_QUOTATIONS = 3
"""Hard cap on prose quotations in one answer, after VECTOR_SPREAD has cut.

The spread gate does the real work; this only stops a pathological tie from
burying the answer.
"""

MAX_CAVEATS = 3
"""Cap on prose caveats attached to one answer.

Uncapped, an executed NdaSurvival answer arrived with eleven caveats -- every
prose clause in the agreement names that module in its `qualifies`, and rightly
so, but eleven blockquotes around one date is not an answer anyone reads. The
caveats shown are the ones closest to the question asked.
"""


class Chat:
    def __init__(
        self,
        store: Any | None = None,
        registry: dict[str, ScopeEntry] | None = None,
        route: RouteIndex | None = None,
    ):
        self.registry = registry if registry is not None else (load_registry() or build_registry())
        self.store = store
        self.route = route if route is not None else RouteIndex.build(self.registry)
        self._caveat_cache: dict[str, tuple[int, np.ndarray]] = {}
        self._ranker = self._pick_ranker(store)

    @staticmethod
    def _pick_ranker(store: Any) -> Any:
        """The store whose scores this layer is allowed to compare.

        Every constant in this module is a cosine similarity from the committed
        static index, and ENGINE_MARGIN compares a prose score against a route
        score directly. A store that returns a differently *scaled* score
        therefore does not merely rank differently -- it silently invalidates
        the arbitration.

        `MongoVectorStore` is exactly that case. Atlas `$vectorSearch` reports
        `(1 + cosine) / 2`, verified numerically here: "which law governs the
        mutual NDA?" returns N-9.1 at cosine 0.5070 from the committed index
        and 0.7535 from Atlas. Its floor is 0.5, so a VECTOR_THRESHOLD of 0.45
        admits every chunk in the corpus and the prose engine wins almost every
        arbitration. Measured over eval/routing.yaml: 83.9 % pass on the
        committed index, 34.8 % through Mongo, with identical code and
        identical chunks.

        So ranking always goes through the committed index, which
        `scripts/check.sh` gate 7 proves matches both the corpus and the triage
        decisions in this checkout. The injected store is still used for
        everything it is authoritative for -- `qualifying()`, `by_ref()`, and
        the chunk text that is quoted -- so the Mongo path keeps its role
        without also setting the router's calibration. Rescaling Atlas's score
        back to cosine was the alternative; it was rejected because the
        transform depends on the similarity function the search index was
        created with, which this layer cannot see, and a wrong guess there
        fails silently.
        """
        if store is None or isinstance(store, VectorStore):
            return store
        try:
            return VectorStore.open()
        except Exception:
            # No committed index to calibrate against. Rank with what we have
            # rather than refusing to answer; the scores may be on another
            # scale, and that is strictly better than nothing.
            return store

    @classmethod
    def open(cls, backend: str = "auto") -> "Chat":
        """`backend`: 'mongo', 'files', or 'auto' (mongo, falling back to files)."""
        store = None
        if backend in ("mongo", "auto"):
            try:
                from .mongo_store import MongoVectorStore
                store = MongoVectorStore.open()
            except Exception:
                if backend == "mongo":
                    raise
        if store is None:
            store = VectorStore.open()
        return cls(store=store)

    # -- CATALA side --------------------------------------------------------

    def _catala_part(
        self, question: str, inputs: dict[str, Any] | None, scope_hint: str | None
    ) -> AnswerPart | None:
        cands = self.route.candidates(question, k=4)
        if scope_hint:
            cands = [c for c in cands if c[0] == scope_hint] or [(scope_hint, 1.0, [])]
        part = self._route_to_part(question, inputs, scope_hint, cands)
        if part is not None and not part.candidates:
            part.candidates = [(k, round(s, 4)) for k, s, _ in cands]
        return part

    def _route_to_part(
        self,
        question: str,
        inputs: dict[str, Any] | None,
        scope_hint: str | None,
        cands: list[tuple[str, float, list[str]]],
    ) -> AnswerPart | None:
        if not cands:
            return None
        key, score, support = cands[0]
        if score < CATALA_THRESHOLD and not scope_hint:
            return None

        # Thin margin, or a clause two scopes both encode: name the candidates
        # instead of asserting one of them.
        close = self._indistinguishable(cands) if not scope_hint else []
        if len(close) > 1:
            lines = [
                f"      {k2}  (from {', '.join(sup2[:2])})" for k2, _s2, sup2 in close
            ]
            return AnswerPart(
                engine=Engine.CATALA, kind="ambiguous-route",
                text=(
                    "This looks like a rule question, but more than one rule module "
                    "matches it about equally well, so naming one would be a guess:\n"
                    + "\n".join(lines)
                    + "\n    Ask again naming the module, or supply its inputs, and "
                    "that scope will be executed. Nothing has been computed."
                ),
                citations=sorted({r for _k, _s, sup2 in close for r in sup2[:2]}),
                score=score,
                reported=[k2 for k2, _s2, _ in close],
            )

        entry = self.registry.get(key)
        if entry is None:
            return None
        cites = support or entry.encodes[:SUPPORT_CITATIONS] or self._module_refs(
            question, entry
        )

        required = list(entry.inputs)
        supplied = dict(inputs or {})
        missing = [i for i in required if i not in supplied]

        if missing:
            ji = [i for i in missing if i in entry.judgement_inputs]
            text = (
                f"This is a rule question. {key} decides it, from the clauses cited "
                f"below. It cannot be answered without values for: "
                f"{', '.join(missing)}."
            )
            if ji:
                text += (
                    f"\n    Of these, {', '.join(ji)} "
                    f"{'is' if len(ji) == 1 else 'are'} a judgement the documents "
                    f"do not define and the system will not decide: a human must "
                    f"supply it."
                )
            text += (
                "\n    No value is being guessed. Supply the inputs and the scope "
                "will be executed."
            )
            return AnswerPart(
                engine=Engine.CATALA, kind="needs-input", text=text,
                citations=cites, scope=key,
                inputs={"required": required, "missing": missing}, score=score,
            )

        try:
            outputs = run_scope(entry.path, entry.scope, {k2: supplied[k2] for k2 in required})
        except ScopeConflict as e:
            return AnswerPart(
                engine=Engine.CATALA, kind="error",
                text=(
                    "The rules conflict on these facts: two or more definitions "
                    "apply and the documents establish no priority between them. "
                    "This is a genuine gap in the source, not a computation "
                    "failure, and it needs a human decision.\n    "
                    + e.diagnostic[:600]
                ),
                citations=cites, scope=key, inputs=supplied, score=score,
            )
        except NoApplicableRule as e:
            return AnswerPart(
                engine=Engine.CATALA, kind="error",
                text=(
                    "No rule applies to these facts, so the documents do not "
                    "determine an answer here.\n    " + e.diagnostic[:600]
                ),
                citations=cites, scope=key, inputs=supplied, score=score,
            )
        except AssertionFailed as e:
            return AnswerPart(
                engine=Engine.CATALA, kind="refused",
                text=(
                    "These facts cannot arise under the documents, so no answer is "
                    "given. The module declines rather than computing a figure from "
                    "an impossible premise.\n    " + e.diagnostic[:600]
                ),
                citations=cites, scope=key, inputs=supplied, score=score,
            )
        except CatalaError as e:
            return AnswerPart(
                engine=Engine.CATALA, kind="error",
                text="The scope could not be executed:\n    " + e.diagnostic[:600],
                citations=cites, scope=key, inputs=supplied, score=score,
            )

        rendered = ", ".join(f"{k2} = {_fmt(v)}" for k2, v in sorted(outputs.items()))
        return AnswerPart(
            engine=Engine.CATALA, kind="computed",
            text=f"{rendered}\n    Computed by executing {key} on the supplied facts.",
            citations=cites, scope=key,
            inputs=supplied, outputs=outputs, score=score,
        )

    # -- VECTOR side --------------------------------------------------------

    def _vector_hits(self, question: str, exclude: set[str], k: int = 6) -> list[Hit]:
        """Quotable prose for this question, best first.

        `exclude` drops clauses the CATALA part has already cited. That is not
        cosmetic de-duplication: a HYBRID clause sits in *both* stores by
        design, so a judgement question matches the rule index and the prose
        index at almost the same score. Quoting the very clause the rule route
        is already citing would report one clause twice under two different
        engine labels -- the clearest possible way to make the boundary between
        the engines look like an accident. It also skews the engine margin,
        because the competing hit is not a competitor at all.
        """
        if self._ranker is None:
            return []
        hits: list[Hit] = self._ranker.search(question, k=k, min_score=VECTOR_THRESHOLD)
        hits = [h for h in hits if h.chunk.ref not in exclude]
        if not hits:
            return []
        best = hits[0].score
        return [h for h in hits if best - h.score <= VECTOR_SPREAD][:MAX_QUOTATIONS]

    @staticmethod
    def _quotation_parts(hits: list[Hit]) -> list[AnswerPart]:
        return [
            AnswerPart(
                engine=Engine.VECTOR, kind="quotation",
                text=f"“{h.chunk.text}”",
                citations=[h.chunk.cite()], score=h.score,
            )
            for h in hits
        ]

    def _caveat_matrix(self, module: str, chunks: list[Chunk]) -> np.ndarray:
        """Row-normalised embeddings of a module's qualifying prose, cached.

        The set of clauses qualifying a module is fixed by triage, so these
        vectors are the same for every question. Re-encoding them per question
        was the single most expensive thing the chat layer did.
        """
        cached = self._caveat_cache.get(module)
        if cached is not None and cached[0] == len(chunks):
            return cached[1]
        m = np.asarray(
            _load_model().encode([c.embed_text() for c in chunks]), dtype=np.float32
        )
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        m = m / norms
        self._caveat_cache[module] = (len(chunks), m)
        return m

    def _caveats(self, module: str, question: str) -> list[AnswerPart]:
        """Prose clauses qualifying `module`, closest to the question first.

        Capped at MAX_CAVEATS. Every prose clause of an agreement may
        legitimately qualify its one rule module -- an executed NdaSurvival
        answer arrived with eleven of them -- and showing all of them buries
        the answer the caveats are meant to qualify.
        """
        if self.store is None:
            return []
        chunks = self.store.qualifying(module)
        if not chunks:
            return []
        if len(chunks) > MAX_CAVEATS:
            q = _embed_query(question)
            m = self._caveat_matrix(module, chunks)
            scores = m @ q
            order = np.argsort(-scores)[:MAX_CAVEATS]
            chosen = [(chunks[int(i)], float(scores[int(i)])) for i in order]
        else:
            chosen = [(c, None) for c in chunks]

        out = []
        for c, sc in chosen:
            out.append(
                AnswerPart(
                    engine=Engine.VECTOR,
                    kind="caveat",
                    text=(
                        f"This prose clause qualifies {module} and is not part of "
                        f"the computation above:\n    \u201c{c.text}\u201d"
                    ),
                    citations=[c.cite()],
                    score=sc,
                )
            )
        if len(chunks) > MAX_CAVEATS:
            out.append(
                AnswerPart(
                    engine=Engine.VECTOR,
                    kind="caveat",
                    text=(
                        f"{len(chunks) - MAX_CAVEATS} further prose clause(s) also "
                        f"qualify {module} and are not shown."
                    ),
                )
            )
        return out


    def _module_refs(self, question: str, entry: ScopeEntry) -> list[str]:
        """Clauses to cite for a scope that encodes none itself.

        An aggregate scope's authority is the clauses of the sibling scopes it
        composes, within its own module. Restricted to the module because
        crossing modules would attribute a rule to a unit that does not claim
        it -- `registry.coverage()` treats a clause encoded in two modules as a
        conflict for exactly that reason.
        """
        siblings = {
            ref
            for other in self.registry.values()
            if other.module == entry.module
            for ref in other.encodes
        }
        return self.route.best_refs(question, siblings, SUPPORT_CITATIONS)

    def _indistinguishable(
        self, cands: list[tuple[str, float, list[str]]]
    ) -> list[tuple[str, float, list[str]]]:
        """Candidates the router cannot honestly choose between.

        Two tests, because there are two ways a route is a guess.

        **Thin margin** (ROUTE_MARGIN): the runner-up scored almost as well, so
        the ranking is noise.

        **Co-encoding** (ROUTE_COENCODER_MARGIN): the clause that put the
        leader on top is a clause the runner-up *also* encodes, according to
        `registry.encodes`. Then the match says nothing about which scope was
        meant, because both scopes really do encode the rule the question
        matched -- C-9.2's rounding proviso lives in `LeaveAccrual.HalfDayRounding`
        and is applied by `LeaveAccrual.MonthlyAccrual`, and a question about
        rounding matches the identical text in both. This is a fact from the
        registry rather than an accident of scoring, so it earns a wider margin
        than the plain thin-margin test: measured on eval/routing.yaml, the
        co-encoder gap on genuinely ambiguous questions runs 0.000 to 0.031
        while the plain runner-up gap on correctly-routed questions is 0.07 and
        up. `registry.coverage()` documents why a clause legitimately appears
        in two scopes of one module; this is the chat layer honouring that.
        """
        if not cands:
            return []
        key, score, support = cands[0]
        close = [c for c in cands if score - c[1] < ROUTE_MARGIN]
        top_ref = support[0] if support else None
        if top_ref:
            for cand in cands[1:]:
                if cand in close or score - cand[1] >= ROUTE_COENCODER_MARGIN:
                    continue
                other = self.registry.get(cand[0])
                if other is not None and top_ref in other.encodes:
                    close.append(cand)
        return sorted(close, key=lambda c: -c[1])

    # -- arbitration between the two engines --------------------------------

    def _parts_for(
        self,
        question: str,
        inputs: dict[str, Any] | None,
        scope: str | None,
        with_caveats: bool,
    ) -> list[AnswerPart]:
        """Decide which engines answer this question, and assemble their parts.

        The decision is a comparison, not a pair of independent thresholds.
        Each engine reports how well its own index matches the question; the
        one that wins by ENGINE_MARGIN answers alone, and if neither does, both
        answer and both are labelled. See ENGINE_MARGIN for the measurement
        this rests on.
        """
        cat = self._catala_part(question, inputs, scope)

        # A caller who named a scope or supplied its inputs has asked for
        # execution. Answering them with prose quotations instead would be
        # substituting our routing judgement for their explicit instruction.
        forced = bool(scope) or bool(inputs)

        # Clauses the routed scope encodes are excluded from quotation. Not
        # just the ones it happened to cite: if the scope encodes the clause,
        # that clause's content is reachable by *executing* it, and quoting it
        # instead is the substitution the architecture forbids. A HYBRID clause
        # is in both stores by design, so without this a scope loses the
        # arbitration to its own clause -- "what service credit do we owe at
        # 98.5% uptime" routed correctly to ServiceCredits.ServiceCredit at
        # 0.5348 and was then answered by quoting L-4.4, one of that scope's
        # own clauses, at 0.5491.
        already = {_ref_of(c) for c in (cat.citations if cat else [])}
        if cat is not None and cat.scope:
            entry = self.registry.get(cat.scope)
            if entry is not None:
                already |= set(entry.encodes)
        hits = [] if forced else self._vector_hits(question, exclude=already)

        rs = cat.score if (cat is not None and cat.score is not None) else 0.0
        vs = hits[0].score if hits else 0.0
        use_cat = cat is not None
        use_vec = bool(hits)
        if use_cat and use_vec:
            if rs - vs >= ENGINE_MARGIN:
                use_vec = False
            elif vs - rs >= ENGINE_MARGIN:
                use_cat = False

        parts: list[AnswerPart] = []
        if use_cat and cat is not None:
            parts.append(cat)
            if with_caveats and cat.scope:
                parts.extend(self._caveats(cat.scope.split(".")[0], question))
        if use_vec:
            parts.extend(self._quotation_parts(hits))
        return parts

    # -- entry point --------------------------------------------------------

    def answer(
        self,
        question: str,
        inputs: dict[str, Any] | None = None,
        scope: str | None = None,
        with_caveats: bool = True,
    ) -> Answer:
        ans = Answer(question=question)
        # ARCHITECTURE.md requires routing "per sub-question". A compound
        # question has one part for each engine and routing it as one string
        # answers only whichever engine wins the whole thing.
        subs = _split_subquestions(question) if not (scope or inputs) else [question]
        for sub in subs:
            for part in self._parts_for(sub, inputs, scope, with_caveats):
                if part not in ans.parts:
                    ans.parts.append(part)
        ans.parts = _drop_redundant_caveats(ans.parts)

        if not ans.parts:
            ans.parts.append(
                AnswerPart(
                    engine=Engine.NONE, kind="no-coverage",
                    text=(
                        "No rule module and no indexed prose clause covers this "
                        "question. The corpus does not answer it, and nothing has "
                        "been inferred."
                    ),
                )
            )
        return ans


def _drop_redundant_caveats(parts: list[AnswerPart]) -> list[AnswerPart]:
    """Remove a caveat whose clause is already quoted as an answer.

    The same clause can legitimately be both: L-1.2 qualifies every
    `ServiceCredits` computation *and* is the direct answer to "are the Service
    Credits the Customer's only financial remedy?". A compound question can
    therefore reach both paths and render one clause twice, once as a quotation
    and once as a caveat.

    The quotation wins. A caveat says "this also bears on the figure above"; a
    quotation says "this answers what you asked". Having said the second, the
    first adds nothing but length. Filtering it out of retrieval instead was
    measured and cost 8.4 points of pass rate, because it silenced the clause
    on the questions it genuinely answers -- see eval/RESULTS.md.
    """
    quoted = {
        _ref_of(c)
        for p in parts
        if p.engine == Engine.VECTOR and p.kind == "quotation"
        for c in p.citations
    }
    return [
        p for p in parts
        if not (
            p.engine == Engine.VECTOR
            and p.kind == "caveat"
            and p.citations
            and all(_ref_of(c) in quoted for c in p.citations)
        )
    ]


_SUBQ_SPLIT = re.compile(r",\s+(?:and|or|but)\s+|;\s+|\?\s+(?=\S)")


def _split_subquestions(question: str) -> list[str]:
    """Split a compound question into the parts that should be routed apart.

    Deliberately conservative. Only a comma followed by a conjunction, a
    semicolon, or a question mark mid-string counts as a boundary: a bare
    "and" joins two halves of one condition at least as often as it joins two
    questions ("hours on a public holiday and above 48 in the week"), and
    splitting that would route each half to a scope that answers neither. Both
    sides must also carry enough words to be a question on their own, and a
    question is never split into more than two.

    A caveat on the evidence: every BOTH case in eval/routing.yaml happens to
    use ", and ", so the eval set can show this rule helping but cannot show
    what it costs on a compound question phrased some other way. That is why
    the rule is narrow rather than clever -- it is doing what the architecture
    contract asks for, on the one pattern that is unambiguous.
    """
    parts = [p.strip(" ?.,;") for p in _SUBQ_SPLIT.split(question)]
    parts = [p for p in parts if len(p.split()) >= 4]
    if len(parts) != 2:
        return [question]
    return parts


def _ref_of(citation: str) -> str:
    """`MSA-SCH4 L-1.2 (004-msa-sla-credits.md:17)` -> `MSA-SCH4 L-1.2`.

    CATALA parts cite bare clause refs; VECTOR parts cite the canonical form
    with the file and line. Comparing the two needs one shape.
    """
    return citation.split(" (")[0].strip()


def _fmt(v: Any) -> str:
    """Render a computed value exactly as Catala produced it.

    Deliberately does not prettify: an earlier version rendered 2.0 as "2",
    which for a legal figure is a change of meaning, not of formatting. The
    raw outputs are also preserved on the part in `outputs`.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
