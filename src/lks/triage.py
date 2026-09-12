"""Triage: decide what each clause *is*, and therefore where it goes.

Three outcomes, not two. The two-way split (rule / not-rule) is the obvious
design and it is wrong, because the most legally consequential clauses are
mixed:

    E-6.2  "a claim submitted after 60 days but within 120 days may be
            reimbursed where the line manager certifies that the delay was
            for good reason"

The 60/120-day arithmetic is perfectly deterministic. "Good reason" is not
computable and never will be. Forcing this clause into Catala means inventing
a definition of "good reason" that the document does not contain; forcing it
into the vector store throws away arithmetic we can verify. So:

    RULE     -> Catala only.
    PROSE    -> vector store only.
    HYBRID   -> Catala, with the non-computable predicate lifted to an
                explicit scope *input* (a "judgement input"), AND a vector
                store entry pointing at that input. The encoding is then
                honest: the module computes the consequence *given* the
                judgement, and never pretends to make it.

Heuristics here only *propose*. Every clause's final label lives in a
committed ledger (`triage/decisions.yaml`) with a stated reason, so that
triage is reproducible from a git checkout and reviewable by a human.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from .model import Clause, exception_cues, referenced_clauses
from .segment import load_corpus


class Label(str, Enum):
    RULE = "RULE"
    PROSE = "PROSE"
    HYBRID = "HYBRID"


# --- signals ---------------------------------------------------------------

MONEY_RE = re.compile(r"(?:£|\$|€|GBP|USD|EUR)\s?[\d,]+(?:\.\d+)?", re.I)
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s?%")
MULTIPLIER_RE = re.compile(r"\b\d+(?:\.\d+)?\s+times\b", re.I)
DURATION_RE = re.compile(
    r"\b\d+\s+(?:day|days|month|months|year|years|week|weeks|hour|hours|minute|minutes"
    r"|business day|business days)\b",
    re.I,
)
NUMERIC_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
THRESHOLD_RE = re.compile(
    r"\b(?:in excess of|exceeds?|exceeding|not less than|no less than|at least|"
    r"more than|less than|up to|not exceed|must not exceed|greater of|lesser of|"
    r"maximum of|minimum of|or more but less than|or above|or below|"
    r"within \d+|after \d+|for a period of|for a continuous period of|"
    r"on or before|measured from|from the date|for more than|"
    r"or above but|but not less than|but within)\b",
    re.I,
)
COMPUTE_RE = re.compile(
    r"\b(?:calculated|divided by|multiplied by|multiplied|plus|minus|rate of|"
    r"reduced by|increased by|pro-rated|rounded|expressed as a percentage|"
    r"accrues at|aggregate of|sum of|less the)\b",
    re.I,
)
ENTITLEMENT_RE = re.compile(
    r"\b(?:is entitled to|entitlement|is payable|is not payable|are retained|"
    r"is retained|forfeits?|accrues?|may carry forward|is reimbursable|"
    r"may be reimbursed|will be reimbursed|will be paid|is paid|may recover|"
    r"may terminate|may be recovered|no recovery|is waived|is forfeited|"
    r"is applied|is capped|must not exceed|is reduced|is increased|"
    r"takes precedence|must not be deleted|will delete|is deleted)\b",
    re.I,
)

# Language that cannot be reduced to a rule without inventing law.
JUDGEMENT_RE = re.compile(
    r"\b(?:reasonabl\w+|ought reasonably|good reason|judgement|judgment|material\b|"
    r"materially|appropriate\w*|satisfactory|discretion\w*|in the opinion of|"
    r"as it sees fit|genuine pre-estimate|adequate|practicable|expected to|"
    r"no less than the degree of care|properly incurred|good faith|"
    r"reasonably anticipated|final\b|determined by the)\b",
    re.I,
)
DEFINITION_RE = re.compile(r'^"?[A-Z][\w\s\-]{2,60}"?\s+(?:means|includes)\b')
PROSE_ONLY_RE = re.compile(
    r"\b(?:purpose of this|headings are for convenience|is governed by the law|"
    r"exclusive jurisdiction|no variation|acknowledges? that|is discretionary|"
    r"does not create any partnership|is not intended to describe|"
    r"remains? responsible for their own|sole financial remedy|"
    r"reserves all rights|is accountable for|no licence|as is|"
    r"gives no warranty|submit to the)\b",
    re.I,
)


@dataclass
class Signals:
    money: int = 0
    percent: int = 0
    multiplier: int = 0
    duration: int = 0
    numeric: int = 0
    threshold: int = 0
    compute: int = 0
    entitlement: int = 0
    judgement: list[str] = field(default_factory=list)
    is_definition: bool = False
    prose_markers: list[str] = field(default_factory=list)
    exception_cues: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)

    @property
    def determinism_score(self) -> int:
        return (
            2 * self.money
            + 2 * self.percent
            + 2 * self.multiplier
            + self.duration
            + 2 * self.threshold
            + 2 * self.compute
            + 2 * self.entitlement
            + min(self.numeric, 4)
        )


def extract_signals(c: Clause) -> Signals:
    t = c.body
    return Signals(
        money=len(MONEY_RE.findall(t)),
        percent=len(PERCENT_RE.findall(t)),
        multiplier=len(MULTIPLIER_RE.findall(t)),
        duration=len(DURATION_RE.findall(t)),
        numeric=len(NUMERIC_RE.findall(t)),
        threshold=len(THRESHOLD_RE.findall(t)),
        compute=len(COMPUTE_RE.findall(t)),
        entitlement=len(ENTITLEMENT_RE.findall(t)),
        judgement=sorted({m.lower() for m in JUDGEMENT_RE.findall(t)}),
        is_definition=bool(DEFINITION_RE.match(t.strip())),
        prose_markers=sorted({m.lower() for m in PROSE_ONLY_RE.findall(t)}),
        exception_cues=exception_cues(t),
        refs=referenced_clauses(t, exclude=c.clause_id),
    )


def propose(c: Clause) -> tuple[Label, str, Signals]:
    """Heuristic proposal. Deliberately conservative: when a clause carries
    both arithmetic and judgement language we propose HYBRID rather than
    guessing, because that is exactly the case a human must look at."""
    s = extract_signals(c)
    det = s.determinism_score

    if s.prose_markers and det < 6:
        return Label.PROSE, f"boilerplate//interpretive markers {s.prose_markers}", s

    if s.is_definition:
        if det >= 4 and not s.judgement:
            return Label.RULE, f"quantified definition (score {det}) -> becomes a struct/type", s
        if det >= 4 and s.judgement:
            return Label.HYBRID, f"definition mixes arithmetic (score {det}) with {s.judgement}", s
        return Label.PROSE, f"qualitative definition; judgement terms {s.judgement or 'none'}", s

    if det >= 4 and s.judgement:
        return Label.HYBRID, f"arithmetic (score {det}) gated on judgement {s.judgement}", s
    if det >= 6:
        return Label.RULE, f"quantified and computable (score {det})", s
    if det >= 3 and not s.judgement:
        return Label.RULE, f"weakly quantified but computable (score {det})", s
    if s.judgement:
        return Label.PROSE, f"judgement-dependent {s.judgement}, score {det}", s
    return Label.PROSE, f"no computable content (score {det})", s


# --- committed ledger ------------------------------------------------------

LEDGER_PATH = Path("triage/decisions.yaml")


@dataclass
class Decision:
    ref: str
    label: Label
    reason: str
    module: str | None = None          # Catala module this clause lands in
    qualifies: list[str] = field(default_factory=list)  # module refs a PROSE clause annotates
    judgement_inputs: list[str] = field(default_factory=list)  # for HYBRID
    source: str = "heuristic"          # "heuristic" | "adjudicated"
    clause_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
            "ref": self.ref,
            "label": self.label.value,
            "reason": self.reason,
            "source": self.source,
            "clause_hash": self.clause_hash,
        }
        if self.module:
            d["module"] = self.module
        if self.qualifies:
            d["qualifies"] = self.qualifies
        if self.judgement_inputs:
            d["judgement_inputs"] = self.judgement_inputs
        return d


def triage_corpus(corpus_dir: str | Path = "corpus") -> list[Decision]:
    out: list[Decision] = []
    for doc in load_corpus(corpus_dir):
        for c in doc.clauses:
            label, reason, _ = propose(c)
            out.append(
                Decision(ref=c.ref, label=label, reason=reason, clause_hash=c.hash)
            )
    return out


def load_ledger(path: str | Path = LEDGER_PATH) -> dict[str, Decision]:
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    out: dict[str, Decision] = {}
    for ref, d in (data.get("decisions") or {}).items():
        out[ref] = Decision(
            ref=ref,
            label=Label(d["label"]),
            reason=d.get("reason", ""),
            module=d.get("module"),
            qualifies=d.get("qualifies", []) or [],
            judgement_inputs=d.get("judgement_inputs", []) or [],
            source=d.get("source", "adjudicated"),
            clause_hash=d.get("clause_hash", ""),
        )
    return out


def write_ledger(decisions: list[Decision], path: str | Path = LEDGER_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "_comment": (
            "Triage ledger. 'heuristic' rows were proposed by lks.triage and have "
            "NOT been adjudicated. Edit a row and set source: adjudicated to fix it; "
            "adjudicated rows always win over the heuristic. clause_hash pins the "
            "decision to the clause text it was made against -- if the clause changes, "
            "lks.triage.stale() will flag the decision for re-review."
        ),
        "decisions": {d.ref: d.to_dict() for d in decisions},
    }
    p.write_text(yaml.safe_dump(body, sort_keys=False, width=100, allow_unicode=True))


def stale(ledger: dict[str, Decision], corpus_dir: str | Path = "corpus") -> list[str]:
    """Decisions whose clause text has changed since the decision was recorded."""
    problems: list[str] = []
    for doc in load_corpus(corpus_dir):
        for c in doc.clauses:
            d = ledger.get(c.ref)
            if d is None:
                problems.append(f"{c.ref}: no triage decision")
            elif d.clause_hash and d.clause_hash != c.hash:
                problems.append(
                    f"{c.ref}: clause changed since triage "
                    f"({d.clause_hash} -> {c.hash}); decision needs re-review"
                )
    return problems
