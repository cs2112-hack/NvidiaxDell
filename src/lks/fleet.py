"""The attack fleet: agents that try to sue this company, and never get to say
whether they succeeded.

Each role plays somebody with a reason to read our documents against us. It is
given the clauses, the interface of one compiled rule, and what a real person
looks like -- and asked for the fact pattern that extracts the most. It is not
given the exposure predicates. That is deliberate: a role shown what counts as
a bad outcome would reproduce it, and the queue would fill with attacks that
land because they were told where to aim. The search has to be blind for a
survivor to mean anything.

## What the fleet is allowed to decide

Nothing. `lks.exposure.adjudicate` executes the scope on the proposed facts and
the outcome is the interpreter's. A role's narrative, its contention and its
citations ride along on a finding that already survived; none of them is
consulted in deciding whether it survives. A role that hallucinates a clause,
mis-computes an entitlement or invents an employee outside the realisable
domain produces nothing -- not a lower-confidence finding, nothing.

That is why a weak local model is enough here, and why the loop can run all
night: the yield is low and the floor is solid.

## Why archetypes rather than one attacker

The same corpus is dangerous in different directions to different people, and a
single prompt asking for "problems" converges on the same two or three. A
regulator does not care what an expense claim costs and an employment claimant
does not care about retention schedules. Splitting the search by who is doing
it is the cheapest way to make it cover ground, and it is also how the finding
has to be written up at the end: an exposure that names a person is acted on
and an exposure that names a record is not.

## Why these roles do not reason first

Every attacker role runs with `think=False`, which is the opposite of what an
adversarial role wants, and it is a measured limitation of the runtime rather
than a view about prompting.

On qwen3.6 under this Ollama build, `think=True` together with Ollama's JSON
mode returns an empty completion with `done_reason: stop` -- the reasoning is
produced and the answer never is. Dropping JSON mode to get reasoning back
costs a 6,000-token deliberation per proposal, which at this model's
throughput is minutes per round; a fleet that runs all night would cover a
fraction of the ground, and coverage is the thing a fleet is for. The same
configuration is why `lks.agents.REVIEWER` has never produced a finding.

The trade is cheap here for the same reason a weak model is: the attacker's
job is variety, and the adjudication is exact whatever the attacker was
thinking. Turn it back on when the runtime stops swallowing the answer, and
raise `num_predict` well past the deliberation when you do.

The budget is 1,600 tokens for the same reason. In JSON mode this model will
spend whatever it is given -- at 3,072 it wrote 2,000-token narratives and a
round took three minutes -- and the object a round has to return is a fact
vector, a paragraph and a list of citations. A tighter budget quadruples the
number of attacks an overnight run gets through, and coverage is what a fleet
is for.

## The demand letter

Written from the other side, and only for a finding the formal layer has
already accepted. It is generated prose and is labelled as such wherever it is
shown; the figure in it is the computed one, never the model's. It exists
because an exposure that arrives as a table is read as a table, and this one is
going to arrive as a letter.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import agents, exposure, llm
from . import surface as sf
from .agents import Role, RoleResult, run_role
from .catala_runner import CatalaError, json_schema, reads_tree
from .registry import ScopeEntry, load_registry
from .reviewer import RELATED_NOTE, related_clauses, strip_implementer_commentary
from .segment import load_corpus

COMMON = (
    "You act for someone with a reason to read this company's own documents "
    "against it. You are not auditing the code and you are not looking for "
    "typos. You are looking for the fact pattern that extracts the most money, "
    "or the most leverage, from the documents AS THEY ARE WRITTEN.\n\n"
    "You will be given the clauses, the machine interface of one compiled rule, "
    "and a description of which facts describe a person who actually exists. "
    "Propose ONE concrete set of facts.\n\n"
    "What makes a good attack, in order of yield: a case the documents plainly "
    "reach but do not decide; two provisions that both apply where the "
    "documents do not say which wins; a word left undefined that the company "
    "gets to apply to itself; a threshold whose wrong side is where the "
    "sympathetic facts are; a provision expressed as an exception that would "
    "swallow its parent if read literally; an entitlement made conditional on "
    "an act by the company that the document does not require the company to "
    "perform.\n\n"
    "Rules you must follow or your proposal is discarded unread:\n"
    "  * Every input in the interface must appear in `facts`, with a value of "
    "the stated type. Do not add inputs that are not there.\n"
    "  * Every value must describe someone who could really exist, within the "
    "stated realisable ranges.\n"
    "  * Cite the clauses your contention rests on, by their reference.\n"
    "  * Describe a PERSON, not a record. 'A Grade 6 incident engineer in her "
    "third year' is the finding; 'ordinal=52, grade=6' is the input vector.\n"
    "  * Keep `narrative` under 120 words and `contends` under 80. A reply that "
    "runs past the token budget is truncated mid-string and discarded unread, "
    "so length costs you the whole round.\n\n"
    "Reply with JSON only: "
    '{"facts": {<name>: <value>, ...}, '
    '"narrative": "<the fact pattern as you would put it in a letter>", '
    '"contends": "<what you say the documents require, and why>", '
    '"citations": ["DOC CLAUSE", ...]}'
)

ARCHETYPES: dict[str, Role] = {
    "employment": Role(
        name="fleet-employment",
        purpose="Plaintiff's employment lawyer looking for an unpaid entitlement.",
        reads=["corpus"],
        forbidden=["exposure/predicates.yaml", "docs", "src", "triage", "tests"],
        temperature=0.8, num_predict=1600, think=False,
        system=(
            "You are a claimant-side employment lawyer. Your client worked for "
            "this company and believes they were underpaid. You are paid on "
            "what you recover, so you want the reading of the contract that "
            "pays the most, held by the largest number of people.\n\n"
            "You think in classes, not individuals: a construction that moves "
            "one hour's pay for every senior employee on every bank holiday is "
            "worth more than a large one-off. You are alert to provisions that "
            "give an entitlement and then take it away somewhere else, to "
            "precedence rules that can be read to defeat the exception they "
            "were meant to serve, and to anything the employer has to imply a "
            "word into the document to make work.\n\n" + COMMON
        ),
    ),
    "customer": Role(
        name="fleet-customer",
        purpose="Counsel for a customer under the services agreement.",
        reads=["corpus"],
        forbidden=["exposure/predicates.yaml", "docs", "src", "triage", "tests"],
        temperature=0.8, num_predict=1600, think=False,
        system=(
            "You act for a customer of this company under a master services "
            "agreement. The service has been poor and your client wants money "
            "back, or wants out.\n\n"
            "You are looking for the month where the credit is largest and the "
            "bar to claiming it is weakest; for conditions on the customer that "
            "the supplier gets to assess for itself; for caps and sole-remedy "
            "clauses that become unreasonable exactly when the failure is "
            "worst; and for termination rights whose trigger has already "
            "occurred.\n\n" + COMMON
        ),
    ),
    "regulator": Role(
        name="fleet-regulator",
        purpose="Regulator testing whether the policy can be complied with.",
        reads=["corpus"],
        forbidden=["exposure/predicates.yaml", "docs", "src", "triage", "tests"],
        temperature=0.8, num_predict=1600, think=False,
        system=(
            "You are a regulator examining this company's written policy. You "
            "are not looking for money. You are looking for the case the policy "
            "cannot answer, the case where it contradicts itself, and the case "
            "where following it would breach a duty owed to an individual.\n\n"
            "Data subjects' rights, retention periods that outlast their "
            "lawful basis, deadlines that depend on an act the company "
            "controls, and holds that block an erasure indefinitely are your "
            "territory. A policy that simply falls silent about a person is a "
            "finding: you do not need it to produce a wrong number.\n\n" + COMMON
        ),
    ),
    "auditor": Role(
        name="fleet-auditor",
        purpose="Auditor reconciling what the policy says against what it pays.",
        reads=["corpus"],
        forbidden=["exposure/predicates.yaml", "docs", "src", "triage", "tests"],
        temperature=0.8, num_predict=1600, think=False,
        system=(
            "You are an auditor. You are testing whether this company's written "
            "policy can be applied consistently by two different people, and "
            "whether the amounts it produces can be defended in a year's time.\n\n"
            "You look for boundaries where a penny either side changes a "
            "category; for orderings of caps, accelerators and rounding that do "
            "not commute; for provisions that can never fire and provisions "
            "that fire for exactly one configuration of facts; and for figures "
            "that depend on a judgement nobody is required to record.\n\n" + COMMON
        ),
    ),
    "contractor": Role(
        name="fleet-contractor",
        purpose="Disgruntled contractor or leaver chasing what they are owed.",
        reads=["corpus"],
        forbidden=["exposure/predicates.yaml", "docs", "src", "triage", "tests"],
        temperature=0.8, num_predict=1600, think=False,
        system=(
            "You are a former worker for this company chasing money you say you "
            "are owed after you left: commission on deals you closed, expenses "
            "you paid out of pocket, leave you never took.\n\n"
            "You are looking at what happens at the end of a relationship: "
            "clawbacks asserted after a long stop has passed, commission on "
            "contracts that churned after you left, claims refused because "
            "somebody who no longer manages you did not sign something, and "
            "balances forfeited on a date nobody told you about.\n\n" + COMMON
        ),
    ),
}


# --- the packet -----------------------------------------------------------

def scope_interface(entry: ScopeEntry) -> dict[str, str]:
    try:
        ins, _outs = json_schema(entry.path, entry.scope)
    except CatalaError:
        return {n: "unknown" for n in entry.inputs}
    from .api import _flatten_schema

    return _flatten_schema(ins)


def realisable_note(sd: sf.ScopeDomain) -> str:
    rows = []
    for name, d in sorted(sd.inputs.items()):
        if d.type == "boolean":
            rows.append(f"  {name}: true or false")
            continue
        span = d.probe or d.realisable
        note = f" — {d.note.strip()}" if d.note.strip() else ""
        rows.append(f"  {name}: {span if span is not None else 'any value'}{note}")
    for c in sd.coherence:
        rows.append(f"  ALSO REQUIRED: {c.holds} — {c.note.strip()}")
    return "\n".join(rows)


@reads_tree
def build_packet(key: str, *, registry=None, domains=None) -> str:
    """What an attacker is shown: the law, the interface, and who exists.

    Assembled the same way as the adversarial reviewer's packet -- implementer
    commentary mechanically stripped -- so the attacker reads the clause rather
    than somebody's account of the clause. The exposure predicates are not in
    it, and neither is anything under `exposure/`.
    """
    registry = registry if registry is not None else load_registry()
    entry = registry[key]
    domains = domains if domains is not None else sf.load_domains()
    sd = domains.get(key) or sf.ScopeDomain(key=key)

    docs = load_corpus()
    clauses = {c.ref: (doc, c) for doc in docs for c in doc.clauses}
    blocks = []
    for ref in entry.encodes:
        got = clauses.get(ref)
        if got is None:
            continue
        doc, c = got
        blocks.append(f"### {c.ref} — {doc.title}\nSection {c.section_id} "
                      f"{c.section_title}\n\n{c.body}\n")
    # the definitions and cross-referred provisions the applied clauses depend
    # on, as in the reviewer's packet: an attacker cannot argue about what
    # "Night Hours" covers if nothing it is shown defines it
    related = related_clauses(entry.encodes, docs)
    if related:
        blocks.append(RELATED_NOTE)
        blocks += [f"### {c.ref} — {doc.title}\nSection {c.section_id} "
                   f"{c.section_title}\n\n{c.body}\n" for doc, c in related]

    artefact, _ = strip_implementer_commentary(Path(entry.path).read_text(encoding="utf-8"))
    iface = scope_interface(entry)
    return "\n".join([
        f"# Target: {key}",
        f"\nThe rule decides: {sd.population or 'one case'}.",
        "\n## The clauses it applies\n",
        "\n".join(blocks) or "(no clauses recorded)",
        "\n## The machine interface — every one of these must appear in `facts`\n",
        "\n".join(f"  {n}: {t}" for n, t in sorted(iface.items())),
        f"\nIt produces: {', '.join(entry.outputs)}",
        "\n## Which facts describe a person who actually exists\n",
        realisable_note(sd),
        "\n## The company's own encoding of these clauses\n",
        "```", artefact, "```",
    ])


# --- proposing an attack ---------------------------------------------------

def _validator(entry: ScopeEntry, iface: dict[str, str]):
    def validate(v: Any) -> dict[str, Any]:
        if not isinstance(v, dict):
            raise ValueError("expected a JSON object")
        facts = v.get("facts")
        if not isinstance(facts, dict):
            raise ValueError("facts must be an object")
        unknown = sorted(set(facts) - set(iface))
        if unknown:
            raise ValueError(f"invented input name(s) not in the interface: {unknown}")
        missing = sorted(set(iface) - set(facts))
        if missing:
            raise ValueError(f"the scope cannot run without {missing}")
        out = {}
        for k, raw in facts.items():
            ty = iface[k]
            if ty in ("integer", "decimal", "money", "boolean", "date"):
                out[k] = agents._coerce(raw, ty)
            else:
                out[k] = raw
        cites = v.get("citations") or []
        if not isinstance(cites, list):
            raise ValueError("citations must be a list")
        return {
            "facts": out,
            "narrative": str(v.get("narrative", "")).strip(),
            "contends": str(v.get("contends", "")).strip(),
            "citations": [str(c) for c in cites],
        }

    return validate


def attack_schema(entry: ScopeEntry) -> dict[str, Any] | None:
    """The shape of a proposal: this scope's exact inputs, and field lengths
    that fit the token budget.

    The first fleet run lost rounds in the two ways this rules out, at
    generation time rather than after it: a reply that ran past the budget and
    was cut off mid-string, and facts the compiler could not parse. `None`
    when the compiler cannot describe the scope, which leaves the prompt to do
    what it did before.
    """
    from .interface import scope_io

    try:
        io = scope_io(entry.path, entry.scope)
    except CatalaError:
        return None
    facts = dict(io.input_schema)
    facts["required"] = list((facts.get("properties") or {}).keys())
    return {
        "type": "object",
        "properties": {
            "facts": facts,
            "narrative": {"type": "string", "maxLength": 800},
            "contends": {"type": "string", "maxLength": 520},
            "citations": {"type": "array", "maxItems": 6,
                          "items": {"type": "string", "maxLength": 40}},
        },
        "required": ["facts", "narrative", "contends", "citations"],
        "additionalProperties": False,
    }


def propose(
    key: str,
    archetype: str,
    packet: str,
    *,
    registry=None,
    model: str = llm.DEFAULT_MODEL,
    seed: int | None = None,
    already_tried: list[str] | None = None,
    iface: dict[str, str] | None = None,
) -> RoleResult:
    """Ask one archetype for one fact pattern.

    `seed=None` by default: an attacker that proposes the same case every round
    has stopped attacking. Variety is free because the adjudicator does not
    care where the facts came from.

    `iface` lets a caller read the scope's interface once, under its own
    execution lock, so the minute spent waiting on the model is not spent
    holding the compiler.
    """
    registry = registry if registry is not None else load_registry()
    entry = registry[key]
    iface = iface if iface is not None else scope_interface(entry)
    role = ARCHETYPES[archetype]
    tried = ""
    if already_tried:
        tried = ("\n\nFact patterns already proposed against this rule. They "
                 "were either rejected or are already on the queue; go "
                 "somewhere else:\n"
                 + "\n".join(f"  - {t}" for t in already_tried[-25:]))
    res = run_role(role, packet + tried, model=model,
                   validate=_validator(entry, iface), seed=seed,
                   schema=attack_schema(entry))
    if res.ok:
        res.value["scope"] = key
        res.value["archetype"] = archetype
    return res


def to_attack(value: dict[str, Any], archetype: str) -> exposure.Attack:
    return exposure.Attack(
        archetype=ARCHETYPES[archetype].purpose.rstrip("."),
        scope=value["scope"],
        facts=value["facts"],
        narrative=value.get("narrative", ""),
        citations=value.get("citations") or [],
        origin=f"fleet:{archetype}",
    )


# --- writing it up from the other side ------------------------------------

DEMAND = Role(
    name="fleet-demand",
    purpose="Write the surviving exposure up as the letter it will arrive as.",
    reads=["corpus"],
    forbidden=["src", "docs", "triage", "tests"],
    as_json=False,
    temperature=0.4,
    num_predict=1600,
    system=(
        "You write the letter before action that this company is going to "
        "receive. You act for the claimant. Be brief, specific and calm: the "
        "letters that get paid are the ones that quote the other side's own "
        "document back at them and state a number.\n\n"
        "You will be given a fact pattern, the provisions relied on, what the "
        "company's own policy computes, and the amount contended for. Use "
        "those figures exactly as given. Do not compute anything, do not round "
        "anything, and do not introduce a figure that is not in front of you: "
        "the amounts were produced by executing the company's own rules and an "
        "invented one destroys the letter's only advantage.\n\n"
        "Six short paragraphs at most: who the client is and what happened; the "
        "provisions relied on, quoted; what the company paid or decided; what "
        "the provisions require on your reading; the amount and how it "
        "multiplies across the class; what you want and by when.\n\n"
        "Write the letter and nothing else. No preamble, no headings, no "
        "explanation of what you are doing."
    ),
)


def demand_letter(
    e: exposure.Exposure, *, model: str = llm.DEFAULT_MODEL
) -> RoleResult:
    """Draft the letter for an exposure the formal layer has already accepted.

    Only ever called on a survivor. The figure, the outputs and the clause
    chain are passed in as computed facts, and the role is told not to produce
    a number of its own -- so the one thing in this system a model writes
    unsupervised is the prose around numbers it did not choose.
    """
    chain = []
    for g in e.clause_chain:
        refs = ", ".join(g.get("clause_refs") or []) or "(unattributed)"
        chain.append(f"  {g['variable']} was decided by {refs}")
    body = [
        f"Archetype: {e.archetype}",
        f"What the rule decides: {e.population or 'one case'}",
        f"\nFact pattern:\n{e.narrative or '(none recorded)'}",
        f"\nThe theory:\n{e.why}",
        f"\nProvisions relied on: {', '.join(e.citations) or '(none)'}",
        f"\nWhat the company's own policy computes on these facts:\n"
        f"{json.dumps(e.outputs, indent=2) if e.outputs else e.diagnostic[:400]}",
        "\nWhich provision decided what:\n" + ("\n".join(chain) or "  (not traced)"),
    ]
    if e.amount is not None:
        body.append(f"\nThe amount, computed: {e.amount}\nHow it was computed: {e.amount_basis}")
    else:
        body.append(f"\nThere is no figure for this one. {e.amount_basis} "
                    f"Do not supply one; write the letter without an amount.")
    return run_role(DEMAND, "\n".join(body), model=model)


# --- one round -------------------------------------------------------------

@dataclass
class RoundResult:
    archetype: str
    scope: str
    ok: bool = False
    landed: bool = False
    recorded: str = ""
    klass: str = ""
    why: str = ""
    narrative: str = ""
    usage: llm.Usage = field(default_factory=llm.Usage)
    kind: str = ""

    def line(self) -> str:
        from . import plain

        if not self.ok:
            return f"no answer      {plain.failed_attempt(self.kind)}"
        if self.recorded:
            return f"EXPOSURE FOUND {self.recorded} ({self.klass})  {self.narrative[:40]}"
        if self.landed:
            return f"already known  {self.klass:<13} {self.narrative[:36]}"
        return f"no exposure    {plain.attack_result(self.why)[:90]}"


def run_round(
    key: str,
    archetype: str,
    packet: str,
    *,
    registry=None,
    domains=None,
    predicates=None,
    model: str = llm.DEFAULT_MODEL,
    already_tried: list[str] | None = None,
    store: str | Path = exposure.QUEUE_DIR,
) -> RoundResult:
    res = propose(key, archetype, packet, registry=registry, model=model,
                  already_tried=already_tried)
    if not res.ok:
        return RoundResult(archetype, key, ok=False, why=res.error, usage=res.usage,
                           kind=res.kind)
    attack = to_attack(res.value, archetype)
    verdict = exposure.adjudicate(attack, registry=registry, domains=domains,
                                  predicates=predicates)
    out = RoundResult(
        archetype, key, ok=True, landed=verdict.landed, klass=verdict.klass,
        why=verdict.why, narrative=attack.narrative, usage=res.usage,
    )
    if verdict.landed:
        rec = exposure.record(attack, verdict, domains=domains,
                              predicates=predicates, store=store)
        out.recorded = rec.id if rec else ""
    return out
