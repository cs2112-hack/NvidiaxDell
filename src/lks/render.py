"""Render a document in the house convention to PDF, with its provenance.

`lks.pdf` is a typesetter and knows nothing about law. This module knows what
a legal document in this repo looks like -- front matter, sections, clauses
with sub-paragraphs -- and what a reader needs to be told about how a
*generated* one came to exist.

It takes a parsed `lks.model.Document`, not markdown, so there is exactly one
parser of the house convention in the system (`lks.segment`). A renderer with
its own reading of `**C-4.2**` would be a second parser, and the day the two
disagreed the PDF would number a clause differently from the citation that
points at it.

## The provenance appendix

A generated document is only as trustworthy as the evidence behind it, and
that evidence is invisible in the prose. So the PDF carries it: every gate and
what it measured, the corpus clauses the draft was built from and how similar
each was, every finding the screen confirmed and how it was resolved, and every
concern a single reviewer raised that did not reach consensus. The last of
those is kept deliberately. It mirrors how this corpus treats its own defects:
31 confirmed breaks were fixed, and 5 ambiguities no encoding could settle were
recorded and left open for a lawyer (`docs/DOCUMENT-DEFECTS.md`). A generated
document gets the same two lists.

## A failed document is still renderable, and says so on every page

The pipeline emits no PDF when a gate fails. `--emit-failed-pdf` overrides
that, for someone who wants to read what the model produced; the result is
stamped `FAILED GATE — NOT FOR EXECUTION` in the running header of every page,
not only the first, because pages get separated from each other.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import pdf
from .model import Clause, Document

FAILED_STAMP = "FAILED GATE — NOT FOR EXECUTION"


@dataclass
class Provenance:
    prompt: str = ""
    model: str = ""
    seed: int | None = None
    started: str = ""
    workspace: str = ""
    catala_iterations: int = 0
    screen_iterations: int = 0
    gates: list[dict[str, Any]] = field(default_factory=list)
    context: list[dict[str, Any]] = field(default_factory=list)
    """Retrieved precedent: {ref, score, doc_title, kind}."""
    confirmed: list[dict[str, Any]] = field(default_factory=list)
    """Findings that blocked an iteration and were resolved by redrafting."""
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    """Judgement-only concerns raised by one reviewer, not agreed by another."""
    agents: list[dict[str, Any]] = field(default_factory=list)
    """Per screen agent: name, enforcement, attacks tried, findings raised."""
    module_sha256: str = ""
    document_sha256: str = ""
    failed: bool = False
    failure_reason: str = ""
    roundtrip_skipped: bool = False


@dataclass
class RenderResult:
    path: Path
    pages: int
    sha256: str
    unmapped: dict[str, int]


_SUBPARA_RE = re.compile(r"^\s{2,}(\(?[a-z0-9ivx]{1,4}[.)]\s.*)$")


def _blocks(body: str) -> tuple[str, list[str]]:
    """Split a clause body into its lead sentence block and indented sub-paragraphs.

    Corpus files are hard-wrapped near 78 columns, so an unindented run of
    lines is one paragraph and must be reflowed. An indented `(a) ...` line is
    structure: a lawyer cites it by letter, so it keeps its own line. A
    continuation of a sub-paragraph is indented further than column 0 but has
    no marker, and joins the sub-paragraph above it.
    """
    lead: list[str] = []
    subs: list[str] = []
    for raw in body.splitlines():
        if not raw.strip():
            continue
        m = _SUBPARA_RE.match(raw)
        if m:
            subs.append(m.group(1).strip())
        elif raw.startswith((" ", "\t")) and subs:
            subs[-1] += " " + raw.strip()
        elif subs:
            # Unindented text after a sub-paragraph list is the clause's
            # closing words ("... in each case within 30 days"). Keep it as its
            # own block after the list rather than gluing it onto (c).
            subs.append("⁣" + raw.strip())
        else:
            lead.append(raw.strip())
    return " ".join(lead), subs


def _clause(L: pdf.Layout, c: Clause) -> None:
    lead, subs = _blocks(c.body)
    L.clause(c.clause_id, lead)
    hang = L.style.body_size * 3.4
    for s in subs:
        if s.startswith("⁣"):
            L.para(s[1:], indent=hang, space_before=2)
            continue
        m = re.match(r"^(\(?[a-z0-9ivx]{1,4}[.)])\s+(.*)$", s)
        marker, text = (m.group(1), m.group(2)) if m else ("", s)
        L.need(L.style.body_leading)
        sub_hang = hang + 22.0
        lines = L.wrap(text, L.style.body_font, L.style.body_size, L.measure - sub_hang)
        if not lines:
            continue
        L.y -= L.style.body_leading
        L.canvas.text(L.left + hang, L.y, marker, L.style.body_font, L.style.body_size)
        L.canvas.text(L.left + sub_hang, L.y, lines[0], L.style.body_font, L.style.body_size)
        for ln in lines[1:]:
            L.need(L.style.body_leading)
            L.y -= L.style.body_leading
            L.canvas.text(L.left + sub_hang, L.y, ln, L.style.body_font, L.style.body_size)


def _title_block(L: pdf.Layout, doc: Document, prov: Provenance | None) -> None:
    L.space(28)
    L.para(doc.doc_id, font=pdf.HELV_BOLD, size=8.5, color=(0.4, 0.4, 0.4), justify=False)
    L.space(6)
    L.para(doc.title, font=pdf.TIMES_BOLD, size=19, leading=24, justify=False)
    L.space(14)
    L.kv("Version", doc.version)
    L.kv("Effective date", doc.effective_date)
    L.kv("Jurisdiction", doc.jurisdiction)
    L.kv("Owner", doc.owner)
    if prov is not None:
        L.kv("Status", "FAILED A GATE" if prov.failed else "Passed every gate")
    L.rule(space=12)


def _provenance(L: pdf.Layout, prov: Provenance, doc: Document) -> None:
    L.page_break()
    L.heading("Provenance", 1)
    L.para(
        "This document was generated from a natural-language request. It is "
        "issued only because the executable encoding of its rules passed every "
        "gate below and three independent reviewers did not reach consensus on "
        "any defect. This appendix records that evidence so it can be checked "
        "rather than taken on trust.",
        size=9.5, leading=13, space_after=6,
    )

    L.heading("The request", 2)
    L.para(prov.prompt or "(not recorded)", font=pdf.TIMES_ITALIC, size=9.5, leading=13)

    L.heading("How it was produced", 2)
    L.kv("Model", prov.model or "(not recorded)")
    if prov.seed is not None:
        L.kv("Seed", str(prov.seed))
    if prov.started:
        L.kv("Generated", prov.started)
    L.kv("Catala iterations", str(prov.catala_iterations))
    L.kv("Screen iterations", str(prov.screen_iterations))
    if prov.document_sha256:
        L.kv("Document SHA-256", prov.document_sha256[:32] + "…")
    if prov.module_sha256:
        L.kv("Encoding SHA-256", prov.module_sha256[:32] + "…")
    if prov.failed:
        L.space(4)
        L.para("Failure: " + (prov.failure_reason or "a gate did not pass"),
               font=pdf.TIMES_BOLD, color=(0.64, 0.17, 0.14), justify=False)

    L.heading("Gates", 2)
    rows = [["Gate", "Result", "What was measured"]]
    for g in prov.gates:
        state = "skipped" if g.get("skipped") else ("pass" if g.get("ok") else "FAIL")
        rows.append([f"{g.get('id', '')} {g.get('name', '')}", state, g.get("detail", "")])
    if prov.roundtrip_skipped and not any(g.get("id") == "G5" for g in prov.gates):
        rows.append(["G5 roundtrip convergence", "skipped",
                     "not run: whether the prose alone reconstructs the logic was not tested"])
    L.table(rows, [2.3, 0.8, 4.4], zebra=True)

    if prov.context:
        L.heading("Precedent retrieved from the corpus", 2)
        L.para(
            "The draft was written with these existing clauses and encodings in "
            "view. Similarity is the retrieval score; it measures closeness of "
            "subject, not approval of content.",
            size=9, leading=12, space_after=2,
        )
        rows = [["Reference", "Kind", "Score", "Document"]]
        for c in prov.context[:24]:
            score = c.get("score")
            rows.append([
                c.get("ref", ""), c.get("kind", ""),
                f"{score:.3f}" if isinstance(score, (int, float)) else "",
                c.get("doc_title", ""),
            ])
        L.table(rows, [1.6, 0.9, 0.6, 3.6], size=8.5)

    # Not "confirmed and resolved": the list also holds judgement findings two
    # reviewers shared, and a later round passing does not resolve anything on
    # its own. Each entry says what replaying it on the final draft showed.
    L.heading("Findings that blocked a round", 2)
    if not prov.confirmed:
        L.para("None. No finding blocked any round of the review.",
               size=9.5, leading=13)
    for f in prov.confirmed:
        kind = ("confirmed by execution" if f.get("status") == "confirmed"
                else "agreed by two reviewers")
        head = (f"{f.get('agent', '').upper()} — {kind} — "
                f"{', '.join(f.get('clause_ids') or [])}"
                + (f" — round {f['round']}" if f.get("round") else ""))
        L.para(head, font=pdf.HELV_BOLD, size=8.5, space_before=6, justify=False)
        L.para(f.get("summary", ""), size=9.5, leading=13)
        how = f.get("verified_by")
        if how:
            L.para("Verified by: " + how, font=pdf.TIMES_ITALIC, size=9, leading=12)
        if f.get("outcome"):
            L.para("On the final draft: " + f["outcome"],
                   font=pdf.TIMES_ITALIC, size=9, leading=12)

    L.heading("Open questions for a lawyer", 2)
    if not prov.open_questions:
        L.para("None raised.", size=9.5, leading=13)
    else:
        L.para(
            "Each was raised by one reviewer and not independently raised by "
            "another, and none could be tested by execution. They did not block "
            "issue. They are the places a lawyer should read first.",
            size=9, leading=12, space_after=2,
        )
    for q in prov.open_questions:
        L.bullet(
            f"[{', '.join(q.get('clause_ids') or [])}] {q.get('summary', '')} "
            f"— raised by {q.get('agent', 'a reviewer')}"
        )

    if prov.agents:
        L.heading("Reviewers", 2)
        rows = [["Reviewer", "Isolation", "Raised", "Confirmed", "Attacks tried"]]
        for a in prov.agents:
            rows.append([
                a.get("name", ""), a.get("enforcement", ""),
                str(a.get("raised", 0)), str(a.get("confirmed", 0)),
                "; ".join(a.get("attacks_tried") or [])[:220],
            ])
        L.table(rows, [1.2, 1.0, 0.6, 0.8, 3.6], size=8)


def render_document(
    doc: Document,
    out: str | Path,
    *,
    provenance: Provenance | None = None,
    page: tuple[float, float] = pdf.A4,
) -> RenderResult:
    """Typeset `doc`, append its provenance if given, and write the PDF.

    Returns the SHA-256 of the bytes written, which the caller records in
    `report.md`. That hash is stable across runs for the same inputs, so a
    report and a PDF can be checked against each other later.
    """
    failed = bool(provenance and provenance.failed)
    header = f"{doc.doc_id}  ·  {doc.title}"
    if failed:
        header = f"{FAILED_STAMP}   ·   {doc.doc_id}"
    style = pdf.Style(
        page=page,
        header=header,
        footer=f"{doc.doc_id} v{doc.version} · effective {doc.effective_date}",
    )
    pdf.reset_unmapped()
    L = pdf.Layout(style)
    _title_block(L, doc, provenance)

    current = None
    for c in doc.clauses:
        if c.section_id != current:
            current = c.section_id
            L.heading(f"{c.section_id}  {c.section_title}", 2)
        _clause(L, c)

    if provenance is not None:
        _provenance(L, provenance, doc)

    L.paginate()
    data = L.to_bytes(
        title=doc.title,
        author=doc.owner,
        subject=f"{doc.doc_id} version {doc.version}",
        creation_date=pdf.pdf_date(doc.effective_date),
    )
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return RenderResult(
        path=path,
        pages=len(L.pages),
        sha256=hashlib.sha256(data).hexdigest(),
        unmapped=pdf.unmapped(),
    )
