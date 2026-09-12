"""The Catala gate: what a generated document must survive before a human reads it.

Step 3 of the generation pipeline is "run it through Catala, and if it does not
pass, loop again". This module is what "pass" means. It is deliberately a
*ladder* rather than a single check, because the four cheap rungs cost seconds
and no model time, and the expensive one costs an agent call -- so the cheap
ones run on every iteration and the expensive one runs once, last.

| Gate | Question it answers                                        | Cost    |
|------|------------------------------------------------------------|---------|
| G1   | Does it compile, and do the compiler's own invariants hold? | ~1s     |
| G2   | Is it *total* -- does every output have a value at every boundary? | ~20s |
| G3   | Can every branch we wrote actually fire?                    | free    |
| G4   | Does the module quote the document verbatim?                | free    |
| G5   | Is the *prose* sufficient to reconstruct the logic?         | ~10 min |

## Why G2 tests boundaries rather than samples

`lks.draft.generate_battery` builds input vectors driven to both sides of and
exactly on every numeric threshold and every date offset that appears in any
condition of the scope's exception trees. That is where two encodings differ,
and it is where an inclusive/exclusive mistake lives. A battery of mid-range
values agrees by luck: a rule that says `>` where the document says `>=` is
correct on every input except one, and that one is the boundary.

## Why G3 is worth a gate of its own, and why it is not hypocritical

`docs/DOCUMENT-DEFECTS.md` AMB-01 records a real dead branch in the committed
corpus: EMP-ANNEX-C C-7.2 compares 2.0 against C-4.2's 1.5 plus 0.25, both
literal constants, so its else-branch can never change an output. The module
reproduces that faithfully and *should*, because the source document is
defective and fidelity to the source is the higher duty -- no input can expose
a wrong number, and a future amendment to C-4.2 would silently activate the
clause, which is exactly why it was raised against the document.

None of that applies here. We are drafting the document. A branch that cannot
fire is a clause we wrote that does nothing, and there is no source to be
faithful to. So the same finding that is a *report* about the corpus is a
*failure* for a generated draft, and it took a human reviewer three rounds to
find it the first time.

G3 decides coverage on the compiler's evidence, never on a reading of the
source: the battery is executed with `--trace`, each `DecisionTaken` event
carries the source line of the definition the interpreter applied, and
`ExceptionNode.lines` says which rung of the hierarchy owns that line. A branch
is dead when no vector in the battery ever landed on it.

## Why G4 exists at all

The PDF is the deliverable and the Catala module is the thing that was
verified. Nothing else in this ladder connects them. Without G4 the pipeline
could emit a beautifully typeset document whose logic was proven -- about
different words. So every `|`-gutter quotation in the module must match the
generated document byte for byte, on the same hash `lks.model.content_hash`
uses for the corpus.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .catala_runner import (
    AssertionFailed,
    json_schema,
    CatalaError,
    ExceptionNode,
    NoApplicableRule,
    ScopeConflict,
    exception_tree,
    run_scope_traced,
    typecheck,
)
from .draft import (
    UngeneratableBattery,
    _schema_enums,
    _schema_types,
    collect_date_offsets,
    collect_thresholds,
    generate_battery,
)
from .literate import parse_literate
from .model import content_hash
from .reviewer import discover_scopes
from .segment import ConventionError, parse_document

BATTERY_CAP = 600
"""Input vectors per scope. At ~31 ms per traced execution this is ~19 seconds,
which is cheap enough to spend on every iteration of the repair loop."""


@dataclass
class GateResult:
    id: str
    name: str
    ok: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    skipped: bool = False

    def __str__(self) -> str:
        state = "skip" if self.skipped else ("pass" if self.ok else "FAIL")
        return f"{self.id} {self.name:<26} {state}" + (f"  {self.detail}" if self.detail else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "ok": self.ok,
            "skipped": self.skipped, "detail": self.detail, "evidence": self.evidence,
        }


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """A skipped gate is not a passed gate, but it does not block.

        G5 is skippable by flag and the omission is stamped on the PDF, so the
        reader can see which evidence was not gathered. Every other gate runs
        unconditionally.
        """
        return all(r.ok for r in self.results if not r.skipped)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.ok and not r.skipped]

    def get(self, gate_id: str) -> GateResult | None:
        for r in self.results:
            if r.id == gate_id:
                return r
        return None

    def summary(self) -> str:
        return "\n".join(str(r) for r in self.results)

    def repair_brief(self) -> str:
        """What to hand back to the drafting agent after a failure.

        Deliberately just the compiler's own words plus the exact input vector:
        a paraphrase of a type error is a worse prompt than the type error, and
        an input vector the model can re-derive an expectation from is worth
        more than a description of the shape of the problem.
        """
        out: list[str] = []
        for r in self.failures:
            out.append(f"## {r.id} {r.name} — FAILED")
            out.append(r.detail)
            for key in ("diagnostic", "failing_vector", "error_class", "dead_branches",
                        "problems", "scope", "variable"):
                v = r.evidence.get(key)
                if v:
                    out.append(f"{key}: " + (v if isinstance(v, str) else json.dumps(v, indent=2)))
            out.append("")
        return "\n".join(out)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "gates": [r.to_dict() for r in self.results]}


# --- G1: it compiles ------------------------------------------------------


def g1_typecheck(module: str | Path) -> GateResult:
    r = typecheck(module, check_invariants=True)
    return GateResult(
        "G1", "typecheck", r.ok,
        detail="" if r.ok else "the module does not compile",
        evidence={} if r.ok else {"diagnostic": r.diagnostic[:4000]},
    )


# --- the battery, executed once and read by G2 and G3 ---------------------


@dataclass
class ScopeRun:
    scope: str
    variables: list[str]
    inputs: list[str] = field(default_factory=list)
    battery: list[dict[str, Any]] = field(default_factory=list)
    trees: dict[str, list[ExceptionNode]] = field(default_factory=dict)
    taken_lines: dict[str, set[int]] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    ungeneratable: str = ""
    admitted: list[dict[str, Any]] = field(default_factory=list)
    """Vectors the module's own assertions accepted, and which therefore
    produced an answer. Coverage is measured over these."""
    rejected: list[dict[str, Any]] = field(default_factory=list)
    """Vectors an `assertion` in the module refused. See `g2_totality` for why
    these are a domain statement rather than a failure."""

    @property
    def unvaried(self) -> set[str]:
        """Inputs that took only one value across every admitted vector.

        A condition mentioning one of these has not been tested in both
        directions, so a branch guarded by it is *untested*, not dead. The
        distinction matters because the two have opposite remedies: a dead
        branch means editing the document, an untested one means widening the
        battery.

        Two things cause it. `generate_battery` pins low-cardinality inputs to
        a single value when the full cross product would exceed the cap -- and
        for a boolean that silently removes half the decision space, which is
        how a correct module first reported five dead branches here. Assertions
        rejecting a slice of the battery can also leave an input constant among
        what survived.
        """
        if not self.admitted:
            return set()
        return {
            k for k in self.admitted[0]
            if len({_hashable(v[k]) for v in self.admitted}) == 1
        }


def _hashable(v: Any) -> Any:
    return json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v


@dataclass
class BatteryRun:
    scopes: list[ScopeRun] = field(default_factory=list)
    setup_errors: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(len(s.battery) for s in self.scopes)


def execute_battery(module: str | Path, *, cap: int = BATTERY_CAP) -> BatteryRun:
    """Drive every scope to every boundary, once, with tracing on.

    G2 reads the errors and G3 reads the taken lines. They share one pass
    because each vector is a compiler subprocess, and running the battery twice
    would double the only part of the cheap ladder that is not instant.
    """
    module = Path(module)
    run = BatteryRun()
    try:
        scopes = discover_scopes(module)
    except OSError as e:
        run.setup_errors.append(f"cannot read {module}: {e}")
        return run

    for scope, qual in sorted(scopes.items()):
        # Outputs are what the document promises. Internals are checked too:
        # unlike the equivalence comparison in `lks.draft` -- where an internal
        # is the author's private factoring and must not gate -- here we are
        # the author, so a dead branch in an internal is still a clause of ours
        # that does nothing.
        variables = list(qual["output"]) + list(qual["internal"])
        sr = ScopeRun(
            scope=scope, variables=variables,
            inputs=list(qual["input"]) + list(qual["context"]),
        )
        run.scopes.append(sr)

        all_trees: list[ExceptionNode] = []
        for var in variables:
            try:
                t = exception_tree(module, scope, var)
            except CatalaError as e:
                sr.errors.append({
                    "kind": "exception-tree",
                    "variable": var,
                    "diagnostic": e.diagnostic[:600],
                })
                continue
            sr.trees[var] = t
            all_trees.extend(t)

        try:
            sr.battery = generate_battery(
                module, scope,
                collect_thresholds(all_trees),
                cap=cap,
                date_offsets=collect_date_offsets(all_trees),
            )
        except UngeneratableBattery as e:
            sr.ungeneratable = str(e)
            continue
        except CatalaError as e:
            sr.errors.append({"kind": "battery", "diagnostic": e.diagnostic[:600]})
            continue

        for inputs in sr.battery:
            _execute_one(module, sr, inputs)
        _sweep_unvaried(module, sr)
    return run


def _classify_runtime(diagnostic: str) -> str:
    """Name a runtime failure that `catala_runner` has no class for.

    The runner distinguishes Conflict, NoValue and assertion failures because
    those are the default-logic outcomes. Everything else arrives as a bare
    `CatalaError`, and "CatalaError x3" is not something a drafting agent can
    act on. Division by zero is the one this corpus actually produces -- the
    committed availability module divides by `total_minutes`, and the battery
    probes 0 -- so it gets its own name, and the repair brief can say "guard
    the zero case with an assertion, or define it" instead of nothing.
    """
    low = diagnostic.lower()
    if "denominator in a division" in low or "division by zero" in low:
        return "DivisionByZero"
    if "ambiguous" in low and "date" in low:
        return "AmbiguousDateComputation"
    return "CatalaError"


def _execute_one(module: Path, sr: ScopeRun, inputs: dict[str, Any]) -> None:
    try:
        _outputs, decisions = run_scope_traced(module, sr.scope, inputs)
    except AssertionFailed as e:
        # Not a failure. See `g2_totality`: an assertion is the document's own
        # statement that this vector is not a fact pattern, and
        # `generate_battery` probes 0 for every integer, so a module guarding
        # `ordinal >= 1` rejects a slice of every battery by design.
        sr.rejected.append({"inputs": inputs, "diagnostic": e.diagnostic[:400]})
        return
    except (ScopeConflict, NoApplicableRule) as e:
        sr.errors.append({
            "kind": type(e).__name__, "inputs": inputs, "diagnostic": e.diagnostic[:600],
        })
        return
    except CatalaError as e:
        sr.errors.append({
            "kind": _classify_runtime(e.diagnostic), "inputs": inputs,
            "diagnostic": e.diagnostic[:600],
        })
        return
    sr.admitted.append(inputs)
    for d in decisions:
        line = d.get("line")
        if isinstance(line, int):
            sr.taken_lines.setdefault(d["variable"], set()).add(line)


SWEEP_PER_INPUT = BATTERY_CAP
"""At most this many admitted vectors are re-run with one pinned input flipped."""


def _sweep_unvaried(module: Path, sr: ScopeRun) -> None:
    """Re-run a sample of the battery with each pinned boolean or enum flipped.

    `generate_battery` stays within its cap by pinning its lowest-cardinality
    inputs to one value, and for a boolean that removes half the decision space
    without saying so. It is how the committed NdaSurvival module first showed
    four branches untested: `is_personal_data` was never true.

    Only booleans and payload-free enumerations are swept, because only for
    those is the whole domain known without inventing anything -- the same
    line `lks.draft` draws. Each flip is a real execution, added to the same
    admitted/rejected/errors ledgers, so a sweep can surface a Conflict the
    pinned battery was hiding as well as coverage it was missing.
    """
    unvaried = sr.unvaried
    if not unvaried or not sr.admitted:
        return
    try:
        schema, _ = json_schema(module, sr.scope)
    except CatalaError:
        return
    types = _schema_types(schema)
    enums = _schema_enums(schema)
    # Every admitted vector, not a sample. A 48-vector sample can miss the one
    # combination the pinned input decides -- `lks.draft.widen_pinned` records
    # the comparison that proved it.
    sample = list(sr.admitted)[:SWEEP_PER_INPUT]
    for name in sorted(unvaried):
        if types.get(name) == "boolean":
            domain: list[Any] = [False, True]
        elif name in enums:
            domain = list(enums[name])
        else:
            continue
        for v in sample:
            for value in domain:
                if _hashable(v[name]) == _hashable(value):
                    continue
                probe = dict(v)
                probe[name] = value
                sr.battery.append(probe)
                _execute_one(module, sr, probe)


# --- G2: totality ---------------------------------------------------------

MIN_ADMITTED_FRACTION = 0.25
"""How much of the battery a scope's own assertions must let through.

An assertion is not a failure -- see `g2_totality` -- but it is a way to pass
this gate vacuously. A module whose first line is `assertion false` admits
nothing, produces no counterexample, and would otherwise be reported as total
over zero vectors. So a scope must answer on at least a quarter of its battery,
and on at least one vector, or totality is declared untested instead.

This used to carry an absolute floor of 8 vectors as well, and it was wrong in
a way the corpus exposed at once: a scope whose inputs carry no thresholds gets
a battery of 1 to 6 vectors, and admitting every one of them still fell short
of 8. Seven correct committed modules failed on that alone -- CarryForward
admitted 2 of 2, accommodation 6 of 6 -- with no rejection anywhere. A floor
must be a fraction of what was offered, or small scopes fail for being small.
"""


def g2_totality(run: BatteryRun) -> GateResult:
    """Every output must have exactly one value at every boundary it admits.

    Two ways a Catala encoding is ill-defined rather than merely wrong, and
    both block: `ScopeConflict` -- two applicable definitions with no priority
    between them, which is what overlapping sibling exceptions produce -- and
    `NoApplicableRule`, a case the author forgot to cover.

    `AssertionFailed` is deliberately *not* one of them. An assertion is the
    document's own statement about which fact patterns exist, and
    `generate_battery` probes 0 for every integer variable, so a module that
    guards `assertion ordinal >= 1 and ordinal <= 168` -- as the committed
    overtime module does -- rejects one slice of every battery by design. The
    first version of this gate counted those 16 rejections as failures and
    reported a correct module as broken. A rejected vector is not a fact
    pattern, so it is dropped from the battery and from coverage, and counted.

    What stops that being a loophole is `MIN_ADMITTED_FRACTION`: a scope that
    rejects nearly everything has not demonstrated totality, it has declined to
    be tested, and this gate says so rather than passing.

    A scope for which no battery could be built does not pass either.
    `lks.draft` makes the same call and says why: an untested comparison that
    reports zero disagreements is indistinguishable from a passing one.
    """
    blocking: list[dict[str, Any]] = []
    admitted = 0
    rejected = 0
    for sr in run.scopes:
        admitted += len(sr.admitted)
        rejected += len(sr.rejected)
        if sr.ungeneratable:
            blocking.append({"scope": sr.scope, "kind": "UngeneratableBattery",
                             "diagnostic": sr.ungeneratable})
            continue
        if not sr.battery:
            blocking.append({"scope": sr.scope, "kind": "EmptyBattery",
                             "diagnostic": "no input vectors could be generated, so "
                                           "totality is UNTESTED"})
            continue
        for e in sr.errors:
            blocking.append({"scope": sr.scope, **e})
        floor = max(1, math.ceil(len(sr.battery) * MIN_ADMITTED_FRACTION))
        if len(sr.admitted) < floor:
            blocking.append({
                "scope": sr.scope, "kind": "AssertionsRejectBattery",
                "diagnostic": (
                    f"the module's own assertions rejected "
                    f"{len(sr.rejected)}/{len(sr.battery)} boundary vectors, "
                    f"admitting only {len(sr.admitted)} (floor {floor}). Totality "
                    f"is UNTESTED: either the assertions are too strong, or the "
                    f"scope's inputs are typed more widely than the document's "
                    f"facts."
                ),
                "inputs": (sr.rejected[0]["inputs"] if sr.rejected else {}),
            })

    ok = not blocking and not run.setup_errors
    first = blocking[0] if blocking else {}
    if run.setup_errors:
        detail = "; ".join(run.setup_errors)
    elif blocking:
        kinds: dict[str, int] = {}
        for b in blocking:
            kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
        detail = (
            f"{len(blocking)} failure(s) over {run.size} vectors: "
            + ", ".join(f"{k}×{v}" for k, v in sorted(kinds.items()))
        )
    else:
        detail = f"{admitted} of {run.size} boundary vectors total"
        if rejected:
            detail += f"; {rejected} outside the document's own domain"

    return GateResult(
        "G2", "totality at boundaries", ok, detail=detail,
        evidence={
            "battery_size": run.size,
            "admitted": admitted,
            "rejected_by_assertion": rejected,
            "per_scope": {s.scope: len(s.battery) for s in run.scopes},
            "failures": blocking[:20],
            "error_class": first.get("kind", ""),
            "failing_vector": first.get("inputs", {}),
            "diagnostic": first.get("diagnostic", ""),
            "scope": first.get("scope", ""),
        },
    )


# --- G3: no dead branch ---------------------------------------------------


def branch_index(trees: list[ExceptionNode]) -> list[tuple[str, list[int]]]:
    """Every rung of the hierarchy as (key, source lines that define it).

    The key format matches `lks.reviewer.exception_branches` so the two are
    comparable, and the lines are what make trace attribution possible.
    """
    out: list[tuple[str, list[int]]] = []

    def walk(n: ExceptionNode, prefix: str) -> None:
        key = f"{prefix}{n.label}"
        out.append((f"{key}[{'; '.join(n.conditions)}]", list(n.lines)))
        for c in n.exceptions:
            walk(c, key + "/")

    for t in trees:
        walk(t, "")
    return out


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

_NOT_VARIABLES = frozenset("""
    and or not xor if then else match with pattern content of true false
    exists for all among we have in is such that let equals maximum minimum
    initial sum number list empty unconditional anything or_if_list_empty
    decimal integer money date duration boolean day month year but replace
    output input days months years
""".split())
"""The plural units matter: the compiler prints `[6 months]`, and `months` read
as a variable made every date-offset condition look "derived", so G3 excused an
unreached date branch instead of reporting it."""


def _mentioned(node: ExceptionNode) -> set[str]:
    """Variables in a node's conditions and in every descendant's.

    Descendants count because they decide whether this node is reached at all:
    a base case fires only when none of its exceptions apply, so a base case
    under `is_public_holiday` exceptions has not been tested in both
    directions unless `is_public_holiday` has.

    Catala keywords, casts, duration units, the `_` wildcard and constructors
    (capitalised, like `Present` and `Tier1`) are not variables. Without that
    filter every `match` branch looked as if it conditioned on a variable
    called `match`. Dotted names are kept whole, because `attainment_calc.
    attainment_pct` is a sub-scope's output and not the input
    `attainment_calc`.
    """
    out: set[str] = set()
    for c in node.conditions:
        for ident in _IDENT_RE.findall(c):
            head = ident.split(".")[0]
            if head in _NOT_VARIABLES or head == "_" or head[:1].isupper():
                continue
            out.add(ident)
    for ch in node.exceptions:
        out |= _mentioned(ch)
    return out


def g3_dead_branches(run: BatteryRun) -> GateResult:
    """A branch the battery provably cannot reach is a clause that does nothing.

    Attribution is the compiler's: a node is covered when some `DecisionTaken`
    event in the trace reported one of the source lines that node owns. A node
    with no lines cannot be attributed and is reported, never silently counted
    as covered.

    An uncovered node is classified before it is blamed, because only one kind
    of uncovered is evidence of anything:

    * **dead** -- every variable its conditions (and its exceptions')
      mention is a scope input that the battery varied. The battery drives each
      such input to both sides of and onto every threshold in those conditions,
      so it has visited every cell of the partition they define, and a node no
      cell reached cannot be reached. This blocks.
    * **untested, derived** -- a condition mentions something that is not an
      input: an internal (`days_elapsed`), a sub-scope field
      (`attainment_calc.attainment_pct`), a `match` binding. The battery varies
      inputs, and cannot aim at a value computed from them. Nothing is proven.
    * **untested, never varied** -- a condition mentions an input that took one
      value across every admitted vector, even after `_sweep_unvaried`.

    Untested branches do **not** block, and this is a deliberate reversal of
    the first version of this gate, forced by the corpus. Blocking them failed
    5 of the 19 committed modules, every one of them correct, every one on a
    condition over a derived value -- and no redraft of a document can widen
    what an input battery reaches, so a pipeline that blocked on them would
    spend its repair iterations on nothing and then refuse a sound document.
    They are instead reported in the gate's detail and on the PDF's provenance
    page, by count and by name, so "we could not exercise it" never reads as
    "it is reachable".
    """
    dead: list[dict[str, Any]] = []
    untested: list[dict[str, Any]] = []
    unattributable: list[str] = []
    total = 0
    covered = 0

    for sr in run.scopes:
        if not sr.admitted:
            continue        # G2 already fails this; nothing to say about coverage
        unvaried = sr.unvaried
        inputs = set(sr.inputs)
        for var, trees in sorted(sr.trees.items()):
            taken = sr.taken_lines.get(var, set())
            index = branch_index(trees)
            if len(index) <= 1:
                # A single unconditional definition has no hierarchy to be dead in.
                continue
            nodes = _flatten(trees)
            for (key, lines), node in zip(index, nodes):
                total += 1
                if not lines:
                    unattributable.append(f"{sr.scope}.{var} {key}")
                    continue
                if taken & set(lines):
                    covered += 1
                    continue
                row = {"scope": sr.scope, "variable": var, "branch": key, "lines": lines}
                names = _mentioned(node)
                derived = sorted(n for n in names if n.split(".")[0] not in inputs)
                blind = sorted(names & unvaried)
                if derived:
                    untested.append({**row, "reason": "derived", "derived": derived})
                elif blind:
                    untested.append({**row, "reason": "never_varied", "never_varied": blind})
                else:
                    dead.append({**row, "unconditional": all(
                        c == "<unconditional>" for c in node.conditions)})

    ok = not dead
    if total == 0:
        detail = "no exception hierarchies to cover"
    else:
        parts = [f"{covered}/{total} branches exercised"]
        if dead:
            parts.append(
                f"{len(dead)} provably dead: "
                + ", ".join(f"{d['scope']}.{d['variable']} {d['branch']}" for d in dead[:3])
            )
            if any(d["unconditional"] for d in dead):
                parts.append(
                    "an <unconditional> dead branch is a base case its exceptions always "
                    "override: drop it, or stop the exceptions covering every case"
                )
        if untested:
            derived_n = sum(1 for u in untested if u["reason"] == "derived")
            pinned_n = len(untested) - derived_n
            bits = []
            if derived_n:
                bits.append(f"{derived_n} condition on derived values the battery cannot aim at")
            if pinned_n:
                bits.append(f"{pinned_n} on inputs that never varied")
            parts.append("NOT EXERCISED (does not block): " + "; ".join(bits))
        detail = "; ".join(parts)
    return GateResult(
        "G3", "no dead branch", ok, detail=detail,
        evidence={
            "total": total, "covered": covered,
            "dead_branches": dead[:20],
            "untested_branches": untested[:20],
            "unattributable": unattributable[:20],
        },
    )


def exercised_clauses(module: str | Path, run: BatteryRun) -> set[str]:
    """Clauses every one of whose encoded branches some admitted vector took.

    G3 passing does not mean this. G3 passes with branches it could not aim at
    ("NOT EXERCISED (does not block)"), so the screen's old test -- G3 passed
    and the clause is encoded -- refuted an INOPERATIVE_CLAUSE finding with
    "every branch fired" on exactly the branches that never had. A clause is
    only in this set when the trace shows every rung in every code block that
    quotes it being taken. A rung with no source lines cannot be attributed,
    and makes every block defining its variable unproven.
    """
    lf = parse_literate(module)
    blocks = [(b.start_line, b.end_line, {q.clause_id for q in b.quotes}, b.code)
              for b in lf.blocks if b.kind == "catala"]
    encoded = {cid for *_, ids, _code in blocks for cid in ids}
    unproven: set[str] = set()
    ran = {sr.scope: sr for sr in run.scopes}

    def defines(code: str, scope: str, var: str) -> bool:
        return (re.search(rf"^\s*scope\s+{re.escape(scope)}\b", code, re.M) is not None
                and re.search(rf"\bdefinition\s+{re.escape(var)}\b", code) is not None)

    for _s, _e, ids, code in blocks:
        for scope in re.findall(r"^\s*scope\s+([A-Z]\w*)", code, re.M):
            sr = ran.get(scope)
            if sr is None or not sr.admitted:
                unproven |= ids
    for sr in run.scopes:
        if not sr.admitted:
            continue
        for var in sr.variables:
            if var not in sr.trees:             # its exception tree could not be read
                unproven |= {cid for _s, _e, ids, code in blocks
                             if defines(code, sr.scope, var) for cid in ids}
                continue
            taken = sr.taken_lines.get(var, set())
            for node in _flatten(sr.trees[var]):
                if node.lines and taken & set(node.lines):
                    continue
                if not node.lines:
                    unproven |= {cid for _s, _e, ids, code in blocks
                                 if defines(code, sr.scope, var) for cid in ids}
                    continue
                unproven |= {cid for s, e, ids, _code in blocks
                             if any(s <= ln <= e for ln in node.lines) for cid in ids}
    return encoded - unproven


def _flatten(trees: list[ExceptionNode]) -> list[ExceptionNode]:
    """Nodes in the same pre-order `branch_index` emits keys in."""
    out: list[ExceptionNode] = []

    def walk(n: ExceptionNode) -> None:
        out.append(n)
        for c in n.exceptions:
            walk(c)

    for t in trees:
        walk(t)
    return out


# --- G4: the module quotes the document verbatim --------------------------


def g4_quotation_fidelity(module: str | Path, document: str | Path) -> GateResult:
    """Every `|` quotation in the module must match the document it cites.

    `lks.literate.check_fidelity` does this for the committed corpus and is
    hard-wired to it: it globs `catala/modules` and loads `corpus`. A generated
    pair is neither, and must be checkable before anyone decides it is fit to
    join either -- so the same comparison is done here against one specific
    document, on the same `content_hash` the corpus uses.
    """
    module, document = Path(module), Path(document)
    try:
        doc = parse_document(document)
    except (ConventionError, OSError) as e:
        return GateResult(
            "G4", "quotation fidelity", False,
            detail="the generated document does not parse in the house convention",
            evidence={"diagnostic": str(e)},
        )

    clauses = {c.clause_id: c for c in doc.clauses}
    lf = parse_literate(module)
    problems: list[str] = []

    if not lf.quotations:
        problems.append(
            "the module quotes no clause at all, so nothing ties the verified "
            "logic to the document that will be printed"
        )

    for q in lf.quotations:
        if q.doc_id != doc.doc_id:
            problems.append(
                f"{module.name}:{q.at_line} quotes {q.ref}, but this document is "
                f"{doc.doc_id}"
            )
            continue
        c = clauses.get(q.clause_id)
        if c is None:
            problems.append(
                f"{module.name}:{q.at_line} quotes {q.ref}, which the document "
                f"does not contain"
            )
            continue
        if content_hash(q.text) != c.hash:
            problems.append(
                f"{module.name}:{q.at_line} quotes {q.ref} with text the document "
                f"does not have ({content_hash(q.text)} != {c.hash})"
            )

    for b in lf.blocks:
        if b.kind != "catala":
            continue
        if not b.quotes and not b.no_clause_reason:
            problems.append(
                f"{module.name}:{b.start_line} is a code block encoding no quoted "
                f"clause; quote the clause it encodes or declare it with "
                f"`| NO-CLAUSE: <reason>`"
            )

    quoted = {q.clause_id for q in lf.quotations if q.doc_id == doc.doc_id}
    ok = not problems
    return GateResult(
        "G4", "quotation fidelity", ok,
        detail=(
            f"{len(lf.quotations)} quotation(s) covering {len(quoted)} of "
            f"{len(doc.clauses)} clauses, all verbatim"
            if ok else f"{len(problems)} problem(s)"
        ),
        evidence={
            "problems": problems[:20],
            "quoted_clauses": sorted(quoted),
            "unquoted_clauses": sorted(set(clauses) - quoted),
            "document_clauses": len(doc.clauses),
        },
    )


# --- G5: the prose is sufficient -----------------------------------------


def g5_roundtrip(
    module: str | Path,
    reencoded: str | Path,
    *,
    scope_map: dict[str, str] | None = None,
    cap: int = BATTERY_CAP,
) -> GateResult:
    """Does an independent re-encoding of the prose behave the same way?

    G1-G4 prove the module is sound. None of them tests the thing the user
    actually receives: whether the *document* says what the code computes. This
    does, by the only means available -- hand the prose to an agent that has
    never seen the module, and compare the two encodings on the compiler's
    canonicalised exception trees and on behaviour across the battery.

    The comparison is `lks.draft.compare_encodings`, which already refuses to
    decide convergence on surface form: scope and label names do not gate,
    condition text does not gate (a factored predicate is the same law written
    twice), and internal variables do not gate. Structure and behaviour do.

    The re-encoding itself is produced by the caller, because "an agent that
    has not seen the original" is a property of how the agent was invoked and
    not something this function can assert.
    """
    from .draft import compare_encodings

    try:
        scopes_a, scopes_b = discover_scopes(module), discover_scopes(reencoded)
        subscopes = set(_SUBSCOPE_RE.findall(Path(module).read_text(encoding="utf-8")))
    except OSError as e:
        return GateResult("G5", "roundtrip convergence", False,
                          detail="the encodings could not be read", evidence={"diagnostic": str(e)})
    smap = scope_map or {s: s for s in scopes_a if s in scopes_b}
    try:
        res = compare_encodings(module, reencoded, scope_map=smap or None, cap=cap)
    except CatalaError as e:
        return GateResult(
            "G5", "roundtrip convergence", False,
            detail="the re-encoding could not be compared",
            evidence={"diagnostic": e.diagnostic[:2000]},
        )
    # `compare_encodings` compares the scopes it is given and nothing else, and
    # the generation pipeline's scope map leaves out any scope whose outputs
    # overlap nothing in the re-encoding. So a scope of the module the prose
    # could not reconstruct at all used to be skipped, and G5 passed on the
    # scopes that remained. It is UNTESTED, and blocks. A scope called only as
    # a sub-scope is exempt: its behaviour reaches the comparison through the
    # scope that calls it.
    unmatched = sorted(s for s, q in scopes_a.items()
                       if q["output"] and s not in smap and s not in subscopes)
    # The other direction does not block, but is printed: outputs the prose
    # yielded that the verified module does not encode are computations the
    # PDF states and nothing executed.
    matched_b = set(smap.values())
    not_in_module = sorted(
        [f"{s}.{v}" for s, q in scopes_b.items() if s not in matched_b for v in q["output"]]
        + [f"{sb}.{v}" for sa, sb in smap.items()
           for v in scopes_b.get(sb, {}).get("output", [])
           if v not in scopes_a.get(sa, {}).get("output", [])]
    )
    detail = (
        f"{res.battery_size} vectors, {len(res.behaviour_diffs)} behavioural "
        f"disagreement(s), {sum(1 for d in res.tree_diffs if not d.equal)} "
        f"structural"
    )
    if unmatched:
        detail += (f"; UNTESTED: the re-encoding has no counterpart for "
                   f"{', '.join(unmatched)}")
    if not_in_module:
        detail += (f"; the prose also yielded {', '.join(not_in_module[:6])}, which the "
                   f"module does not encode (does not block)")
    return GateResult(
        "G5", "roundtrip convergence", res.converged and not unmatched,
        detail=detail,
        evidence={"summary": res.summary()[:4000], "unmatched_scopes": unmatched,
                  "not_in_module": not_in_module[:20]},
    )


_SUBSCOPE_RE = re.compile(r"^\s+(?:(?:input|output|internal|context)\s+)?\w+\s+scope\s+([A-Z]\w*)\s*$",
                          re.M)


# --- the cheap ladder, in order ------------------------------------------


def run_cheap_gates(
    module: str | Path, document: str | Path, *, cap: int = BATTERY_CAP
) -> tuple[GateReport, BatteryRun]:
    """G1 to G4. No model calls, so this runs on every iteration.

    G1 short-circuits: a module that does not compile cannot be executed, and
    reporting "0 boundary vectors, all total" underneath a type error would be
    an honest number that reads as reassurance.
    """
    report = GateReport()
    g1 = g1_typecheck(module)
    report.results.append(g1)
    if not g1.ok:
        # Skipped, not failed: G1 already blocks, and a FAIL here put two
        # "not reached" sections into the repair brief next to the one
        # diagnostic the encoder can act on.
        for gid, name in (("G2", "totality at boundaries"), ("G3", "no dead branch")):
            report.results.append(
                GateResult(gid, name, False, detail="not reached: the module does not compile",
                           skipped=True)
            )
        report.results.append(g4_quotation_fidelity(module, document))
        return report, BatteryRun()

    run = execute_battery(module, cap=cap)
    report.results.append(g2_totality(run))
    report.results.append(g3_dead_branches(run))
    report.results.append(g4_quotation_fidelity(module, document))
    return report, run
