"""Core data model: documents, clauses, and stable identity.

Clause identity is the spine of the whole system. A clause ID must be stable
across edits to *other* clauses, must be derivable from the source document
alone, and must be quotable in a citation. We therefore use the legal
numbering the document already carries (e.g. "C-4.2") rather than positional
indices, and we carry a content hash separately so that a *changed* clause is
detectable without its identity shifting.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def content_hash(text: str) -> str:
    """Stable hash of clause text, insensitive to whitespace reflow only.

    Legal text is whitespace-reflowed by editors constantly; a hash that
    changes on rewrap would make every document look modified. But we must
    NOT normalise anything semantic: punctuation, case, and digits are all
    load-bearing in legal text.
    """
    normalised = " ".join(text.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Clause:
    """One addressable unit of legal text."""

    doc_id: str
    clause_id: str          # e.g. "C-4.2" -- from the document's own numbering
    section_id: str         # e.g. "C-4"   -- enclosing section
    section_title: str      # e.g. "Overtime -- general entitlement"
    text: str               # verbatim clause text, including the marker
    body: str               # clause text without the "**C-4.2**" marker
    line_start: int         # 1-indexed line in the source file
    line_end: int
    source_path: str

    @property
    def hash(self) -> str:
        return content_hash(self.body)

    @property
    def ref(self) -> str:
        """Canonical citation reference, e.g. 'EMP-ANNEX-C C-4.2'."""
        return f"{self.doc_id} {self.clause_id}"

    def cite(self) -> str:
        return f"{self.ref} ({Path(self.source_path).name}:{self.line_start})"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["hash"] = self.hash
        d["ref"] = self.ref
        return d


@dataclass
class Document:
    doc_id: str
    title: str
    version: str
    effective_date: str
    jurisdiction: str
    owner: str
    source_path: str
    clauses: list[Clause] = field(default_factory=list)
    raw: str = ""

    @property
    def hash(self) -> str:
        return content_hash(self.raw)

    def clause(self, clause_id: str) -> Clause:
        for c in self.clauses:
            if c.clause_id == clause_id:
                return c
        raise KeyError(f"{self.doc_id} has no clause {clause_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "version": self.version,
            "effective_date": self.effective_date,
            "jurisdiction": self.jurisdiction,
            "owner": self.owner,
            "source_path": self.source_path,
            "hash": self.hash,
            "clauses": [c.to_dict() for c in self.clauses],
        }


# --- cross-reference extraction -------------------------------------------
# Legal text refers to itself constantly ("by way of exception to C-4.1").
# These references are what let us verify that an encoded exception hierarchy
# matches the hierarchy the document actually states.

CLAUSE_REF_RE = re.compile(r"\b([A-Z]{1,4}-\d+(?:\.\d+)*(?:\([a-z]\))?)(?![\w-])")

EXCEPTION_CUES = (
    "by way of exception to",
    "notwithstanding",
    "save that",
    "save in respect of",
    "except that",
    "in substitution for",
    "does not apply to",
    "subject to",
)


def referenced_clauses(text: str, exclude: str = "") -> list[str]:
    """Clause IDs mentioned in `text`, excluding the clause's own ID."""
    out: list[str] = []
    for m in CLAUSE_REF_RE.finditer(text):
        ref = m.group(1)
        if ref == exclude or ref in out:
            continue
        out.append(ref)
    return out


def exception_cues(text: str) -> list[str]:
    low = text.lower()
    return [cue for cue in EXCEPTION_CUES if cue in low]
