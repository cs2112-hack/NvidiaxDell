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
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import llm

REPO = Path(__file__).resolve().parents[2]


class Enforcement:
    SANDBOX = "sandbox"
    """Proven: a sandbox was actually created and carried only the role's
    permitted paths."""

    UNPROVEN = "sandbox-unproven"
    """OpenShell is installed and its gateway answers, but no sandbox has been
    successfully created, so isolation is NOT established.

    This state exists because an earlier version of this module had only two.
    It reported `sandbox` on the strength of `openshell sandbox list`
    succeeding, while `sandbox create` was in fact failing with
    ContainerRestarting -- so it claimed enforced isolation and had none. A
    capability you have not exercised is not a capability you have."""

    PROMPT = "prompt"
    """OpenShell is absent. A role is only asked not to look."""


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
        if not openshell_available():
            return Enforcement.PROMPT
        return Enforcement.SANDBOX if sandbox_proven() else Enforcement.UNPROVEN

    def isolation_note(self) -> str:
        state = self.enforcement()
        if state == Enforcement.SANDBOX:
            return (f"a sandbox carried only {', '.join(self.reads) or 'nothing'}; "
                    f"{', '.join(self.forbidden)} were never placed in it")
        if state == Enforcement.UNPROVEN:
            return (f"OpenShell answers but no sandbox has been created yet, so "
                    f"isolation is NOT established. Until one is, this role is "
                    f"only asked not to read {', '.join(self.forbidden)}"
                    + (f" (last create error: {last_create_error()[:120]})"
                       if last_create_error() else ""))
        ok, why = openshell_probe()
        return (f"prompt only — {why}. The packet is stripped, but nothing "
                f"prevents a tool-using role from reading "
                f"{', '.join(self.forbidden)}")


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
    # 1,400 tokens and a schema, measured rather than chosen. With 6,144 in
    # plain JSON mode every round ran to the limit -- 8m41s and 8m45s, the two
    # rounds on record -- because this model does not stop when its object is
    # complete, and the truncated reply was thrown away. `propose_attack` now
    # passes a JSON Schema whose string lengths the runtime enforces (verified
    # on this Ollama: told to write 500 words into a field capped at 40
    # characters, it stopped at 40, in 50 tokens, with valid JSON), and the
    # client stops reading when the object closes. The whole reply fits in
    # about 800 tokens, so a round takes a minute or two and cannot end
    # mid-string.
    num_predict=1400,
    # think=False is measured, not chosen. This role ran with think=True and
    # had never produced a finding: on qwen3.6 under this Ollama build,
    # thinking and JSON mode together return an empty completion with
    # done_reason "stop" -- the deliberation is generated and the answer never
    # is. Dropping JSON mode to recover the reasoning spends the whole
    # 6,144-token budget on deliberation and still returns nothing. Both were
    # verified directly against this model. Until the runtime stops swallowing
    # the answer, this role reasons in its output rather than before it; the
    # harness re-executes its findings either way.
    think=False,
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
        "Write for two readers. `fact_pattern` and `reasoning` are for a lawyer: "
        "exact, and citing clauses. `headline` and `why_it_matters` are for a "
        "manager with no legal or technical training: plain words, no variable "
        "names, no clause numbers, no code -- who is affected and what goes "
        "wrong for them.\n\n"
        "In `inputs`, give every input the rule needs, by its exact name. In "
        "`expected`, name only the results you are making a claim about, by "
        "their exact names. Be brief: every field has a length limit and the "
        "reply is cut off at it.\n\n"
        "Reply with JSON only, one object, with the fields in this order: "
        '{"attacks_tried": ["<a few words each>", ...], '
        '"fact_pattern": "<the situation, as a lawyer would state it>", '
        '"inputs": {<name>: <value>, ...}, '
        '"reasoning": "<which clause requires what, and which clause defeats which>", '
        '"expected": {<result name>: <what the clause requires>, ...}, '
        '"citations": ["DOC CLAUSE", ...], '
        '"verdict": "BREAK|AMBIGUITY|NO_BREAK_FOUND", '
        '"headline": "<one plain sentence: what goes wrong, and for whom>", '
        '"why_it_matters": "<one or two plain sentences>"}'
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

GENERAL = Role(
    name="general",
    purpose="Answer a question no rule or clause matched closely, from the "
            "closest clauses of the company's documents and general knowledge.",
    reads=["corpus"],
    forbidden=["catala", "docs", "src"],
    as_json=False,
    num_predict=1024,
    system=(
        "You answer a question about a company's documents that the automatic "
        "search could not answer: no rule and no clause matched it closely "
        "enough to compute or quote. You are given the clauses of the company's "
        "documents that came closest. Answer briefly and plainly, in a few short "
        "paragraphs at most. Plain text only, no Markdown headings or tables.\n\n"
        "Where a clause you were given bears on the question, rely on it and cite "
        "its reference in square brackets exactly as given, e.g. [EMP-ANNEX-C C-1.1]. "
        "Never cite a reference you were not given and never invent a clause. "
        "Where the clauses do not answer the question, say so plainly and give "
        "the general position from general knowledge, marked as such. If the "
        "question turns on the law, say it has to be checked against the law "
        "that actually applies. If you do not know, say so."
    ),
)

# --- document generation ----------------------------------------------------
#
# Five roles, all proposal roles like the four above. The drafter and encoder
# write; the three screens attack. What checks each: the drafter by the house
# convention parser and then everything downstream of it; the encoder by the
# Catala gate (`lks.gates`); the screens by re-execution where a claim can be
# executed and by a consensus rule where it cannot (`lks.screen`).

_HOUSE_CONVENTION = (
    "The document MUST follow this exact convention or it is rejected by the "
    "parser before anyone reads it:\n"
    "  * YAML front matter between two `---` lines with exactly these keys: "
    "doc_id (UPPER-CASE-WITH-HYPHENS), title (quoted), version (quoted), "
    "effective_date (YYYY-MM-DD, and only a date the request states; otherwise \"TO BE CONFIRMED\"), jurisdiction (quoted), owner (quoted).\n"
    "  * Then one `# Title` line.\n"
    "  * Sections: `## P-1 Section title`, where P is 1-4 capital letters used "
    "for the whole document and the number increments.\n"
    "  * Clauses: a line starting `**P-1.1** ` followed by the clause text. A "
    "clause id MUST begin with its section's id and a dot: `**P-2.3**` lives "
    "under `## P-2`. Never reuse an id.\n"
    "  * Sub-paragraphs belong to the clause above them and are indented two "
    "spaces: `  (a) ...`.\n"
    "  * Cross-references name a clause id that exists in this document.\n"
)

DRAFTER = Role(
    name="drafter",
    purpose="Draft a legal document in the house convention from a request and "
            "retrieved precedent.",
    reads=["generated/<workspace>/context.json"],
    forbidden=["docs", "src", "tests", "triage", "reviewer"],
    as_json=False,
    num_predict=8192,
    think=True,
    temperature=0.2,
    system=(
        "You draft legal documents for a company whose rules are compiled into "
        "executable code and then attacked by adversarial reviewers. A document "
        "that reads well but cannot be executed unambiguously will be sent back.\n\n"
        + _HOUSE_CONVENTION + "\n"
        "Draft for execution:\n"
        "  * State every figure exactly, with its unit. Never 'approximately', "
        "'around', or 'a reasonable amount'.\n"
        "  * State whether every threshold is inclusive or exclusive: 'more than "
        "40 hours', 'at least 12 months'. 'Over' and 'within' are ambiguous.\n"
        "  * Where two provisions could apply to the same facts, say which "
        "prevails, by clause id. 'By way of exception to P-4.1' or "
        "'Notwithstanding P-3.2'.\n"
        "  * Define every capitalised term in a Definitions section: "
        "'\"Payroll Week\" means ...'. Use each defined term identically "
        "everywhere.\n"
        "  * Never write a clause that cannot change any outcome. A 'greater of' "
        "whose second limb can never exceed its first is a defect.\n"
        "  * Where a rule genuinely turns on a human judgement ('good reason', "
        "'material breach'), keep the judgement and say explicitly who makes "
        "it. Do not disguise it as a computation, and do not delete it.\n\n"
        "You will be shown precedent from the company's existing documents and "
        "their encodings. Follow their structure and drafting habits. Do not "
        "copy their figures unless the request asks for them.\n\n"
        "Reply with the complete markdown file and nothing else."
    ),
)

CATALA_SYNTAX_NOTES = (
    "Syntax the reference does not spell out, verified against the Catala 1.2.1 "
    "compiler in this repo:\n"
    "  * A function is applied with `of`, arguments separated by commas, never "
    "with parentheses: `Date.add_round_down of payment_date, 6 month`, "
    "`Date.max of a, b`. Writing `f(a, b)` is a syntax error.\n"
    "  * Adding months or years to a date needs a rounding mode. Put the line "
    "`date round down` (or `date round up`) inside the scope body, before its "
    "definitions. `down` and `up` are the only accepted words.\n\n"
)
"""Catala syntax the encoding roles got wrong in practice, stated as the
compiler settled it.

The first real encoding that reached the compiler wrote
`Date.add_round_down(payment_date, 6 month)` three times and failed G1 with
nine errors. `draft/roundtrips/overtime/catala-reference.md` names
`Date.add_round_down` but never shows how to apply it, and its only `of`
examples are casts, so a model encoding without reasoning guessed a
parenthesised call. Each line here was checked with a probe module before it
was written down: `of` application compiles and computes 31 Jan + 6 months as
31 Jul; `date round down` inside the scope body compiles; `date round
decreasing` is rejected with "valid at this point: down, up".
"""


ENCODER = Role(
    name="encoder",
    purpose="Encode the computational clauses of a drafted document as a "
            "literate Catala module.",
    reads=["generated/<workspace>/document.md",
           "draft/roundtrips/overtime/catala-reference.md"],
    forbidden=["catala/modules", "docs", "src", "tests", "triage", "reviewer"],
    as_json=False,
    num_predict=8192,
    # think=False is measured, not chosen -- the same lesson as REVIEWER. The
    # first real generation run gave this role think=True and 8,192 tokens: it
    # spent the entire budget deliberating over a 4.4 KB document and returned
    # no module at all ("the token budget (8192) was consumed by reasoning").
    # The budget cannot simply grow: the prompt (the document, the Catala
    # reference and a precedent module) already takes about half of the
    # 16,384-token context, and at ~12 tok/s a longer deliberation costs 25+
    # minutes per attempt. An unreasoned module is rougher, but G1-G4 and the
    # repair loop exist to catch rough; nothing can repair a reply that never
    # arrives. The drafter keeps think=True, with `lks.generate`'s fallback: it fit its budget in the first real run and exhausted it in the second, so a deliberation that eats the budget is repeated at once without reasoning.
    think=False,
    system=(
        "You encode legal rules in Catala 1.2.1, which expresses law as "
        "prioritised default logic. You are given one document and a reference "
        "to the language. Encode every clause that states a computation or a "
        "yes/no rule. Do not encode clauses that state no computation.\n\n"
        "The file you write:\n"
        "  1. `# <document title>`, then `> Module <CamelCaseName>`. No `> Using` "
        "and no `> Include`: the module must be self-contained.\n"
        "  2. Declarations in ```catala-metadata blocks; definitions in ```catala "
        "blocks. Fences at column 0.\n"
        "  3. Directly above EVERY ```catala block, on its own line, write which "
        "clauses it encodes:\n"
        "       | ENCODES: P-4.1, P-4.2\n"
        "     The harness replaces that line with the verbatim clause text. Do "
        "not quote clauses yourself. A block that encodes no clause must "
        "instead carry `| NO-CLAUSE: <why>`.\n\n"
        "Make it testable, or it will be rejected:\n"
        "  * Scope INPUTS may only be boolean, integer, decimal, money, date, or "
        "an enumeration you declare whose constructors carry no content. Never "
        "a list, structure or duration as an input.\n"
        "  * Give every definition in a hierarchy an explicit `label`, and every "
        "exception an explicit parent. A Catala exception does NOT inherit its "
        "parent's condition: restate it in full.\n"
        "  * Sibling exceptions must have mutually exclusive conditions, or the "
        "scope raises Conflict at runtime.\n"
        "  * Every output must have a value for every input: write an "
        "unconditional base case.\n"
        "  * Where a clause turns on a judgement the document does not define, "
        "make that judgement a boolean INPUT named for it in snake_case. Never "
        "compute it.\n"
        "  * Use `assertion` only to exclude facts that cannot exist (a negative "
        "number of hours). Never to exclude inconvenient cases.\n\n"
        + CATALA_SYNTAX_NOTES
        + "Reply with the contents of the .catala_en file and nothing else."
    ),
)

_SCREEN_COMMON = (
    "You are one of three independent reviewers of a newly drafted legal "
    "document. The others are attacking different things and you will not see "
    "what they find. You do not approve documents; there is no 'looks fine' "
    "outcome. If you find nothing, return an empty findings list and list what "
    "you tried.\n\n"
    "Every clause id you cite must exist in the document. Every `quote` must be "
    "copied exactly from the clause you cite; a finding whose quote is not in "
    "that clause is discarded unread. Report at most 4 findings, the most "
    "consequential first, and keep each field short: a reply that runs past the "
    "token budget is truncated and discarded whole.\n\n"
)

SCREEN_LOGIC = Role(
    name="screen-logic",
    purpose="Find a fact pattern on which the document's executable encoding "
            "gives an answer the document's words do not.",
    reads=["generated/<workspace>/document.md", "generated/<workspace>/packet-logic.md"],
    forbidden=["docs", "src", "tests", "triage", "corpus", "catala/modules"],
    temperature=0.7,
    num_predict=2400,
    think=False,        # think+JSON returns empty on this runtime; see REVIEWER
    system=(
        _SCREEN_COMMON
        + "Your target is LOGIC. You are given the document and the compiled "
        "rule that encodes it, with its exact inputs. Find concrete facts on "
        "which the rule's answer differs from what the document's words require.\n\n"
        "Attack, in order of yield: exact threshold boundaries, probing both "
        "sides and the boundary itself; two exceptions applying at once; "
        "'in addition to' against 'in substitution for'; the order of caps, "
        "multipliers and rounding; greater-of and lesser-of; inclusive and "
        "exclusive dates; a base case reached when an exception should have "
        "applied.\n\n"
        "Derive `expected` FROM THE DOCUMENT'S WORDS, citing the clauses that "
        "compel it. The rule is re-executed on your inputs; a finding whose "
        "expected value matches what the rule actually produces is discarded. "
        "`inputs` must name exactly the rule's inputs with values of their types. "
        "If the words genuinely do not decide the case, use verdict AMBIGUITY.\n\n"
        "Reply with JSON only: "
        '{"findings": [{"verdict": "BREAK|AMBIGUITY", "clause_ids": ["P-1.1"], '
        '"scope": "<ScopeName>", "inputs": {...}, "expected": {<output>: <value>}, '
        '"quote": "<exact words>", "summary": "<one sentence>", '
        '"reasoning": "<which clause requires what>"}], '
        '"attacks_tried": ["..."]}'
    ),
)

SCREEN_LANGUAGE = Role(
    name="screen-language",
    purpose="Find wording in the document that a court could read two ways.",
    reads=["generated/<workspace>/document.md"],
    forbidden=["docs", "src", "tests", "triage", "corpus", "catala"],
    temperature=0.3,
    num_predict=2400,
    think=False,
    system=(
        _SCREEN_COMMON
        + "Your target is LANGUAGE. You see only the document, never its code. "
        "Find wording two competent lawyers would read differently.\n\n"
        "Kinds, and use exactly these names:\n"
        "  UNDEFINED_TERM         a capitalised or technical term used but never defined\n"
        "  INCONSISTENT_TERM      one concept named two ways, or one name used for two concepts\n"
        "  AMBIGUOUS_REFERENT     'it', 'such', 'the relevant period' with more than one candidate\n"
        "  UNQUANTIFIED_STANDARD  'reasonable', 'promptly', 'material' with no measure or decider\n"
        "  DANGLING_REFERENCE     a cross-reference to a clause that does not exist\n"
        "  CIRCULAR_DEFINITION    a definition that depends on itself\n"
        "  UNSTATED_PRECEDENCE    two clauses reach the same facts and neither says which wins\n"
        "  INOPERATIVE_CLAUSE     a clause that can never change any outcome\n\n"
        "Reply with JSON only: "
        '{"findings": [{"kind": "<KIND>", "clause_ids": ["P-1.1"], '
        '"quote": "<exact words>", "summary": "<one sentence>", '
        '"fix": "<the smallest wording change that removes it>"}], '
        '"attacks_tried": ["..."]}'
    ),
)

SCREEN_CONSISTENCY = Role(
    name="screen-consistency",
    purpose="Find where the document contradicts the company's existing "
            "documents or itself, or leaves a case it reaches undecided.",
    reads=["generated/<workspace>/document.md", "generated/<workspace>/packet-consistency.md"],
    forbidden=["docs", "src", "tests", "triage", "catala/modules"],
    temperature=0.4,
    num_predict=2400,
    think=False,
    system=(
        _SCREEN_COMMON
        + "Your target is CONSISTENCY. You are given the new document, the "
        "company's existing clauses closest to it, and the new document's "
        "compiled rule and inputs.\n\n"
        "Kinds, and use exactly these names:\n"
        "  CONTRADICTS_CORPUS      the same facts get a different answer here than "
        "under an existing clause, and neither says which prevails\n"
        "  INTERNAL_CONTRADICTION  two clauses of this document require incompatible things\n"
        "  UNDECIDED_CASE          facts the document plainly reaches but does not decide\n\n"
        "For CONTRADICTS_CORPUS cite the existing clause in `corpus_refs` by its "
        "full reference ('EMP-ANNEX-C C-4.1'). Where a case can be put to the "
        "compiled rule, give `scope`, `inputs` naming exactly its inputs, and "
        "`expected`: it will be executed.\n\n"
        "Reply with JSON only: "
        '{"findings": [{"kind": "<KIND>", "clause_ids": ["P-1.1"], '
        '"corpus_refs": ["DOC CLAUSE"], "quote": "<exact words>", '
        '"summary": "<one sentence>", "scope": "<ScopeName or empty>", '
        '"inputs": {...}, "expected": {...}}], "attacks_tried": ["..."]}'
    ),
)

SCREENS = (SCREEN_LOGIC, SCREEN_LANGUAGE, SCREEN_CONSISTENCY)

ROLES = {
    r.name: r
    for r in (TRIAGE, REVIEWER, REENCODER, SLOTFILL, GENERAL, DRAFTER, ENCODER, *SCREENS)
}


# --- OpenShell ------------------------------------------------------------

_OPENSHELL_PROBE: tuple[bool, str] | None = None
_last_create_error: list[str] = []


def last_create_error() -> str:
    return _last_create_error[0] if _last_create_error else ""


def _openshell_bin() -> str | None:
    for c in (shutil.which("openshell"), str(Path.home() / ".local/bin/openshell")):
        if c and Path(c).exists():
            return c
    return None


def openshell_probe(refresh: bool = False) -> tuple[bool, str]:
    """Whether a sandbox can ACTUALLY be created, and why not if it cannot.

    This used to be `shutil.which("openshell")`. That is not the same question.
    A binary on PATH with an unreachable gateway would have made this module
    report enforced isolation while enforcing nothing -- the one lie it must not
    tell -- so the check now asks the gateway to list sandboxes and believes
    only a real answer.
    """
    global _OPENSHELL_PROBE
    if _OPENSHELL_PROBE is not None and not refresh:
        return _OPENSHELL_PROBE
    if os.environ.get("LKS_FORCE_PROMPT_ISOLATION") == "1":
        _OPENSHELL_PROBE = (False, "forced off by LKS_FORCE_PROMPT_ISOLATION")
        return _OPENSHELL_PROBE
    exe = _openshell_bin()
    if exe is None:
        _OPENSHELL_PROBE = (False, "openshell is not installed")
        return _OPENSHELL_PROBE
    try:
        proc = subprocess.run(
            ["sg", "docker", "-c", f"{exe} sandbox list"],
            capture_output=True, text=True, timeout=90,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _OPENSHELL_PROBE = (False, f"gateway did not answer: {type(e).__name__}")
        return _OPENSHELL_PROBE
    blob = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        _OPENSHELL_PROBE = (False, f"gateway refused: {blob.strip()[:200]}")
        return _OPENSHELL_PROBE
    _OPENSHELL_PROBE = (True, blob.strip().splitlines()[0][:120] if blob.strip() else "gateway reachable")
    return _OPENSHELL_PROBE


def openshell_available() -> bool:
    """Whether the gateway answers. NOT whether a sandbox can be created --
    see Enforcement.UNPROVEN for why those are different questions."""
    return openshell_probe()[0]


_CREATE_PROVEN: bool | None = None


def sandbox_proven(refresh: bool = False) -> bool:
    """Whether a sandbox has actually been created in this session.

    Deliberately not probed eagerly: creating one is slow. It is set by
    `open_sandbox` on success, so the first real use establishes it and
    `enforcement()` tells the truth from then on. Until then the state is
    UNPROVEN, which reads as "not established" everywhere it is printed.
    """
    global _CREATE_PROVEN
    if refresh:
        _CREATE_PROVEN = None
    return bool(_CREATE_PROVEN)


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


@dataclass
class Sandbox:
    """A live OpenShell sandbox holding only what one role may see.

    Files are UPLOADED rather than bind-mounted, which is stricter: the
    sandbox has no view of the host filesystem at all, so a role's forbidden
    paths are not merely unreadable, they were never there.
    """

    name: str
    role: str
    exe: str
    uploaded: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def _run(self, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sg", "docker", "-c", f"{self.exe} " + " ".join(args)],
            capture_output=True, text=True, timeout=timeout,
        )

    def upload(self, local: Path, remote: str) -> bool:
        # upload takes positionals: <NAME> <LOCAL_PATH> [DEST]
        r = self._run([
            "sandbox", "upload", "--no-git-ignore",
            self.name, f"'{local}'", f"'{remote}'",
        ], timeout=600)
        if r.returncode == 0:
            self.uploaded.append(remote)
        else:
            self.errors.append(f"upload {remote}: {(r.stderr or r.stdout).strip()[:160]}")
        return r.returncode == 0

    def exec(self, command: str, timeout: int = 600) -> tuple[int, str, str]:
        esc = command.replace("'", "'\\''")
        r = self._run(["sandbox", "exec", "-n", self.name, "--no-tty",
                       "sh", "-lc", f"'{esc}'"], timeout=timeout)
        return r.returncode, r.stdout, r.stderr

    def delete(self) -> None:
        # openshell 0.0.106 has no --yes on delete; passing it made this a no-op
        self._run(["sandbox", "delete", self.name], timeout=300)


def open_sandbox(role: Role, suffix: str = "") -> Sandbox | None:
    """Create a sandbox carrying exactly the paths `role` may read.

    Returns None when no sandbox can be created, so a caller can fall back to
    the prompt-only path with its eyes open rather than silently believing it
    is isolated.
    """
    ok, _why = openshell_probe()
    exe = _openshell_bin()
    if not ok or exe is None:
        return None
    # the gateway's naming rule: 1-19 chars, lowercase, starts with a letter,
    # single internal hyphens, ends alphanumeric
    base = f"lks-{role.name}{('-' + suffix) if suffix else ''}"
    name = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", base.lower()))[:19].rstrip("-")
    sb = Sandbox(name=name, role=role.name, exe=exe)
    image = os.environ.get("LKS_SANDBOX_IMAGE", "docker.io/library/python:3.12-slim")
    r = sb._run(
        ["sandbox", "create", "--name", name, "--from", image], timeout=900
    )
    if r.returncode != 0:
        sb.errors.append((r.stderr or r.stdout).strip()[:400])
        _last_create_error.clear()
        _last_create_error.append(sb.errors[-1])
        return None
    global _CREATE_PROVEN
    _CREATE_PROVEN = True
    for rel in role.reads:
        src = REPO / rel
        if src.exists():
            sb.upload(src, f"/work/{rel}")
    return sb


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
    kind: str = ""
    """Why a failed run failed, so it can be worded for a person: `timeout`
    (the model was busy or went silent), `unavailable`, `truncated` (the reply
    hit the token limit before it was complete), `invalid` (complete, but not
    the shape the role requires) or `refused` (empty)."""

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
    schema: dict[str, Any] | None = None,
    timeout: int = 900,
) -> RoleResult:
    """Run one role once and validate its output.

    A role that returns malformed output fails rather than returning something
    approximate, because every consumer of these results treats them as
    candidate legal content.

    `schema` constrains a JSON role's reply at generation time. It is how a
    caller that knows the target -- a scope's exact inputs, the lengths that
    fit the token budget -- makes a malformed or truncated reply impossible
    rather than merely discouraged.

    Nothing the model client raises escapes: a timeout or a dropped connection
    is one failed run, reported with its `kind`, not a crashed loop.
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
            schema=schema if role.as_json else None,
            num_predict=role.num_predict,
            think=role.think,
            timeout=timeout,
        )
    except llm.ModelTimedOut as e:
        return RoleResult(role.name, False, error=str(e), enforcement=enforcement,
                          kind="timeout")
    except llm.ModelUnavailable as e:
        return RoleResult(role.name, False, error=str(e), enforcement=enforcement,
                          kind="unavailable")
    except llm.ModelRefused as e:
        return RoleResult(role.name, False, error=str(e), enforcement=enforcement,
                          kind="refused")

    try:
        value = reply.json() if role.as_json else reply.text
        if validate is not None:
            value = validate(value)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        if reply.truncated:
            error = (f"the reply reached the {role.num_predict}-token limit before it "
                     f"was complete; {error}")
        return RoleResult(
            role.name, False, error=error,
            enforcement=enforcement, usage=reply.usage, raw=reply.text[:1200],
            kind="truncated" if reply.truncated else "invalid",
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


# --- role: general fallback -----------------------------------------------

def answer_general(
    question: str,
    clauses: list[tuple[str, str, str]],
    *,
    model: str = llm.DEFAULT_MODEL,
    timeout: int = 180,
) -> RoleResult:
    """An answer to a question no rule or clause matched closely enough.

    `clauses` are the closest clauses of the corpus, as (ref, heading, body).
    The reply's value is `{"text", "cited"}`, where `cited` keeps only the
    references it was actually given: a citation to a clause it never saw is
    dropped, not shown. It is read by a person waiting on an answer, hence the
    short `timeout`: a model busy with a long job is reported as busy.
    """
    given = {ref for ref, _h, _b in clauses}

    def validate(v: Any) -> dict[str, Any]:
        text = str(v).strip()
        if not text:
            raise ValueError("empty answer")
        cited = [r for r in given if f"[{r}]" in text or r in text]
        return {"text": text, "cited": sorted(cited)}

    listing = "\n\n".join(f"[{ref}] {head}\n{body}" for ref, head, body in clauses)
    prompt = (f"Closest clauses from the company's documents:\n\n{listing or '(none)'}"
              f"\n\nQuestion:\n{question}\n")
    return run_role(GENERAL, prompt, model=model, validate=validate, timeout=timeout)


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
    if "reasoning" in v and not v.get("source_reasoning"):
        v["source_reasoning"] = v.pop("reasoning")
    v["verdict"] = verdict
    return v


def finding_schema(io: Any = None) -> dict[str, Any]:
    """The reviewer's reply: this rule's exact inputs and results, and field
    lengths that together fit inside `REVIEWER.num_predict`.

    `reasoning` comes before `expected` and `verdict` on purpose. The role
    cannot deliberate before answering on this runtime (D-10), so the order of
    the fields is the order it thinks in."""
    def text(n: int) -> dict[str, Any]:
        return {"type": "string", "maxLength": n}

    props: dict[str, Any] = {
        "attacks_tried": {"type": "array", "maxItems": 4, "items": text(100)},
        "fact_pattern": text(600),
        "inputs": io.input_schema if io is not None else {"type": "object"},
        "reasoning": text(900),
        "expected": io.output_schema if io is not None else {"type": "object"},
        "citations": {"type": "array", "maxItems": 6, "items": text(40)},
        "verdict": {"type": "string", "enum": ["BREAK", "AMBIGUITY", "NO_BREAK_FOUND"]},
        "headline": text(160),
        "why_it_matters": text(300),
    }
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


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

    The rule's exact inputs and results go into the prompt and into the reply
    schema, so the reviewer cannot name an input the rule lacks or give a value
    of the wrong type -- the commonest way an attempt used to be wasted.
    """
    from .interface import scope_io

    try:
        io = scope_io(module_path, scope)
    except Exception:                                           # noqa: BLE001
        io = None
    tried = ""
    if already_tried:
        tried = (
            "\nFact patterns already tried on this rule, which either held or "
            "were rejected. Do not repeat them; go somewhere else:\n"
            + "\n".join(f"  - {t}" for t in already_tried[-25:])
        )
    interface = ""
    if io is not None:
        interface = (
            "\n\nThe rule's inputs (* = required):\n"
            + "\n".join(f"  {n}: {t}{' *' if n in io.required else ''}"
                        + (f" (one of {', '.join(io.enums[n])})" if n in io.enums else "")
                        for n, t in io.inputs.items())
            + "\nThe rule's results:\n"
            + "\n".join(f"  {n}: {t}" for n, t in io.outputs.items())
        )
    prompt = (
        f"{packet}\n\n"
        f"To execute the rule, the harness will run scope {scope!r} of "
        f"{module_path!r} on the `inputs` object you return, so those inputs "
        f"must match the declaration block above exactly.{interface}{tried}\n"
    )
    res = run_role(REVIEWER, prompt, model=model, validate=_validate_finding, seed=seed,
                   schema=finding_schema(io))
    if res.ok and isinstance(res.value, dict):
        # assigned, not defaulted: this role attacks a compiled rule, and a
        # finding claiming another component would not be re-executed
        res.value["component"] = "catala"
        res.value["target"] = {
            "module": Path(module_path).stem, "path": module_path, "scope": scope,
        }
    return res
