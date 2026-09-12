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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .catala_runner import CatalaError, NoApplicableRule, ScopeConflict, run_scope
from .registry import ScopeEntry, build_registry, load_registry
from .segment import load_corpus
from .triage import Label, load_ledger
from .vector import Chunk, Hit, VectorStore, _load_model


class Engine:
    CATALA = "CATALA"
    VECTOR = "VECTOR"
    NONE = "NONE"


@dataclass
class AnswerPart:
    engine: str
    kind: str                 # "computed" | "needs-input" | "quotation" | "caveat" | "no-coverage" | "error"
    text: str
    citations: list[str] = field(default_factory=list)
    scope: str | None = None
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    score: float | None = None

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


@dataclass
class RouteIndex:
    """Maps a question to candidate Catala scopes.

    Built over the text of the RULE and HYBRID clauses each scope encodes. The
    payload is the scope key; the clause text is used for matching only and is
    never returned for display, so this index cannot become a back door for
    quoting rule text as prose.
    """

    keys: list[str]
    refs: list[str]
    vectors: np.ndarray

    @classmethod
    def build(cls, registry: dict[str, ScopeEntry] | None = None) -> "RouteIndex":
        reg = registry if registry is not None else (load_registry() or build_registry())
        ledger = load_ledger()
        clauses = {c.ref: c for d in load_corpus("corpus") for c in d.clauses}

        texts: list[str] = []
        keys: list[str] = []
        refs: list[str] = []
        for key, e in sorted(reg.items()):
            for ref in e.encodes:
                d = ledger.get(ref)
                if d is None or d.label is Label.PROSE:
                    continue
                c = clauses.get(ref)
                if c is None:
                    continue
                texts.append(f"{c.section_title} :: {c.body}")
                keys.append(key)
                refs.append(ref)
        if not texts:
            return cls(keys=[], refs=[], vectors=np.zeros((0, 1), dtype=np.float32))
        v = np.asarray(_load_model().encode(texts), dtype=np.float32)
        n = np.linalg.norm(v, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return cls(keys=keys, refs=refs, vectors=v / n)

    def candidates(self, question: str, k: int = 3) -> list[tuple[str, float, list[str]]]:
        """(scope_key, best_score, supporting_clause_refs), best first."""
        if not self.keys:
            return []
        q = np.asarray(_load_model().encode([question]), dtype=np.float32)[0]
        nn = float(np.linalg.norm(q))
        if nn:
            q = q / nn
        scores = self.vectors @ q
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
            sup = [r for _, r in sorted(support[key], key=lambda t: -t[0])[:3]]
            out.append((key, s, sup))
        return out


# --- the answerer ----------------------------------------------------------

CATALA_THRESHOLD = 0.42
VECTOR_THRESHOLD = 0.30


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
        cands = self.route.candidates(question, k=3)
        if scope_hint:
            cands = [c for c in cands if c[0] == scope_hint] or [(scope_hint, 1.0, [])]
        if not cands:
            return None
        key, score, support = cands[0]
        if score < CATALA_THRESHOLD and not scope_hint:
            return None
        entry = self.registry.get(key)
        if entry is None:
            return None

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
                citations=support or entry.encodes[:4], scope=key,
                inputs={"required": required, "missing": missing}, score=score,
            )

        try:
            outputs = run_scope(entry.path, entry.scope, {k: supplied[k] for k in required})
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
                citations=support or entry.encodes[:4], scope=key, inputs=supplied, score=score,
            )
        except NoApplicableRule as e:
            return AnswerPart(
                engine=Engine.CATALA, kind="error",
                text=(
                    "No rule applies to these facts, so the documents do not "
                    "determine an answer here.\n    " + e.diagnostic[:600]
                ),
                citations=support or entry.encodes[:4], scope=key, inputs=supplied, score=score,
            )
        except CatalaError as e:
            return AnswerPart(
                engine=Engine.CATALA, kind="error",
                text="The scope could not be executed:\n    " + e.diagnostic[:600],
                citations=support or entry.encodes[:4], scope=key, inputs=supplied, score=score,
            )

        rendered = ", ".join(f"{k} = {_fmt(v)}" for k, v in sorted(outputs.items()))
        return AnswerPart(
            engine=Engine.CATALA, kind="computed",
            text=f"{rendered}\n    Computed by executing {key} on the supplied facts.",
            citations=support or entry.encodes[:4], scope=key,
            inputs=supplied, outputs=outputs, score=score,
        )

    # -- VECTOR side --------------------------------------------------------

    def _vector_parts(self, question: str, k: int = 3) -> list[AnswerPart]:
        if self.store is None:
            return []
        hits: list[Hit] = self.store.search(question, k=k, min_score=VECTOR_THRESHOLD)
        parts = []
        for h in hits:
            c = h.chunk
            parts.append(
                AnswerPart(
                    engine=Engine.VECTOR, kind="quotation",
                    text=f"“{c.text}”",
                    citations=[c.cite()], score=h.score,
                )
            )
        return parts

    def _caveats(self, module: str) -> list[AnswerPart]:
        if self.store is None:
            return []
        out = []
        for c in self.store.qualifying(module):
            out.append(
                AnswerPart(
                    engine=Engine.VECTOR, kind="caveat",
                    text=(
                        f"This prose clause qualifies {module} and is not part of "
                        f"the computation above:\n    “{c.text}”"
                    ),
                    citations=[c.cite()],
                )
            )
        return out

    # -- entry point --------------------------------------------------------

    def answer(
        self,
        question: str,
        inputs: dict[str, Any] | None = None,
        scope: str | None = None,
        with_caveats: bool = True,
    ) -> Answer:
        ans = Answer(question=question)

        cat = self._catala_part(question, inputs, scope)
        if cat is not None:
            ans.parts.append(cat)
            if with_caveats and cat.scope:
                module = cat.scope.split(".")[0]
                ans.parts.extend(self._caveats(module))

        vec = self._vector_parts(question)
        # A rule question that executed cleanly does not need prose padding;
        # quoting loosely-related prose beside a computed figure is how the two
        # engines start to look like one.
        if cat is None or cat.kind in ("needs-input", "error"):
            ans.parts.extend(vec)
        elif vec and vec[0].score and vec[0].score > 0.55:
            ans.parts.append(vec[0])

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


def _fmt(v: Any) -> str:
    """Render a computed value exactly as Catala produced it.

    Deliberately does not prettify: an earlier version rendered 2.0 as "2",
    which for a legal figure is a change of meaning, not of formatting. The
    raw outputs are also preserved on the part in `outputs`.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
