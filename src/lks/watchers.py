"""Standing watchers on the boundary between the corpus and the world.

The corpus is static. Nothing else is. Three things arrive from outside and
each of them can make a document that was fine yesterday indefensible today:

  * a ruling, which changes what the documents are allowed to mean;
  * somebody else's paper, which has to compose with ours;
  * our own operations, which are what we actually did as distinct from what we
    wrote down.

All three reduce to the same computation. The corpus is a decision function;
each watcher produces a constraint or a fact vector; the constraint is run over
the function's regions, or the fact vector through the function, and what comes
back is an outcome nobody had to judge.

## Which of these is the one that goes quiet

The operations watcher. A hypothetical exposure is an argument about a
document. A divergence is a list of decisions the company actually made that
its own written policy does not produce -- each with a date, a subject and two
numbers. There is nothing to debate and no reading to prefer; either the
policy computes what was paid or it does not.

It is also the only watcher that can put a real population on a region of the
map, which is what turns "the corpus is silent here" into "the corpus is
silent about these eleven people".

## The ruling watcher does not get to change the rules

A formalised holding lands in `exposure/holdings/proposed/`, never in
`exposure/predicates.yaml`. It can be *executed* from there, so the question
"what would this ruling do to us" is answerable in seconds -- but it does not
start producing findings against the live corpus until a person moves it
across. That is the same discipline `ingest/proposals/` applies to documents,
for the same reason: an agent reading a judgment is proposing legal content,
and legal content in this system is adjudicated by a human before it binds.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from . import exposure, llm
from . import surface as sf
from .agents import Role, RoleResult, run_role
from .catala_runner import CatalaError, run_scope_traced, values_agree
from .registry import load_registry

OPERATIONS = Path("operations/decisions.jsonl")
HOLDINGS_IN = Path("exposure/holdings/incoming")
HOLDINGS_PROPOSED = Path("exposure/holdings/proposed")


# --- watcher 1: our own operations ----------------------------------------

@dataclass
class Decision:
    """One decision the company actually made."""

    id: str
    scope: str
    subject: str
    decided_on: str
    facts: dict[str, Any]
    actual: dict[str, Any]
    source: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Decision":
        return cls(
            id=str(d["id"]), scope=str(d["scope"]), subject=str(d.get("subject", "")),
            decided_on=str(d.get("decided_on", "")), facts=d.get("facts") or {},
            actual=d.get("actual") or {}, source=str(d.get("source", "")),
        )


def load_decisions(path: str | Path = OPERATIONS) -> list[Decision]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        if not isinstance(d, dict) or "id" not in d or "scope" not in d:
            # JSONL has nowhere to put a header, so a feed carries its
            # provenance as an object of its own. Anything that does not name
            # a decision and a scope is not one.
            continue
        out.append(Decision.from_dict(d))
    return out


def _as_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


@dataclass
class Divergence:
    decision: Decision
    field: str
    recorded: Any
    computed: Any
    clause_refs: list[str] = field(default_factory=list)
    error: str = ""
    favours: str = ""
    """Who a larger value of this field favours, from the scope's declared
    domain. Empty where nobody has declared it."""

    @property
    def direction(self) -> str:
        """Which way the divergence runs, which decides what it is evidence of.

        Raw comparison only: whether the recorded value was above or below the
        computed one. Who that favours is `generosity`, and needs the declared
        polarity, because a larger figure is good news for opposite parties
        depending on which figure it is.
        """
        if self.error:
            return "unanswerable"
        a, b = _as_number(self.recorded), _as_number(self.computed)
        if a is None or b is None:
            return "different"
        return "more" if a > b else "less"

    @property
    def generosity(self) -> str:
        """Whether the decision was `more` or `less` favourable to the other
        party than the policy provides, or `""` where nobody has said which way
        this field points.

        A decision LESS favourable than the policy is a claim by the person it
        was made about. A decision MORE favourable is not a gift, it is
        evidence: the company is not applying the construction it would have to
        rely on to refuse somebody else. Conflating the two produces a queue
        that demands payment from the company for having paid, which is how a
        tool loses a reader.
        """
        if self.direction in ("unanswerable", "different") or not self.favours:
            return ""
        if self.favours == "counterparty":
            return self.direction
        return "less" if self.direction == "more" else "more"

    @property
    def headline(self) -> str:
        if self.error:
            return (f"the policy cannot answer for {self.subject_short}: {self.error}")
        word = {"more": "more than", "less": "less than",
                "different": "something other than"}.get(self.direction, "")
        return (f"{self.field} was recorded as {self.recorded}, {word} the "
                f"{self.computed} the policy computes")

    @property
    def subject_short(self) -> str:
        return self.decision.subject or self.decision.id


def check_operations(
    decisions: list[Decision] | None = None, *, registry=None
) -> list[Divergence]:
    """Execute the corpus over decisions the company actually made.

    Every field a decision record claims is compared against what the scope
    computes from the same facts, using the same value comparison the
    counterexample suite uses -- so a money figure written as a quoted string
    in an export still compares numerically, and a boolean never agrees with
    something that is not one.

    A scope that refuses or conflicts on a real decision is itself a
    divergence, and a more serious one: the company did something its written
    policy cannot even evaluate.
    """
    registry = registry if registry is not None else load_registry()
    domains = sf.load_domains()
    out: list[Divergence] = []
    for d in decisions if decisions is not None else load_decisions():
        entry = registry.get(d.scope)
        if entry is None:
            out.append(Divergence(d, "", None, None, error=f"no scope {d.scope!r}"))
            continue
        try:
            outputs, decs = run_scope_traced(entry.path, entry.scope, d.facts)
        except CatalaError as e:
            out.append(Divergence(d, "", None, None,
                                  error=f"{type(e).__name__}: {e.diagnostic[:200]}"))
            continue
        index = sf.clause_index(entry.path)
        for fld, recorded in d.actual.items():
            if fld not in outputs:
                out.append(Divergence(d, fld, recorded, None,
                                      error=f"{entry.scope} produces no {fld!r}"))
                continue
            computed = outputs[fld]
            if values_agree(recorded, computed, money=True):
                continue
            line = next((x["line"] for x in decs if x["variable"] == fld), None)
            sd = domains.get(d.scope)
            out.append(Divergence(
                d, fld, recorded, computed,
                clause_refs=sf.refs_at_line(index, line),
                favours=(sd.favours.get(fld, "") if sd else ""),
            ))
    return out


def _divergence_theory(d: "Decision", dv: "Divergence") -> str:
    head = (f"On {d.decided_on} the company decided this case for "
            f"{dv.subject_short}. Its own written policy, executed on the same "
            f"facts, does not produce that decision. Source: "
            f"{d.source or 'unstated'}.")
    if dv.generosity == "less":
        return head + (
            " The decision was less favourable than the policy provides, so it "
            "is a claim by the person it was made about, for the difference, "
            "and by everyone else the same clause was applied to the same way.")
    if dv.generosity == "more":
        return head + (
            " The decision was MORE favourable than the policy provides. That "
            "is not a loss and there is nobody to pay; it is evidence. The "
            "company is not applying the construction of this clause that it "
            "would have to rely on to refuse somebody else, and the first "
            "person it does refuse will put its own records to it. Where an "
            "exposure on this queue turns on that construction, this is what "
            "the other side cites.")
    if dv.direction in ("more", "less") and not dv.generosity:
        return head + (
            f" Nobody has declared which party a larger {dv.field} favours, so "
            f"this is reported as a difference and not as a gain by either "
            f"side. Declare it under `favours` in exposure/domains.yaml and it "
            f"will be read correctly.")
    if dv.direction == "unanswerable":
        return head + (
            " The policy cannot evaluate these facts at all, which is the more "
            "serious case: the company did something its written policy does "
            "not merely disagree with but cannot express.")
    return head


def record_divergences(
    divs: list[Divergence], *, store: str | Path = exposure.QUEUE_DIR
) -> list[exposure.Exposure]:
    """Put divergences on the same queue as everything else.

    Deliberately the same queue. A lawyer working it should not have to hold
    two mental models: an exposure is an exposure whether it was imagined by an
    agent or read off last month's payroll, and the ones read off payroll
    should sort to the top on their own merits rather than by living somewhere
    else.
    """
    domains = sf.load_domains()
    out = []
    for dv in divs:
        d = dv.decision
        sd = domains.get(d.scope) or sf.ScopeDomain(key=d.scope)
        amount = None
        basis = ""
        if not dv.error and sd.measure_output == dv.field:
            try:
                from decimal import Decimal

                amount = abs(Decimal(str(dv.recorded)) - Decimal(str(dv.computed)))
                basis = (f"recorded {dv.recorded}, policy computes {dv.computed}; "
                         f"difference {amount} {sd.measure_unit or 'units'} on one "
                         f"{sd.population or 'case'}")
            except Exception:
                amount = None
        e = exposure.Exposure(
            id="EXP-0000",
            klass=exposure.Klass.DIVERGENCE,
            scope=d.scope,
            archetype="auditor reconciling the record against the policy",
            headline=dv.headline,
            facts=dict(d.facts),
            why=_divergence_theory(d, dv),
            citations=list(dv.clause_refs),
            clause_chain=[{"variable": dv.field, "line": None,
                           "clause_refs": list(dv.clause_refs), "law_headings": []}],
            outputs={dv.field: dv.computed} if dv.field else {},
            amount=(str(amount) if amount is not None else None),
            amount_basis=basis or (
                f"{d.scope} declares no measured output for {dv.field!r}, so this "
                f"divergence is reported without a figure"),
            population=sd.population,
            diagnostic=dv.error,
            narrative=f"{d.subject} ({d.id}, decided {d.decided_on})",
            headline_direction=(dv.generosity or dv.direction),
            origin=f"operations:{d.id}",
            found=date.today().isoformat(),
        )
        # A divergence is about one decision, so it is not deduplicated against
        # the clause-level fingerprint the fleet uses: ten employees underpaid
        # by the same clause are ten facts, not one argument.
        out.append(exposure.create_exposure(e, store))
    return out


def populate(surface: sf.Surface, decisions: list[Decision] | None = None) -> sf.Surface:
    """Count real decisions into the regions they fall in.

    This is what gives a region on the map an area that means something. A
    region with no decisions in it is drawn small and is not thereby safe --
    nobody has been there *that we have records of*, which for a policy is
    often the most dangerous kind of region rather than the least.
    """
    for d in decisions if decisions is not None else load_decisions():
        if d.scope != surface.key:
            continue
        r = sf.locate(surface, d.facts)
        if r is not None:
            r.population += 1
    return surface


# --- watcher 2: rulings ----------------------------------------------------

HOLDING = Role(
    name="holding",
    purpose="Formalise the holding of a ruling as a constraint over our own "
            "decision function.",
    reads=["exposure/holdings/incoming", "corpus"],
    forbidden=["exposure/predicates.yaml", "src", "docs", "tests"],
    temperature=0.1,
    num_predict=2048,
    system=(
        "You read a court or tribunal decision and state its holding as a "
        "constraint on what a company's policy is allowed to produce.\n\n"
        "You are given the ruling and the machine interface of one compiled "
        "rule: the facts it takes and the values it produces. Express the "
        "holding as the combination of facts and outcomes that the ruling makes "
        "indefensible. Not what the case was about -- what our rule must now "
        "never do.\n\n"
        "Each condition is `<name> <op> <value>` and they are joined by AND. "
        "A condition may name EITHER a fact the rule takes OR a value the rule "
        "produces, and a holding almost always needs both: a combination of "
        "circumstances, plus the outcome that is no longer defensible in those "
        "circumstances. For a ruling that senior staff working long hours may "
        "no longer be paid nothing, against a rule taking `grade` and `hours` "
        "and producing `rate`, the constraint is "
        '["grade >= 5", "hours > 40", "rate = 0.0"] — the facts that bring the '
        "ruling into play, and the outcome it forbids.\n\n"
        "Do not require the rule to have an input named after a legal concept. "
        "A holding bites on what a rule PRODUCES in given circumstances, not on "
        "how it is implemented, and a rule that reaches the forbidden outcome "
        "is caught however it got there. Only answer "
        '{"applicable": false, "why": "..."} where the rule genuinely cannot '
        "reach the outcome the ruling forbids -- because it decides a different "
        "question entirely, or takes no fact the ruling turns on.\n\n"
        "State the outcome the ruling requires instead, where it fixes one.\n\n"
        "Reply with JSON only: "
        '{"applicable": true, "title": "<one line>", '
        '"holding": "<the ratio, in two sentences>", '
        '"when": ["<name> <op> <value>", ...], '
        '"contends": {"output": "<name>", "value": <value>}, '
        '"citations": ["<the case, and the clauses of ours it bites on>", ...]}'
    ),
)


@dataclass
class ProposedHolding:
    id: str
    scope: str
    title: str
    holding: str
    when: list[str]
    citations: list[str] = field(default_factory=list)
    contends_output: str = ""
    contends_value: Any = None
    source_file: str = ""
    proposed: str = ""
    status: str = "proposed"
    """Never `live`. A holding becomes live by a person copying it into
    exposure/predicates.yaml, which is a reviewed act."""

    def as_predicate(self) -> exposure.Predicate:
        return exposure.Predicate(
            id=self.id, scope=self.scope, title=self.title,
            archetype="a claimant relying on new authority",
            theory=self.holding, citations=list(self.citations),
            when=[exposure.Atom(w).compile() for w in self.when],
            contends_output=self.contends_output, contends_value=self.contends_value,
            source=f"holding:{self.id}",
        )

    def save(self, dirpath: str | Path = HOLDINGS_PROPOSED) -> Path:
        d = Path(dirpath)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.id}.yaml"
        body = {k: v for k, v in self.__dict__.items()}
        p.write_text(yaml.safe_dump(body, sort_keys=False, width=88, allow_unicode=True))
        return p


def load_proposed_holdings(dirpath: str | Path = HOLDINGS_PROPOSED) -> list[ProposedHolding]:
    d = Path(dirpath)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.yaml")):
        out.append(ProposedHolding(**yaml.safe_load(p.read_text())))
    return out


def formalise_holding(
    ruling_path: str | Path, scope: str, *, model: str = llm.DEFAULT_MODEL
) -> RoleResult:
    from .fleet import scope_interface

    entry = load_registry()[scope]
    iface = scope_interface(entry)
    text = Path(ruling_path).read_text(encoding="utf-8")

    def validate(v: Any) -> dict[str, Any]:
        if not isinstance(v, dict):
            raise ValueError("expected a JSON object")
        if not v.get("applicable", True):
            return {"applicable": False, "why": str(v.get("why", "")).strip()}
        when = v.get("when") or []
        if not isinstance(when, list) or not when:
            raise ValueError("an applicable holding must state at least one condition")
        known = set(iface) | set(entry.outputs) | set(entry.internals)
        atoms = []
        for w in when:
            a = exposure.Atom(str(w)).compile()
            if a.lhs not in known:
                raise ValueError(f"condition {w!r} names {a.lhs!r}, which {scope} has no such value")
            atoms.append(str(w))
        if not (v.get("citations") or []):
            raise ValueError("a holding must cite the authority it comes from")
        c = v.get("contends") or {}
        if c.get("output") and c["output"] not in entry.outputs:
            raise ValueError(f"contends about {c['output']!r}, not an output of {scope}")
        return {
            "applicable": True, "title": str(v.get("title", "")).strip(),
            "holding": str(v.get("holding", "")).strip(), "when": atoms,
            "citations": [str(x) for x in v["citations"]],
            "contends_output": str(c.get("output", "")),
            "contends_value": c.get("value"),
        }

    prompt = "\n".join([
        f"# The ruling\n\n{text}",
        f"\n# Our rule: {scope}",
        f"\nIt decides: one case of this kind.",
        "\nFacts it takes:\n" + "\n".join(f"  {n}: {t}" for n, t in sorted(iface.items())),
        "\nValues it produces:\n" + "\n".join(f"  {n}" for n in entry.outputs),
    ])
    return run_role(HOLDING, prompt, model=model, validate=validate)


@dataclass
class HoldingImpact:
    """Which regions of the decision space a holding makes indefensible."""

    holding: str
    scope: str
    regions: list[dict[str, Any]] = field(default_factory=list)
    cells_hit: int = 0
    cells_total: int = 0
    population: int = 0
    clause_refs: list[str] = field(default_factory=list)
    contends_no_change: int = 0
    """Cells the constraint catches where the value it contends for is the one
    the rule already produces.

    The same degeneracy `record_counterexample` refuses: a constraint whose
    required outcome equals the current outcome forbids nothing. It is the
    most common way a formalised holding is wrong -- the ratio is read
    correctly and the direction is copied from the wrong side -- and it is
    caught by execution rather than by reading, which is the only way it can
    be caught reliably."""
    cells_caught: int = 0
    """Computed cells the holding's conditions match, whatever it contends.
    `cells_hit` is the subset whose outcome it would actually have to change
    in the claimant's favour; only those are indefensible."""
    contends_against: int = 0
    """Cells where the contended value is LESS favourable to the population
    than what the rule already produces, by the output's declared polarity.

    A ruling formalised as exposure cannot require that the claimant get less:
    those cells are not indefensible, the company would welcome them. Equality
    alone does not see this. HOLD-0001 was first drafted with the employer's
    0.0 as the contended multiplier, and because most of the cases it caught
    were paying 1.25 or 2.0 -- different from 0.0 -- it reported 240 cases of
    senior overtime as indefensible when those were the cases already paying.
    Every one of them moved against the claimant, which is what gives the
    inverted direction away."""

    @property
    def degenerate(self) -> bool:
        return bool(self.cells_caught) and not self.cells_hit and not self.contends_against

    @property
    def inverted(self) -> bool:
        return bool(self.contends_against) and not self.cells_hit

    def summary(self) -> str:
        if not self.cells_caught:
            return (f"{self.holding}: bites on nothing in {self.scope} as the "
                    f"documents currently stand")
        if self.degenerate:
            return (f"{self.holding}: catches {self.cells_caught} cases in "
                    f"{self.scope} but requires the outcome they already have, "
                    f"so it forbids nothing. Read the direction of the "
                    f"contention again before this goes live")
        if self.inverted:
            return (f"{self.holding}: catches {self.cells_caught} cases in "
                    f"{self.scope}, and every one it would change "
                    f"({self.contends_against}) it changes against the claimant, "
                    f"so it creates no exposure. The contention is almost "
                    f"certainly copied from the losing side. Read its direction "
                    f"again before this goes live")
        s = (f"{self.holding}: {len(self.regions)} region(s) of {self.scope} "
             f"become indefensible, {self.cells_hit}/{self.cells_total} cases, "
             f"{self.population} real decision(s) on record, created by "
             f"{', '.join(self.clause_refs) or 'unattributed clauses'}")
        if self.contends_against:
            s += (f". A further {self.contends_against} case(s) it catches would "
                  f"move against the claimant and are not counted: check the "
                  f"direction of the contention")
        return s


def holding_impact(
    h: ProposedHolding, *, decisions: list[Decision] | None = None
) -> HoldingImpact:
    """Run a proposed holding against the whole mapped decision space.

    The answer is not "this may be relevant". It is the set of regions whose
    outcome the holding forbids, the clauses that produce them, and how many
    decisions already on record fall inside.
    """
    from decimal import Decimal, InvalidOperation

    p = h.as_predicate()
    surf = populate(sf.partition(h.scope), decisions)
    dom = sf.load_domains().get(h.scope)
    larger_favours = dom.favours.get(p.contends_output, "") if dom else ""
    out = HoldingImpact(holding=h.id, scope=h.scope, cells_total=surf.cells_run)

    def movement(c) -> str:
        """`same`, `for` / `against` the counterparty, or `differs` where no
        polarity is declared or the value is not a number."""
        if not p.contends_output:
            return "differs"
        got, want = c.outputs.get(p.contends_output), p.contends_value
        if isinstance(want, str) and want.startswith("@"):
            want = c.outputs.get(want[1:])
        try:
            g, w = Decimal(str(got)), Decimal(str(want))
        except (InvalidOperation, TypeError, ValueError):
            return "same" if got == want else "differs"
        if g == w:
            return "same"
        if larger_favours == "counterparty":
            return "for" if w > g else "against"
        if larger_favours == "company":
            return "for" if w < g else "against"
        return "differs"

    for r in surf.regions:
        caught = [c for c in r.cells
                  if c.outcome == sf.OUTCOME_COMPUTED
                  and p.fires({**c.inputs, **c.outputs})]
        if not caught:
            continue
        moves = [movement(c) for c in caught]
        hit = [c for c, m in zip(caught, moves) if m in ("for", "differs")]
        no_change, against = moves.count("same"), moves.count("against")
        out.cells_caught += len(caught)
        out.contends_no_change += no_change
        out.contends_against += against
        if not hit:
            continue
        out.regions.append({
            "id": r.id, "cells": len(hit), "of": r.size, "caught": len(caught),
            "clause_refs": list(r.clause_refs), "exemplar": hit[0].inputs,
            "outputs": hit[0].outputs, "population": r.population,
            "contends_no_change": no_change, "contends_against": against,
        })
        out.cells_hit += len(hit)
        out.population += r.population if len(hit) == r.size else 0
        for ref in r.clause_refs:
            if ref not in out.clause_refs:
                out.clause_refs.append(ref)
    return out


# --- watcher 3: somebody else's paper -------------------------------------

REDLINE = Role(
    name="redline",
    purpose="Turn a conflict between an inbound document and ours into a "
            "redline and a fallback.",
    reads=["corpus", "ingest/incoming"],
    forbidden=["src", "docs", "tests", "exposure"],
    temperature=0.2,
    num_predict=1536,
    system=(
        "You negotiate contracts for this company. You are given one conflict "
        "between a document somebody has sent us and the documents we already "
        "have, detected mechanically: the clause references, what collides, and "
        "why.\n\n"
        "Give two things. A redline: the exact wording we send back, drafted "
        "in the register of the clause it replaces. And a fallback: what we "
        "accept if they refuse the redline, with the condition that makes it "
        "acceptable. A fallback of 'we accept their wording' is legitimate and "
        "is sometimes the right answer -- say so plainly and say what it costs "
        "us.\n\n"
        "Do not quantify anything. Amounts in this system come from executing "
        "the rules and yours would be a guess.\n\n"
        "Reply with JSON only: "
        '{"position": "<one line: what we want>", "redline": "<the wording we '
        'send back>", "fallback": "<what we accept instead>", '
        '"fallback_condition": "<what has to be true for the fallback to be '
        'acceptable>", "cost_if_conceded": "<in words, not numbers>"}'
    ),
)


def _validate_redline(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise ValueError("expected a JSON object")
    for k in ("position", "redline", "fallback"):
        if not str(v.get(k, "")).strip():
            raise ValueError(f"a negotiating position must state {k}")
    return {k: str(v.get(k, "")).strip() for k in
            ("position", "redline", "fallback", "fallback_condition", "cost_if_conceded")}


def redline_conflict(conflict: Any, *, model: str = llm.DEFAULT_MODEL) -> RoleResult:
    prompt = "\n".join([
        f"Conflict kind: {conflict.kind}",
        f"Their clause: {conflict.incoming_ref}",
        f"Our clauses it collides with: {', '.join(conflict.existing_refs) or '(none named)'}",
        f"Severity: {conflict.severity}",
        f"\nWhat was detected:\n{conflict.detail}",
    ])
    return run_role(REDLINE, prompt, model=model, validate=_validate_redline)


def review_inbound(
    proposal_id: str, *, model: str = llm.DEFAULT_MODEL, advisory: bool = False
) -> dict[str, Any]:
    """Every place an inbound document and ours both bear on the same thing.

    The conflicts themselves are found mechanically by `lks.ingest`, which
    already compares terms, figures, references and -- where the document has
    been encoded -- re-runs the counterexample suite against the candidate
    modules. What this adds is the negotiating half: a redline and a fallback
    for each, so the output is a position rather than a list of differences.

    What it is honest about: composing two contracts properly means executing
    both, and we can only execute ours. Where their clause has been encoded as
    a candidate module, `lks.remedy.counterfactual` will show exactly which
    regions of our decision space move if we sign. Where it has not, this is a
    textual comparison with a negotiating position attached, and says so.
    """
    from .ingest import Proposal

    pr = Proposal.load(proposal_id)
    rows = []
    for c in pr.conflicts:
        if c.severity != "blocking" and not advisory:
            continue
        res = redline_conflict(c, model=model)
        rows.append({
            "kind": c.kind,
            "their_clause": c.incoming_ref,
            "our_clauses": list(c.existing_refs),
            "detail": c.detail,
            "severity": c.severity,
            "position": res.value if res.ok else None,
            "error": "" if res.ok else res.error,
        })
    return {
        "proposal": pr.id,
        "document": pr.title,
        "doc_id": pr.doc_id,
        "conflicts": len(pr.conflicts),
        "blocking": len(pr.blocking),
        "candidate_modules": list(pr.candidate_modules),
        "executable": bool(pr.candidate_modules),
        "note": (
            "their clauses have been encoded, so `lks exposure counterfactual` "
            "will compute which regions of our decision space move if we sign"
            if pr.candidate_modules else
            "their clauses are not encoded, so this is a textual comparison with "
            "a negotiating position attached; nothing here was executed"
        ),
        "positions": rows,
    }
