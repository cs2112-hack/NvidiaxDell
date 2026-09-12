"""Agent roles for the legal knowledge system, and what each is allowed to see.

## The problem this solves

The verification loop the brief cares about most -- an adversarial reviewer
that keeps attacking until it runs out of attacks -- has until now been played
by whoever was sitting at a terminal. That makes it an activity, not a
property. A local model turns it into something the system does by itself.

## The roles, and why each is safe to give a model

Every role is a *proposal* role whose output is checked by something that
cannot be talked round. None of them decides a legal question.

| Role | Proposes | Checked by |
|---|---|---|
| `TRIAGE` | a clause's label and why | accuracy against the adjudicated ledger |
| `REVIEWER` | a fact pattern where code and clause disagree | re-executing the scope; expected != observed; citations required |
| `REENCODER` | Catala from an English spec | typecheck, exception-tree and behavioural equivalence |
| `SLOTFILL` | machine inputs from a question in prose | the user sees every extracted fact before anything runs |

A weak model therefore costs little. Its bad triage labels show up as a low
score against ground truth; its bad fact patterns are rejected by the harness;
its bad Catala fails to typecheck. What survives is worth having, and the loop
can run all night.

## Isolation is enforced, not requested

`reviewer/PROTOCOL.md` tells the adversarial reviewer not to read the
implementer's reasoning. An instruction is not a control. With OpenShell
present, a role runs in a container where only its `reads` paths are mounted,
so `docs/` and `src/` are not merely forbidden to the reviewer -- they are
absent from its filesystem. `Role.enforcement` reports which mode is live, and
`run_role` refuses to claim enforced isolation when it only has the prompt.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import llm

REPO = Path(__file__).resolve().parents[2]


class Enforcement:
    SANDBOX = "sandbox"   # the paths it must not see are not mounted
    PROMPT = "prompt"     # it is only asked not to look


@dataclass
class Role:
    name: str
    purpose: str
    system: str
    reads: list[str] = field(default_factory=list)
    """Repo-relative paths this role may see. With OpenShell, exactly these are
    mounted read-only and nothing else exists."""
    forbidden: list[str] = field(default_factory=list)
    """Paths whose absence is the point. Recorded so a run can state what it
    was isolated from, and so `audit_isolation` can check the claim."""
    temperature: float = 0.0
    as_json: bool = True
    num_predict: int = 2048
    think: bool = False
    """Whether to let the model reason before answering.

    qwen3.6 is a reasoning model and reasoning roughly halves its throughput on
    this hardware, so it is spent only where it buys something. Classification
    and extraction do not need it. Deriving an expected legal answer from a
    clause, and writing Catala, plainly do."""

    def enforcement(self) -> str:
        return Enforcement.SANDBOX if openshell_available() else Enforcement.PROMPT


# --- the roles -------------------------------------------------------------

TRIAGE = Role(
    name="triage",
    purpose="Decide whether a clause is an executable rule, quoted prose, or a "
            "rule gated on a human judgement.",
    reads=["corpus"],
    forbidden=["triage/decisions.yaml", "catala", "src", "docs"],
    system=(
        "You classify clauses of a legal document for a system that compiles "
        "rules into executable code.\n\n"
        "Exactly three labels:\n"
        "RULE   - the clause reduces to a computation. Every term it needs is "
        "either a number, a date, a duration, or a record someone can look up. "
        "No judgement is required to apply it.\n"
        "PROSE  - the clause states no computation, or states one that cannot be "
        "applied without a judgement nobody has defined. Boilerplate, governing "
        "law, interpretation, obligations of conduct.\n"
        "HYBRID - the clause carries a real computation that is GATED on a "
        "predicate the documents do not define. 'may be reimbursed where the "
        "line manager certifies the delay was for good reason' is HYBRID: the "
        "60/120-day arithmetic is exact, 'good reason' is not computable. For "
        "HYBRID you must name the judgement as a snake_case input.\n\n"
        "The distinction that matters: a term is NOT a judgement merely because "
        "it is defined elsewhere or looked up in a system of record. A job grade "
        "recorded in an HR system is data. 'Material breach' is a judgement.\n\n"
        "Reply with JSON only: "
        '{"label": "RULE|PROSE|HYBRID", "reason": "<one sentence>", '
        '"judgement_inputs": ["snake_case", ...]}. '
        "judgement_inputs must be [] unless the label is HYBRID."
    ),
)

REVIEWER = Role(
    name="reviewer",
    purpose="Find a fact pattern on which the compiled rule and the source "
            "clause disagree.",
    reads=["reviewer/PROTOCOL.md", "reviewer/packets", "corpus", "scripts/probe.py"],
    forbidden=["docs", "src", "catala", "tests", "triage", "reviewer/findings"],
    temperature=0.7,
    num_predict=6144,
    think=True,
    system=(
        "You are an adversarial reviewer of legal software. You do not approve "
        "anything. There is no 'looks correct' outcome available to you.\n\n"
        "You are given a clause of a legal document and a compiled rule that "
        "claims to encode it. Your only job is to produce a concrete fact "
        "pattern on which the rule's answer differs from what the clause "
        "requires.\n\n"
        "Attack, in order of yield: exact threshold boundaries (probe both sides "
        "AND the boundary itself); two exceptions applying at once, where the "
        "document states which wins; a premium stated to apply 'in addition to' "
        "against one stated 'in substitution for'; the order of caps, "
        "accelerators and rounding, which do not commute; greater-of and "
        "lesser-of where the usually-larger branch is not; inclusive versus "
        "exclusive date endpoints and month-end arithmetic; a branch that can "
        "never fire.\n\n"
        "You must derive the expected answer FROM THE CLAUSE TEXT and cite the "
        "clauses that compel it. Never copy the expected answer from the rule's "
        "output and never adjust it to match -- a finding whose expected value "
        "equals the observed value is rejected automatically. Reason against "
        "every clause that bears on the facts, not only the one you are "
        "attacking: a previous reviewer reported a real break but computed its "
        "expectation from one clause while a second also applied, and the number "
        "was wrong.\n\n"
        "If the clause genuinely does not decide the question, that is a finding "
        "about the DOCUMENT and is valuable: say so with verdict AMBIGUITY.\n\n"
        "Reply with JSON only, one object: "
        '{"verdict": "BREAK|AMBIGUITY|NO_BREAK_FOUND", "fact_pattern": "<prose, '
        'as a lawyer would state it>", "inputs": {<exact machine inputs>}, '
        '"expected": <what the clause requires>, "citations": ["DOC CLAUSE", ...], '
        '"source_reasoning": "<which clause defeats which>", '
        '"attacks_tried": ["...", ...]}'
    ),
)

REENCODER = Role(
    name="reencoder",
    purpose="Implement an English specification in Catala, having never seen "
            "the original encoding.",
    reads=["draft/unseen/english.md", "draft/roundtrips/overtime/catala-reference.md"],
    forbidden=["catala", "corpus", "docs", "src", "triage", "reviewer"],
    as_json=False,
    num_predict=8192,
    think=True,
    system=(
        "You implement a specification in the Catala language, which encodes "
        "legal rules as prioritised default logic.\n\n"
        "Derive everything from the specification you are given. An "
        "implementation exists elsewhere and you have not seen it; the point of "
        "the exercise is to find out whether the specification alone is enough.\n\n"
        "Build every rule hierarchy with explicit `label` and `exception`. A "
        "Catala exception does NOT inherit its parent's condition, so restate "
        "each condition in full. Put declarations in a ```catala-metadata block "
        "and definitions in ```catala blocks.\n\n"
        "Reply with the contents of the .catala_en file and nothing else."
    ),
)

SLOTFILL = Role(
    name="slotfill",
    purpose="Turn a question in prose into the machine inputs a rule needs.",
    reads=[],
    forbidden=["corpus", "catala", "docs", "src"],
    num_predict=512,
    system=(
        "You extract facts from a question so that a rule can be executed.\n\n"
        "You are given the question and the exact inputs the rule needs, with "
        "their types. Return only the inputs the question actually states. "
        "NEVER invent a value, never guess a default, and never infer a value "
        "from another: a fact nobody stated must be left out so that a person is "
        "asked for it. Omitting is always correct; inventing is never.\n\n"
        "Types: integer and decimal are JSON numbers, money is a JSON number, "
        "boolean is true/false, date is \"YYYY-MM-DD\".\n\n"
        'Reply with JSON only: {"facts": {<name>: <value>, ...}, '
        '"omitted": ["<name the question does not state>", ...]}'
    ),
)

ROLES = {r.name: r for r in (TRIAGE, REVIEWER, REENCODER, SLOTFILL)}


# --- OpenShell ------------------------------------------------------------

def openshell_available() -> bool:
    """Whether a policy-enforcing sandbox runtime is present.

    Deliberately strict: the point of the sandbox is that a role's forbidden
    paths do not exist, and claiming that when the gateway is missing would be
    the one lie this module must not tell.
    """
    if os.environ.get("LKS_FORCE_PROMPT_ISOLATION") == "1":
        return False
    return bool(shutil.which("openshell") or shutil.which("nemohermes"))


def sandbox_spec(role: Role) -> dict[str, Any]:
    """The mount policy for a role, as data.

    Emitted whether or not a sandbox is available, because it documents the
    isolation the role is *supposed* to have and can be diffed in review.
    """
    return {
        "role": role.name,
        "image": os.environ.get("LKS_SANDBOX_IMAGE", "lks-agent:latest"),
        "network": "none" if role.name in ("triage", "reviewer", "reencoder") else "none",
        "mounts": [
            {"source": str(REPO / p), "target": f"/work/{p}", "mode": "ro"}
            for p in role.reads
        ],
        "absent": [f"/work/{p}" for p in role.forbidden],
        "enforcement": role.enforcement(),
    }


def audit_isolation(role: Role) -> list[str]:
    """Problems with a role's declared isolation, as text.

    Catches the case where a `reads` path is a parent of a `forbidden` one --
    mounting `.` to let a reviewer see the corpus would hand it `docs/` as
    well, and the policy would look strict while enforcing nothing.
    """
    problems: list[str] = []
    for r in role.reads:
        for f in role.forbidden:
            rp, fp = Path(r), Path(f)
            if rp == Path(".") or fp == rp or str(fp).startswith(str(rp) + "/"):
                problems.append(
                    f"{role.name}: reads {r!r} which contains forbidden {f!r}; "
                    f"the mount policy would expose it"
                )
    for r in role.reads:
        if not (REPO / r).exists():
            problems.append(f"{role.name}: reads {r!r}, which does not exist")
    return problems


# --- running a role -------------------------------------------------------

@dataclass
class RoleResult:
    role: str
    ok: bool
    value: Any = None
    error: str = ""
    enforcement: str = Enforcement.PROMPT
    usage: llm.Usage = field(default_factory=llm.Usage)
    raw: str = ""

    def __str__(self) -> str:
        head = f"[{self.role}/{self.enforcement}] {'ok' if self.ok else 'FAILED'}"
        return f"{head} ({self.usage})" + ("" if self.ok else f": {self.error}")


def run_role(
    role: Role,
    prompt: str,
    *,
    model: str = llm.DEFAULT_MODEL,
    validate: Callable[[Any], Any] | None = None,
    seed: int | None = llm.DEFAULT_SEED,
) -> RoleResult:
    """Run one role once and validate its output.

    A role that returns malformed output fails rather than returning something
    approximate, because every consumer of these results treats them as
    candidate legal content.
    """
    enforcement = role.enforcement()
    try:
        reply = llm.generate(
            prompt,
            system=role.system,
            model=model,
            temperature=role.temperature,
            seed=seed,
            as_json=role.as_json,
            num_predict=role.num_predict,
            think=role.think,
        )
    except (llm.ModelUnavailable, llm.ModelRefused) as e:
        return RoleResult(role.name, False, error=str(e), enforcement=enforcement)

    try:
        value = reply.json() if role.as_json else reply.text
        if validate is not None:
            value = validate(value)
    except Exception as e:
        return RoleResult(
            role.name, False, error=f"{type(e).__name__}: {e}",
            enforcement=enforcement, usage=reply.usage, raw=reply.text[:1200],
        )
    return RoleResult(
        role.name, True, value=value, enforcement=enforcement,
        usage=reply.usage, raw=reply.text[:1200],
    )


# --- role: triage ---------------------------------------------------------

def _validate_triage(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise ValueError("expected a JSON object")
    label = str(v.get("label", "")).upper().strip()
    if label not in ("RULE", "PROSE", "HYBRID"):
        raise ValueError(f"label must be RULE, PROSE or HYBRID, got {label!r}")
    ji = v.get("judgement_inputs") or []
    if not isinstance(ji, list):
        raise ValueError("judgement_inputs must be a list")
    if label != "HYBRID" and ji:
        raise ValueError(f"judgement_inputs must be empty unless HYBRID (got {ji})")
    if label == "HYBRID" and not ji:
        raise ValueError("HYBRID requires at least one judgement input to be named")
    return {
        "label": label,
        "reason": str(v.get("reason", "")).strip(),
        "judgement_inputs": [str(x) for x in ji],
    }


def triage_clause(clause, *, model: str = llm.DEFAULT_MODEL) -> RoleResult:
    prompt = (
        f"Document: {clause.doc_id}\n"
        f"Section: {clause.section_id} {clause.section_title}\n"
        f"Clause {clause.clause_id}:\n\n{clause.body}\n"
    )
    return run_role(TRIAGE, prompt, model=model, validate=_validate_triage)


# --- role: slotfill -------------------------------------------------------

def _coerce(value: Any, ty: str) -> Any:
    if ty == "boolean":
        if isinstance(value, bool):
            return value
        raise ValueError(f"expected a boolean, got {value!r}")
    if ty == "integer":
        if isinstance(value, bool):
            raise ValueError("a boolean is not an integer")
        return int(value)
    if ty in ("decimal", "money"):
        if isinstance(value, bool):
            raise ValueError("a boolean is not a number")
        return float(value)
    if ty == "date":
        s = str(value)
        if len(s) != 10 or s[4] != "-" or s[7] != "-":
            raise ValueError(f"expected YYYY-MM-DD, got {value!r}")
        return s
    return value


def extract_facts(
    question: str, inputs: dict[str, str], *, model: str = llm.DEFAULT_MODEL
) -> RoleResult:
    """Pull the facts a question states, leaving the rest for a person.

    `inputs` maps input name to Catala type, taken from the compiler's own JSON
    Schema rather than from anyone's guess.
    """

    def validate(v: Any) -> dict[str, Any]:
        if not isinstance(v, dict):
            raise ValueError("expected a JSON object")
        facts = v.get("facts")
        if not isinstance(facts, dict):
            raise ValueError("facts must be an object")
        out: dict[str, Any] = {}
        unknown = [k for k in facts if k not in inputs]
        if unknown:
            raise ValueError(f"invented input name(s) not in the schema: {unknown}")
        for k, raw in facts.items():
            if raw is None:
                continue
            out[k] = _coerce(raw, inputs[k])
        return {"facts": out, "omitted": sorted(set(inputs) - set(out))}

    listing = "\n".join(f"  {n}: {t}" for n, t in sorted(inputs.items()))
    prompt = f"Question:\n{question}\n\nThe rule needs these inputs:\n{listing}\n"
    return run_role(SLOTFILL, prompt, model=model, validate=validate)


# --- role: adversarial reviewer ------------------------------------------

def _validate_finding(v: Any) -> dict[str, Any]:
    if isinstance(v, list):
        if not v:
            raise ValueError("empty findings list")
        v = v[0]
    if not isinstance(v, dict):
        raise ValueError("expected a JSON object")
    verdict = str(v.get("verdict", "")).upper().strip()
    if verdict not in ("BREAK", "AMBIGUITY", "NO_BREAK_FOUND"):
        raise ValueError(f"unknown verdict {verdict!r}")
    if verdict == "BREAK":
        for k in ("fact_pattern", "inputs", "expected", "citations"):
            if k not in v or v[k] in (None, "", [], {}):
                raise ValueError(f"a BREAK must state {k}")
        if not isinstance(v["inputs"], dict):
            raise ValueError("inputs must be an object")
        if not isinstance(v["citations"], list) or not v["citations"]:
            raise ValueError("a BREAK must cite the clauses that compel its expected value")
    v["verdict"] = verdict
    return v


def propose_attack(
    packet: str,
    scope_key: str,
    module_path: str,
    scope: str,
    *,
    model: str = llm.DEFAULT_MODEL,
    seed: int | None = None,
    already_tried: list[str] | None = None,
) -> RoleResult:
    """Ask for one fact pattern that breaks the rule.

    `seed=None` lets the round vary: an adversarial reviewer that proposes the
    same attack every time has stopped being adversarial. The output is
    validated by re-execution regardless, so variety costs nothing.
    """
    tried = ""
    if already_tried:
        tried = (
            "\nFact patterns already tried on this rule, which either held or "
            "were rejected. Do not repeat them; go somewhere else:\n"
            + "\n".join(f"  - {t}" for t in already_tried[-25:])
        )
    prompt = (
        f"{packet}\n\n"
        f"To execute the rule, the harness will run scope {scope!r} of "
        f"{module_path!r} on the `inputs` object you return, so those inputs "
        f"must match the declaration block above exactly.{tried}\n"
    )
    res = run_role(REVIEWER, prompt, model=model, validate=_validate_finding, seed=seed)
    if res.ok and isinstance(res.value, dict):
        res.value.setdefault("component", "catala")
        res.value["target"] = {
            "module": Path(module_path).stem, "path": module_path, "scope": scope,
        }
    return res
