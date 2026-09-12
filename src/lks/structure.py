"""Infer clause structure from a real document, and convert it -- visibly.

`lks.segment` parses the house convention and nothing else, which is correct:
clause identity is the spine of this system and a parser that guesses is a
parser that silently renumbers the law. This module is where the guessing is
allowed to happen, and it is quarantined behind two properties:

1. **It produces a document in the house convention**, written to
   `ingest/converted/<doc-id>.md`. Everything downstream -- segmentation,
   triage, the vector index, citation -- then runs on the house convention
   exactly as before, with no new code path and no "real document" mode.
2. **It produces a report** next to it, `<doc-id>.report.md`, which accounts
   for *every* paragraph of the input and says, per clause, whether its id
   came from the document or was synthesised. The converted file is what a
   human reviews; the report is what makes the review possible.

## Deciding the numbering scheme, once, for the whole document

Real legal documents number themselves in a handful of recognisable ways, and
crucially each document picks one and sticks to it. Classifying paragraph by
paragraph is therefore the wrong shape: in a decimal-outline contract the
paragraph `(a) the Customer's own act or omission` is a sub-paragraph of the
clause above it, while in a lettered policy the identical paragraph is a
clause in its own right. The same text, two structures, decided by the
document and not by the paragraph.

So the markers are counted across the whole document first, a *section layer*
and a *clause layer* are chosen from those counts, and only then is the
document walked. The counts and the choice are both written into the report,
so a wrong guess is visible as a wrong guess rather than as mysterious
output. Schemes recognised:

| layer   | scheme        | looks like                                  |
|---------|---------------|---------------------------------------------|
| clause  | `decimal`     | `4.2`, `4.2.1`, `C-4.2`                     |
| clause  | `lettered`    | `(a)`, `(i)`, `(A)`                         |
| clause  | `numbered`    | `7. The Supplier shall ...`                 |
| clause  | `paragraph`   | no clause numbering at all -- synthesised   |
| section | `decimal-head`| section = the integer part of `4.2`         |
| section | `article`     | `Article 7`, `Clause 7`, `Section 7`        |
| section | `numbered`    | `4. Payment Terms` (heading-shaped)         |
| section | `heading`     | a heading with no number                    |
| recital | `recitals`    | `WHEREAS ...`, `NOW THEREFORE ...`          |

## Synthesised ids are marked as synthesised

An id is only as good as its provenance. Three provenances, and the report
states which applies to every clause:

* `document`   -- the whole number is in the document (`4.2` -> `CL-4.2`).
  A lawyer can cite it.
* `derived`    -- the document's number carried through a lossless mapping
  (`(b)` under clause 3 -> `CL-3.2`; a single numbered item `7.` -> `CL-7.1`).
  Citable as the document's own number, which the report prints alongside.
* `synthesised`-- a position we invented because the document numbers nothing
  (`P-3.2`: the second paragraph under the third heading). **Not citable.**
  It is stable for a given input, and that is all it is.

The `P-` prefix is reserved for synthesised ids precisely so that a
synthesised citation is recognisable on sight.

## What is never invented

A missing `effective_date` is not guessed. An invented effective date would
silently date a rule -- the clause would look authoritative and be wrong
about when it bites, which is worse than a refusal. Where the document does
not state one, the front matter carries an explicit sentinel, the report says
what a human must supply and how, and `lks.ingest` raises a blocking
conflict so the proposal cannot be merged until they do.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .extract import Extracted, Para, extract

CONVERTED = Path("ingest/converted")

NEEDS_HUMAN = "NEEDS-HUMAN-INPUT"
UNKNOWN = "UNKNOWN"

# --- marker patterns -------------------------------------------------------
# A marker must be followed by text that starts like legal prose (a capital, a
# quote, a bracket or a digit-led amount). This is what keeps `1.25 times the
# Base Hourly Rate` from being read as clause 1.25: the word after it is
# lower-case, so it is prose, not a number.
_AFTER = r'(?=[A-Z"“‘\'(\[£$€\d])'

DECIMAL_RE = re.compile(r"^((?:[A-Z]{1,4}[-.])?\d{1,3}(?:\.\d{1,3})+)\.?[ \t]+" + _AFTER)
NUMBERED_RE = re.compile(r"^(\d{1,3})[.)][ \t]+" + _AFTER)
LETTERED_RE = re.compile(r"^\(([A-Za-z]{1,5})\)[ \t]+" + _AFTER)
ARTICLE_RE = re.compile(
    r"^(ARTICLE|Article|CLAUSE|Clause|SECTION|Section|PARAGRAPH|Paragraph|PART|Part"
    r"|SCHEDULE|Schedule|ANNEX|Annex|APPENDIX|Appendix)\s+"
    r"(\d{1,3}|I|V|X|[IVXLCDM]{2,7})\b[.:]?[ \t]*(.*)$"
)
RECITAL_RE = re.compile(
    r"^(WHEREAS|Whereas|NOW,? THEREFORE|Now,? therefore|IN WITNESS WHEREOF|"
    r"RECITALS?|BACKGROUND)\b"
)
# A heading's own number: "C-1 Purpose", "4 Payment Terms", "7.2 Fees". This is
# deliberately separate from the clause markers above -- a heading number needs no
# trailing punctuation and no prose-shaped remainder, so accepting it as a clause
# marker would misread "1.25 times the Base Hourly Rate" as clause 1.25.
HEAD_NUM_RE = re.compile(r"^((?:[A-Z]{1,4}[-.])?\d{1,3}(?:\.\d{1,3})*)[.:)]?[ \t]+(\S.*)$")

BULLET_RE = re.compile(r"^([•‣●▪·⁃*–—-])[ \t]+\S")

ARTICLE_PREFIX = {
    "ARTICLE": "ART", "CLAUSE": "CL", "SECTION": "SEC", "PARAGRAPH": "PARA",
    "PART": "PT", "SCHEDULE": "SCH", "ANNEX": "ANX", "APPENDIX": "APP",
}
ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}

# Page furniture: a running header, a page number, a classification stamp.
FURNITURE_RE = re.compile(
    r"^(?:page\s+\d+(?:\s+of\s+\d+)?|\d{1,3}|[-–—]\s*\d{1,3}\s*[-–—]|"
    r"confidential(?:\s*[-–—/]\s*\w+)?|draft|privileged\s*(?:and|&)?\s*"
    r"confidential|internal use only|\[?signature page follows\]?|_{4,}|"
    r"\.{4,}|\W{1,4})$",
    re.I,
)

DATE_CONTEXT = (
    r"(?:effective(?:\s+(?:date|as\s+of|from|on))?|with\s+effect\s+(?:from|on)|"
    r"commenc(?:es|ing|ement)(?:\s+(?:on|date))?|takes?\s+effect\s+(?:on|from)|"
    r"dated|made\s+on|entered\s+into\s+on|in\s+force\s+(?:from|on))"
)
_MONTHS = ("january february march april may june july august september october "
           "november december").split()
_MONTH_RE = "|".join(_MONTHS) + "|" + "|".join(m[:3] for m in _MONTHS)
DATE_PATTERNS = (
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_RE + r")\.?,?\s+(\d{4})\b",
               re.I),
    re.compile(r"\b(" + _MONTH_RE + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
               re.I),
)
AMBIGUOUS_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")


# --- results ---------------------------------------------------------------


@dataclass
class Inferred:
    """One inferred clause, with the provenance of its id."""

    clause_id: str
    section_id: str
    section_title: str
    lines: list[str]                     # body lines as they will be written
    id_source: str                       # document | derived | synthesised
    document_number: str = ""            # what a lawyer would cite, if anything
    paras: list[int] = field(default_factory=list)
    note: str = ""

    @property
    def synthesised(self) -> bool:
        return self.id_source == "synthesised"

    @property
    def body(self) -> str:
        return "\n".join(self.lines).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "clause_id": self.clause_id, "section_id": self.section_id,
            "section_title": self.section_title, "id_source": self.id_source,
            "document_number": self.document_number, "paras": list(self.paras),
            "synthesised": self.synthesised, "note": self.note,
        }


@dataclass
class Disposition:
    """What happened to one source paragraph. One per input paragraph, always."""

    index: int
    kind: str            # title|heading|clause|continuation|subparagraph|
                         # recital|furniture|unassigned
    clause_id: str = ""
    reason: str = ""
    text: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.kind in ("unassigned", "furniture")


@dataclass
class Scheme:
    name: str
    section_layer: str
    clause_layer: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        return (f"{self.name} (sections: {self.section_layer}, "
                f"clauses: {self.clause_layer})")


@dataclass
class Structured:
    meta: dict[str, str]
    meta_sources: dict[str, str]
    needs_human: dict[str, str]          # field -> why, and how to supply it
    clauses: list[Inferred]
    dispositions: list[Disposition]
    scheme: Scheme
    extracted: Extracted
    warnings: list[str] = field(default_factory=list)

    @property
    def synthesised(self) -> list[Inferred]:
        return [c for c in self.clauses if c.synthesised]

    @property
    def unassigned(self) -> list[Disposition]:
        return [d for d in self.dispositions if d.kind == "unassigned"]

    @property
    def furniture(self) -> list[Disposition]:
        return [d for d in self.dispositions if d.kind == "furniture"]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.dispositions:
            out[d.kind] = out.get(d.kind, 0) + 1
        return out


@dataclass
class Conversion:
    converted_path: Path
    report_path: Path
    structured: Structured

    @property
    def doc_id(self) -> str:
        return self.structured.meta["doc_id"]

    def to_dict(self) -> dict[str, Any]:
        s = self.structured
        return {
            "original_path": s.extracted.source_path,
            "source_format": s.extracted.fmt,
            "extracted_with": s.extracted.tool,
            "converted_path": str(self.converted_path),
            "report_path": str(self.report_path),
            "scheme": s.scheme.describe(),
            "n_source_paragraphs": s.extracted.n_paras,
            "n_clauses": len(s.clauses),
            "n_synthesised_ids": len(s.synthesised),
            "synthesised_ids": [c.clause_id for c in s.synthesised],
            "n_unassigned_paragraphs": len(s.unassigned),
            "n_furniture_paragraphs": len(s.furniture),
            "dispositions": s.counts(),
            "needs_human": dict(s.needs_human),
        }


# --- scheme detection ------------------------------------------------------


def _heading_like(text: str) -> bool:
    """Does this read as a heading rather than as a clause?

    Short, no sentence-ending punctuation, few words. `4. Payment Terms` is a
    heading; `4. The Supplier shall deliver the Services...` is a clause.
    """
    t = text.strip()
    if not t or "\n" in t:
        return False
    if t.endswith((".", ";", ":", ",")) and not t.endswith("..."):
        return False
    return len(t) <= 70 and len(t.split()) <= 9


CAPS_HEADING_RE = re.compile(r"^[^a-z]{2,60}$")


def _caps_heading(p: Para) -> bool:
    """An all-capitals short line is a heading in a plain-text document.

    `.txt` and `.pdf` carry no heading styles at all, so a document that puts
    its section titles in capitals -- which is most of them -- would otherwise
    have no section layer. Restricted to a single short line with at least two
    letters and no sentence-ending punctuation, so that a capitalised defined
    term inside a sentence cannot be mistaken for a heading.
    """
    t = p.text.strip()
    if p.kind == "heading" or "\n" in t or not CAPS_HEADING_RE.match(t):
        return False
    letters = [c for c in t if c.isalpha()]
    return len(letters) >= 2 and not t.endswith((".", ";", ","))


def is_heading(p: Para) -> bool:
    return p.kind == "heading" or _caps_heading(p)


def _marker(p: Para) -> tuple[str, str, str]:
    """Classify one paragraph's leading marker: (kind, token, remainder)."""
    first = p.text.split("\n", 1)[0].strip()
    m = RECITAL_RE.match(first)
    if m:
        return "recital", m.group(1), p.text.strip()
    m = ARTICLE_RE.match(first)
    if m:
        return "article", f"{m.group(1)} {m.group(2)}", m.group(3).strip()
    m = DECIMAL_RE.match(first)
    if m:
        return "decimal", m.group(1), first[m.end():].strip()
    m = NUMBERED_RE.match(first)
    if m:
        return "numbered", m.group(1), first[m.end():].strip()
    m = LETTERED_RE.match(first)
    if m:
        return "lettered", m.group(1), first[m.end():].strip()
    if BULLET_RE.match(first):
        return "bullet", first[0], first[1:].strip()
    return "none", "", first


def detect_scheme(ex: Extracted) -> Scheme:
    """Count markers over the whole document, then choose the layers."""
    counts = {k: 0 for k in ("decimal", "numbered", "lettered", "article",
                             "recital", "bullet", "none")}
    numbered_headinglike = 0
    fmt_headings = 0
    decimal_depths: set[int] = set()
    seq: list[int] = []
    for p in ex.paras:
        heading = is_heading(p)
        if heading:
            fmt_headings += 1
        kind, token, rest = _marker(p)
        counts[kind] += 1
        if kind == "decimal":
            decimal_depths.add(len(token.split(".")))
            head = token.split(".")[0]
            digits = re.sub(r"^[A-Z]{1,4}[-.]", "", head)
            if digits.isdigit():
                seq.append(int(digits))
        if kind == "numbered" and (_heading_like(rest) or heading):
            numbered_headinglike += 1
        if kind == "article":
            numbered_headinglike += 0

    n_body = max(1, sum(1 for p in ex.paras if p.kind != "heading"))
    monotone = (sum(1 for a, b in zip(seq, seq[1:]) if b >= a) / max(1, len(seq) - 1)
                if len(seq) > 1 else 1.0)

    # clause layer
    if counts["decimal"] >= 2 and counts["decimal"] / n_body >= 0.12 and monotone >= 0.7:
        clause_layer = "decimal"
    elif counts["lettered"] >= 3 and counts["lettered"] / n_body >= 0.15 \
            and counts["decimal"] == 0:
        clause_layer = "lettered"
    elif counts["numbered"] - numbered_headinglike >= 2:
        clause_layer = "numbered"
    else:
        clause_layer = "paragraph"

    # section layer
    if counts["article"] >= 2:
        section_layer = "article"
    elif numbered_headinglike >= 2:
        section_layer = "numbered"
    elif clause_layer == "decimal":
        section_layer = "decimal-head"
    elif fmt_headings >= 1:
        section_layer = "heading"
    elif counts["numbered"] >= 2 and clause_layer == "numbered":
        section_layer = "per-clause"
    else:
        section_layer = "none"

    if clause_layer == "decimal":
        name = "decimal-outline"
    elif clause_layer == "lettered":
        name = "lettered-subparagraphs"
    elif clause_layer == "numbered":
        name = "numbered-clauses"
    else:
        name = "headings-only" if (fmt_headings or numbered_headinglike
                                   or counts["article"]) else "unstructured-prose"
    if counts["recital"]:
        name += "+recitals"

    return Scheme(
        name=name, section_layer=section_layer, clause_layer=clause_layer,
        evidence={
            "paragraphs": ex.n_paras, "body_paragraphs": n_body,
            "format_headings": fmt_headings,
            "marker_counts": counts,
            "numbered_heading_like": numbered_headinglike,
            "decimal_depths": sorted(decimal_depths),
            "decimal_first_component_monotone": round(monotone, 2),
            "why": _why(clause_layer, section_layer, counts, numbered_headinglike,
                        fmt_headings, monotone),
        },
    )


def _why(clause_layer: str, section_layer: str, counts: dict[str, int],
         nhl: int, fmt_headings: int, monotone: float) -> str:
    bits = []
    if clause_layer == "decimal":
        bits.append(f"{counts['decimal']} paragraphs open with a decimal number and "
                    f"their first components are {monotone:.0%} non-decreasing, so "
                    f"decimal outline numbering is the document's own scheme")
    elif clause_layer == "lettered":
        bits.append(f"{counts['lettered']} paragraphs open with a lettered marker and "
                    f"none opens with a decimal number, so the letters are the clause "
                    f"level rather than sub-paragraphs of something")
    elif clause_layer == "numbered":
        bits.append(f"{counts['numbered']} paragraphs open with a plain number, "
                    f"{nhl} of them heading-shaped, leaving "
                    f"{counts['numbered'] - nhl} numbered clauses")
    else:
        bits.append("no paragraph opens with a clause number, so clause ids must be "
                    "synthesised from position")
    if section_layer == "article":
        bits.append(f"{counts['article']} Article/Clause/Section headings give the "
                    f"section layer")
    elif section_layer == "numbered":
        bits.append(f"{nhl} heading-shaped numbered lines give the section layer")
    elif section_layer == "decimal-head":
        bits.append("sections are the integer part of the clause numbers")
    elif section_layer == "heading":
        bits.append(f"{fmt_headings} headings carried by the file format give the "
                    f"section layer")
    else:
        bits.append("no section layer was detectable")
    if counts["recital"]:
        bits.append(f"{counts['recital']} recital paragraph(s) (WHEREAS / NOW "
                    f"THEREFORE) are collected into section R-1")
    return "; ".join(bits)


# --- id helpers ------------------------------------------------------------


def _roman_value(tok: str) -> int | None:
    t = tok.lower()
    if not t or any(ch not in ROMAN for ch in t):
        return None
    total, prev = 0, 0
    for ch in reversed(t):
        v = ROMAN[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def _letter_ordinal(tok: str, prev_ordinal: int) -> tuple[int, str]:
    """Ordinal for a lettered token, with the roman/letter ambiguity resolved.

    `(i)` is the ninth letter and also roman one. The sequence decides: if the
    previous sibling was `(h)` it is the letter, if there was no previous
    sibling it is roman one. Ambiguity that the sequence cannot resolve falls
    back to the letter reading, which is the commoner list in English
    drafting, and the report prints the literal token either way.
    """
    if len(tok) == 1 and tok.isalpha():
        letter = ord(tok.lower()) - 96
        rv = _roman_value(tok)
        if rv is not None and rv != letter:
            if prev_ordinal + 1 == letter:
                return letter, "letter"
            if prev_ordinal + 1 == rv:
                return rv, "roman"
        return letter, "letter"
    rv = _roman_value(tok)
    if rv is not None:
        return rv, "roman"
    # multi-letter alphabetic run: aa, bb (rare) -> continue the sequence
    return prev_ordinal + 1, "letter-run"


def _slug_prefix(text: str, fallback: str = "P") -> str:
    letters = re.sub(r"[^A-Za-z]", "", text).upper()
    return (letters[:4] or fallback)


def _section_from_decimal(token: str) -> tuple[str, str]:
    """`C-4.2` -> (`C-4`, `C-4.2`); `4.2.1` -> (`CL-4`, `CL-4.2.1`)."""
    m = re.match(r"^([A-Z]{1,4})[-.](.*)$", token)
    if m:
        prefix, digits = m.group(1), m.group(2)
    else:
        prefix, digits = "CL", token
    parts = digits.split(".")
    return f"{prefix}-{parts[0]}", f"{prefix}-{'.'.join(parts)}"


def _section_id_from_number(num: str) -> str:
    """`C-1` -> `C-1`; `4` -> `CL-4`; `7.2` -> `CL-7` (a section is the first
    component: the house convention requires clause ids to nest under it)."""
    m = re.match(r"^([A-Z]{1,4})[-.](.*)$", num)
    prefix, digits = (m.group(1), m.group(2)) if m else ("CL", num)
    return f"{prefix}-{digits.split('.')[0]}"


def _section_from_article(token: str) -> str:
    word, _, num = token.partition(" ")
    prefix = ARTICLE_PREFIX.get(word.upper(), _slug_prefix(word, "SEC"))
    n = num if num.isdigit() else str(_roman_value(num) or 1)
    return f"{prefix}-{n}"


# --- the walk --------------------------------------------------------------


class _Builder:
    def __init__(self, ex: Extracted, scheme: Scheme) -> None:
        self.ex = ex
        self.scheme = scheme
        self.clauses: list[Inferred] = []
        self.disp: list[Disposition] = []
        self.used: set[str] = set()
        self.section_id = ""
        self.section_title = ""
        self.pending_heading: tuple[str, str] | None = None   # (number, title)
        self.heading_ordinal = 0
        self.para_counter: dict[str, int] = {}
        self.sub_ordinal = 0
        self.warnings: list[str] = []
        self.title_para: int | None = None
        self.repeated = self._repeated_paragraphs()

    # -- bookkeeping
    def _repeated_paragraphs(self) -> set[str]:
        """Text appearing three or more times is a running header or footer."""
        seen: dict[str, int] = {}
        for p in self.ex.paras:
            key = " ".join(p.text.split())
            if len(key) <= 80:
                seen[key] = seen.get(key, 0) + 1
        return {k for k, n in seen.items() if n >= 3}

    def _record(self, p: Para, kind: str, clause_id: str = "", reason: str = "") -> None:
        self.disp.append(Disposition(index=p.index, kind=kind, clause_id=clause_id,
                                     reason=reason, text=p.text))

    def _unique(self, cid: str, id_source: str) -> tuple[str, str, str]:
        if cid not in self.used:
            self.used.add(cid)
            return cid, id_source, ""
        n = 1
        while f"{cid}.{n}" in self.used:
            n += 1
        new = f"{cid}.{n}"
        self.used.add(new)
        return new, "synthesised", (
            f"the document uses the number {cid} more than once; this occurrence was "
            f"disambiguated positionally as {new}. A human must renumber or confirm."
        )

    def _open_section(self, sid: str, title: str) -> None:
        if sid != self.section_id:
            self.section_id, self.section_title = sid, title
        elif title and not self.section_title:
            self.section_title = title

    def _synth_section(self) -> str:
        if self.section_id:
            return self.section_id
        self._open_section("P-0", "(text before the first heading)")
        return self.section_id

    def _add(self, p: Para, cid: str, id_source: str, doc_number: str,
             lines: list[str], kind: str = "clause") -> Inferred:
        cid, id_source, note = self._unique(cid, id_source)
        cl = Inferred(clause_id=cid, section_id=self.section_id,
                      section_title=self.section_title, lines=list(lines),
                      id_source=id_source, document_number=doc_number,
                      paras=[p.index], note=note)
        self.clauses.append(cl)
        self.sub_ordinal = 0
        self._record(p, kind, cid, note)
        return cl

    @property
    def current(self) -> Inferred | None:
        return self.clauses[-1] if self.clauses else None

    def _append(self, p: Para, lines: list[str], kind: str, reason: str = "") -> bool:
        cur = self.current
        if cur is None:
            return False
        cur.lines.extend(lines)
        cur.paras.append(p.index)
        self._record(p, kind, cur.clause_id, reason)
        return True

    # -- the walk itself
    def run(self) -> tuple[list[Inferred], list[Disposition], list[str]]:
        paras = self.ex.paras
        self.title_para = self._pick_title_para()
        for p in paras:
            if p.index == self.title_para:
                self._record(p, "title", reason="taken as the document title")
                continue
            if self._is_furniture(p):
                self._record(p, "furniture", reason=(
                    "matches page furniture (running header/footer, page number or "
                    "classification stamp) and is excluded from the converted "
                    "document; the full text is preserved here"))
                continue
            kind, token, rest = _marker(p)
            if is_heading(p) and kind in ("none", "bullet"):
                self._heading(p, "", p.text.strip())
                continue
            handler = {
                "recital": self._recital, "article": self._article,
                "decimal": self._decimal, "numbered": self._numbered,
                "lettered": self._lettered, "bullet": self._bullet,
                "none": self._plain,
            }[kind]
            handler(p, token, rest)
        return self.clauses, self.disp, self.warnings

    def _pick_title_para(self) -> int | None:
        """The first heading is the document title when it stands alone.

        A single top-level heading above a run of deeper headings is the
        document's title, not a section of it. A document whose sections are
        all top-level headings has no such paragraph, and nothing is consumed.
        """
        headings = [p for p in self.ex.paras if is_heading(p)]
        if not headings:
            return None
        first = headings[0]
        if first.kind != "heading":
            # a plain-text document: its first capitalised line is its title
            # only when it is the very first paragraph of the file.
            return first.index if first.index == 1 else None
        if first.level == 0:
            return None
        same = [h for h in headings if h.level == first.level]
        deeper = [h for h in headings if h.level > first.level]
        if len(same) == 1 and (deeper or len(headings) == 1):
            if _marker(first)[0] in ("none", "bullet"):
                return first.index
        return None

    def _is_furniture(self, p: Para) -> bool:
        flat = " ".join(p.text.split())
        if flat in self.repeated and len(flat.split()) <= 12:
            return True
        return bool(FURNITURE_RE.match(flat)) and len(flat.split()) <= 6

    # -- handlers
    def _heading(self, p: Para, number: str, title: str) -> None:
        self.heading_ordinal += 1
        kind, token, rest = _marker(p)
        if kind == "article":
            sid = _section_from_article(token)
            self._open_section(sid, rest or f"({token})")
            self.pending_heading = (token, rest)
            self._record(p, "heading", sid, f"section {sid} from '{token}'")
            return
        if kind == "numbered":
            sid = f"CL-{token}"
            self._open_section(sid, rest or f"(clause {token})")
            self.pending_heading = (token, rest)
            self._record(p, "heading", sid, f"section {sid} from numbered heading "
                                            f"'{token}.'")
            return
        if kind == "decimal":
            sid, _ = _section_from_decimal(token)
            self.pending_heading = (token, rest)
            if self.scheme.clause_layer != "decimal":
                self._open_section(sid, rest)
            self._record(p, "heading", sid, f"heading numbered {token}")
            return
        hm = HEAD_NUM_RE.match(p.text.strip().split("\n")[0])
        if hm and kind == "none":
            number, htitle = hm.group(1), hm.group(2).strip()
            sid = _section_id_from_number(number)
            self.pending_heading = (number, htitle)
            if self.scheme.clause_layer != "decimal":
                self._open_section(sid, htitle)
            self._record(p, "heading", sid,
                         f"section {sid} from the heading's own number {number!r}")
            return
        # unnumbered heading
        sid = f"P-{self.heading_ordinal}"
        self.pending_heading = ("", title)
        if self.scheme.clause_layer in ("paragraph", "lettered"):
            self._open_section(sid, title)
            self._record(p, "heading", sid, f"synthesised section {sid} from heading "
                                            f"#{self.heading_ordinal}")
        else:
            self._record(p, "heading", "", "heading text kept as the section title of "
                                           "the clauses that follow")

    def _consume_heading_title(self, sid: str, number: str) -> str:
        """Title for `sid`, from the pending heading when it plausibly belongs."""
        if self.pending_heading is None:
            return ""
        hnum, htitle = self.pending_heading
        if hnum and hnum not in (number, sid) and not sid.endswith("-" + hnum):
            # the heading numbers something else; do not attach its title
            return ""
        self.pending_heading = None
        return htitle

    def _recital(self, p: Para, token: str, rest: str) -> None:
        self._open_section("R-1", "Recitals")
        n = self.para_counter.get("R-1", 0) + 1
        self.para_counter["R-1"] = n
        self._add(p, f"R-1.{n}", "synthesised", "",
                  p.text.split("\n"), kind="recital")
        self.clauses[-1].note = (
            self.clauses[-1].note or
            f"recital, which the document does not number; id synthesised from "
            f"position ({n} of the recitals). Not citable as a clause number.")

    def _article(self, p: Para, token: str, rest: str) -> None:
        sid = _section_from_article(token)
        if _heading_like(rest) or not rest:
            self._heading(p, token, rest)
            return
        # `Clause 7 The Supplier shall ...` -- both a section and a clause
        self._open_section(sid, f"({token})")
        n = self.para_counter.get(sid, 0) + 1
        self.para_counter[sid] = n
        cl = self._add(p, f"{sid}.{n}", "derived" if n == 1 else "synthesised",
                       token, [rest] + p.text.split("\n")[1:])
        cl.note = cl.note or (f"the document numbers this '{token}'; the house "
                              f"convention requires a section-relative clause id")

    def _decimal(self, p: Para, token: str, rest: str) -> None:
        if self.scheme.clause_layer != "decimal":
            if _heading_like(rest):
                self._heading(p, token, rest)
            else:
                self._plain(p, token, p.text.split("\n")[0])
            return
        sid, cid = _section_from_decimal(token)
        title = self._consume_heading_title(sid, token)
        if sid != self.section_id:
            self._open_section(sid, title or f"(clause {token.split('.')[0]})")
        elif title and not self.section_title:
            self.section_title = title
        lines = [rest] + p.text.split("\n")[1:]
        self._add(p, cid, "document", token, lines)

    def _numbered(self, p: Para, token: str, rest: str) -> None:
        if _heading_like(rest) or is_heading(p):
            self._heading(p, token, rest)
            return
        sid = f"CL-{token}"
        title = self._consume_heading_title(sid, token)
        self._open_section(sid, title or f"(clause {token})")
        n = self.para_counter.get(sid, 0) + 1
        self.para_counter[sid] = n
        cl = self._add(p, f"{sid}.{n}", "derived" if n == 1 else "synthesised",
                       token if n == 1 else f"{token} (paragraph {n})",
                       [rest] + p.text.split("\n")[1:])
        if n == 1:
            cl.note = cl.note or (
                f"the document numbers this clause '{token}.'; the house convention "
                f"requires a section-relative id, so it is cited as {token}")

    def _lettered(self, p: Para, token: str, rest: str) -> None:
        indented = p.text.startswith(" ")
        ordinal, reading = _letter_ordinal(token, self.sub_ordinal)
        if self.scheme.clause_layer == "lettered" and not indented:
            sid = self._synth_section()
            n = self.para_counter.get(sid, 0)
            cid = f"{sid}.{ordinal}"
            self.para_counter[sid] = max(n, ordinal)
            doc_num = f"{self.section_number(sid)}({token})".lstrip()
            cl = self._add(p, cid, "derived", doc_num,
                           [rest] + p.text.split("\n")[1:])
            cl.note = cl.note or (
                f"the document marks this '({token})' ({reading}); mapped to "
                f"ordinal {ordinal}")
            self.sub_ordinal = ordinal
            return
        # sub-paragraph of the clause above: kept INSIDE that clause, following
        # lks.segment's rule that splitting a sub-paragraph off its parent
        # detaches it from the sentence that gives it legal force.
        self.sub_ordinal = ordinal
        lines = ["  " + ln.strip() for ln in p.text.split("\n")]
        if not self._append(p, lines, "subparagraph",
                            f"sub-paragraph ({token}) of the clause above"):
            self._record(p, "unassigned", reason=(
                f"sub-paragraph ({token}) appears before any clause, so there is no "
                f"clause for it to belong to. A human must place it."))

    def _bullet(self, p: Para, token: str, rest: str) -> None:
        lines = ["  " + ln.strip() for ln in p.text.split("\n")]
        if not self._append(p, lines, "subparagraph", "bulleted list item"):
            self._plain(p, "", p.text)

    def _plain(self, p: Para, token: str, rest: str) -> None:
        lines = p.text.split("\n")
        cur = self.current
        if cur is not None and self.scheme.clause_layer != "paragraph" \
                and self._is_continuation(p):
            self._append(p, lines, "continuation",
                         "unnumbered paragraph following a numbered clause")
            return
        sid = self._synth_section()
        n = self.para_counter.get(sid, 0) + 1
        self.para_counter[sid] = n
        cl = self._add(p, f"{sid}.{n}", "synthesised", "", lines)
        cl.note = cl.note or (
            f"the document gives this paragraph no number; the id is synthesised "
            f"from position (paragraph {n} of section {sid}) and CANNOT be cited")

    def _is_continuation(self, p: Para) -> bool:
        """An unnumbered paragraph inside a numbered document continues the
        clause above it: in every numbering scheme here, a new clause starts
        with its number. The alternative -- inventing a clause id in the
        middle of a numbered document -- would produce an id that contradicts
        the document's own numbering."""
        return self.current is not None

    def section_number(self, sid: str) -> str:
        m = re.match(r"^[A-Z]{1,4}-(\d+)$", sid)
        return m.group(1) if m and not sid.startswith("P-") else ""


# --- metadata --------------------------------------------------------------


def _iso_date(groups: tuple[str, ...], pattern_index: int) -> str | None:
    try:
        if pattern_index == 0:
            y, m, d = int(groups[0]), int(groups[1]), int(groups[2])
        elif pattern_index == 1:
            d = int(groups[0])
            m = _month(groups[1])
            y = int(groups[2])
        else:
            m = _month(groups[0])
            d = int(groups[1])
            y = int(groups[2])
        return datetime.date(y, m, d).isoformat()
    except Exception:
        return None


def _month(name: str) -> int:
    n = name.lower().rstrip(".")
    for i, full in enumerate(_MONTHS, start=1):
        if n == full or n == full[:3]:
            return i
    raise ValueError(name)


def find_effective_date(ex: Extracted) -> tuple[str | None, str, list[str]]:
    """An effective date only where the document says it is one.

    Returns (iso_date_or_None, evidence, candidate_notes). A bare date
    somewhere in the text is NOT accepted: a contract is full of dates
    (notice periods, prior agreements, signature dates) and picking one would
    date the rule by luck. Only a date in effective-date context counts, and
    the matched phrase is quoted in the report so a human can check it.
    """
    notes: list[str] = []
    for p in ex.paras:
        flat = " ".join(p.text.split())
        for idx, rx in enumerate(DATE_PATTERNS):
            for m in rx.finditer(flat):
                start = max(0, m.start() - 80)
                window = flat[start:m.start()]
                iso = _iso_date(m.groups(), idx)
                if iso is None:
                    continue
                if re.search(DATE_CONTEXT, window, re.I):
                    phrase = flat[start:min(len(flat), m.end() + 10)]
                    return iso, f"paragraph {p.index}: “...{phrase}...”", notes
                notes.append(f"paragraph {p.index}: date {iso} found, but not in "
                             f"effective-date context (“...{flat[start:m.end()][-60:]}...”)")
        for m in AMBIGUOUS_DATE_RE.finditer(flat):
            notes.append(
                f"paragraph {p.index}: {m.group(0)!r} is an ambiguous numeric date "
                f"(day/month or month/day) and is deliberately not interpreted")
    for key in ("created", "modified"):
        if ex.meta.get(key):
            notes.append(f"file metadata {key}={ex.meta[key]!r} is a file timestamp, "
                         f"not an effective date, and is not used as one")
    return None, "", notes


def _derive_doc_id(ex: Extracted, title: str) -> tuple[str, str]:
    if ex.meta.get("doc_id"):
        return ex.meta["doc_id"], "front matter"
    stem = Path(ex.source_path).stem
    stem = re.sub(r"^\d+[-_ ]+", "", stem)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").upper()
    if not slug:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").upper() or "IMPORTED-DOC"
    parts = [p for p in slug.split("-") if p][:5]
    return "-".join(parts)[:32], f"derived from the file name {Path(ex.source_path).name!r}"


def _derive_title(ex: Extracted) -> tuple[str, str]:
    if ex.meta.get("title"):
        src = "front matter" if "_front_matter" in ex.meta else "file metadata (title)"
        return ex.meta["title"], src
    headings = [p for p in ex.paras if p.kind == "heading"]
    if headings:
        return headings[0].text.strip(), f"first heading (paragraph {headings[0].index})"
    for p in ex.paras[:5]:
        flat = " ".join(p.text.split())
        if 8 <= len(flat) <= 120 and not flat.endswith("."):
            return flat, f"first title-shaped line (paragraph {p.index})"
    return "", ""


def build_meta(ex: Extracted, overrides: dict[str, str] | None = None
               ) -> tuple[dict[str, str], dict[str, str], dict[str, str], list[str]]:
    """Front-matter metadata, its provenance, and what a human must supply."""
    ov = {k: v for k, v in (overrides or {}).items() if v}
    meta: dict[str, str] = {}
    src: dict[str, str] = {}
    need: dict[str, str] = {}
    notes: list[str] = []

    title, tsrc = _derive_title(ex)
    if "title" in ov:
        title, tsrc = ov["title"], "supplied by hand"
    doc_id, dsrc = _derive_doc_id(ex, title)
    if "doc_id" in ov:
        doc_id, dsrc = ov["doc_id"], "supplied by hand"

    meta["doc_id"], src["doc_id"] = doc_id, dsrc
    if title:
        meta["title"], src["title"] = title, tsrc
    else:
        meta["title"], src["title"] = NEEDS_HUMAN, "not found"
        need["title"] = ("the document carries no title metadata and no title-shaped "
                         "first line. Supply it with --title \"...\".")

    if "effective_date" in ov:
        meta["effective_date"] = ov["effective_date"]
        src["effective_date"] = "supplied by hand"
    elif ex.meta.get("effective_date"):
        meta["effective_date"] = str(ex.meta["effective_date"])
        src["effective_date"] = "front matter"
    else:
        iso, evidence, cand = find_effective_date(ex)
        notes.extend(cand)
        if iso:
            meta["effective_date"], src["effective_date"] = iso, evidence
        else:
            meta["effective_date"] = NEEDS_HUMAN
            src["effective_date"] = "not found"
            need["effective_date"] = (
                "no date in effective-date context was found, and a date is NOT "
                "guessed from anything else: an invented effective date would "
                "silently date a rule. Supply it with --effective-date YYYY-MM-DD."
                + (f" Candidates seen: {len(cand)} (listed below)." if cand else ""))

    for field_, fallback, where in (
        ("version", UNKNOWN, "version"),
        ("jurisdiction", UNKNOWN, None),
        ("owner", UNKNOWN, "creator"),
    ):
        if field_ in ov:
            meta[field_], src[field_] = ov[field_], "supplied by hand"
            continue
        got = ex.meta.get(field_) or (ex.meta.get(where) if where else "")
        if got:
            meta[field_] = str(got)
            src[field_] = ("front matter" if field_ in ex.meta
                           else f"file metadata ({where})")
        else:
            meta[field_], src[field_] = fallback, "not found"
            notes.append(f"{field_} is unknown and written as {fallback!r}; supply it "
                         f"with --{field_.replace('_', '-')} before merging. It does "
                         f"not date or alter any rule, so it is not a blocking gap.")
    return meta, src, need, notes


# --- structure + write -----------------------------------------------------


def infer_structure(ex: Extracted, overrides: dict[str, str] | None = None
                    ) -> Structured:
    scheme = detect_scheme(ex)
    clauses, disp, warnings = _Builder(ex, scheme).run()
    meta, src, need, notes = build_meta(ex, overrides)

    # every input paragraph is accounted for, exactly once. This is an
    # invariant and not a hope: the report is only worth reading if it is
    # complete, so a gap here is a crash rather than a quiet omission.
    seen = [d.index for d in disp]
    expected = [p.index for p in ex.paras]
    if sorted(seen) != expected:
        missing = sorted(set(expected) - set(seen))
        extra = sorted(i for i in seen if seen.count(i) > 1)
        raise AssertionError(
            f"conversion accounting is broken: {len(missing)} paragraph(s) "
            f"unaccounted {missing[:10]}, {len(extra)} double-counted {extra[:10]}. "
            f"Refusing to write a report that does not account for the input."
        )
    return Structured(meta=meta, meta_sources=src, needs_human=need, clauses=clauses,
                      dispositions=disp, scheme=scheme, extracted=ex,
                      warnings=list(ex.warnings) + warnings + notes)


def _yaml_scalar(v: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    if '"' in v or "\\" in v:
        import yaml

        return yaml.safe_dump(v, default_flow_style=True, allow_unicode=True).strip()
    return f'"{v}"'


_HOUSE_ORDER = ("doc_id", "title", "version", "effective_date", "jurisdiction", "owner")


def _safe_line(line: str) -> str:
    """Keep a body line from being read as a marker by lks.segment."""
    if line.lstrip().startswith("#") or line.lstrip().startswith("**"):
        return "  " + line.strip()
    return line


def _document_heading(s: Structured) -> str:
    """The document's own top-level heading, where it had one."""
    for d in s.dispositions:
        if d.kind == "title" and d.text.strip():
            return " ".join(d.text.split())
    return s.meta.get("title") or s.meta["doc_id"]


def render_document(s: Structured) -> str:
    out: list[str] = ["---"]
    for k in _HOUSE_ORDER:
        out.append(f"{k}: {_yaml_scalar(s.meta.get(k, UNKNOWN))}"
                   if k != "doc_id" else f"doc_id: {s.meta['doc_id']}")
    ex = s.extracted
    out += [
        f"source_file: {_yaml_scalar(Path(ex.source_path).name)}",
        f"source_format: {ex.fmt}",
        f"converted_by: {_yaml_scalar('lks.structure (' + s.scheme.name + ')')}",
        f"conversion_report: {_yaml_scalar(s.meta['doc_id'] + '.report.md')}",
        "---",
        "",
        f"# {_document_heading(s)}",
        "",
    ]
    section = ""
    for c in s.clauses:
        if c.section_id != section:
            section = c.section_id
            out += [f"## {c.section_id} {c.section_title or '(untitled section)'}", ""]
        lines = [ln for ln in c.lines]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        first = lines[0].strip() if lines else ""
        out.append(f"**{c.clause_id}** {first}".rstrip())
        for ln in lines[1:]:
            out.append(_safe_line(ln))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _trunc(t: str, n: int = 90) -> str:
    flat = " ".join(t.split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


def render_report(s: Structured, converted_path: Path) -> str:
    ex = s.extracted
    counts = s.counts()
    L: list[str] = [
        f"# Conversion report — {Path(ex.source_path).name}",
        "",
        "This file is the audit trail for a machine conversion. The converted "
        "document is what the system will use; **this report is what tells you "
        "whether to trust it**. Every paragraph of the input appears in the last "
        "table exactly once.",
        "",
        "| | |",
        "|---|---|",
        f"| source | `{ex.source_path}` |",
        f"| format | {ex.fmt} (read with {ex.tool or 'builtin'}) |",
        f"| converted | `{converted_path}` |",
        f"| numbering scheme | **{s.scheme.name}** — {s.scheme.describe()} |",
        f"| source paragraphs | {ex.n_paras} |",
        f"| clauses inferred | {len(s.clauses)} |",
        f"| ids from the document | {len(s.clauses) - len(s.synthesised)} |",
        f"| ids synthesised (NOT citable) | {len(s.synthesised)} |",
        f"| paragraphs not assigned to any clause | {len(s.unassigned)} |",
        f"| paragraphs excluded as page furniture | {len(s.furniture)} |",
        "",
    ]

    if s.needs_human:
        L += ["## A HUMAN MUST SUPPLY THIS", "",
              "The conversion is not usable until these are supplied. They are not "
              "guessed. `lks.ingest` raises a blocking conflict for each, so the "
              "proposal cannot be merged while any remains.", ""]
        for k, why in s.needs_human.items():
            L.append(f"- **{k}** — {why}")
        L.append("")
    else:
        L += ["## Human input required", "", "None outstanding: `doc_id`, `title` and "
              "`effective_date` all have a stated source below.", ""]

    L += ["## Metadata and where each field came from", "",
          "| field | value | source |", "|---|---|---|"]
    for k in _HOUSE_ORDER:
        L.append(f"| {k} | {s.meta.get(k, '')} | {s.meta_sources.get(k, '')} |")
    L.append("")

    ev = s.scheme.evidence
    L += ["## How the scheme was chosen", "", ev.get("why", ""), "",
          "| marker | paragraphs opening with it |", "|---|---|"]
    for k, v in sorted(ev.get("marker_counts", {}).items(), key=lambda kv: -kv[1]):
        L.append(f"| {k} | {v} |")
    L += [f"| (headings carried by the format) | {ev.get('format_headings', 0)} |",
          f"| (numbered lines that are heading-shaped) | "
          f"{ev.get('numbered_heading_like', 0)} |", ""]

    L += ["## Clauses", "",
          "| clause id | id source | citable as | source paragraphs | text |",
          "|---|---|---|---|---|"]
    for c in s.clauses:
        citable = c.document_number if c.id_source != "synthesised" else "—"
        flag = "**synthesised**" if c.synthesised else c.id_source
        L.append(f"| `{c.clause_id}` | {flag} | {citable or '—'} | "
                 f"{', '.join(str(i) for i in c.paras)} | {_trunc(c.body, 70)} |")
    L.append("")

    synth = s.synthesised
    L += ["## Synthesised ids", ""]
    if synth:
        L += ["These ids are positional. The document does not contain them, so "
              "**they cannot be cited** — a citation to `P-3.2` is a citation to "
              "our guess, not to the document. Renumber in the converted file if the "
              "document really does number these clauses (Word's automatic list "
              "numbering is invisible to text extraction, which is the usual cause).",
              ""]
        for c in synth:
            L.append(f"- `{c.clause_id}` (source paragraph "
                     f"{', '.join(str(i) for i in c.paras)}): {c.note}")
        L.append("")
    else:
        L += ["None: every clause id came from the document's own numbering.", ""]

    L += ["## Paragraphs the segmenter could not confidently assign", ""]
    attention = [d for d in s.dispositions if d.needs_attention]
    if attention:
        L += ["Nothing here was discarded — the full text of each is reproduced "
              "so it can be placed by hand. Paragraphs marked `furniture` were kept "
              "out of the converted document because attaching a page number or a "
              "running header to a clause would corrupt the clause.", ""]
        for d in attention:
            L += [f"### paragraph {d.index} — {d.kind}", "", f"*{d.reason}*", "",
                  "```", d.text, "```", ""]
    else:
        L += ["None: every paragraph became a clause, a continuation of one, a "
              "sub-paragraph of one, a section heading, or the document title.", ""]

    if s.warnings:
        L += ["## Warnings from extraction", ""]
        L += [f"- {w}" for w in s.warnings]
        L.append("")

    L += [f"## Every source paragraph ({ex.n_paras} of {ex.n_paras} accounted for)",
          "", "| # | disposition | clause | text |", "|---|---|---|---|"]
    for d in sorted(s.dispositions, key=lambda d: d.index):
        L.append(f"| {d.index} | {d.kind} | `{d.clause_id}` | {_trunc(d.text, 80)} |")
    L += ["", f"Disposition counts: {counts}.", ""]
    return "\n".join(L)


def convert_file(path: str | Path, out_dir: str | Path = CONVERTED,
                 overrides: dict[str, str] | None = None) -> Conversion:
    """Extract, infer, and write the house-convention document plus its report."""
    ex = extract(path)
    s = infer_structure(ex, overrides)
    if not s.clauses:
        from .extract import ExtractionError

        raise ExtractionError(
            f"{path}: no clauses could be inferred from {ex.n_paras} paragraph(s). "
            f"Check that the file is the document you meant and that its text "
            f"extracted correctly (see `lks convert` output)."
        )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc_id = s.meta["doc_id"]
    converted = out_dir / f"{doc_id}.md"
    report = out_dir / f"{doc_id}.report.md"
    converted.write_text(render_document(s), encoding="utf-8")
    report.write_text(render_report(s, converted), encoding="utf-8")
    return Conversion(converted_path=converted, report_path=report, structured=s)
