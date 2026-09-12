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

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import plain
from .catala_runner import (
    AssertionFailed,
    CatalaError,
    NoApplicableRule,
    ScopeConflict,
    exception_tree,
    reads_tree,
    run_scope,
    tree_signature,
    values_agree,
)
from .counterexample import (
    STORE,
    Counterexample,
    DegenerateCounterexample,
    load_all,
    record_counterexample,
    save,
)
from .interface import check_facts, check_results, scope_io
from .literate import CITATION_RE, NOCLAUSE_RE, parse_literate
from .segment import load_corpus

# `#[test]`, `#[doc = ...]` etc. are semantic attributes, not commentary.
# A bare `#` line, or `##` doc text, is implementer voice and gets stripped.
COMMENT_LINE_RE = re.compile(r"^\s*#(?!\[)")
FENCE_OPEN_RE = re.compile(r"^```catala(?:-metadata|-test-cli|-test)?\s*$")
FENCE_CLOSE_RE = re.compile(r"^```\s*$")
DIRECTIVE_RE = re.compile(r"^>\s*(Module|Using|Include)\b")
CLAUSE_ID_RE = re.compile(r"\b[A-Z]{1,4}-\d+(?:\.\d+)*\b")
HEADING_MARKS_RE = re.compile(r"^#+")
DEFINITION_RE = re.compile(r'^\s*["“]([^"”]+)["”]\s+means\b')


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
      * quotations: a `| DOC CLAUSE (file:line)` citation line and the gutter
        lines that run on from it (the law, and the module's claim about which
        clause it encodes -- a mis-attribution must stay reviewable). What a
        quotation says is held to the corpus by `literate.check_fidelity`.
      * `#` headings, reduced to their level and the clause ids they name. The
        structure is needed to read the file; the words are the implementer's,
        and "## S-6.1 The gateway is a fact about the absence, not about the
        records" states a reading as plainly as a note does.
      * `> Module` / `> Using` / `> Include` directives (semantics)
      * blank lines
    Gutter text that is not a quotation is dropped. The gutter is only a way
    of keeping text away from Catala's parser, and implementers use it for
    their own voice too: a `| NOTE:` setting out "Reading (A), adopted here"
    passed into the packets while only quotations were meant to. Likewise a
    `| NO-CLAUSE: <reason>` block is reduced to its bare marker: the reason is
    the implementer's justification for a choice the law does not make, which
    is the reading a reviewer must form independently. That the block encodes
    no clause stays visible; why the implementer thinks so does not.
    Everything else in free text is dropped. Inside code blocks, `#` comment
    lines are dropped but semantic attributes (`#[test]`, `#[doc = ...]`) are
    kept.
    """
    out: list[str] = []
    removed = 0
    in_code = False
    in_quote = False
    for line in src.splitlines():
        if not line.startswith("|"):
            in_quote = False        # a quotation runs only as far as the gutter does
        if not in_code and NOCLAUSE_RE.match(line):
            out.append("| NO-CLAUSE")
            removed += 1
            in_quote = False
            continue
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
        if line.startswith("|"):
            if CITATION_RE.match(line):
                in_quote = True
            if in_quote:
                out.append(line)
            else:
                removed += 1
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            out.append(" ".join([HEADING_MARKS_RE.match(stripped).group(0),
                                 *CLAUSE_ID_RE.findall(stripped)]))
            continue
        if not stripped or DIRECTIVE_RE.match(line):
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


@reads_tree
def build_catala_packet(
    module_path: str | Path, corpus_dir: str | Path = "corpus"
) -> ReviewPacket:
    """Assemble a blind review packet for one Catala module.

    The source section contains the *full* text of every clause the module
    quotes, taken from the corpus rather than from the module -- so that a
    drifted or truncated quotation inside the artefact is visible as a
    disagreement with the authoritative text. After them come the provisions
    those clauses cross-refer to or whose defined terms they use
    (`related_clauses`), so what the implementer chose to quote does not bound
    what the reviewer can check.
    """
    module_path = Path(module_path)
    lf = parse_literate(module_path)
    docs = load_corpus(corpus_dir)
    clauses = {c.ref: (doc, c) for doc in docs for c in doc.clauses}

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
        blocks.append(clause_block(*entry))
    related = related_clauses(seen, docs)
    if related:
        blocks.append(RELATED_NOTE)
        blocks += [clause_block(doc, c) for doc, c in related]

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


def clause_block(doc: Any, c: Any) -> str:
    return (f"### {c.ref} — {doc.title} (v{doc.version}, effective {doc.effective_date})\n"
            f"Section {c.section_id} {c.section_title}\n\n{c.body}\n")


RELATED_NOTE = ("### Not quoted by the artefact\n\n"
                "The clauses above cross-refer to the provisions below, or use terms "
                "they define.\n")


def related_clauses(refs: list[str] | set[str], docs: list[Any]) -> list[tuple[Any, Any]]:
    """The provisions needed to apply the clauses `refs` that are not among
    them, as (document, clause) in document order.

    A packet of only the clauses a module quotes let the implementer's choice
    of what to quote bound what the reviewer could check. The overtime packet
    had no C-2, so nothing in it said what a Night Hour or a Grade is: the
    protocol's defined-term attack had nothing to attack with, and a provision
    the encoding leaves out altogether could not be seen to be missing.

    One step out, within each document: the clauses and whole sections a
    quoted clause names ("By way of exception to C-4"), then the definitions
    of terms used by the quoted clauses or those.
    """
    wanted = set(refs)
    quoted = [(doc, c) for doc in docs for c in doc.clauses if c.ref in wanted]
    extra: set[str] = set()
    for doc, c in quoted:
        named = set(CLAUSE_ID_RE.findall(c.body))
        extra |= {o.ref for o in doc.clauses if o.clause_id in named or o.section_id in named}
    texts = [c.body for _, c in quoted] + [
        c.body for doc in docs for c in doc.clauses if c.ref in extra]
    quoted_docs = {doc.doc_id for doc, _ in quoted}
    for doc in docs:
        if doc.doc_id not in quoted_docs:
            continue
        for o in doc.clauses:
            m = DEFINITION_RE.match(o.body)
            if not m:
                continue
            term = m.group(1)
            # "Night Hours" is used as "each Night Hour worked"
            forms = (term, term[:-1]) if term.endswith("s") else (term,)
            if any(f in t for f in forms for t in texts):
                extra.add(o.ref)
    extra -= wanted
    return [(doc, c) for doc in docs for c in doc.clauses if c.ref in extra]


USING_RE = re.compile(r"^>\s*Using\s+([A-Z]\w*)", re.M)
MODULE_DECL_RE = re.compile(r"^>\s*Module\s+([A-Z]\w*)", re.M)


@reads_tree
def rule_fingerprint(
    module_path: str | Path,
    catala_dir: str | Path = "catala/modules",
    corpus_dir: str | Path = "corpus",
) -> str:
    """What a sign-off is a claim about: the review packet, and the code of
    every module the rule uses.

    A sign-off used to outlive the rule it was earned on. The packet is the
    law the reviewer read and the code it attacked, so a changed clause or a
    changed line of code changes this, and a sign-off recorded against another
    value no longer counts. Implementer commentary is not part of it -- the
    reviewer never saw it -- so rewording an explanation does not undo a
    sign-off. A `> Using` dependency is not in the packet but does change what
    the rule computes, so its stripped source is included too.
    """
    by_name: dict[str, Path] = {}
    for p in sorted(Path(catala_dir).glob("*.catala_en")):
        m = MODULE_DECL_RE.search(p.read_text(encoding="utf-8"))
        by_name[m.group(1) if m else p.stem] = p
    h = hashlib.sha256(build_catala_packet(module_path, corpus_dir).render().encode("utf-8"))
    seen: set[str] = set()
    todo = USING_RE.findall(Path(module_path).read_text(encoding="utf-8"))
    while todo:
        name = todo.pop(0)
        if name in seen:
            continue
        seen.add(name)
        dep = by_name.get(name)
        if dep is None:
            h.update(f"\0missing {name}".encode("utf-8"))
            continue
        src = dep.read_text(encoding="utf-8")
        h.update(f"\0{name}\0".encode("utf-8"))
        h.update(strip_implementer_commentary(src)[0].encode("utf-8"))
        todo += USING_RE.findall(src)
    return h.hexdigest()[:16]


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


REPORTS = Path("reviewer/reports")


@dataclass
class FindingResult:
    accepted: list[Counterexample] = field(default_factory=list)
    rejected: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    """(finding, why), the reason worded for someone who does not read code."""
    ambiguities: list[dict[str, Any]] = field(default_factory=list)
    no_break: list[dict[str, Any]] = field(default_factory=list)
    held: list[dict[str, Any]] = field(default_factory=list)
    """Rejected because the rule already gives the reviewer's expected answer.
    The only rejection that says something about the rule rather than about
    the reviewer, which is why a loop counts it towards a quiet run."""

    @property
    def broke_it(self) -> bool:
        return bool(self.accepted)


@reads_tree
def record_findings(
    findings: list[dict[str, Any]],
    round: int,
    *,
    store: str | Path = STORE,
    reports: str | Path | None = REPORTS,
) -> FindingResult:
    """Validate and record reviewer findings.

    A claimed BREAK is *re-executed here* before being accepted. The reviewer
    reports what it observed, but this harness does not take its word for it:
    if re-running the artefact on the stated inputs does not reproduce the
    stated disagreement, the finding is rejected. Otherwise a reviewer that
    mis-executed, or hallucinated an output, would plant a permanent test
    asserting the wrong thing -- and a false test in a legal regression suite
    is worse than a missing one.

    Before executing, the claim must be executable *as stated*: the inputs the
    rule needs and no others, of the right types, and an expected answer that
    names results the rule produces. Each of these used to get through. A
    misspelt input made Catala refuse to parse the facts, the refusal was
    accepted as an observation, and it disagreed with the expected value -- a
    permanent test that fails forever and says nothing about the law. An
    expected answer naming an output that does not exist compared as "no
    match", which is also a break. And a bare expected value matched *any*
    output that happened to be equal, rejecting real breaks and letting
    regression tests pass by coincidence.

    The same held for the rest of the claim. An expected value of the wrong
    type (`null`, `"maybe"`) never equals the rule's output, so it was always a
    break; a citation was only required to be present, so a clause that does
    not exist established the answer; and a finding naming another component
    was recorded with its own `observed`, never executed. Now the expected
    values must have the types of the results they name, every citation must
    be a clause that exists in the corpus, and only a finding about a compiled
    rule is recorded.

    None of this checks that the reviewer's reading of the clauses is right, or
    that its inputs describe its fact pattern. A finding that gets through is a
    well-formed claim that the rule disagrees with the reviewer, which is why
    every report says a person must confirm the reading before anything changes.

    Only a refusal to decide -- the documents establish no priority, or cover
    no case -- is an observation. A failed assertion is not recorded: it may be
    the rule correctly declining facts that describe nobody, and that is a
    judgement for a person.

    Each accepted break is also written up in `reports` as a page someone
    outside engineering can read.
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
            res.rejected.append((f, f"The reviewer's verdict {verdict!r} is not one the "
                                    f"checker understands."))
            continue

        component = f.get("component", "catala")
        if component != "catala":
            res.rejected.append((f, f"The finding is about {component!r}, and only a claim "
                                    "about a compiled rule can be checked here by running "
                                    "it. Nothing would check this one, so it was not "
                                    "recorded."))
            continue
        target = f.get("target") or {}
        inputs = f.get("inputs") or {}
        path, scope = target.get("path"), target.get("scope")
        if not path or not scope:
            res.rejected.append((f, "The finding does not say which rule it is about."))
            continue
        try:
            io = scope_io(path, scope)
        except CatalaError as e:
            res.rejected.append((f, "The rule's inputs could not be read, so the claim "
                                    f"could not be checked: {plain.first_line(e.diagnostic)}"))
            continue
        types = io.outputs
        problems = check_facts(io, inputs)
        if problems:
            res.rejected.append((f, "The reviewer's example cannot be run as written: "
                                    + "; ".join(problems) + "."))
            continue
        expected, why = normalise_expected(f.get("expected"), io.outputs)
        if why:
            res.rejected.append((f, why))
            continue
        problems = check_results(io, expected)
        if problems:
            res.rejected.append((f, "The reviewer's expected answer cannot be compared with "
                                    "the rule's: " + "; ".join(problems) + "."))
            continue
        citations, why = normalise_citations(f.get("citations"), *clause_refs(path))
        if why:
            res.rejected.append((f, why))
            continue
        try:
            observed = run_scope(path, scope, inputs)
        except (ScopeConflict, NoApplicableRule) as e:
            observed = {"__error__": type(e).__name__, "diagnostic": e.diagnostic[:400]}
        except AssertionFailed:
            res.rejected.append((f, "The rule refused these facts as impossible. That "
                                    "can be correct, so it was not recorded as a defect; "
                                    "a person should decide whether someone in this "
                                    "situation can really exist."))
            continue
        except CatalaError as e:
            res.rejected.append((f, "The rule could not be run on the reviewer's "
                                    f"example: {plain.first_line(e.diagnostic)}"))
            continue
        agreement = compare(expected, observed)
        if agreement == AGREES:
            res.held.append(f)
            res.rejected.append((f, "The rule already gives what the reviewer said the "
                                    "document requires ("
                                    f"{plain.outcome(expected, types)}), so nothing is "
                                    "wrong here."))
            continue
        if agreement == UNCLEAR:
            res.rejected.append((f, "The reviewer's answer and the rule's answer do not "
                                    "name the same results, so they cannot be compared."))
            continue

        try:
            ce = record_counterexample(
                component=component,
                fact_pattern=f.get("fact_pattern", ""),
                citations=citations,
                source_reasoning=f.get("source_reasoning", ""),
                expected=expected,
                observed=observed,
                target=target,
                inputs=inputs,
                round=round,
                notes="; ".join(f.get("attacks_tried") or []),
                headline=str(f.get("headline") or "").strip(),
                why_it_matters=str(f.get("why_it_matters") or "").strip(),
                store=store,
            )
        except (DegenerateCounterexample, ValueError) as e:
            res.rejected.append((f, f"Not recorded: {e}"))
            continue
        if reports is not None:
            Path(reports).mkdir(parents=True, exist_ok=True)
            (Path(reports) / f"{ce.id}.md").write_text(plain.break_report(ce, types),
                                                       encoding="utf-8")
        res.accepted.append(ce)
    return res


SUBPARAGRAPH_RE = re.compile(r"(?:\s*\([a-z0-9]{1,4}\))+$", re.I)


def corpus_refs(corpus_dir: str | Path = "corpus") -> set[str]:
    return {c.ref for doc in load_corpus(corpus_dir) for c in doc.clauses}


def clause_refs(module_path: str | Path, corpus_dir: str | Path = "corpus") -> tuple[set[str], set[str]]:
    """(the clauses this module quotes, every clause in the corpus)."""
    corpus = corpus_refs(corpus_dir)
    shown = {q.ref for q in parse_literate(module_path).quotations} & corpus
    return shown, corpus


def resolve_citation(citation: Any, shown: set[str], corpus: set[str]) -> str | None:
    """A citation as a full clause ref, keeping any sub-paragraph, or None when
    it names no clause.

    A sub-paragraph (`E-3.1(c)`) cites its clause. A bare id (`C-7.2`) resolves
    when it names exactly one clause in `shown`, or failing that exactly one
    clause of the documents `shown` comes from."""
    text = " ".join(str(citation).split())
    m = SUBPARAGRAPH_RE.search(text)
    suffix = m.group(0).replace(" ", "") if m else ""
    ref = text[:m.start()] if m else text
    if ref in corpus:
        return ref + suffix
    docs = {s.split(" ", 1)[0] for s in shown}
    same_id = ([s for s in shown if s.split(" ", 1)[-1] == ref]
               or [s for s in corpus if s.split(" ", 1)[0] in docs
                   and s.split(" ", 1)[-1] == ref])
    return same_id[0] + suffix if len(same_id) == 1 else None


def normalise_citations(citations: Any, shown: set[str], corpus: set[str]) -> tuple[list[str], str]:
    """The citations as full clause refs, or the reason they cannot establish
    anything.

    Every citation must be a clause that exists. One that does not turns the
    whole finding away: reasoning that rests on a clause nobody wrote is not
    reasoning from the document. A citation need not be in the packet, though.
    The reviewer role may read the whole corpus (`agents.REVIEWER.reads`), and
    22 of the first 31 recorded breaks, every one since confirmed and fixed,
    rest partly on a definition or a neighbouring section the packet leaves out.

    How a citation resolves is `resolve_citation`.
    """
    if not isinstance(citations, list) or not citations:
        return [], "The reviewer did not cite the clauses its answer rests on."
    out: list[str] = []
    unknown: list[str] = []
    for c in citations:
        ref = resolve_citation(c, shown, corpus)
        if ref is None:
            unknown.append(str(c))
        elif ref not in out:
            out.append(ref)
    if unknown:
        return [], (f"The reviewer cites {plain.names(unknown)}, which "
                    f"{'is not a clause' if len(unknown) == 1 else 'are not clauses'} of "
                    f"any document in the corpus, so nothing in the documents establishes "
                    f"its answer.")
    return out, ""


def normalise_expected(expected: Any, outputs: dict[str, str]) -> tuple[dict[str, Any] | None, str]:
    """An expected answer as {output: value}, or the reason it cannot be one.

    A bare value is accepted only for a rule with a single result, because for
    any other rule it does not say which result it is about."""
    if isinstance(expected, dict):
        if not expected:
            return None, "The reviewer did not say what the answer should be."
        unknown = sorted(set(expected) - set(outputs) - {"__error__"})
        if unknown:
            return None, (f"The reviewer's expected answer names {plain.names(unknown)}, "
                          f"which the rule does not produce (it produces "
                          f"{plain.names(sorted(outputs))}).")
        return expected, ""
    if expected is None or expected == "" or expected == []:
        return None, "The reviewer did not say what the answer should be."
    if len(outputs) == 1:
        return {next(iter(outputs)): expected}, ""
    return None, (f"The reviewer gave an answer ({expected!r}) without saying which of "
                  f"the rule's {len(outputs)} results it is ({plain.names(sorted(outputs))}).")


AGREES, DISAGREES, UNCLEAR = "agrees", "disagrees", "unclear"


def compare(expected: Any, actual: Any) -> str:
    """Whether the artefact's output agrees with the expected legal answer.

    `unclear` when the two cannot be compared: a bare expected value against a
    rule with several results, or an expected answer naming a result the rule
    did not produce. The old boolean folded that case into one side or the
    other depending on its shape -- "no match", which recorded a break, or
    "matches any equal field", which passed a regression test on a coincidence.
    """
    if isinstance(actual, dict) and not isinstance(expected, dict):
        if len(actual) != 1:
            return UNCLEAR
        expected = {next(iter(actual)): expected}
    if isinstance(expected, dict) and isinstance(actual, dict):
        if not expected:
            return UNCLEAR
        if "__error__" in actual:
            return AGREES if expected.get("__error__") == actual["__error__"] else DISAGREES
        if "__error__" in expected:
            return DISAGREES
        if set(expected) - set(actual):
            return UNCLEAR
        ok = all(values_agree(expected[k], actual[k]) for k in expected)
        return AGREES if ok else DISAGREES
    return AGREES if values_agree(expected, actual) else DISAGREES


def _matches(expected: Any, actual: Any) -> bool:
    """True only if the output demonstrably agrees; see `compare`."""
    return compare(expected, actual) == AGREES


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
        detail = ""
        try:
            actual = run_scope(path, scope, ce.inputs)
        except CatalaError as e:
            # An error is a legitimate expected RESULT, not automatically a
            # failure. A counterexample may assert that impossible facts are
            # refused (AssertionFailed), that the documents establish no
            # priority (ScopeConflict), or that no rule applies (NoValue) --
            # and for a legal system those are the correct answers, so they
            # must be assertable. Hardcoding failure here made an expected
            # refusal impossible to satisfy.
            actual = {"__error__": type(e).__name__}
            detail = e.diagnostic[:300]
        agreement = compare(ce.expected, actual)
        ok = agreement == AGREES
        if agreement == UNCLEAR and isinstance(ce.expected, dict) and isinstance(actual, dict):
            gone = sorted(set(ce.expected) - set(actual))
            detail = (f"this test checks {plain.names(gone)}, which the rule no longer "
                      f"produces, so that part of it is not being tested at all")
        out.append(
            RegressionResult(
                ce.id, ce.component, ok, ce.expected, actual,
                detail="" if ok else detail,
            )
        )
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
