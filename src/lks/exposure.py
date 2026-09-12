"""Adjudication: whether an attack reaches a bad outcome, decided by execution.

An agent proposes a fact pattern. This module decides whether it survives. The
decision is never a judgement and never a model's opinion; it is one of six
outcomes, five of which the Catala interpreter reaches on its own and the
sixth of which is a human-authored predicate over the values the interpreter
produced.

    CONFLICT       Catala refuses to choose between two applicable definitions.
                   The company cannot say what its own policy is for this
                   person, and an adversary will propose the reading that pays.
    SILENCE        No definition applies. The corpus does not answer for
                   someone who exists, and silence is construed against the
                   party that drafted it.
    REFUSAL        An assertion rejected facts that are realisable. The policy
                   declines to answer about a person who is really there.
    CONTRADICTION  Two scopes with authority over the same question return
                   different answers on identical facts.
    DIVERGENCE     What the company actually did differs from what its own
                   policy computes.
    ADVERSE        A declared exposure predicate fires on the outputs.

Only ADVERSE needs legal content that is not already in the corpus, and that
content lives in `exposure/predicates.yaml` where it is reviewable, cited and
version-controlled. **A model never writes a predicate and never decides an
outcome.** It chooses inputs. That division is the whole claim: the search is
open-ended and creative, and the adjudication is mechanical, so a bad proposal
costs a subprocess and reaches nobody.

## Why the false positive rate is structurally zero rather than low

A finding is emitted only when the interpreter reached a bad-outcome state on
inputs that satisfy the declared realisable domain and its coherence
constraints. Both halves are checkable by a reader: the state is reproducible
by re-executing the scope, and the domain is a file of stated claims about the
world. There is no step at which something is *probably* a problem.

The cost is that the engine is blind wherever the domain is wrong, which is a
recall problem, not a precision one, and is the direction a queue a lawyer
works must fail in.

## The figure

Where an exposure has an amount, the amount is computed, never estimated:

  * ADVERSE and CONTRADICTION are a difference between two values that were
    both produced by executing something.
  * CONFLICT, SILENCE and REFUSAL have no value at all -- that is what makes
    them findings -- so what is quantified is the *spread*: the range the
    measured output takes across the immediate neighbourhood of the region,
    which is the amount the corpus leaves undetermined.
  * A scope that declares no measure produces findings with `amount: null` and
    says why. An invented number would be worse than no number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from . import surface as sf
from .catala_runner import (
    AssertionFailed,
    CatalaError,
    NoApplicableRule,
    ScopeConflict,
    reads_tree,
    run_scope_traced,
)
from .registry import ScopeEntry, load_registry

PREDICATES_PATH = Path("exposure/predicates.yaml")
QUEUE_DIR = Path("exposure/queue")


class Klass:
    CONFLICT = "CONFLICT"
    SILENCE = "SILENCE"
    REFUSAL = "REFUSAL"
    CONTRADICTION = "CONTRADICTION"
    DIVERGENCE = "DIVERGENCE"
    ADVERSE = "ADVERSE"
    ALL = (CONFLICT, SILENCE, REFUSAL, CONTRADICTION, DIVERGENCE, ADVERSE)

    STRUCTURAL = (CONFLICT, SILENCE, REFUSAL)
    """Reached by the compiler alone. These need no legal content beyond the
    corpus, which is why they are the findings hardest to argue with."""


DIED_UNREALISABLE = "the facts describe nobody: {why}"
DIED_NO_BAD_OUTCOME = "the scope computed an answer and no predicate fired"
DIED_OFF_SCHEMA = "the proposed inputs are not the inputs the scope takes: {why}"


# --- predicates ------------------------------------------------------------

ATOM_RE = re.compile(
    r"^\s*(?P<lhs>[a-z_][a-z0-9_]*)\s*(?P<op>>=|<=|!=|>|<|=)\s*(?P<rhs>.+?)\s*$"
)

_CMP = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _coerce(raw: str) -> Any:
    t = raw.strip().strip('"').strip("'")
    low = t.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return Decimal(t)
    except InvalidOperation:
        return t


def _compare(a: Any, op: str, b: Any) -> bool:
    """Compare an executed value against a predicate's literal.

    Booleans are compared only against booleans, for the reason spelled out in
    `catala_runner._as_bool`: Python truthiness once let a boolean output agree
    with the string "0", and a comparison that loose in an adjudicator would
    let a predicate fire on a scope that is behaving correctly.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        if not (isinstance(a, bool) and isinstance(b, bool)):
            return False
        return _CMP[op](int(a), int(b))
    try:
        return _CMP[op](Decimal(str(a)), Decimal(str(b)))
    except (InvalidOperation, TypeError, ValueError):
        return _CMP[op](str(a), str(b))


@dataclass
class Atom:
    text: str
    lhs: str = ""
    op: str = ""
    rhs: Any = None

    def compile(self) -> "Atom":
        m = ATOM_RE.match(self.text)
        if not m:
            raise ValueError(f"condition {self.text!r} is not `<name> <op> <value>`")
        self.lhs, self.op = m.group("lhs"), m.group("op")
        self.rhs = _coerce(m.group("rhs"))
        return self

    def holds(self, env: dict[str, Any]) -> bool:
        if not self.op:
            self.compile()
        if self.lhs not in env:
            return False
        return _compare(env[self.lhs], self.op, self.rhs)


@dataclass
class Predicate:
    """A bad outcome under the company's own policy, stated by a human.

    `when` is a conjunction over the scope's executed outputs and the inputs it
    ran on. There is no disjunction: two ways of being bad are two predicates,
    which costs a few lines and buys a queue where every entry names exactly
    one theory.
    """

    id: str
    scope: str
    title: str
    archetype: str
    theory: str
    citations: list[str] = field(default_factory=list)
    when: list[Atom] = field(default_factory=list)
    contends_output: str = ""
    """The output the adversary says should have taken a different value."""
    contends_value: Any = None
    """What the adversary says the value should have been.

    A literal, or `@name` to mean another of the scope's executed values --
    which is what a contention of the form "the cap should have been paid"
    needs, and which keeps the figure computed rather than transcribed.
    """
    source: str = "policy"
    """`policy` for an exposure in the documents as they stand; `holding:<id>`
    for one a ruling created. A holding-sourced predicate is the same object,
    which is the point: a new judgment does not need new machinery, only a new
    constraint over the same decision function."""

    def fires(self, env: dict[str, Any]) -> bool:
        return bool(self.when) and all(a.holds(env) for a in self.when)


def load_predicates(path: str | Path = PREDICATES_PATH) -> list[Predicate]:
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or {}
    out = []
    for d in data.get("predicates") or []:
        c = d.get("contends") or {}
        out.append(Predicate(
            id=str(d["id"]),
            scope=str(d["scope"]),
            title=str(d.get("title", "")),
            archetype=str(d.get("archetype", "")),
            theory=str(d.get("theory", "")).strip(),
            citations=[str(x) for x in (d.get("citations") or [])],
            when=[Atom(str(w)).compile() for w in (d.get("when") or [])],
            contends_output=str(c.get("output", "")),
            contends_value=c.get("value"),
            source=str(d.get("source", "policy")),
        ))
    return out


def validate_predicates(
    preds: list[Predicate] | None = None,
    registry: dict[str, ScopeEntry] | None = None,
) -> list[str]:
    """Problems with the declared predicates.

    The rule that matters here is the one `record_counterexample` already
    enforces for the reviewer: a predicate asserts that an outcome is bad in
    law, so it must cite the provisions that make it bad. An uncited predicate
    is somebody's opinion with a dollar sign attached, and a queue of those is
    the report a GC ignores.
    """
    preds = load_predicates() if preds is None else preds
    registry = load_registry() if registry is None else registry
    problems: list[str] = []
    seen: set[str] = set()
    for p in preds:
        if p.id in seen:
            problems.append(f"{p.id}: duplicate predicate id")
        seen.add(p.id)
        entry = registry.get(p.scope)
        if entry is None:
            problems.append(f"{p.id}: names scope {p.scope!r}, which is not registered")
            continue
        known = set(entry.inputs) | set(entry.outputs) | set(entry.internals)
        for a in p.when:
            if a.lhs not in known:
                problems.append(
                    f"{p.id}: condition {a.text!r} names {a.lhs!r}, which is neither "
                    f"an input nor an output of {p.scope}"
                )
        if not p.when:
            problems.append(f"{p.id}: has no conditions, so it would fire on everything")
        if not p.citations:
            problems.append(
                f"{p.id}: cites nothing. A predicate says an outcome is bad in law "
                f"and must name the provisions that make it so"
            )
        if not p.theory.strip():
            problems.append(f"{p.id}: states no theory of liability")
        cv = p.contends_value
        if isinstance(cv, str) and cv.startswith("@") and cv[1:] not in known:
            problems.append(
                f"{p.id}: contends the value of {cv[1:]!r}, which {p.scope} does not produce"
            )
        if p.contends_output and p.contends_output not in entry.outputs:
            problems.append(
                f"{p.id}: contends about {p.contends_output!r}, not an output of {p.scope}"
            )
    return problems


# --- attacks and verdicts --------------------------------------------------

@dataclass
class Attack:
    archetype: str
    scope: str
    facts: dict[str, Any]
    narrative: str = ""
    """The fact pattern in prose, as the proposing role stated it. Carried
    through because a finding that reads as a person is acted on and a finding
    that reads as an input vector is not -- but it is never consulted by the
    adjudicator."""
    citations: list[str] = field(default_factory=list)
    origin: str = "fleet"


@dataclass
class Verdict:
    landed: bool
    klass: str = ""
    why: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    governing: list[dict[str, Any]] = field(default_factory=list)
    predicate: str = ""
    diagnostic: str = ""
    amount: Decimal | None = None
    amount_basis: str = ""
    """How the figure was arrived at, or why there is none. Printed next to
    every amount so no number in this system is ever bare."""


def _env(facts: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    env = dict(facts)
    env.update(outputs)
    return env


@reads_tree
def adjudicate(
    attack: Attack,
    *,
    registry: dict[str, ScopeEntry] | None = None,
    domains: dict[str, sf.ScopeDomain] | None = None,
    predicates: list[Predicate] | None = None,
) -> Verdict:
    """Execute the attack and decide whether it reached a bad outcome.

    Dying is the common case and is silent by design. The only way through is
    an outcome the interpreter actually reached on facts the domain admits.
    """
    registry = registry if registry is not None else load_registry()
    entry = registry.get(attack.scope)
    if entry is None:
        return Verdict(False, why=DIED_OFF_SCHEMA.format(why=f"no scope {attack.scope!r}"))

    domains = domains if domains is not None else sf.load_domains()
    sd = domains.get(attack.scope)
    if sd is None:
        return Verdict(False, why=f"{attack.scope} has no declared realisable domain")

    missing = sorted(set(entry.inputs) - set(attack.facts))
    extra = sorted(set(attack.facts) - set(entry.inputs))
    if missing or extra:
        return Verdict(False, why=DIED_OFF_SCHEMA.format(
            why=f"missing {missing}, unexpected {extra}"))

    if not sd.realisable(attack.facts):
        return Verdict(False, why=DIED_UNREALISABLE.format(
            why=sd.unrealisable_reason(attack.facts)))

    try:
        outputs, decisions = run_scope_traced(entry.path, entry.scope, attack.facts)
    except ScopeConflict as e:
        return Verdict(True, Klass.CONFLICT,
                       "two definitions apply and the documents establish no priority",
                       diagnostic=e.diagnostic[:900])
    except NoApplicableRule as e:
        return Verdict(True, Klass.SILENCE,
                       "no definition applies to a case that can arise",
                       diagnostic=e.diagnostic[:900])
    except AssertionFailed as e:
        return Verdict(True, Klass.REFUSAL,
                       "the policy refuses to answer about facts that are realisable",
                       diagnostic=e.diagnostic[:900])
    except CatalaError as e:
        return Verdict(False, why=f"the scope could not be executed: {e.diagnostic[:200]}")

    index = sf.clause_index(entry.path)
    governing = [
        {"variable": d["variable"], "line": d["line"],
         "law_headings": d.get("law_headings") or [],
         "clause_refs": sf.refs_at_line(index, d.get("line"))}
        for d in decisions
    ]
    env = _env(attack.facts, outputs)
    for p in (predicates if predicates is not None else load_predicates()):
        if p.scope != attack.scope or not p.fires(env):
            continue
        v = Verdict(True, Klass.ADVERSE, p.title or p.id, outputs=outputs,
                    governing=governing, predicate=p.id)
        v.amount, v.amount_basis = contended_amount(p, env, sd)
        return v
    return Verdict(False, why=DIED_NO_BAD_OUTCOME, outputs=outputs, governing=governing)


# --- what it costs ---------------------------------------------------------

def _dec(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def contended_amount(
    p: Predicate, env: dict[str, Any], sd: sf.ScopeDomain
) -> tuple[Decimal | None, str]:
    """The difference between what the policy paid and what the adversary says
    it owes, per unit of the population."""
    if not p.contends_output:
        return None, (
            f"{p.id} contends no figure, so this exposure is stated without one "
            f"rather than with an invented one"
        )
    contended = p.contends_value
    if isinstance(contended, str) and contended.startswith("@"):
        ref = contended[1:]
        if ref not in env:
            return None, f"{p.id} contends {contended}, which the scope did not produce"
        contended = env[ref]
    got, want = _dec(env.get(p.contends_output)), _dec(contended)
    if got is None or want is None:
        return None, (
            f"{p.contends_output} is not a quantity, so the exposure is "
            f"qualitative rather than costed"
        )
    delta = abs(want - got)
    unit = sd.measure_unit or "units"
    ref_note = (f" (the executed value of {p.contends_value[1:]})"
                if isinstance(p.contends_value, str) and p.contends_value.startswith("@")
                else "")
    basis = (f"{p.contends_output} computed as {got}, contended to be {want}{ref_note}; "
             f"difference {delta} {unit} per {sd.population or 'case'}")
    if sd.scale_by:
        basis += f", to be multiplied by {sd.scale_by} for currency"
    return delta, basis


def undetermined_amount(
    surface: sf.Surface, region: sf.Region, sd: sf.ScopeDomain
) -> tuple[Decimal | None, str]:
    """The range the measured output takes in the immediate neighbourhood of a
    region the corpus does not decide.

    A conflicted or silent region has no value, which is exactly what makes it
    a finding -- so the quantity worth reporting is how much turns on the
    question the corpus failed to answer. That is bounded by what the
    neighbouring cases pay: cells one axis-step away, which is where the
    argument about the right answer will actually be conducted.
    """
    out = sd.measure_output
    if not out:
        return None, (
            f"{sd.key} declares no measured output, so its structural findings "
            f"are reported without a figure"
        )
    ex = region.exemplar()
    vals: list[Decimal] = []
    for r in surface.regions:
        if r.outcome != sf.OUTCOME_COMPUTED:
            continue
        for c in r.cells:
            diff = sum(1 for k, v in c.inputs.items() if ex.get(k) != v)
            if diff != 1:
                continue
            d = _dec(c.outputs.get(out))
            if d is not None:
                vals.append(d)
    scope_note = "the cells one fact away from it"
    if not vals:
        for r in surface.regions:
            if r.outcome != sf.OUTCOME_COMPUTED:
                continue
            for c in r.cells:
                d = _dec(c.outputs.get(out))
                if d is not None:
                    vals.append(d)
        scope_note = "every decided cell of the scope (no immediate neighbour is decided)"
    if not vals:
        return None, f"no decided cell of {sd.key} produces a value for {out}"
    lo, hi = min(vals), max(vals)
    unit = sd.measure_unit or "units"
    basis = (f"{out} ranges {lo} to {hi} {unit} across {scope_note}; the corpus "
             f"leaves {hi - lo} per {sd.population or 'case'} undetermined here")
    if sd.scale_by:
        basis += f", to be multiplied by {sd.scale_by} for currency"
    return hi - lo, basis


# --- contradictions between documents -------------------------------------

@dataclass
class Rivalry:
    """Two scopes asserted to answer the same question, and the outputs that
    must agree. Declared, because nothing in the corpus says that two modules
    are talking about the same thing."""

    left: str
    right: str
    question: str
    agree_on: list[tuple[str, str]] = field(default_factory=list)
    shared_inputs: dict[str, str] = field(default_factory=dict)
    """Keyed by the right scope's input name, valued with the left scope's.
    The grid comes from the left side and the right side is fed from it, so the
    mapping reads in that direction."""
    fixed_inputs: dict[str, Any] = field(default_factory=dict)
    """Inputs the right scope takes that the left has no counterpart for,
    pinned to the value under which the two are asserted to agree. This is
    where a rivalry's claim actually lives: two scopes agree *given* some state
    of the world, and pinning it says which one, in the file, where it can be
    argued with."""
    citations: list[str] = field(default_factory=list)


def load_rivalries(path: str | Path = PREDICATES_PATH) -> list[Rivalry]:
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or {}
    out = []
    for d in data.get("rivalries") or []:
        out.append(Rivalry(
            left=str(d["left"]), right=str(d["right"]),
            question=str(d.get("question", "")),
            agree_on=[(str(a["left"]), str(a["right"])) for a in (d.get("agree_on") or [])],
            shared_inputs={str(k): str(v) for k, v in (d.get("shared_inputs") or {}).items()},
            fixed_inputs=dict(d.get("fixed_inputs") or {}),
            citations=[str(x) for x in (d.get("citations") or [])],
        ))
    return out


@dataclass
class Contradiction:
    rivalry: Rivalry
    facts: dict[str, Any]
    left_value: Any = None
    right_value: Any = None
    field: str = ""
    error: str = ""

    @property
    def headline(self) -> str:
        if self.error:
            return f"{self.rivalry.left} and {self.rivalry.right} cannot be compared: {self.error}"
        return (f"{self.rivalry.left} says {self.left_value} and "
                f"{self.rivalry.right} says {self.right_value} about {self.field}")


def check_rivalries(
    rivalries: list[Rivalry] | None = None,
    *,
    registry: dict[str, ScopeEntry] | None = None,
    domains: dict[str, sf.ScopeDomain] | None = None,
) -> list[Contradiction]:
    """Execute both sides of every declared rivalry over the left side's grid.

    Two documents that disagree about the same question is the one finding a
    company cannot argue with, because there is no reading to prefer: both
    answers are its own. The grid comes from the left scope's borders, so the
    comparison happens where the clauses actually change behaviour rather than
    at arbitrary points.

    Inputs the right scope names differently are mapped through the declared
    `shared_inputs`; inputs it takes and the left does not are left to the
    right scope's own declared domain, and where it has none the pair is
    reported as uncomparable rather than compared on invented values.
    """
    registry = registry if registry is not None else load_registry()
    domains = domains if domains is not None else sf.load_domains()
    out: list[Contradiction] = []
    for riv in (rivalries if rivalries is not None else load_rivalries()):
        left, right = registry.get(riv.left), registry.get(riv.right)
        if left is None or right is None:
            out.append(Contradiction(riv, {}, error="one side is not a registered scope"))
            continue
        try:
            surf = sf.partition(riv.left, domains=domains, registry=registry)
        except (KeyError, CatalaError) as e:
            out.append(Contradiction(riv, {}, error=f"the left side cannot be mapped: {e}"))
            continue
        rd = domains.get(riv.right)
        for region in surf.regions:
            if region.outcome != sf.OUTCOME_COMPUTED:
                continue
            cell = region.exemplar()
            facts = {}
            for name in right.inputs:
                src = riv.shared_inputs.get(name, name)
                if name in riv.fixed_inputs:
                    facts[name] = riv.fixed_inputs[name]
                elif src in cell:
                    facts[name] = cell[src]
                elif rd is not None and name in rd.inputs:
                    ep = rd.inputs[name].endpoints()
                    if not ep:
                        facts = {}
                        break
                    facts[name] = ep[0]
                else:
                    facts = {}
                    break
            if not facts:
                out.append(Contradiction(
                    riv, cell,
                    error=f"{riv.right} takes inputs the left side does not supply and "
                          f"no domain declares; nothing was executed"))
                break
            try:
                got = run_scope_traced(right.path, right.scope, facts)[0]
            except CatalaError as e:
                out.append(Contradiction(riv, facts, error=f"{type(e).__name__} on the right side: "
                                                           f"{e.diagnostic[:150]}"))
                continue
            for lf, rf in riv.agree_on:
                lv = next((c.outputs.get(lf) for c in region.cells), None)
                rv = got.get(rf)
                if _dec(lv) is not None and _dec(rv) is not None:
                    if _dec(lv) == _dec(rv):
                        continue
                elif lv == rv:
                    continue
                out.append(Contradiction(riv, facts, lv, rv, field=f"{lf}/{rf}"))
    return out


# --- the queue -------------------------------------------------------------

@dataclass
class Exposure:
    id: str
    klass: str
    scope: str
    archetype: str
    headline: str
    facts: dict[str, Any]
    why: str = ""
    predicate: str = ""
    citations: list[str] = field(default_factory=list)
    clause_chain: list[dict[str, Any]] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    amount: str | None = None
    amount_basis: str = ""
    population: str = ""
    diagnostic: str = ""
    narrative: str = ""
    demand_letter: str = ""
    closing_edit: dict[str, Any] = field(default_factory=dict)
    origin: str = "fleet"
    headline_direction: str = ""
    """For a divergence: `less`, `more`, `different` or `unanswerable`. Which
    way it runs decides whether it is a claim against the company or evidence
    to be used against the company's own reading, and those are not the same
    finding. Empty for every other class."""
    found: str = ""
    status: str = "open"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Exposure":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def fingerprint(self) -> str:
        """What makes two findings the same finding.

        The class, the scope, the predicate and the clauses that governed --
        not the facts, because a fleet running continuously will reach the same
        legal hole from a thousand fact patterns and a queue with a thousand
        copies of one problem is a queue nobody works.
        """
        refs = sorted({r for g in self.clause_chain for r in g.get("clause_refs", [])})
        return f"{self.klass}|{self.scope}|{self.predicate}|{','.join(refs)}"


def _next_id(store: Path) -> str:
    n = 0
    for p in store.glob("EXP-*.yaml"):
        m = re.match(r"EXP-(\d+)", p.stem)
        if m:
            n = max(n, int(m.group(1)))
    return f"EXP-{n + 1:04d}"


def load_queue(store: str | Path = QUEUE_DIR) -> list[Exposure]:
    store = Path(store)
    if not store.exists():
        return []
    return [Exposure.from_dict(yaml.safe_load(p.read_text()))
            for p in sorted(store.glob("EXP-*.yaml"))]


def save_exposure(e: Exposure, store: str | Path = QUEUE_DIR) -> Path:
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    path = store / f"{e.id}.yaml"
    path.write_text(yaml.safe_dump(e.to_dict(), sort_keys=False, width=88,
                                   allow_unicode=True))
    return path


def record(
    attack: Attack,
    verdict: Verdict,
    *,
    domains: dict[str, sf.ScopeDomain] | None = None,
    predicates: list[Predicate] | None = None,
    store: str | Path = QUEUE_DIR,
) -> Exposure | None:
    """Put a surviving attack on the queue, unless the hole is already there.

    Returns None for a duplicate, which is not a failure: it is the loop
    confirming a known hole by another route.
    """
    if not verdict.landed:
        return None
    domains = domains if domains is not None else sf.load_domains()
    sd = domains.get(attack.scope) or sf.ScopeDomain(key=attack.scope)
    preds = {p.id: p for p in (predicates if predicates is not None else load_predicates())}
    p = preds.get(verdict.predicate)

    if verdict.klass in Klass.STRUCTURAL and verdict.amount is None:
        # A conflicted, silent or refusing case has no value at all, so what
        # can be costed is the spread its neighbours pay. Partitioning is
        # cached against the module's content, so this is a lookup after the
        # first finding in a scope.
        try:
            surf = sf.partition(attack.scope)
            region = sf.locate(surf, attack.facts)
            if region is not None:
                verdict.amount, verdict.amount_basis = undetermined_amount(surf, region, sd)
            else:
                verdict.amount_basis = (
                    "the facts lie off the mapped grid, so the undetermined "
                    "amount was not computed"
                )
        except (KeyError, CatalaError) as e:
            verdict.amount_basis = f"the surface could not be built to cost this: {e}"

    e = Exposure(
        id="EXP-0000",
        klass=verdict.klass,
        scope=attack.scope,
        # Who will bring it, not who found it. A predicate names the party whose
        # theory this is; the role that stumbled into the fact pattern may have
        # been playing somebody else entirely, and the queue is read by
        # somebody asking who is going to sue. The finder is kept in `origin`.
        archetype=(p.archetype if p and p.archetype else attack.archetype),
        headline=verdict.why,
        facts=dict(attack.facts),
        why=(p.theory if p else verdict.why),
        predicate=verdict.predicate,
        citations=(list(p.citations) if p else
                   checked_citations(attack.citations, attack.scope, verdict.governing)),
        clause_chain=list(verdict.governing),
        outputs=dict(verdict.outputs),
        amount=(str(verdict.amount) if verdict.amount is not None else None),
        amount_basis=verdict.amount_basis,
        population=sd.population,
        diagnostic=verdict.diagnostic,
        narrative=attack.narrative,
        origin=attack.origin,
        found=date.today().isoformat(),
    )
    existing = {x.fingerprint for x in load_queue(store)}
    if e.fingerprint in existing:
        return None
    return create_exposure(e, store)


def checked_citations(citations: list[str], scope: str,
                      governing: list[dict[str, Any]]) -> list[str]:
    """The proposing role's citations that name a real clause, resolved.

    A finding with no predicate -- a conflict, a silence, a refusal -- has no
    human-written citations, so it carried the role's own onto the queue and
    into the demand letter's "provisions relied on", unchecked. That was the
    one place a model's words reached a finding as law. A citation that names
    no clause is dropped; the finding survived on execution and does not rest
    on it."""
    from .reviewer import corpus_refs, resolve_citation

    entry = load_registry().get(scope)
    shown = set(entry.encodes) if entry else set()
    shown |= {r for g in governing for r in g.get("clause_refs") or []}
    corpus = corpus_refs()
    out: list[str] = []
    for c in citations:
        ref = resolve_citation(c, shown, corpus)
        if ref and ref not in out:
            out.append(ref)
    return out


def create_exposure(e: Exposure, store: str | Path = QUEUE_DIR) -> Exposure:
    """Give `e` the next free id and write it, exclusively.

    The fleet from the command line, a fleet job in the interface and the
    operations watcher can all record at once; two of them scanning the queue
    at the same moment would take the same id, and the second write would
    replace the first finding."""
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    while True:
        e.id = _next_id(store)
        try:
            with (store / f"{e.id}.yaml").open("x", encoding="utf-8") as fh:
                fh.write(yaml.safe_dump(e.to_dict(), sort_keys=False, width=88,
                                        allow_unicode=True))
            return e
        except FileExistsError:
            continue


def summary(store: str | Path = QUEUE_DIR) -> dict[str, Any]:
    q = load_queue(store)
    by_class: dict[str, int] = {}
    for e in q:
        by_class[e.klass] = by_class.get(e.klass, 0) + 1
    return {
        "total": len(q),
        "open": sum(1 for e in q if e.status == "open"),
        "by_class": by_class,
        "structural": sum(1 for e in q if e.klass in Klass.STRUCTURAL),
        "costed": sum(1 for e in q if e.amount is not None),
    }
