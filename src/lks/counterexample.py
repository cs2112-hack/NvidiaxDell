"""Counterexamples: the permanent memory of every way this system has been wrong.

The adversarial reviewer's output is not a bug report that gets read once and
closed. Each counterexample it finds is appended here as a fact pattern with
the concrete inputs, the answer the *source document* requires, and the
citations that establish that answer. It is then re-run forever.

Why store the *fact pattern* and not just the failing inputs: a bare input
vector tells a future maintainer that some number came out wrong. A fact
pattern tells them which clauses were in tension and what the document says
should win, which is the only thing that lets them fix the encoding rather
than tune the number until the test passes.

`expected` is authored from the source document by the reviewer, never copied
from what the implementation produced. A test whose expectation was harvested
from the code under test cannot detect that the code is wrong -- it only
detects that the code changed. That inversion is the most common way a
regression suite becomes worthless, so `record_counterexample` refuses an
entry whose expected value equals the observed value.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any

import yaml

STORE = Path("tests/counterexamples")


class Component:
    CATALA = "catala"
    CHAT = "chat"
    INGEST = "ingest"
    DRAFT = "draft"
    TRIAGE = "triage"
    ALL = (CATALA, CHAT, INGEST, DRAFT, TRIAGE)


@dataclass
class Counterexample:
    id: str
    component: str
    fact_pattern: str                       # prose: the scenario, as a lawyer would state it
    citations: list[str]                    # clause refs that establish `expected`
    source_reasoning: str                   # why the document requires `expected`
    expected: Any                           # what the source document requires
    observed: Any = None                    # what the artefact produced when found
    target: dict[str, Any] = field(default_factory=dict)   # module/scope or question
    inputs: dict[str, Any] = field(default_factory=dict)
    round: int = 0
    discovered: str = ""
    status: str = "open"                    # open | fixed
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Counterexample":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class DegenerateCounterexample(ValueError):
    """Raised when an entry could not possibly fail, or was harvested from the
    implementation instead of from the source document."""


def _next_id(store: Path) -> str:
    n = 0
    for p in store.glob("CE-*.yaml"):
        m = re.match(r"CE-(\d+)", p.stem)
        if m:
            n = max(n, int(m.group(1)))
    return f"CE-{n + 1:04d}"


def record_counterexample(
    component: str,
    fact_pattern: str,
    citations: list[str],
    source_reasoning: str,
    expected: Any,
    observed: Any,
    target: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    round: int = 0,
    store: str | Path = STORE,
    notes: str = "",
) -> Counterexample:
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)

    if component not in Component.ALL:
        raise ValueError(f"unknown component {component!r}; expected one of {Component.ALL}")
    if not citations:
        raise DegenerateCounterexample(
            "a counterexample must cite the clause(s) that establish the expected "
            "answer, otherwise it asserts a number with no legal authority"
        )
    if expected == observed:
        raise DegenerateCounterexample(
            f"expected == observed ({expected!r}); this is not a counterexample. "
            "The expected value must be derived from the source document, not "
            "copied from the artefact's output"
        )
    if not str(fact_pattern).strip():
        raise DegenerateCounterexample("fact_pattern is required")

    ce = Counterexample(
        id=_next_id(store),
        component=component,
        fact_pattern=fact_pattern.strip(),
        citations=list(citations),
        source_reasoning=source_reasoning.strip(),
        expected=expected,
        observed=observed,
        target=target or {},
        inputs=inputs or {},
        round=round,
        discovered=date.today().isoformat(),
        status="open",
        notes=notes,
    )
    path = store / f"{ce.id}.yaml"
    path.write_text(yaml.safe_dump(ce.to_dict(), sort_keys=False, width=88, allow_unicode=True))
    return ce


def load_all(store: str | Path = STORE) -> list[Counterexample]:
    store = Path(store)
    if not store.exists():
        return []
    out = []
    for p in sorted(store.glob("CE-*.yaml")):
        out.append(Counterexample.from_dict(yaml.safe_load(p.read_text())))
    return out


def save(ce: Counterexample, store: str | Path = STORE) -> None:
    Path(store).mkdir(parents=True, exist_ok=True)
    (Path(store) / f"{ce.id}.yaml").write_text(
        yaml.safe_dump(ce.to_dict(), sort_keys=False, width=88, allow_unicode=True)
    )


def summary(store: str | Path = STORE) -> dict[str, Any]:
    ces = load_all(store)
    by_component: dict[str, dict[str, int]] = {}
    for ce in ces:
        d = by_component.setdefault(ce.component, {"open": 0, "fixed": 0})
        d[ce.status] = d.get(ce.status, 0) + 1
    return {
        "total": len(ces),
        "open": sum(1 for c in ces if c.status == "open"),
        "fixed": sum(1 for c in ces if c.status == "fixed"),
        "by_component": by_component,
        "max_round": max((c.round for c in ces), default=0),
    }
