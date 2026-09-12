"""Segment a corpus document into addressable clauses.

The corpus convention (enforced by `lks.segment.check_conventions`) is:
  * YAML front matter delimited by `---`
  * `## <SECTION-ID> <Section title>`   e.g. `## C-4 Overtime -- general`
  * `**<CLAUSE-ID>** <clause text>`     e.g. `**C-4.2** By way of exception...`
  * Sub-paragraphs are indented lines belonging to the preceding clause.

A clause runs until the next clause marker or section heading. Indented
sub-paragraph lines `(a) ...` are *part of* their parent clause: splitting them
out would detach "Tier 1 city: GBP 75" from the sentence that gives it legal
force, and a citation to the fragment alone would be misleading.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .model import Clause, Document

FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
SECTION_RE = re.compile(r"^##\s+([A-Z]{1,4}-\d+)\s+(.*?)\s*$")
CLAUSE_RE = re.compile(r"^\*\*([A-Z]{1,4}-\d+(?:\.\d+)*)\*\*\s*(.*)$")
REQUIRED_META = ("doc_id", "title", "version", "effective_date", "jurisdiction", "owner")


class ConventionError(ValueError):
    pass


def parse_document(path: str | Path) -> Document:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")

    fm = FRONT_MATTER_RE.match(raw)
    if not fm:
        raise ConventionError(f"{path}: missing YAML front matter")
    meta = yaml.safe_load(fm.group(1)) or {}
    missing = [k for k in REQUIRED_META if k not in meta]
    if missing:
        raise ConventionError(f"{path}: front matter missing {missing}")

    doc = Document(
        doc_id=str(meta["doc_id"]),
        title=str(meta["title"]),
        version=str(meta["version"]),
        effective_date=str(meta["effective_date"]),
        jurisdiction=str(meta["jurisdiction"]),
        owner=str(meta["owner"]),
        source_path=str(path),
        raw=raw,
    )

    lines = raw.splitlines()
    section_id = ""
    section_title = ""
    # (clause_id, start_line_idx, [text lines])
    pending: tuple[str, int, list[str], str, str] | None = None

    def flush(end_idx: int) -> None:
        nonlocal pending
        if pending is None:
            return
        cid, start_idx, buf, sid, stitle = pending
        while buf and not buf[-1].strip():
            buf.pop()
        body = "\n".join(buf).strip()
        doc.clauses.append(
            Clause(
                doc_id=doc.doc_id,
                clause_id=cid,
                section_id=sid,
                section_title=stitle,
                text=f"**{cid}** {body}",
                body=body,
                line_start=start_idx + 1,
                line_end=min(end_idx, start_idx + len(buf)) + 1,
                source_path=str(path),
            )
        )
        pending = None

    for i, line in enumerate(lines):
        sec = SECTION_RE.match(line)
        if sec:
            flush(i - 1)
            section_id, section_title = sec.group(1), sec.group(2)
            continue
        cl = CLAUSE_RE.match(line)
        if cl:
            flush(i - 1)
            cid, first = cl.group(1), cl.group(2)
            if not section_id:
                raise ConventionError(f"{path}:{i+1}: clause {cid} outside any section")
            if not cid.startswith(section_id + "."):
                raise ConventionError(
                    f"{path}:{i+1}: clause {cid} does not belong to section {section_id}"
                )
            pending = (cid, i, [first], section_id, section_title)
            continue
        if pending is not None:
            pending[2].append(line)

    flush(len(lines) - 1)

    if not doc.clauses:
        raise ConventionError(f"{path}: no clauses found")
    seen: set[str] = set()
    for c in doc.clauses:
        if c.clause_id in seen:
            raise ConventionError(f"{path}: duplicate clause id {c.clause_id}")
        seen.add(c.clause_id)
    return doc


def load_corpus(corpus_dir: str | Path = "corpus") -> list[Document]:
    docs = [parse_document(p) for p in sorted(Path(corpus_dir).glob("*.md"))]
    ids = [d.doc_id for d in docs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ConventionError(f"duplicate doc_id across corpus: {sorted(dupes)}")
    return docs


def check_dangling_refs(docs: list[Document]) -> list[str]:
    """Every clause reference in the corpus must resolve. A dangling reference
    means either a typo in the document or a clause we failed to segment --
    both are things we must know about before encoding anything."""
    from .model import referenced_clauses

    known: set[str] = set()
    for d in docs:
        for c in d.clauses:
            known.add(c.clause_id)
            known.add(c.section_id)
    # sub-paragraph refs resolve to their parent clause
    problems: list[str] = []
    for d in docs:
        for c in d.clauses:
            for ref in referenced_clauses(c.body, exclude=c.clause_id):
                base = re.sub(r"\([a-z]\)$", "", ref)
                if base not in known:
                    problems.append(f"{c.ref}: dangling reference to {ref}")
    return problems
