"""Drafting roundtrip: Catala -> English -> Catala, and whether the two agree.

The loop the brief asks for is: write Catala, render it to English, have a
fresh agent that has never seen the original re-encode that English, diff the
two, and iterate until they converge.

This module supplies the *comparison*, which is the hard half. The generation
steps are agent calls, orchestrated outside Python, because "a fresh agent that
has not seen the original" is a property of how the agent is invoked, not
something a library can assert.

## Why not a textual AST diff

Catala 1.2.1 has no structured AST serialisation (verified; see DECISIONS.md
D-1). Diffing the pretty-printed `scopelang` dump directly compares surface
form, so it fires on a renamed local or a reordered-but-equivalent definition.
A convergence loop driven by that signal never terminates.

So convergence is decided by two gating comparisons plus one advisory one:

1. `exception_equivalence` — the canonicalised exception tree per scope
   variable, from `catala exceptions -F json`. This is real structured data and
   it is the legally load-bearing structure: which definition defeats which,
   under what condition, at what depth.
2. `behavioural_equivalence` — both encodings executed over an input battery
   driven to the boundary of every condition that appears in either exception
   tree. Boundaries are where two encodings that differ will differ; a battery
   of mid-range values can agree by luck.
3. `structural_note` — a normalised textual diff of the `scopelang` dump.
   Reported, never gating, because its false-positive rate is the reason (1)
   and (2) exist.
"""
from __future__ import annotations

import itertools
import json
import re
import subprocess
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .catala_runner import (
    AssertionFailed,
    CatalaError,
    NoApplicableRule,
    ScopeConflict,
    ExceptionNode,
    shape_signature,
    _run,
    exception_tree,
    json_schema,
    run_scope,
    structural_signature,
    toolchain,
    tree_signature,
    values_agree,
)
from .reviewer import discover_scopes

# --- input battery from condition boundaries -------------------------------

COMPARISON_RE = re.compile(
    r"\b([a-z]\w*)\s*(>=|<=|!=|=|>|<)\s*(-?\d+(?:\.\d+)*)\b"
)
OFFSET_RE = re.compile(r"\b(\d+)\s*(day|month|year)s?\b", re.I)
"""Durations as the compiler prints them in an exception tree: `[6 months]`,
`[30 days]`, `[2 years]`. Always plural. The pattern once required the singular,
which is how the source spells them, so it matched nothing the compiler
printed. Every date battery was then built without a single offset, and a rule
such as "repay half if you leave 6 to 12 months after payment" never had a
vector inside its window."""
PIVOT = date(2026, 6, 15)
"""Fixed pivot for date batteries. Deterministic so a battery is reproducible,
and mid-month/mid-year so month-end and year-end arithmetic is reachable by
offsetting rather than only by luck."""


def collect_date_offsets(trees: list[ExceptionNode]) -> set[tuple[int, str]]:
    """Durations appearing in any condition, e.g. (12, 'month'), (179, 'day').

    These are where two encodings of a date rule will differ, so the battery
    has to straddle them.
    """
    out: set[tuple[int, str]] = set()

    def walk(n: ExceptionNode) -> None:
        for cond in n.conditions:
            for num, unit in OFFSET_RE.findall(cond):
                out.add((int(num), unit.lower()))
        for c in n.exceptions:
            walk(c)

    for t in trees:
        walk(t)
    return out


def _shift(d: date, n: int, unit: str) -> date:
    if unit == "day":
        return d + timedelta(days=n)
    months = n * (12 if unit == "year" else 1)
    y, m = divmod((d.year * 12 + d.month - 1) + months, 12)
    m += 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def date_pool(offsets: set[tuple[int, str]]) -> list[str]:
    """Dates straddling every offset, plus month-end and leap-day cases.

    A date rule is wrong at a boundary or not at all, so the pool is built
    backwards from the pivot by each offset found in the conditions, one day
    either side -- which is exactly where an inclusive/exclusive or a
    rounding-direction disagreement shows up.

    Nothing is thinned out. The pool used to be cut to 9 dates at even
    intervals, and with two offsets that dropped the date exactly on the
    six-month anniversary, which is the date an inclusive/exclusive mistake
    needs. `generate_battery` still caps the cross product, by pinning whole
    inputs rather than losing boundary dates.
    """
    pool = {PIVOT, PIVOT - timedelta(days=1), PIVOT + timedelta(days=1)}
    for n, unit in sorted(offsets):
        base = _shift(PIVOT, -n, unit)
        pool |= {base, base - timedelta(days=1), base + timedelta(days=1)}
    # month-end and leap-day, which is where Catala's date arithmetic raises
    pool |= {date(2024, 2, 29), date(2026, 1, 31)}
    return [d.isoformat() for d in sorted(pool)]


def collect_thresholds(trees: list[ExceptionNode]) -> dict[str, set[float]]:
    """Numeric thresholds mentioned in any condition, per variable."""
    out: dict[str, set[float]] = {}
    def walk(n: ExceptionNode) -> None:
        for cond in n.conditions:
            for var, _op, lit in COMPARISON_RE.findall(cond):
                try:
                    out.setdefault(var, set()).add(float(lit))
                except ValueError:
                    pass
        for c in n.exceptions:
            walk(c)
    for t in trees:
        walk(t)
    return out


def _schema_types(input_schema: dict[str, Any]) -> dict[str, str]:
    """input variable -> catala-ish type name, read off the generated schema."""
    defs = input_schema.get("definitions", {})
    ref = input_schema.get("$ref", "")
    root = defs.get(ref.split("/")[-1], {})
    out: dict[str, str] = {}
    for name, spec in (root.get("properties") or {}).items():
        r = spec.get("$ref", "")
        t = r.split("/")[-1] if r else spec.get("type", "unknown")
        out[name] = t
    return out


def _schema_enums(input_schema: dict[str, Any]) -> dict[str, list[str]]:
    """input variable -> every constructor of its enumeration, for payload-free enums.

    The compiler's schema gives an enumeration as a definition with `"type":
    "string"` and an `enum` list, so the whole domain is stated and nothing has
    to be guessed. An enumeration whose constructors carry content is an object
    schema instead, and is deliberately absent here: its payloads have types
    of their own, and inventing values for them is the guess this module
    refuses to make.
    """
    defs = input_schema.get("definitions", {})
    ref = input_schema.get("$ref", "")
    root = defs.get(ref.split("/")[-1], {})
    out: dict[str, list[str]] = {}
    for name, spec in (root.get("properties") or {}).items():
        target = defs.get((spec.get("$ref") or "").split("/")[-1], spec)
        values = target.get("enum")
        if target.get("type") == "string" and isinstance(values, list) and values:
            out[name] = [str(v) for v in values]
    return out


def generate_battery(
    path: str | Path,
    scope: str,
    thresholds: dict[str, set[float]],
    cap: int = 600,
    date_offsets: set[tuple[int, str]] | None = None,
) -> list[dict[str, Any]]:
    """Input vectors driven to the boundary of every condition.

    For a threshold t on a numeric variable, probes t-1, t and t+1 -- both
    sides plus the boundary itself, because an inclusive/exclusive mistake
    shows up only exactly on it. Booleans get both values. Types come from the
    compiler's own JSON Schema, so a variable we cannot type is skipped rather
    than guessed at.
    """
    in_schema, _ = json_schema(path, scope)
    types = _schema_types(in_schema)
    if not types:
        return []
    enums = _schema_enums(in_schema)

    domains: dict[str, list[Any]] = {}
    for name, t in sorted(types.items()):
        if t == "boolean":
            domains[name] = [False, True]
        elif t in ("integer",):
            ts = sorted(thresholds.get(name, set()))
            vals = {0}
            for x in ts:
                vals.update({int(x) - 1, int(x), int(x) + 1})
            domains[name] = sorted(vals)
        elif t in ("decimal", "money"):
            ts = sorted(thresholds.get(name, set()))
            vals = {0.0}
            for x in ts:
                vals.update({x - 0.01, x, x + 0.01})
            domains[name] = sorted(vals)
        elif t == "date":
            domains[name] = date_pool(date_offsets or set())
        elif name in enums:
            # Every constructor, because an enumeration has no boundary to
            # straddle: Tier 2 is not "between" Tier 1 and Tier 3, and a rule
            # that mishandles one constructor is wrong on that constructor and
            # nowhere else.
            domains[name] = list(enums[name])
        else:
            # durations, lists, structs: no safe generic generator. Returning an
            # empty battery here would report "0 disagreements", which reads as
            # success -- so say so instead.
            raise UngeneratableBattery(
                f"{scope}.{name} has type {t!r}, for which no input battery can be "
                f"generated. Behavioural equivalence CANNOT be checked for this "
                f"scope and must not be reported as passing."
            )

    keys = list(domains)
    total = 1
    for k in keys:
        total *= len(domains[k])
    if total > cap:
        # Vary the variables that carry thresholds fully; pin the rest to a
        # single representative value, so the battery stays targeted at the
        # boundaries rather than exploding combinatorially.
        ranked = sorted(keys, key=lambda k: -len(domains[k]))
        keep = []
        running = 1
        for k in ranked:
            if running * len(domains[k]) <= cap:
                keep.append(k)
                running *= len(domains[k])
        for k in keys:
            if k not in keep:
                domains[k] = [domains[k][len(domains[k]) // 2]]
    return [dict(zip(keys, combo)) for combo in itertools.product(*(domains[k] for k in keys))]


# --- comparisons -----------------------------------------------------------


class UngeneratableBattery(RuntimeError):
    """No input battery can be built for a scope, so behavioural equivalence
    cannot be tested. Raised rather than returning an empty battery, because an
    untested comparison that reports zero disagreements is indistinguishable
    from a passing one."""


@dataclass
class VariableDiff:
    scope: str
    variable: str
    signature_a: str          # label-blind: shape + conditions
    signature_b: str
    labels_a: str = ""        # label-bearing, advisory only
    labels_b: str = ""

    shape_a: str = ""         # depth/arity only, no labels and no condition text
    shape_b: str = ""

    @property
    def equal(self) -> bool:
        """Agreement about the exception STRUCTURE.

        Compares depth and parent/child arity, not condition text. Text is
        confounded by factoring: `ceased_within_qualifying_period` and
        `has_ceased and cessation_date <= qualifying_period_end` are the same
        condition written two ways, and no amount of string normalisation tells
        them apart. What distinguishes *meaning* is behaviour, which
        `compare_encodings` tests directly over a battery driven to every
        boundary in either tree -- so text here would only add false positives.
        Condition differences are still reported, as `conditions_differ`.
        """
        return (self.shape_a or self.signature_a) == (self.shape_b or self.signature_b)

    @property
    def conditions_differ(self) -> bool:
        return self.signature_a != self.signature_b

    @property
    def labels_differ(self) -> bool:
        return bool(self.labels_a) and self.labels_a != self.labels_b


@dataclass
class BehaviourDiff:
    scope: str
    inputs: dict[str, Any]
    output_a: Any
    output_b: Any


@dataclass
class RoundtripResult:
    scopes: list[str] = field(default_factory=list)
    tree_diffs: list[VariableDiff] = field(default_factory=list)
    behaviour_diffs: list[BehaviourDiff] = field(default_factory=list)
    battery_size: int = 0
    structural_note: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def converged(self) -> bool:
        return (
            not self.errors
            and all(d.equal for d in self.tree_diffs)
            and not self.behaviour_diffs
        )

    def summary(self) -> str:
        bad_trees = [d for d in self.tree_diffs if not d.equal]
        lines = [
            f"scopes compared: {', '.join(self.scopes) or '(none)'}",
            f"exception trees: {len(self.tree_diffs) - len(bad_trees)}/{len(self.tree_diffs)} identical",
            f"behaviour: {self.battery_size} input vectors, {len(self.behaviour_diffs)} disagreement(s)",
        ]
        refactored = [d for d in self.tree_diffs if d.equal and d.conditions_differ]
        if refactored:
            lines.append(
                f"  {len(refactored)} hierarchy/hierarchies identical in structure "
                f"but with a condition written differently (a factored predicate, "
                f"not a divergence): "
                + ", ".join(f"{d.scope}.{d.variable}" for d in refactored[:6])
            )
        renamed = [d for d in self.tree_diffs if d.equal and d.labels_differ]
        if renamed:
            lines.append(
                f"  {len(renamed)} hierarchy/hierarchies identical in shape and "
                f"conditions but with different label names (not a divergence): "
                + ", ".join(f"{d.scope}.{d.variable}" for d in renamed[:6])
            )
        for d in bad_trees[:5]:
            lines.append(f"  tree differs at {d.scope}.{d.variable}:")
            lines.append(f"    A: {d.signature_a.replace(chr(10), ' | ')[:150]}")
            lines.append(f"    B: {d.signature_b.replace(chr(10), ' | ')[:150]}")
        for d in self.behaviour_diffs[:5]:
            lines.append(f"  behaviour differs on {json.dumps(d.inputs, sort_keys=True)}")
            lines.append(f"    A: {d.output_a}")
            lines.append(f"    B: {d.output_b}")
        if self.errors:
            lines.append("errors: " + "; ".join(self.errors[:4]))
        lines.append(f"CONVERGED: {self.converged}")
        if self.structural_note:
            lines.append("structural (advisory): " + self.structural_note)
        return "\n".join(lines)


def scopelang_normalised(path: str | Path) -> str:
    """Pretty-printed scopelang with incidental differences removed.

    Advisory only. Alpha-renames nothing -- it merely strips positions,
    collapses whitespace and sorts lines, which is enough to stop reporting
    pure formatting churn while remaining a weak signal.
    """
    proc = _run([toolchain()["catala"], "scopelang", str(path)])
    if proc.returncode != 0:
        return ""
    body = proc.stdout
    body = re.sub(r"[^\s]*\.catala_en:\d+\.\d+-\d+", "", body)
    lines = sorted(re.sub(r"\s+", " ", ln).strip() for ln in body.splitlines() if ln.strip())
    return "\n".join(lines)


def widen_pinned(path: str | Path, scope: str, battery: list[dict[str, Any]],
                 cap: int = 600) -> list[dict[str, Any]]:
    """Add vectors that flip every boolean or enumeration the battery pinned.

    `generate_battery` keeps under its cap by pinning its lowest-cardinality
    inputs to a single value, and for a boolean that removes half the decision
    space silently. Comparing the overtime module with a mutant that turned
    `grade >= 5 and ordinal > 40` into `... or ...` at a cap of 240, every
    vector had `is_public_holiday` pinned to true -- where C-7.1's 2.0 rate
    overrides everything -- so 224 vectors found no difference between rules
    that pay a Grade 3 employee's 41st hour 1.25 and 0.0. An equivalence check
    with that blind spot calls different law the same law.

    So each pinned input is flipped across the whole battery, not a sample,
    up to `cap` extra vectors per input. Only booleans and payload-free
    enumerations are widened, because only their domains are known without
    inventing values. This can only add vectors, so it can only make a
    comparison stricter.
    """
    if not battery:
        return battery
    in_schema, _ = json_schema(path, scope)
    types = _schema_types(in_schema)
    enums = _schema_enums(in_schema)
    out = list(battery)
    seen = {json.dumps(v, sort_keys=True) for v in battery}
    for k in sorted(battery[0]):
        if len({json.dumps(v[k], sort_keys=True) for v in battery}) != 1:
            continue
        domain = [False, True] if types.get(k) == "boolean" else enums.get(k)
        if not domain:
            continue
        added = 0
        for v in battery:
            for value in domain:
                if v[k] == value or added >= cap:
                    continue
                probe = dict(v)
                probe[k] = value
                key = json.dumps(probe, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    out.append(probe)
                    added += 1
    return out


def _observe(path: str | Path, scope: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """What a scope does on one vector, with failures sorted by what they mean.

    A Conflict, a NoValue, a failed assertion or an evaluation error is
    behaviour: two encodings that both refuse the same facts agree, and one
    that refuses where the other answers differs. Anything else -- a module
    that cannot be found, a build that failed -- is not behaviour at all, and
    is returned as `__infra__` so the caller can exclude the vector instead of
    comparing it. Counted as behaviour, the same infrastructure failure on both
    sides would read as agreement and hide any difference; on one side it would
    read as a difference that does not exist.
    """
    try:
        return run_scope(path, scope, inputs)
    except (ScopeConflict, NoApplicableRule, AssertionFailed) as e:
        return {"__error__": type(e).__name__}
    except CatalaError as e:
        if "during evaluation" in e.diagnostic.lower():
            return {"__error__": "RuntimeError"}
        return {"__infra__": " ".join(e.diagnostic.split())[:200]}


def compare_encodings(
    path_a: str | Path,
    path_b: str | Path,
    scope_map: dict[str, str] | None = None,
    cap: int = 600,
) -> RoundtripResult:
    """Compare two Catala encodings of the same law.

    `scope_map` maps a scope name in A to its counterpart in B, for when the
    re-encoding agent chose different names. Scope *names* are surface form and
    must not decide convergence; the hierarchy and the behaviour must.
    """
    res = RoundtripResult()
    scopes_a = discover_scopes(path_a)
    scopes_b = discover_scopes(path_b)
    smap = scope_map or {s: s for s in scopes_a if s in scopes_b}
    if not smap:
        res.errors.append(
            f"no corresponding scopes: A has {sorted(scopes_a)}, B has {sorted(scopes_b)}. "
            f"Supply scope_map."
        )
        return res

    for sa, sb in sorted(smap.items()):
        res.scopes.append(f"{sa}->{sb}" if sa != sb else sa)
        qa, qb = scopes_a[sa], scopes_b[sb]
        # Only OUTPUTS gate. An internal variable is a place the author chose to
        # factor a sub-expression, not a thing the law names: this encoding put
        # "ceased within the qualifying period" in an internal while the
        # re-encoding inlined it, which is the same law written two ways. Demanding
        # a counterpart for every internal made a faithful encoding look divergent
        # -- the same surface-form mistake as gating on label names.
        vars_a = list(qa["output"])
        vars_b = set(qb["output"])

        all_trees: list[ExceptionNode] = []
        for var in vars_a:
            if var not in vars_b:
                res.errors.append(f"{sa}.{var} has no counterpart in {sb}")
                continue
            try:
                ta = exception_tree(path_a, sa, var)
                tb = exception_tree(path_b, sb, var)
            except CatalaError as e:
                res.errors.append(f"exception tree for {var}: {e.diagnostic[:120]}")
                continue
            all_trees.extend(ta)
            res.tree_diffs.append(
                VariableDiff(
                    sa, var,
                    structural_signature(ta), structural_signature(tb),
                    tree_signature(ta), tree_signature(tb),
                    shape_signature(ta), shape_signature(tb),
                )
            )

        thresholds = collect_thresholds(all_trees)
        offsets = collect_date_offsets(all_trees)
        try:
            battery = generate_battery(
                path_a, sa, thresholds, cap=cap, date_offsets=offsets
            )
            battery = widen_pinned(path_a, sa, battery, cap=cap)
        except UngeneratableBattery as e:
            res.errors.append(str(e))
            battery = []
        except CatalaError as e:
            res.errors.append(f"battery for {sa}: {e.diagnostic[:120]}")
            battery = []
        if not battery:
            res.errors.append(
                f"no behavioural battery for {sa}: equivalence is UNTESTED and must "
                f"not be read as agreement"
            )
        res.battery_size += len(battery)

        untested = 0
        first_infra = ""
        for inputs in battery:
            oa = _observe(path_a, sa, inputs)
            ob = _observe(path_b, sb, inputs)
            if "__infra__" in oa or "__infra__" in ob:
                untested += 1
                first_infra = first_infra or oa.get("__infra__") or ob.get("__infra__", "")
                continue
            common = set(oa) & set(ob)
            if not common or not all(values_agree(oa[k], ob[k]) for k in common):
                res.behaviour_diffs.append(BehaviourDiff(sa, inputs, oa, ob))
        if untested:
            res.errors.append(
                f"{sa}: {untested} of {len(battery)} vectors could not be executed at all "
                f"({first_infra[:120]}); they are UNTESTED, not agreed"
            )

    na, nb = scopelang_normalised(path_a), scopelang_normalised(path_b)
    res.structural_note = "identical" if na == nb else "differs (advisory only)"
    return res
