"""Harness for the adversarial reviewer loop.

Division of labour: this module does the deterministic mechanics -- assembling
what the reviewer is allowed to see, validating what it claims, recording
confirmed breaks as permanent tests, and measuring exception-branch coverage.
The adversarial generation itself is done by a reviewer agent, driven by
`reviewer/PROTOCOL.md`.

The one property this module exists to *enforce* rather than request:

    the reviewer sees the source document and the artefact,
    and never the implementer's reasoning.

Asking an agent nicely not to look at design notes is not a control. So the
packet is assembled by construction: it contains the corpus clauses and the
artefact with implementer commentary mechanically stripped. `docs/`, commit
messages, and module docstrings are never included, and inline `#` comments
are removed from Catala code blocks before the artefact is shown.

Why that matters: a reviewer that reads "we interpreted C-8.1 as applying only
to the highest multiplier" has been handed the implementer's reading of the
clause, and will tend to verify the code against that reading rather than
against the clause. The whole value of the exercise is an independent reading.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catala_runner import (
    CatalaError,
    exception_tree,
    run_scope,
    tree_signature,
    values_agree,
)
from .counterexample import (
    Counterexample,
    DegenerateCounterexample,
    load_all,
    record_counterexample,
    save,
)
from .literate import parse_literate
from .segment import load_corpus

# `#[test]`, `#[doc = ...]` etc. are semantic attributes, not commentary.
# A bare `#` line, or `##` doc text, is implementer voice and gets stripped.
COMMENT_LINE_RE = re.compile(r"^\s*#(?!\[)")
FENCE_OPEN_RE = re.compile(r"^```catala(?:-metadata|-test-cli|-test)?\s*$")
FENCE_CLOSE_RE = re.compile(r"^```\s*$")
DIRECTIVE_RE = re.compile(r"^>\s*(Module|Using|Include)\b")


def strip_implementer_commentary(src: str) -> tuple[str, int]:
    """Reduce a literate Catala module to law plus code, dropping every line
    of implementer voice.

    Returns (stripped_source, n_lines_removed).

    This is stricter than removing `#` comments, and it has to be. A literate
    module's free text is a *mixture*: the `|`-gutter quotations are the law
    and the reviewer must see them, but the explanatory paragraphs around them
    are the implementer explaining their reading of the clause -- exactly what
    a blind reviewer must not be handed. A paragraph like "C-8.1 is discharged
    structurally rather than by a rule of its own" tells the reviewer how to
    interpret the code, and a reviewer given that will tend to check the code
    against it instead of against the clause.

    So in free-text regions only these survive:
      * `|` quotation lines (the law, and the module's claim about which
        clause it encodes -- a mis-attribution must stay reviewable)
      * `#` headings (structure, needed to read the file)
      * `> Module` / `> Using` / `> Include` directives (semantics)
      * blank lines
    Everything else in free text is dropped. Inside code blocks, `#` comment
    lines are dropped but semantic attributes (`#[test]`, `#[doc = ...]`) are
    kept.
    """
    out: list[str] = []
    removed = 0
    in_code = False
    for line in src.splitlines():
        if not in_code and FENCE_OPEN_RE.match(line):
            in_code = True
            out.append(line)
            continue
        if in_code and FENCE_CLOSE_RE.match(line):
            in_code = False
            out.append(line)
            continue
        if in_code:
            if COMMENT_LINE_RE.match(line):
                removed += 1
                continue
            out.append(line)
            continue
        # free text
        stripped = line.strip()
        if (
            not stripped
            or line.startswith("|")
            or stripped.startswith("#")
            or DIRECTIVE_RE.match(line)
        ):
            out.append(line)
            continue
        removed += 1
    # collapse the runs of blank lines left behind by removed paragraphs
    collapsed: list[str] = []
    for line in out:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)
    return "\n".join(collapsed) + ("\n" if src.endswith("\n") else ""), removed


@dataclass
class ReviewPacket:
    component: str
    target: dict[str, Any]
    source_clauses: str          # verbatim corpus text the artefact claims to encode
    artefact: str                # the artefact, commentary stripped
    executable: dict[str, Any] = field(default_factory=dict)  # how to run it
    commentary_lines_removed: int = 0

    def render(self) -> str:
        parts = [
            f"# Review packet: {self.component}",
            f"\ntarget: {json.dumps(self.target)}",
            "\n## Source document (authoritative)\n",
            self.source_clauses,
            "\n## Artefact under review\n",
            "```",
            self.artefact,
            "```",
        ]
        if self.executable:
            parts += ["\n## How to execute the artefact\n", json.dumps(self.executable, indent=2)]
        return "\n".join(parts)


def build_catala_packet(
    module_path: str | Path, corpus_dir: str | Path = "corpus"
) -> ReviewPacket:
    """Assemble a blind review packet for one Catala module.

    The source section contains the *full* text of every clause the module
    quotes, taken from the corpus rather than from the module -- so that a
    drifted or truncated quotation inside the artefact is visible as a
    disagreement with the authoritative text.
    """
    module_path = Path(module_path)
    lf = parse_literate(module_path)
    clauses = {}
    for doc in load_corpus(corpus_dir):
        for c in doc.clauses:
            clauses[c.ref] = (doc, c)

    seen: list[str] = []
    blocks: list[str] = []
    for q in lf.quotations:
        if q.ref in seen:
            continue
        seen.append(q.ref)
        entry = clauses.get(q.ref)
        if entry is None:
            blocks.append(f"### {q.ref}\n\n(NOT PRESENT IN THE CORPUS)\n")
            continue
        doc, c = entry
        blocks.append(
            f"### {c.ref} — {doc.title} (v{doc.version}, effective {doc.effective_date})\n"
            f"Section {c.section_id} {c.section_title}\n\n{c.body}\n"
        )

    artefact, removed = strip_implementer_commentary(
        module_path.read_text(encoding="utf-8")
    )
    scopes = discover_scopes(module_path)
    return ReviewPacket(
        component="catala",
        target={"module": module_path.stem, "path": str(module_path)},
        source_clauses="\n".join(blocks) if blocks else "(module quotes no clauses)",
        artefact=artefact,
        executable={
            "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
            "path": str(module_path),
            "scopes": scopes,
        },
        commentary_lines_removed=removed,
    )


SCOPE_DECL_RE = re.compile(r"^\s*(?:#\[test\]\s*)?declaration scope ([A-Z]\w*)\s*:", re.M)
VAR_DECL_RE = re.compile(
    r"^\s*(input|output|internal|context)(?:\s+output)?\s+([a-z]\w*)\s+(?:content|condition)", re.M
)


def discover_scopes(module_path: str | Path) -> dict[str, dict[str, list[str]]]:
    """Scopes and their variables, read from the declarations.

    This reads source text, which the rest of the system is forbidden from
    doing to *compute answers*. It is legitimate here: we are enumerating what
    to execute and what to ask the compiler about, not predicting results.
    """
    src = Path(module_path).read_text(encoding="utf-8")
    out: dict[str, dict[str, list[str]]] = {}
    positions = [(m.start(), m.group(1)) for m in SCOPE_DECL_RE.finditer(src)]
    for i, (pos, name) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(src)
        body = src[pos:end]
        # stop at the end of the declaration block
        fence = body.find("```")
        if fence > 0:
            body = body[:fence]
        qual: dict[str, list[str]] = {"input": [], "output": [], "internal": [], "context": []}
        for m in VAR_DECL_RE.finditer(body):
            q, v = m.group(1), m.group(2)
            if v not in qual[q]:
                qual[q].append(v)
        out[name] = qual
    return out


# --- recording reviewer findings ------------------------------------------


@dataclass
class FindingResult:
    accepted: list[Counterexample] = field(default_factory=list)
    rejected: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    ambiguities: list[dict[str, Any]] = field(default_factory=list)
    no_break: list[dict[str, Any]] = field(default_factory=list)

    @property
    def broke_it(self) -> bool:
        return bool(self.accepted)


def record_findings(findings: list[dict[str, Any]], round: int) -> FindingResult:
    """Validate and record reviewer findings.

    A claimed BREAK is *re-executed here* before being accepted. The reviewer
    reports what it observed, but this harness does not take its word for it:
    if re-running the artefact on the stated inputs does not reproduce the
    stated disagreement, the finding is rejected. Otherwise a reviewer that
    mis-executed, or hallucinated an output, would plant a permanent test
    asserting the wrong thing -- and a false test in a legal regression suite
    is worse than a missing one.
    """
    res = FindingResult()
    for f in findings:
        verdict = (f.get("verdict") or "").upper()
        if verdict == "AMBIGUITY":
            res.ambiguities.append(f)
            continue
        if verdict == "NO_BREAK_FOUND":
            res.no_break.append(f)
            continue
        if verdict != "BREAK":
            res.rejected.append((f, f"unknown verdict {verdict!r}"))
            continue

        component = f.get("component", "catala")
        target = f.get("target") or {}
        inputs = f.get("inputs") or {}
        expected = f.get("expected")

        observed = f.get("observed")
        if component == "catala":
            path, scope = target.get("path"), target.get("scope")
            if not path or not scope:
                res.rejected.append((f, "catala finding needs target.path and target.scope"))
                continue
            try:
                actual = run_scope(path, scope, inputs)
            except CatalaError as e:
                # An error IS a legitimate observation (a Conflict or NoValue
                # on inputs the document answers plainly is a real break).
                actual = {"__error__": type(e).__name__, "diagnostic": e.diagnostic[:400]}
            observed = actual
            if _matches(expected, actual):
                res.rejected.append(
                    (f, f"re-execution agrees with expected {expected!r}; not a break")
                )
                continue

        try:
            ce = record_counterexample(
                component=component,
                fact_pattern=f.get("fact_pattern", ""),
                citations=f.get("citations") or [],
                source_reasoning=f.get("source_reasoning", ""),
                expected=expected,
                observed=observed,
                target=target,
                inputs=inputs,
                round=round,
                notes="; ".join(f.get("attacks_tried") or []),
            )
        except (DegenerateCounterexample, ValueError) as e:
            res.rejected.append((f, str(e)))
            continue
        res.accepted.append(ce)
    return res


def _matches(expected: Any, actual: Any) -> bool:
    """True if the artefact's output agrees with the expected legal answer.

    `expected` may be a bare value (compared against the sole output, or
    against any single output field) or a dict of output fields.
    """
    if isinstance(expected, dict) and isinstance(actual, dict):
        common = set(expected) & set(actual)
        if common:
            return all(values_agree(expected[k], actual[k]) for k in common)
        return False
    if isinstance(actual, dict):
        if len(actual) == 1:
            return values_agree(expected, next(iter(actual.values())))
        return any(values_agree(expected, v) for v in actual.values())
    return values_agree(expected, actual)


# --- permanent regression suite -------------------------------------------


@dataclass
class RegressionResult:
    id: str
    component: str
    passed: bool
    expected: Any = None
    actual: Any = None
    detail: str = ""


def run_regression(component: str | None = None) -> list[RegressionResult]:
    """Re-run every recorded counterexample. These run forever."""
    out: list[RegressionResult] = []
    for ce in load_all():
        if component and ce.component != component:
            continue
        if ce.component != "catala":
            out.append(
                RegressionResult(
                    ce.id, ce.component, passed=False,
                    detail="non-catala counterexamples are run by their own component harness",
                )
            )
            continue
        path, scope = ce.target.get("path"), ce.target.get("scope")
        if not path or not scope:
            out.append(RegressionResult(ce.id, ce.component, False, detail="missing target"))
            continue
        try:
            actual = run_scope(path, scope, ce.inputs)
        except CatalaError as e:
            out.append(
                RegressionResult(
                    ce.id, ce.component, False, ce.expected,
                    {"__error__": type(e).__name__},
                    detail=e.diagnostic[:300],
                )
            )
            continue
        ok = _matches(ce.expected, actual)
        out.append(RegressionResult(ce.id, ce.component, ok, ce.expected, actual))
    return out


def sync_statuses() -> dict[str, int]:
    """Mark counterexamples fixed/open according to the current behaviour."""
    counts = {"fixed": 0, "open": 0}
    by_id = {ce.id: ce for ce in load_all()}
    for r in run_regression("catala"):
        ce = by_id.get(r.id)
        if ce is None:
            continue
        new = "fixed" if r.passed else "open"
        if ce.status != new:
            ce.status = new
            save(ce)
        counts[new] += 1
    return counts


# --- exception-branch coverage --------------------------------------------


@dataclass
class BranchCoverage:
    module: str
    scope: str
    variable: str
    branches: list[str]
    covered: list[str]

    @property
    def uncovered(self) -> list[str]:
        return [b for b in self.branches if b not in self.covered]

    @property
    def complete(self) -> bool:
        return not self.uncovered


def exception_branches(module_path: str | Path, scope: str, variable: str) -> list[str]:
    """Every node in the exception hierarchy, as `label[condition]` keys.

    The acceptance criterion is "every scope has test cases covering its
    exception branches", so the branch list must come from the compiler's own
    view of the hierarchy rather than from anyone's reading of the source.
    """
    trees = exception_tree(module_path, scope, variable)

    def walk(n, prefix: str) -> list[str]:
        key = f"{prefix}{n.label}"
        rows = [f"{key}[{'; '.join(n.conditions)}]"]
        for c in n.exceptions:
            rows += walk(c, key + "/")
        return rows

    out: list[str] = []
    for t in trees:
        out += walk(t, "")
    return out


def hierarchy_signature(module_path: str | Path, scope: str, variable: str) -> str:
    return tree_signature(exception_tree(module_path, scope, variable))
