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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catala_runner import (
    CatalaError,
    ExceptionNode,
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
BARE_BOOL_RE = re.compile(r"\b(?:not\s+)?([a-z]\w*)\b")


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


def generate_battery(
    path: str | Path, scope: str, thresholds: dict[str, set[float]], cap: int = 600
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
        else:
            # dates, durations, lists, structs: no safe generic generator.
            return []

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


@dataclass
class VariableDiff:
    scope: str
    variable: str
    signature_a: str          # label-blind: shape + conditions
    signature_b: str
    labels_a: str = ""        # label-bearing, advisory only
    labels_b: str = ""

    @property
    def equal(self) -> bool:
        """Agreement about the law: same shape, same conditions. Label names
        are recorded but do not decide this."""
        return self.signature_a == self.signature_b

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
        vars_a = qa["output"] + qa["internal"]
        vars_b = set(qb["output"] + qb["internal"])

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
                )
            )

        thresholds = collect_thresholds(all_trees)
        try:
            battery = generate_battery(path_a, sa, thresholds, cap=cap)
        except CatalaError as e:
            res.errors.append(f"battery for {sa}: {e.diagnostic[:120]}")
            battery = []
        res.battery_size += len(battery)

        for inputs in battery:
            try:
                oa = run_scope(path_a, sa, inputs)
            except CatalaError as e:
                oa = {"__error__": type(e).__name__}
            try:
                ob = run_scope(path_b, sb, inputs)
            except CatalaError as e:
                ob = {"__error__": type(e).__name__}
            common = set(oa) & set(ob)
            if not common or not all(values_agree(oa[k], ob[k]) for k in common):
                res.behaviour_diffs.append(BehaviourDiff(sa, inputs, oa, ob))

    na, nb = scopelang_normalised(path_a), scopelang_normalised(path_b)
    res.structural_note = "identical" if na == nb else "differs (advisory only)"
    return res
