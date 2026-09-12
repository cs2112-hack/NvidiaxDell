"""The screen: three reviewers attack a draft that has already passed Catala.

Step 4 of the generation pipeline. A draft reaches this module only after its
encoding has passed G1-G4 (`lks.gates`), so what is left to find is not an
ill-formed rule. It is a well-formed rule that says something the words do not,
words that say two things, and a document that disagrees with the company's
existing paper.

## The rule this module exists to keep

Every other agent in this repo proposes and something that cannot be talked
round decides. `lks.fleet` says it outright -- "What the fleet is allowed to
decide: Nothing." Three language models voting on whether a legal document is
sound is precisely the shape that rule forbids, so the three reviewers here do
not vote on facts. Each finding is put to the strongest check its claim
admits, and only what no check can reach goes to consensus.

| Reviewer    | Sees                          | Its findings are checked by                     |
|-------------|-------------------------------|-------------------------------------------------|
| logic       | document + stripped encoding  | re-executing the scope on the reviewer's inputs |
| language    | document only                 | clause/quote validation; dangling references and |
|             |                               | definitions checked mechanically; the rest is judgement |
| consistency | document + nearest corpus     | execution where it gives inputs; corpus refs must |
|             | clauses + rule interface      | exist; the rest is judgement                     |

## Consensus, computed after the fact

* A **confirmed** finding blocks on its own. One reviewer is enough, because a
  re-executed contradiction is a fact about the artefact and not an opinion
  about it. A 2-of-3 rule applied to facts would discard a proven break
  because two models missed it.
* A **judgement** finding blocks when a *different* reviewer independently
  raised a judgement finding on an overlapping clause. Agreement is measured
  by clause id, not wording: two reviewers who describe C-6.1 differently are
  pointing at the same place.
* A lone judgement finding does **not** block. It becomes an open question in
  the PDF's provenance appendix.

That split is not invented here. It is how this corpus already treats its own
defects: 31 confirmed breaks were fixed (`tests/counterexamples`), and five
ambiguities no encoding could settle were recorded and left for a lawyer
(`docs/DOCUMENT-DEFECTS.md`).

## Blind, and all three must actually run

No reviewer sees another's findings, and there is no deliberation round. That
keeps the searches independent -- deliberation among samples of one model
produces correlated agreement, not coverage -- and it is cheaper, because
Ollama serves this host one request at a time.

A reviewer whose reply cannot be used is retried once on a different seed. If
it fails again the screen is *incomplete*, and an incomplete screen blocks.
Passing on two reviewers out of three would report a search that did not
happen as a search that found nothing, which is the one outcome this repo
refuses everywhere else.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import agents, llm
from .agents import SCREEN_CONSISTENCY, SCREEN_LANGUAGE, SCREEN_LOGIC, Role, run_role
from .catala_runner import AssertionFailed, CatalaError, json_schema, run_scope
from .draft import _schema_enums, _schema_types
from .model import Document, referenced_clauses
from .reviewer import AGREES, UNCLEAR, compare, discover_scopes, strip_implementer_commentary

# --- findings -------------------------------------------------------------

CONFIRMED = "confirmed"
REFUTED = "refuted"
JUDGEMENT = "judgement"
DISCARDED = "discarded"

LANGUAGE_KINDS = {
    "UNDEFINED_TERM", "INCONSISTENT_TERM", "AMBIGUOUS_REFERENT",
    "UNQUANTIFIED_STANDARD", "DANGLING_REFERENCE", "CIRCULAR_DEFINITION",
    "UNSTATED_PRECEDENCE", "INOPERATIVE_CLAUSE",
}
CONSISTENCY_KINDS = {"CONTRADICTS_CORPUS", "INTERNAL_CONTRADICTION", "UNDECIDED_CASE"}
LOGIC_KINDS = {"BREAK", "AMBIGUITY"}


@dataclass
class Finding:
    agent: str
    kind: str
    clause_ids: list[str]
    summary: str
    quote: str = ""
    fix: str = ""
    reasoning: str = ""
    scope: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    expected: Any = None
    observed: Any = None
    corpus_refs: list[str] = field(default_factory=list)
    status: str = JUDGEMENT
    verified_by: str = ""
    discard_reason: str = ""
    agreed_with: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentRun:
    agent: str
    role: str
    ok: bool
    enforcement: str = ""
    error: str = ""
    seconds: float = 0.0
    attacks_tried: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["findings"] = [f.to_dict() for f in self.findings]
        return d


@dataclass
class ScreenResult:
    agents: list[AgentRun] = field(default_factory=list)

    @property
    def all_findings(self) -> list[Finding]:
        return [f for a in self.agents for f in a.findings]

    @property
    def confirmed(self) -> list[Finding]:
        return [f for f in self.all_findings if f.status == CONFIRMED]

    @property
    def agreed(self) -> list[Finding]:
        return [f for f in self.all_findings if f.status == JUDGEMENT and f.agreed_with]

    @property
    def open_questions(self) -> list[Finding]:
        return [f for f in self.all_findings if f.status == JUDGEMENT and not f.agreed_with]

    @property
    def discarded(self) -> list[Finding]:
        return [f for f in self.all_findings if f.status in (DISCARDED, REFUTED)]

    @property
    def incomplete(self) -> list[AgentRun]:
        return [a for a in self.agents if not a.ok]

    @property
    def blocking(self) -> list[Finding]:
        return self.confirmed + self.agreed

    @property
    def passed(self) -> bool:
        return not self.incomplete and not self.blocking

    def verdict(self) -> str:
        if self.incomplete:
            return ("INCOMPLETE: "
                    + ", ".join(f"{a.agent} ({a.error[:80]})" for a in self.incomplete))
        if self.blocking:
            return (f"BLOCKED: {len(self.confirmed)} confirmed, "
                    f"{len(self.agreed)} agreed by two or more reviewers")
        return f"PASSED: {len(self.open_questions)} open question(s) recorded, none agreed"

    def repair_brief(self) -> str:
        """What the drafter is told after a blocked screen.

        Confirmed findings carry their evidence -- the inputs and what the rule
        produced -- because a drafter told only "C-4.2 is wrong at the
        boundary" will move the boundary rather than state it. Agreed judgement
        findings carry every reviewer's wording and suggested fix, so the
        drafter sees the concern from more than one side.
        """
        out = ["The draft was blocked by the review screen. Fix every item below. "
               "Do not change anything the items do not require.\n"]
        for f in self.confirmed:
            out.append(f"## CONFIRMED — {f.kind} — {', '.join(f.clause_ids)} (from {f.agent})")
            out.append(f.summary)
            if f.verified_by:
                out.append(f"Evidence: {f.verified_by}")
            if f.reasoning:
                out.append(f"Why the words require otherwise: {f.reasoning}")
            if f.fix:
                out.append(f"Suggested fix: {f.fix}")
            out.append("")
        seen: set[int] = set()
        for f in self.agreed:
            if id(f) in seen:
                continue
            group = [f] + [g for g in self.agreed
                           if g is not f and set(g.clause_ids) & set(f.clause_ids)]
            for g in group:
                seen.add(id(g))
            ids = sorted({c for g in group for c in g.clause_ids})
            out.append(f"## AGREED BY {len({g.agent for g in group})} REVIEWERS — {', '.join(ids)}")
            for g in group:
                out.append(f"- {g.agent} ({g.kind}): {g.summary}"
                           + (f"  Quote: \"{g.quote}\"" if g.quote else "")
                           + (f"  Suggested fix: {g.fix}" if g.fix else ""))
            out.append("")
        return "\n".join(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "verdict": self.verdict(),
            "agents": [a.to_dict() for a in self.agents],
            "confirmed": [f.to_dict() for f in self.confirmed],
            "agreed": [f.to_dict() for f in self.agreed],
            "open_questions": [f.to_dict() for f in self.open_questions],
            "discarded": [f.to_dict() for f in self.discarded],
        }


# --- what each reviewer is shown -----------------------------------------


def scope_interfaces(module: str | Path) -> dict[str, dict[str, Any]]:
    """Every scope's inputs with their types, and its outputs.

    Types are the compiler's, from its JSON Schema, so a reviewer cannot be
    shown an input the scope does not take. Enumerations are spelled out with
    their constructors, because `"city_tier": "Tier2"` is only proposable by
    a reviewer who has been told Tier2 exists.
    """
    out: dict[str, dict[str, Any]] = {}
    for scope, qual in discover_scopes(module).items():
        try:
            ins, _ = json_schema(module, scope)
        except CatalaError:
            continue
        types = _schema_types(ins)
        enums = _schema_enums(ins)
        out[scope] = {
            "inputs": {
                n: (f"one of {'|'.join(enums[n])}" if n in enums else t)
                for n, t in sorted(types.items())
            },
            "raw_types": {n: ("enum" if n in enums else t) for n, t in types.items()},
            "enums": enums,
            "outputs": list(qual["output"]),
        }
    return out


def _interface_text(ifaces: dict[str, dict[str, Any]]) -> str:
    rows = []
    for scope, d in sorted(ifaces.items()):
        rows.append(f"### {scope}")
        rows.append("inputs:")
        rows += [f"  {n}: {t}" for n, t in d["inputs"].items()]
        rows.append("outputs: " + ", ".join(d["outputs"]))
        rows.append("")
    return "\n".join(rows) or "(no executable scopes)"


def logic_packet(document_md: str, module: Path, ifaces: dict[str, dict[str, Any]]) -> str:
    """Document, encoding with commentary stripped, and the exact interface.

    Stripped by the same function the adversarial reviewer's packet uses: the
    reviewer should check the code against the clause, not against the
    encoder's account of the clause.
    """
    stripped, _ = strip_implementer_commentary(module.read_text(encoding="utf-8"))
    return "\n".join([
        "# The document", "", document_md, "",
        "# The compiled rule that claims to encode it", "",
        "```", stripped, "```", "",
        "# The rule's exact interface — `inputs` must name exactly these", "",
        _interface_text(ifaces),
    ])


def language_packet(document_md: str) -> str:
    return "# The document\n\n" + document_md


def consistency_packet(
    document_md: str,
    corpus_context: list[dict[str, Any]],
    ifaces: dict[str, dict[str, Any]],
) -> str:
    rows = ["# The new document", "", document_md, "",
            "# The company's existing clauses closest to it", ""]
    for c in corpus_context:
        if c.get("kind") not in ("prose", "rule", "hybrid", "clause"):
            continue
        rows.append(f"## {c['ref']} — {c.get('doc_title', '')}")
        rows.append(c.get("text", "").strip())
        rows.append("")
    rows += ["# The new document's compiled rule — `inputs` must name exactly these", "",
             _interface_text(ifaces)]
    return "\n".join(rows)


# --- validating and verifying one finding --------------------------------

_QUOTE_NORMALISE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})


def _norm(text: str) -> str:
    return " ".join(str(text).translate(_QUOTE_NORMALISE).split()).lower()


def _clause_text(doc: Document, clause_ids: list[str]) -> str:
    known = {c.clause_id: c.body for c in doc.clauses}
    return " ".join(known.get(cid, "") for cid in clause_ids)


def _definitions(doc: Document) -> set[str]:
    """Terms the document defines, by the `"Term" means` convention.

    Straight and curly quotes both count, and `includes` is accepted alongside
    `means` because both are how a definition section is written.
    """
    out: set[str] = set()
    for c in doc.clauses:
        for m in re.finditer(r'["“]([^"”]{1,80})["”]\s+(?:means|includes|has the meaning)',
                             c.body):
            out.add(_norm(m.group(1)))
    return out


def _coerce_inputs(
    raw: Any, iface: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    """Inputs typed to the scope, or a reason they cannot be.

    Mirrors `lks.fleet._validator`: an invented input name, a missing input,
    or a value of the wrong type is rejected before execution, so a finding is
    never confirmed on inputs the rule could not have been given.
    """
    if not isinstance(raw, dict):
        return None, "inputs is not an object"
    want = set(iface["raw_types"])
    unknown = sorted(set(raw) - want)
    if unknown:
        return None, f"invented input name(s) {unknown}"
    missing = sorted(want - set(raw))
    if missing:
        return None, f"missing input(s) {missing}"
    out: dict[str, Any] = {}
    for k, v in raw.items():
        ty = iface["raw_types"][k]
        if ty == "enum":
            if str(v) not in iface["enums"][k]:
                return None, f"{k}={v!r} is not one of {iface['enums'][k]}"
            out[k] = str(v)
        elif ty in ("integer", "decimal", "money", "boolean", "date"):
            try:
                out[k] = agents._coerce(v, ty)
            except (ValueError, TypeError) as e:
                return None, f"{k}={v!r} is not a {ty}: {e}"
        else:
            out[k] = v
    return out, ""


def _execute_claim(
    f: Finding, module: Path, ifaces: dict[str, dict[str, Any]]
) -> None:
    """Put a finding's facts to the rule. Sets status in place.

    Confirmed when the rule's answer differs from what the reviewer derived
    from the words; refuted when it agrees. The comparison is
    `lks.reviewer.compare`, the same one that decides whether an adversarial
    reviewer's BREAK enters the permanent regression suite, so "confirmed"
    means here exactly what it means there. An expected value that cannot be
    compared -- a bare value against a rule with several results -- is
    discarded rather than confirmed.
    """
    iface = ifaces.get(f.scope)
    if iface is None:
        f.status, f.discard_reason = DISCARDED, f"no scope named {f.scope!r} in the encoding"
        return
    inputs, why = _coerce_inputs(f.inputs, iface)
    if inputs is None:
        f.status, f.discard_reason = DISCARDED, why
        return
    if f.expected is None or f.expected == {} or f.expected == "":
        f.status, f.discard_reason = DISCARDED, "no expected value was derived"
        return
    if isinstance(f.expected, dict):
        bogus = sorted(set(f.expected) - set(iface["outputs"]))
        if bogus:
            f.status, f.discard_reason = DISCARDED, f"expected names non-output(s) {bogus}"
            return
    f.inputs = inputs
    try:
        actual: Any = run_scope(module, f.scope, inputs)
    except CatalaError as e:
        # An error on inputs the document plainly answers is itself a break,
        # exactly as `lks.reviewer.record_findings` treats it.
        actual = {"__error__": type(e).__name__, "diagnostic": e.diagnostic[:300]}
    f.observed = actual
    agreement = compare(f.expected, actual)
    if agreement == UNCLEAR:
        f.status, f.discard_reason = DISCARDED, (
            "the expected value does not say which of the rule's results it is about")
        return
    if agreement == AGREES:
        f.status = REFUTED
        f.verified_by = (f"executed {f.scope} on {json.dumps(inputs, sort_keys=True)}: "
                         f"the rule already produces the expected {json.dumps(f.expected)}")
        return
    f.status = CONFIRMED
    f.verified_by = (f"executed {f.scope} on {json.dumps(inputs, sort_keys=True)}: "
                     f"the words require {json.dumps(f.expected)}, the rule produced "
                     f"{json.dumps(actual)}")


def _probe_judgement(
    f: Finding, module: Path, ifaces: dict[str, dict[str, Any]]
) -> None:
    """Put an UNDECIDED_CASE or INTERNAL_CONTRADICTION to the rule without
    letting the rule's answer decide it. Sets status in place.

    These claims are about the words: the document does not decide these facts,
    or two clauses decide them differently. The reviewer's `expected` is its
    guess at an answer, and the encoding is the encoder's guess. The two
    agreeing used to REFUTE the finding, which dropped it from the open
    questions too -- run 4 lost "no rule for exactly 37.5 hours" and "R-3.3 and
    R-5.3 both claim to prevail" that way. Agreement between two readings of
    the words is not evidence that the words decide the case.

    What execution can establish is the converse: if the rule itself has no
    single answer on these facts -- a Conflict, no applicable rule, a runtime
    error -- the case is undecided in the artefact as well, and that is
    CONFIRMED. An assertion refusing the facts means the document says they
    cannot arise, which settles nothing about the words, so it stays a
    judgement. Inputs that do not fit the scope do not discard the finding
    either: they were offered as evidence, and the claim stands without them.
    """
    f.status = JUDGEMENT
    iface = ifaces.get(f.scope)
    inputs, _why = _coerce_inputs(f.inputs, iface) if iface else (None, "")
    if inputs is None:
        return
    f.inputs = inputs
    ran = f"executed {f.scope} on {json.dumps(inputs, sort_keys=True)}"
    try:
        f.observed = run_scope(module, f.scope, inputs)
    except AssertionFailed as e:
        f.observed = {"__error__": type(e).__name__, "diagnostic": e.diagnostic[:300]}
        f.verified_by = f"{ran}: the rule's own assertion refuses these facts"
        return
    except CatalaError as e:
        f.observed = {"__error__": type(e).__name__, "diagnostic": e.diagnostic[:300]}
        f.status = CONFIRMED
        f.verified_by = f"{ran}: the rule has no single answer either ({type(e).__name__})"
        return
    f.verified_by = (f"{ran}: the rule answers {json.dumps(f.observed)}; whether the words "
                     f"decide that is a judgement, so the answer neither confirms nor refutes it")


def verify(
    f: Finding,
    *,
    doc: Document,
    module: Path,
    ifaces: dict[str, dict[str, Any]],
    corpus_refs: set[str],
    exercised_clauses: set[str],
) -> Finding:
    """Apply the strongest check this finding's claim admits.

    Validation first, for every reviewer: a finding citing a clause the
    document does not contain, or quoting words its clause does not contain,
    is discarded. That is the check that catches a hallucinated defect, and it
    runs before anything is believed.
    """
    known = {c.clause_id for c in doc.clauses}
    if not f.clause_ids:
        f.status, f.discard_reason = DISCARDED, "cites no clause"
        return f
    bad = [c for c in f.clause_ids if c not in known]
    if bad:
        f.status, f.discard_reason = DISCARDED, f"cites clause(s) not in the document: {bad}"
        return f
    if f.quote and _norm(f.quote) not in _norm(_clause_text(doc, f.clause_ids)):
        f.status, f.discard_reason = DISCARDED, "quote does not appear in the cited clause(s)"
        return f

    if f.agent == "logic":
        if f.kind == "BREAK":
            _execute_claim(f, module, ifaces)
        elif f.kind == "AMBIGUITY":
            if not f.quote:
                f.status, f.discard_reason = DISCARDED, "an ambiguity must quote the words"
            else:
                f.status = JUDGEMENT
        else:
            f.status, f.discard_reason = DISCARDED, f"unknown verdict {f.kind!r}"
        return f

    if f.agent == "language":
        if f.kind not in LANGUAGE_KINDS:
            f.status, f.discard_reason = DISCARDED, f"unknown kind {f.kind!r}"
            return f
        if f.kind == "DANGLING_REFERENCE":
            targets = known | {c.section_id for c in doc.clauses}
            dangling = [
                r for cid in f.clause_ids
                for r in referenced_clauses(_clause_text(doc, [cid]), exclude=cid)
                if re.sub(r"\([a-z]\)$", "", r) not in targets
            ]
            if dangling:
                f.status = CONFIRMED
                f.verified_by = f"resolved every cross-reference: {sorted(set(dangling))} do not exist"
            else:
                f.status = REFUTED
                f.verified_by = "every cross-reference in the cited clause(s) resolves"
            return f
        if f.kind == "UNDEFINED_TERM" and f.quote and _norm(f.quote) in _definitions(doc):
            f.status = REFUTED
            f.verified_by = f'the document defines "{f.quote}"'
            return f
        if f.kind == "INOPERATIVE_CLAUSE" and set(f.clause_ids) <= exercised_clauses:
            # `exercised_clauses` (lks.gates) -- not "G3 passed", which it does
            # with branches it never reached.
            f.status = REFUTED
            f.verified_by = ("the boundary battery's trace shows every exception branch "
                             "encoding these clauses being taken")
            return f
        if not f.quote:
            f.status, f.discard_reason = DISCARDED, "a language finding must quote the words"
            return f
        f.status = JUDGEMENT
        return f

    if f.agent == "consistency":
        if f.kind not in CONSISTENCY_KINDS:
            f.status, f.discard_reason = DISCARDED, f"unknown kind {f.kind!r}"
            return f
        if f.kind == "CONTRADICTS_CORPUS":
            if not f.corpus_refs:
                f.status, f.discard_reason = DISCARDED, "names no existing clause it contradicts"
                return f
            missing = [r for r in f.corpus_refs if r not in corpus_refs]
            if missing:
                f.status, f.discard_reason = DISCARDED, f"cites corpus clause(s) that do not exist: {missing}"
                return f
        if f.scope and f.inputs and f.expected not in (None, {}, ""):
            if f.kind == "CONTRADICTS_CORPUS":
                # `expected` is the answer the existing clause gives, so the
                # rule agreeing with it refutes the contradiction and the rule
                # differing from it confirms one.
                _execute_claim(f, module, ifaces)
                return f
            _probe_judgement(f, module, ifaces)
            return f
        f.status = JUDGEMENT
        return f

    f.status, f.discard_reason = DISCARDED, f"unknown reviewer {f.agent!r}"
    return f


def apply_consensus(findings: list[Finding]) -> None:
    """Mark judgement findings that a different reviewer independently shares.

    Agreement is on clause ids, never on wording, and a reviewer cannot agree
    with itself: two findings from the logic reviewer on the same clause are
    one reviewer being thorough, not two reviewers agreeing.
    """
    judged = [f for f in findings if f.status == JUDGEMENT]
    for f in judged:
        f.agreed_with = sorted({
            g.agent for g in judged
            if g.agent != f.agent and set(g.clause_ids) & set(f.clause_ids)
        })


# --- running the screen --------------------------------------------------


def _parse(agent: str, value: Any) -> tuple[list[Finding], list[str]]:
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    raw = value.get("findings")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("findings must be a list")
    tried = value.get("attacks_tried") or []
    out: list[Finding] = []
    for r in raw[:6]:
        if not isinstance(r, dict):
            continue
        kind = str(r.get("verdict") or r.get("kind") or "").upper().strip()
        ids = r.get("clause_ids") or []
        if isinstance(ids, str):
            ids = [ids]
        out.append(Finding(
            agent=agent,
            kind=kind,
            clause_ids=[str(i).strip() for i in ids if str(i).strip()],
            summary=str(r.get("summary", "")).strip(),
            quote=str(r.get("quote", "") or "").strip(),
            fix=str(r.get("fix", "") or "").strip(),
            reasoning=str(r.get("reasoning", "") or "").strip(),
            scope=str(r.get("scope", "") or "").strip(),
            inputs=r.get("inputs") if isinstance(r.get("inputs"), dict) else {},
            expected=r.get("expected"),
            corpus_refs=[str(x) for x in (r.get("corpus_refs") or []) if str(x).strip()],
        ))
    return out, [str(t) for t in tried][:12]


REVIEWERS: tuple[tuple[str, Role], ...] = (
    ("logic", SCREEN_LOGIC),
    ("language", SCREEN_LANGUAGE),
    ("consistency", SCREEN_CONSISTENCY),
)


def run_screen(
    *,
    doc: Document,
    document_md: str,
    module: Path,
    workspace: Path,
    corpus_context: list[dict[str, Any]],
    corpus_refs: set[str],
    exercised_clauses: set[str],
    iteration: int = 1,
    model: str = llm.DEFAULT_MODEL,
    base_seed: int = llm.DEFAULT_SEED,
    emit=print,
    call=run_role,
) -> ScreenResult:
    """Run the three reviewers blind, verify every finding, then apply consensus.

    `call` is injectable so the consensus and verification logic can be tested
    without a model -- the part of this module that must be right is not the
    prompting, it is what happens to a reply.

    Seeds differ per reviewer and per iteration: a reviewer that proposes the
    same attack on every redraft has stopped reviewing, and a fixed seed per
    (reviewer, iteration) still makes a whole run reproducible.
    """
    ifaces = scope_interfaces(module)
    packets = {
        "logic": logic_packet(document_md, module, ifaces),
        "language": language_packet(document_md),
        "consistency": consistency_packet(document_md, corpus_context, ifaces),
    }
    for name, text in packets.items():
        (workspace / f"packet-{name}.md").write_text(text, encoding="utf-8")

    result = ScreenResult()
    for idx, (name, role) in enumerate(REVIEWERS):
        run = AgentRun(agent=name, role=role.name, ok=False)
        result.agents.append(run)
        for attempt in (1, 2):
            run.attempts = attempt
            seed = base_seed + 101 * (idx + 1) + 7919 * iteration + 31 * (attempt - 1)
            emit(f"  screen/{name}: attempt {attempt} (seed {seed})")
            res = call(role, packets[name], model=model, seed=seed)
            run.enforcement = res.enforcement
            run.seconds += res.usage.seconds
            if not res.ok:
                run.error = res.error
                emit(f"  screen/{name}: unusable reply — {res.error[:160]}")
                continue
            try:
                findings, tried = _parse(name, res.value)
            except ValueError as e:
                run.error = f"{type(e).__name__}: {e}"
                emit(f"  screen/{name}: malformed — {run.error}")
                continue
            run.ok, run.error = True, ""
            run.findings, run.attacks_tried = findings, tried
            break

        for f in run.findings:
            verify(
                f, doc=doc, module=module, ifaces=ifaces, corpus_refs=corpus_refs,
                exercised_clauses=exercised_clauses,
            )
        if run.ok:
            counts: dict[str, int] = {}
            for f in run.findings:
                counts[f.status] = counts.get(f.status, 0) + 1
            emit(f"  screen/{name}: {len(run.findings)} finding(s) "
                 + (", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "")
                 + f" in {run.seconds:.0f}s")

    apply_consensus(result.all_findings)
    emit(f"  screen: {result.verdict()}")
    return result
