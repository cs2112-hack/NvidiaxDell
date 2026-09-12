"""The decision surface: the corpus as one function, and its equivalence classes.

The corpus composes into a decision function. This module executes that
function densely enough to recover its shape: the regions of input space that
share a legal outcome, the borders between them, and which clause created each
border.

## Where the grid comes from, and why that is the whole argument

A sweep over arbitrary values would tell you very little. The reason this is
worth doing is that **the grid is derived from the compiler's own exception
conditions**, not from anyone's guess about interesting inputs.
`catala exceptions` reports, for every output variable, the hierarchy of
definitions and the condition attached to each. Those conditions *are* the
borders: `ordinal > 40` is not a plausible place to probe, it is the exact
point at which C-4.1 starts to govern. So the probe set for an input is the
literals that some clause tests it against, each with its immediate
neighbours, plus the ends of the realisable domain.

That has three consequences worth stating:

  * A border on the map is always attributable to a clause, because it came
    from a clause's condition in the first place.
  * A region cannot be an artefact of sampling. Two cells are in the same
    region because the interpreter took the *same definitions* for them, which
    the trace reports; not because their outputs happened to look alike.
  * Adding a clause redraws the map without anyone updating a sweep.

## The one thing a human must supply

Which input values correspond to a person who actually exists. `ordinal = -3`
is not an hour of anyone's week, and reporting that the corpus is silent there
would be noise dressed as a finding. That claim about the world cannot be
derived from the documents, so it is declared in `exposure/domains.yaml` and
every narrowing carries a note saying what makes it true. Everything else on
this map is computed.

## Cost

`catala interpret --trace` costs about 80ms per cell, an order cheaper than
`clerk run`, and it returns the governing line for free. A scope's grid is a
few hundred cells, so a surface is seconds, and results are cached against the
module's content hash.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import product
from pathlib import Path
from typing import Any, Iterable

import yaml

from .catala_runner import (
    AssertionFailed,
    CatalaError,
    NoApplicableRule,
    ScopeConflict,
    exception_tree,
    json_schema,
    reads_tree,
    run_scope_traced,
)
from .literate import parse_literate
from .registry import ScopeEntry, load_registry

DOMAINS_PATH = Path("exposure/domains.yaml")
CACHE_PATH = Path(".run/surface-cache.json")

MAX_CELLS = 4000
"""Hard ceiling on cells per scope.

A surface that silently sampled would be the worst of both worlds: it would
look complete and would not be. When the product of the probe values exceeds
this, `partition` narrows the grid by dropping the *least discriminating*
inputs first -- those no clause condition mentions -- and records exactly what
it dropped in `Surface.elided`, so the map can say which dimensions it is not
showing rather than pretending they were flat.
"""


# --- what a real person looks like ----------------------------------------

NATURAL: dict[str, tuple[Any, Any]] = {
    "integer": (-(2**31), 2**31),
    "decimal": (Decimal("-1e9"), Decimal("1e9")),
    "money": (Decimal("-1e9"), Decimal("1e9")),
}


@dataclass
class InputDomain:
    """The values one scope input can take for a person who actually exists."""

    name: str
    type: str
    realisable: list[Any] | None = None   # [lo, hi] for numbers; explicit list otherwise
    probe: list[Any] | None = None
    """Values to try, where they cannot be derived.

    Dates and enumerations have no numeric neighbourhood to step through, and
    a clause's condition on them is usually expressed over a derived variable
    (`days_elapsed > 60`, not a comparison of the two dates the scope takes).
    So for those the probe set is declared, and is reported as declared --
    `Surface.axis_reasons` distinguishes an axis read off a clause from one a
    person chose, because only the first carries the argument that the map
    cannot be an artefact of sampling.
    """
    note: str = ""
    """Why the narrowing is true of the world. Required whenever `realisable`
    is narrower than the type's natural domain, because a narrowing is a
    factual claim and an unexplained one is indistinguishable from a
    convenience that happens to suppress findings."""

    def contains(self, v: Any) -> bool:
        if self.type == "boolean":
            return isinstance(v, bool)
        if self.type in ("enum", "struct"):
            allowed = self.realisable or self.probe
            return allowed is None or v in allowed
        if self.realisable is None:
            return True
        if self.type == "date":
            return str(self.realisable[0]) <= str(v) <= str(self.realisable[1])
        try:
            d = Decimal(str(v))
        except (InvalidOperation, TypeError):
            return False
        if len(self.realisable) == 2:
            return Decimal(str(self.realisable[0])) <= d <= Decimal(str(self.realisable[1]))
        return any(Decimal(str(x)) == d for x in self.realisable)

    def endpoints(self) -> list[Any]:
        if self.type == "boolean":
            return [False, True]
        if self.probe:
            return list(self.probe)
        if self.realisable is None:
            return []
        if len(self.realisable) == 2 and self.type != "date":
            return [self.realisable[0], self.realisable[1]]
        return list(self.realisable)


COHERENCE_RE = re.compile(
    r"^\s*(?P<lhs>[a-z_][a-z0-9_]*)\s*(?P<op>>=|<=|!=|>|<|=)\s*(?P<rhs>\S+)\s*$"
)

_CMP = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


@dataclass
class Coherence:
    """A relation between two inputs that any real case satisfies.

    Per-input domains cannot say that a claim is submitted no earlier than the
    expense it claims for: each date is realisable alone, and it is the pair
    that is impossible. Without this the grid manufactures cases no person is
    in, the scope's assertion correctly refuses them, and the map reports six
    regions of "the policy refuses to answer" that are the map's own fault.
    That is precisely the false positive this engine is not allowed to have,
    so incoherent cells are never executed at all.

    The language is deliberately two terms and one comparison, with no
    disjunction and no arithmetic. A constraint elaborate enough to need those
    is usually not a fact about the world but an argument about the policy, and
    an argument about the policy belongs in a predicate where it is visible and
    costed, not in the grid where it silently deletes cases.
    """

    holds: str
    note: str = ""
    lhs: str = ""
    op: str = ""
    rhs: str = ""

    def compile(self) -> "Coherence":
        m = COHERENCE_RE.match(self.holds)
        if not m:
            raise ValueError(
                f"coherence constraint {self.holds!r} is not "
                f"`<input> <op> <input-or-literal>`"
            )
        self.lhs, self.op, self.rhs = m.group("lhs"), m.group("op"), m.group("rhs")
        return self

    def satisfied(self, cell: dict[str, Any]) -> bool:
        if not self.op:
            self.compile()
        if self.lhs not in cell:
            return True
        a = cell[self.lhs]
        b = cell[self.rhs] if self.rhs in cell else self.rhs.strip('"\'')
        if isinstance(a, str) or isinstance(b, str):
            return _CMP[self.op](str(a), str(b))
        try:
            return _CMP[self.op](Decimal(str(a)), Decimal(str(b)))
        except (InvalidOperation, TypeError):
            return _CMP[self.op](str(a), str(b))


@dataclass
class ScopeDomain:
    key: str
    population: str = ""
    """What one cell of this scope is a decision *about* -- an employee-hour, a
    service-month, a claim. Printed on findings so an exposure reads as a
    person rather than a record."""
    measure_output: str = ""
    """The output that carries the money, or the multiple of money. Exposure is
    measured on this and nothing else; a scope that names none can still
    produce structural findings, but they will carry no figure."""
    measure_unit: str = ""
    scale_by: str = ""
    """An operations field that turns `measure_output` into currency (a
    multiplier times Base Hourly Rate is not yet money). Empty when the
    measure is already money."""
    favours: dict[str, str] = field(default_factory=dict)
    """Per output: who a LARGER value of it favours, `counterparty` or
    `company`.

    Not derivable and not guessable. A larger overtime rate favours the
    employee; a larger recoverable commission favours the employer; both are
    money and both come out of the same kind of scope. Without this the
    operations watcher reports that the company recovering a time-barred
    clawback was "more favourable than the policy provides", which is exactly
    backwards and is the sort of error that ends a reader's trust in one
    line.

    An output with no declared polarity is reported as differing, with no claim
    about who gained. That is the honest answer and it is better than a
    plausible one.
    """
    inputs: dict[str, InputDomain] = field(default_factory=dict)
    coherence: list[Coherence] = field(default_factory=list)

    def coherent(self, cell: dict[str, Any]) -> bool:
        return all(c.satisfied(cell) for c in self.coherence)

    def incoherence_reason(self, cell: dict[str, Any]) -> str:
        for c in self.coherence:
            if not c.satisfied(cell):
                return f"{c.holds} does not hold: {c.note or 'no note given'}"
        return ""

    def realisable(self, cell: dict[str, Any]) -> bool:
        return self.coherent(cell) and all(
            (d.contains(cell[k]) if k in cell else True)
            for k, d in self.inputs.items()
        )

    def unrealisable_reason(self, cell: dict[str, Any]) -> str:
        why = self.incoherence_reason(cell)
        if why:
            return why
        for k, d in self.inputs.items():
            if k in cell and not d.contains(cell[k]):
                return f"{k}={cell[k]!r} is outside {d.realisable!r}: {d.note or 'no note given'}"
        return ""


def load_domains(path: str | Path = DOMAINS_PATH) -> dict[str, ScopeDomain]:
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    out: dict[str, ScopeDomain] = {}
    for key, d in (data.get("scopes") or {}).items():
        m = d.get("measure") or {}
        inputs = {}
        for name, spec in (d.get("inputs") or {}).items():
            spec = spec or {}
            inputs[name] = InputDomain(
                name=name,
                type=str(spec.get("type", "integer")),
                realisable=spec.get("realisable"),
                probe=spec.get("probe"),
                note=str(spec.get("note", "")),
            )
        out[key] = ScopeDomain(
            key=key,
            population=str(d.get("population", "")),
            measure_output=str(m.get("output", "")),
            measure_unit=str(m.get("unit", "")),
            scale_by=str(m.get("scale_by", "")),
            favours={str(k): str(v) for k, v in (d.get("favours") or {}).items()},
            inputs=inputs,
            coherence=[
                Coherence(holds=str(c.get("holds", "")), note=str(c.get("note", ""))).compile()
                for c in (d.get("coherence") or [])
            ],
        )
    return out


def validate_domains(
    domains: dict[str, ScopeDomain] | None = None,
    registry: dict[str, ScopeEntry] | None = None,
) -> list[str]:
    """Problems with the declared domains, as text.

    Checked rather than trusted for the same reason the literate quotations
    are: a domain file is a set of claims about the world that silently goes
    stale when a scope gains an input, and a stale domain file suppresses
    findings without anyone noticing. An input that no longer exists, or a
    narrowing with no note, is a defect in the claim, not a detail.
    """
    domains = load_domains() if domains is None else domains
    registry = load_registry() if registry is None else registry
    problems: list[str] = []
    for key, sd in sorted(domains.items()):
        entry = registry.get(key)
        if entry is None:
            problems.append(f"{key}: declared in domains.yaml but not in the registry")
            continue
        declared, actual = set(sd.inputs), set(entry.inputs)
        for missing in sorted(actual - declared):
            problems.append(
                f"{key}: input {missing!r} has no declared domain, so the map "
                f"cannot say whether a value of it describes a real person"
            )
        for extra in sorted(declared - actual):
            problems.append(f"{key}: domain declared for {extra!r}, which the scope does not take")
        if sd.measure_output and sd.measure_output not in entry.outputs:
            problems.append(
                f"{key}: measure output {sd.measure_output!r} is not an output of the scope"
            )
        for out, who in sorted(sd.favours.items()):
            if out not in entry.outputs:
                problems.append(f"{key}: favours names {out!r}, not an output of the scope")
            if who not in ("counterparty", "company"):
                problems.append(
                    f"{key}.{out}: favours must be `counterparty` or `company`, got {who!r}"
                )
        for c in sd.coherence:
            for term in (c.lhs, c.rhs):
                if term and term[0].isalpha() and term not in sd.inputs and term not in ("true", "false"):
                    problems.append(
                        f"{key}: coherence {c.holds!r} names {term!r}, which is not an input"
                    )
            if not c.note.strip():
                problems.append(
                    f"{key}: coherence {c.holds!r} deletes cases from the map with no "
                    f"note. A constraint that suppresses findings must say what makes it true"
                )
        for name, d in sorted(sd.inputs.items()):
            if d.type == "date" and not (d.probe or d.realisable):
                problems.append(
                    f"{key}.{name}: a date has no numeric neighbourhood to step "
                    f"through, so the dates to try must be declared under `probe`"
                )
            if d.type == "struct" and not d.probe:
                problems.append(
                    f"{key}.{name}: a structured input bundles several facts at "
                    f"once, so the scenarios to try must be declared under `probe`"
                )
            if d.type == "enum" and not enum_values(entry, name):
                problems.append(
                    f"{key}.{name}: declared as an enumeration, but the compiler's "
                    f"schema for {key} reports no cases for it"
                )
            if d.type in ("boolean", "enum", "struct") or d.realisable is None:
                continue
            nat = NATURAL.get(d.type)
            narrowed = d.type == "date" or len(d.realisable) != 2 or (
                nat is not None
                and (Decimal(str(d.realisable[0])) > Decimal(str(nat[0]))
                     or Decimal(str(d.realisable[1])) < Decimal(str(nat[1])))
            )
            if narrowed and not d.note.strip():
                problems.append(
                    f"{key}.{name}: narrows the domain to {d.realisable!r} with no note. "
                    f"A narrowing is a claim about the world and must say what makes it true"
                )
    return problems


# --- borders, read off the compiler's exception conditions ----------------

ATOM_RE = re.compile(
    r"(?:^|\s|\()(?P<var>[a-z_][a-z0-9_]*)\s*(?P<op>>=|<=|!=|>|<|=)\s*"
    r"(?P<lit>-?\d+(?:\.\d+)?)"
)
BARE_RE = re.compile(r"(?:^|\s|\()(?P<var>[a-z_][a-z0-9_]*)(?:\s|\)|$)")


@dataclass
class Border:
    """One comparison a clause makes against one input.

    This is what separates two regions of the map, and it is attributable by
    construction: it was read out of the condition attached to a definition,
    and that definition's source line resolves to the clause quoted above it.
    """

    variable: str
    op: str
    literal: str
    label: str
    output: str
    line: int | None = None
    clause_refs: list[str] = field(default_factory=list)
    law_headings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return f"{self.variable} {self.op} {self.literal}"


def _step_for(literal: str) -> Decimal:
    """One unit at the literal's own precision.

    `ordinal > 40` steps by 1; `availability_percentage < 99.9` steps by 0.1.
    Taking the step from the literal rather than fixing it means the probe
    lands just off the border the clause actually drew, instead of at some
    distance chosen by this module.
    """
    if "." in literal:
        return Decimal(1).scaleb(-len(literal.split(".", 1)[1]))
    return Decimal(1)


def clause_index(module_path: str | Path) -> list[tuple[int, int, list[str]]]:
    """(start, end, clause refs) for every code block, for line attribution."""
    lf = parse_literate(module_path)
    return [(b.start_line, b.end_line, [q.ref for q in b.quotes]) for b in lf.blocks]


def refs_at_line(index: list[tuple[int, int, list[str]]], line: int | None) -> list[str]:
    if line is None:
        return []
    for start, end, refs in index:
        if start <= line <= end:
            return list(refs)
    return []


def borders(
    key: str, entry: ScopeEntry | None = None
) -> list[Border]:
    """Every comparison any clause of a scope makes against any of its inputs.

    Reads the exception hierarchy of each output variable. A variable whose
    hierarchy the compiler refuses to report (some derived outputs have no
    definitions of their own) is skipped rather than guessed at.
    """
    entry = entry or load_registry().get(key)
    if entry is None:
        return []
    index = clause_index(entry.path)
    inputs = set(entry.inputs)
    seen: set[tuple[str, str, str]] = set()
    out: list[Border] = []
    for var in entry.outputs:
        try:
            trees = exception_tree(entry.path, entry.scope, var)
        except CatalaError:
            continue
        stack = list(trees)
        while stack:
            n = stack.pop()
            stack.extend(n.exceptions)
            line = n.lines[0] if n.lines else None
            for cond in n.conditions:
                if cond == "<unconditional>":
                    continue
                for m in ATOM_RE.finditer(cond):
                    v, op, lit = m.group("var"), m.group("op"), m.group("lit")
                    if v not in inputs or (v, op, lit) in seen:
                        continue
                    seen.add((v, op, lit))
                    out.append(Border(
                        variable=v, op=op, literal=lit, label=n.label, output=var,
                        line=line, clause_refs=refs_at_line(index, line),
                        law_headings=list(n.law_headings),
                    ))
    return out


def enum_values(entry: ScopeEntry | None, name: str) -> list[str]:
    """The cases of an enumerated input, from the compiler's own JSON schema.

    Derived rather than declared: an enumeration gains a case when someone
    amends the document, and a hand-listed set of tiers would map three of the
    four and report nothing about the fourth.
    """
    if entry is None:
        return []
    try:
        ins, _outs = json_schema(entry.path, entry.scope)
    except CatalaError:
        return []
    defs = ins.get("definitions") or {}
    root = defs.get(f"{entry.scope}_in") or {}
    prop = (root.get("properties") or {}).get(name) or {}
    ref = str(prop.get("$ref", "")).rsplit("/", 1)[-1]
    return list((defs.get(ref) or {}).get("enum") or [])


def probe_values(
    sd: ScopeDomain, bs: list[Border], entry: ScopeEntry | None = None
) -> tuple[dict[str, list[Any]], dict[str, list[str]]]:
    """The values to try for each input, and which border produced each.

    For a numeric input: every literal a clause compares it against, with the
    value one step either side, so both sides of every border and the border
    itself are visited. Plus the ends of the realisable domain, because the
    extremes are where a clause that was never meant to reach gets reached.
    Everything is clipped to the realisable domain and de-duplicated.
    """
    values: dict[str, list[Any]] = {}
    why: dict[str, list[str]] = {}
    by_var: dict[str, list[Border]] = {}
    for b in bs:
        by_var.setdefault(b.variable, []).append(b)

    for name, d in sd.inputs.items():
        if d.type == "boolean":
            values[name] = [False, True]
            why[name] = ["both truth values"]
            continue
        if d.type == "struct":
            values[name] = list(d.probe or [])
            why[name] = ["declared scenarios; a structured input has no neighbourhood"]
            continue
        if d.type == "enum":
            vals = d.probe or d.realisable or enum_values(entry, name)
            values[name] = list(vals)
            why[name] = (["declared probe values"] if (d.probe or d.realisable)
                         else ["every case of the enumeration, from the compiler's schema"])
            continue
        if d.type == "date":
            values[name] = list(d.probe or d.endpoints())
            why[name] = ["declared probe dates; no numeric neighbourhood to derive"]
            continue
        cand: list[Decimal] = []
        reasons: list[str] = []
        for b in by_var.get(name, []):
            lit = Decimal(b.literal)
            step = _step_for(b.literal)
            cand += [lit - step, lit, lit + step]
            reasons.append(f"{b.text} ({', '.join(b.clause_refs) or b.label})")
        for e in (d.probe or d.endpoints()):
            cand.append(Decimal(str(e)))
        if d.probe:
            reasons.append("declared probe values")
        if not cand:
            continue
        keep: list[Decimal] = []
        for c in cand:
            if not d.contains(c):
                continue
            if not any(c == k for k in keep):
                keep.append(c)
        keep.sort()
        values[name] = [
            int(c) if d.type == "integer" else float(c) for c in keep
        ]
        why[name] = reasons or ["declared domain endpoints; no clause tests this input"]
    return values, why


# --- executing the grid ---------------------------------------------------

OUTCOME_COMPUTED = "computed"
OUTCOME_CONFLICT = "conflict"
"""Two definitions apply and the documents establish no priority between them.
The company cannot say what its own policy is for this person."""
OUTCOME_SILENT = "silent"
"""No definition applies. The corpus does not answer for a person who exists."""
OUTCOME_REFUSED = "refused"
"""An assertion rejected the facts. Correct where the facts are impossible;
exposure where they are not, which is what the realisable domain decides."""


@dataclass
class Cell:
    inputs: dict[str, Any]
    outcome: str
    outputs: dict[str, Any] = field(default_factory=dict)
    governing: list[dict[str, Any]] = field(default_factory=list)
    diagnostic: str = ""

    @property
    def signature(self) -> str:
        """What makes two cells the same case in law.

        Deliberately the *definitions the interpreter took*, not the values it
        produced. Two cells that happen to produce 1.25 by different provisions
        are two regions, because a clause amendment moves one and not the
        other; and two cells governed alike but computing different amounts
        from a formula are one region, because they are one legal case.
        """
        if self.outcome != OUTCOME_COMPUTED:
            return f"{self.outcome}:{self.diagnostic[:80]}"
        return "|".join(
            f"{g['variable']}@{g['line']}" for g in sorted(
                self.governing, key=lambda g: str(g["variable"])
            )
        )


@dataclass
class Region:
    id: str
    signature: str
    outcome: str
    governing: list[dict[str, Any]] = field(default_factory=list)
    clause_refs: list[str] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    population: int = 0
    """Real decisions from `operations/` that land in this region. Zero means
    nobody observed here, which is not the same as nobody being here."""

    @property
    def size(self) -> int:
        return len(self.cells)

    def value_range(self, output: str) -> tuple[Any, Any] | None:
        vals = []
        for c in self.cells:
            v = c.outputs.get(output)
            if v is None:
                continue
            try:
                vals.append(Decimal(str(v)))
            except (InvalidOperation, TypeError):
                return None
        if not vals:
            return None
        return (min(vals), max(vals))

    def exemplar(self) -> dict[str, Any]:
        return self.cells[0].inputs if self.cells else {}


@dataclass
class Surface:
    key: str
    regions: list[Region] = field(default_factory=list)
    axes: dict[str, list[Any]] = field(default_factory=dict)
    axis_reasons: dict[str, list[str]] = field(default_factory=dict)
    borders: list[Border] = field(default_factory=list)
    elided: list[str] = field(default_factory=list)
    """Inputs held fixed to stay under the cell ceiling, with the value used.
    Stated rather than hidden: a map with a dimension flattened is a map of a
    slice, and saying which slice is the difference between a limitation and a
    lie."""
    cells_run: int = 0
    incoherent: int = 0
    """Grid points dropped before execution as cases no one can be in. Counted
    rather than discarded quietly: a coherence constraint suppresses findings,
    so how much it suppressed is part of reading the map."""
    coherence: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def by_outcome(self, outcome: str) -> list[Region]:
        return [r for r in self.regions if r.outcome == outcome]

    def singletons(self) -> list[Region]:
        """Regions holding exactly one cell.

        Almost always an accident: a legal rule that applies to exactly one
        configuration of facts is usually a drafting slip rather than a policy
        anyone chose. Worth looking at on its own.
        """
        return [r for r in self.regions if r.size == 1]

    def summary(self) -> dict[str, Any]:
        return {
            "scope": self.key,
            "regions": len(self.regions),
            "cells": self.cells_run,
            "computed": len(self.by_outcome(OUTCOME_COMPUTED)),
            "conflict": len(self.by_outcome(OUTCOME_CONFLICT)),
            "silent": len(self.by_outcome(OUTCOME_SILENT)),
            "refused": len(self.by_outcome(OUTCOME_REFUSED)),
            "singletons": len(self.singletons()),
            "elided": list(self.elided),
            "incoherent": self.incoherent,
            "coherence": list(self.coherence),
        }


def _grid(
    values: dict[str, list[Any]], order: list[str], cap: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """The cartesian product, narrowed to fit under `cap`.

    Inputs are dropped in `order` -- least discriminating first -- and each
    dropped input is pinned to its first probe value, which is the low end of
    its realisable domain.
    """
    elided: list[str] = []
    live = dict(values)
    fixed: dict[str, Any] = {}

    def total() -> int:
        n = 1
        for v in live.values():
            n *= max(1, len(v))
        return n

    for name in order:
        if total() <= cap:
            break
        if name not in live or len(live[name]) <= 1:
            continue
        fixed[name] = live[name][0]
        elided.append(f"{name} held at {live[name][0]!r}")
        del live[name]

    names = sorted(live)
    cells = []
    for combo in product(*(live[n] for n in names)):
        cell = dict(zip(names, combo))
        cell.update(fixed)
        cells.append(cell)
        if len(cells) >= cap:
            break
    return cells, elided


_MODULE_DECL_RE = re.compile(r"^>\s*Module\s+(\w+)", re.M)
_USING_RE = re.compile(r"^>\s*Using\s+(\w+)", re.M)


def source_hash(path: str | Path) -> str:
    """Content hash of a module and of every module it transitively uses.

    A scope's answer depends on the modules it `> Using`s as much as on its
    own text. Keyed on its own bytes alone, an edit to ExpenseDefs left every
    MealPerDiem cell under the same key, so the cache served the map of the
    unedited policy -- and the counterfactual, which reads through this cache,
    reported that the edit moved nothing. That is the failure D-9 exists to
    prevent, arriving by a different door.
    """
    root = Path(path)
    by_name: dict[str, Path] = {}
    for f in root.parent.glob("*.catala_en"):
        m = _MODULE_DECL_RE.search(f.read_text(encoding="utf-8"))
        if m:
            by_name[m.group(1)] = f
    seen: set[Path] = set()
    stack = [root]
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        for dep in _USING_RE.findall(f.read_text(encoding="utf-8")):
            if dep in by_name:
                stack.append(by_name[dep])
    h = hashlib.sha256()
    for f in sorted(seen, key=lambda p: p.name):
        h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def _cache_key(entry: ScopeEntry, cell: dict[str, Any], src_hash: str) -> str:
    return hashlib.sha256(
        f"{entry.qualified}|{src_hash}|{json.dumps(cell, sort_keys=True)}".encode()
    ).hexdigest()[:24]


class _Cache:
    """Executions keyed by scope, module content and inputs.

    Keyed on the module's content hash so an edited clause invalidates every
    cell of that module rather than serving a map of the previous wording --
    which is the failure that would make a live-redrawing map quietly wrong.
    """

    def __init__(self, path: Path = CACHE_PATH):
        self.path = path
        self.data: dict[str, Any] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                self.data = {}
        self.dirty = False

    def get(self, k: str) -> dict[str, Any] | None:
        return self.data.get(k)

    def put(self, k: str, v: dict[str, Any]) -> None:
        self.data[k] = v
        self.dirty = True

    def flush(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data))
        self.dirty = False


def execute_cell(
    entry: ScopeEntry, cell: dict[str, Any], index: list[tuple[int, int, list[str]]]
) -> Cell:
    """Run one point of the grid and classify what came back.

    Every outcome here is the interpreter's, including the failures. A conflict
    is not this module's opinion that the documents are ambiguous; it is Catala
    declining to pick between two applicable definitions.
    """
    try:
        outputs, decisions = run_scope_traced(entry.path, entry.scope, cell)
    except ScopeConflict as e:
        return Cell(cell, OUTCOME_CONFLICT, diagnostic=e.diagnostic[:600])
    except NoApplicableRule as e:
        return Cell(cell, OUTCOME_SILENT, diagnostic=e.diagnostic[:600])
    except AssertionFailed as e:
        return Cell(cell, OUTCOME_REFUSED, diagnostic=e.diagnostic[:600])
    except CatalaError as e:
        return Cell(cell, OUTCOME_REFUSED, diagnostic=f"{type(e).__name__}: {e.diagnostic[:400]}")
    governing = [
        {
            "variable": d["variable"],
            "line": d["line"],
            "law_headings": d.get("law_headings") or [],
            "clause_refs": refs_at_line(index, d.get("line")),
        }
        for d in decisions
    ]
    return Cell(cell, OUTCOME_COMPUTED, outputs=outputs, governing=governing)


@reads_tree
def partition(
    key: str,
    *,
    domains: dict[str, ScopeDomain] | None = None,
    registry: dict[str, ScopeEntry] | None = None,
    cap: int = MAX_CELLS,
    workers: int = 8,
    use_cache: bool = True,
) -> Surface:
    """Execute a scope over its border-derived grid and group the results."""
    registry = registry if registry is not None else load_registry()
    entry = registry.get(key)
    if entry is None:
        raise KeyError(f"no registered scope {key!r}")
    domains = domains if domains is not None else load_domains()
    sd = domains.get(key)
    if sd is None:
        raise KeyError(
            f"{key} has no declared domain in {DOMAINS_PATH}; without one the map "
            f"cannot tell a real person from an impossible one"
        )

    bs = borders(key, entry)
    values, reasons = probe_values(sd, bs, entry)
    tested = {b.variable for b in bs}
    # least discriminating first: inputs no clause tests, then fewest borders
    order = sorted(values, key=lambda n: (n in tested, sum(1 for b in bs if b.variable == n)))
    cells, elided = _grid(values, order, cap)
    incoherent = [c for c in cells if not sd.coherent(c)]
    cells = [c for c in cells if sd.coherent(c)]

    index = clause_index(entry.path)
    src_hash = source_hash(entry.path)
    cache = _Cache() if use_cache else None

    def one(cell: dict[str, Any]) -> Cell:
        if cache is not None:
            hit = cache.get(_cache_key(entry, cell, src_hash))
            if hit is not None:
                return Cell(cell, hit["outcome"], hit.get("outputs") or {},
                            hit.get("governing") or [], hit.get("diagnostic", ""))
        c = execute_cell(entry, cell, index)
        if cache is not None:
            cache.put(_cache_key(entry, cell, src_hash), {
                "outcome": c.outcome, "outputs": c.outputs,
                "governing": c.governing, "diagnostic": c.diagnostic,
            })
        return c

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, cells))
    if cache is not None:
        cache.flush()

    groups: dict[str, Region] = {}
    for c in results:
        sig = c.signature
        r = groups.get(sig)
        if r is None:
            refs: list[str] = []
            for g in c.governing:
                for ref in g["clause_refs"]:
                    if ref not in refs:
                        refs.append(ref)
            r = Region(
                id=f"R{len(groups) + 1:03d}", signature=sig, outcome=c.outcome,
                governing=c.governing, clause_refs=refs,
            )
            groups[sig] = r
        r.cells.append(c)

    regions = sorted(groups.values(), key=lambda r: (-r.size, r.id))
    for i, r in enumerate(regions, 1):
        r.id = f"R{i:03d}"
    return Surface(
        key=key, regions=regions, axes=values, axis_reasons=reasons, borders=bs,
        elided=elided, cells_run=len(results), incoherent=len(incoherent),
        coherence=[c.holds for c in sd.coherence],
    )


def locate(surface: Surface, facts: dict[str, Any]) -> Region | None:
    """Which region a set of facts falls in.

    Matches on the axes the map actually varied, so a fact vector carrying
    extra fields (a real decision record usually does) still lands. Returns
    None when the facts are off the grid entirely, which the caller must not
    read as "no exposure".
    """
    axes = [a for a in surface.axes if a in facts]
    if not axes:
        return None
    best: Region | None = None
    best_dist: Decimal | None = None
    for r in surface.regions:
        for c in r.cells:
            dist = Decimal(0)
            ok = True
            for a in axes:
                want, got = c.inputs.get(a), facts[a]
                if isinstance(want, bool) or isinstance(got, bool):
                    if bool(want) != bool(got):
                        ok = False
                        break
                    continue
                try:
                    dist += abs(Decimal(str(want)) - Decimal(str(got)))
                except (InvalidOperation, TypeError):
                    if want != got:
                        ok = False
                        break
            if not ok:
                continue
            if best_dist is None or dist < best_dist:
                best, best_dist = r, dist
    return best


def mapped_scopes(
    domains: dict[str, ScopeDomain] | None = None
) -> list[str]:
    return sorted(load_domains() if domains is None else domains)
