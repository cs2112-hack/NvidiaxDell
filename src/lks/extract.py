"""Turn a real file into plain text plus whatever metadata the format carries.

This module is the front door for a document that was **not** written in the
house convention: a signed PDF, a Word file from a law firm, an HTML export
from a policy portal. Its job is deliberately small -- produce paragraphs and
metadata, and nothing else. Inferring clause structure is
`lks.structure`'s job; both are kept separate so that a bad structural guess
can never be confused with a bad text extraction.

Two rules govern everything here.

**Paragraph breaks are load-bearing.** Clause boundaries in a real document
are carried by paragraph breaks far more reliably than by any numbering, so a
paragraph break is preserved exactly and never invented. Within a paragraph
the original line breaks are preserved too, because a sub-paragraph list
    (a) Tier 1 city: GBP 75 per Travel Day;
    (b) Tier 2 city: GBP 55 per Travel Day;
is one paragraph whose lines are semantically separate items; reflowing it
into a single line would destroy the only signal that says so.

**Normalisation never touches the words.** Digits, currency symbols, letters
and ordinary punctuation are left exactly as they are: in legal text `1.25`,
`£75` and `Grade 5 or above` are the operative content, and a hash over the
clause is what tells the rest of the system that the clause has changed.
What *is* normalised is the typographic noise that word processors and PDF
extractors add: curly quotes to straight quotes (so that a defined term
written `"Base Hourly Rate"` is recognisable as one), non-breaking and thin
spaces to ordinary spaces, soft hyphens and zero-width characters away, and
the ligatures that PDF text layers emit (`oﬃce` -> `office`) back to the
letters they stand for.

Dashes are *canonicalised, not ASCII-folded*: the several unicode dashes are
mapped onto the two the corpus already uses (en dash U+2013, em dash U+2014),
rather than being rewritten as `-` or `--`. Folding an em dash to `--` would
change the printed text of a clause -- a visible edit to a legal document
made by a tool, which is exactly what this pipeline exists to prevent -- and
it would give a document a different content hash depending on which route it
entered the system by.
"""
from __future__ import annotations

import datetime
import html as _html
import importlib.util
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

SUPPORTED_SUFFIXES = (".txt", ".md", ".markdown", ".text", ".docx", ".pdf", ".html", ".htm")

# Formats we deliberately do not handle, and what each would need. Named in
# the error message so the human is told what to do rather than just refused.
UNSUPPORTED_SUFFIXES = {
    ".doc": "legacy binary Word; convert to .docx or .pdf first (Word, or "
            "`libreoffice --headless --convert-to docx`)",
    ".rtf": "no standard-library RTF reader; convert to .docx or .pdf first",
    ".odt": "not implemented; convert to .docx or .pdf first",
    ".pages": "proprietary Apple bundle; export to .docx or .pdf first",
    ".xlsx": "a spreadsheet is not a prose document; extract the relevant "
             "text into .md or .docx",
}


class ExtractionError(RuntimeError):
    """The file cannot be turned into text. Always names a remedy."""


@dataclass
class Para:
    """One source paragraph, with whatever the format told us about it.

    `index` is 1-based and stable: the conversion report accounts for every
    paragraph by this number, so that "nothing was dropped" is checkable by a
    human rather than asserted by the tool.
    """

    index: int
    text: str
    kind: str = "body"        # "body" | "heading" | "furniture"
    level: int = 0            # heading level where the format carries one
    note: str = ""            # e.g. "docx auto-numbered list item"

    @property
    def lines(self) -> list[str]:
        return self.text.split("\n")

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "kind": self.kind, "level": self.level,
                "note": self.note, "text": self.text}


@dataclass
class Extracted:
    source_path: str
    fmt: str
    paras: list[Para] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)   # what the FORMAT carried
    warnings: list[str] = field(default_factory=list)
    tool: str = ""            # which backend produced the text

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.paras)

    @property
    def n_paras(self) -> int:
        return len(self.paras)


# --- normalisation ---------------------------------------------------------

# Quotes and apostrophes: word processors curl them, the corpus does not.
# `"Base Hourly Rate" means ...` is how these documents mark a definition and
# the defined-term detector in lks.ingest matches on the straight form.
_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
}
# Dash family -> the two dashes the corpus uses. See the module docstring:
# canonicalised, never folded to ASCII.
_DASHES = {
    "\u2010": "-",         # hyphen -> ASCII hyphen (same glyph, same word)
    "\u2011": "-",         # non-breaking hyphen
    "\u2012": "\u2013",    # figure dash -> en dash
    "\u2015": "\u2014",    # horizontal bar -> em dash
    "\u2212": "-",         # minus sign -> ASCII hyphen
}
# Spaces that are spaces, and invisibles that are nothing. Written as escapes
# on purpose: a literal soft hyphen in this source would be invisible.
_SPACES = {c: " " for c in (
    "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u202f\u205f\u3000\t")}
_INVISIBLE = {c: "" for c in "\u00ad\u200b\u200c\u200d\u2060\ufeff"}
# Ligatures: a PDF text layer emits these for ordinary letter pairs. Mapping
# them back restores the WORD; leaving them would corrupt it.
_LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
}
_OTHER = {"\u2026": "...", "\u2028": "\n", "\u2029": "\n\n",
          "\r\n": "\n", "\r": "\n"}

_TRANSLATIONS: dict[str, str] = {}
for _m in (_QUOTES, _DASHES, _SPACES, _INVISIBLE, _LIGATURES):
    _TRANSLATIONS.update(_m)


def normalise_text(s: str) -> str:
    """Conservative typographic normalisation. Idempotent.

    Whitespace: CRLF -> LF, exotic spaces -> space, trailing space stripped,
    runs of blank lines collapsed to one (a paragraph break), runs of spaces
    inside a line collapsed to one. Collapsing intra-line spaces is safe --
    `lks.model.content_hash` is whitespace-reflow-insensitive by design -- and
    it removes the ragged inter-word padding that `pdftotext` produces.
    """
    for a, b in _OTHER.items():
        s = s.replace(a, b)
    s = s.translate(str.maketrans(_TRANSLATIONS))
    s = s.replace("\f", "\n\n")
    out_lines: list[str] = []
    for line in s.split("\n"):
        stripped = line.rstrip()
        indent = len(stripped) - len(stripped.lstrip(" "))
        body = re.sub(r" {2,}", " ", stripped.strip())
        out_lines.append((" " * min(indent, 4)) + body if body else "")
    s = "\n".join(out_lines)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip("\n")


def split_paragraphs(text: str, start_index: int = 1) -> list[Para]:
    """Blank-line separated blocks, with in-block line breaks preserved."""
    paras: list[Para] = []
    i = start_index
    for block in re.split(r"\n\s*\n", text):
        block = block.strip("\n")
        if not block.strip():
            continue
        paras.append(Para(index=i, text=block))
        i += 1
    return paras


# A line that opens a numbered or lettered item. Used ONLY to recover
# paragraph breaks from a format that lost them (see
# `paragraphs_from_flat_text`); deciding what the numbering MEANS is
# lks.structure's job, not this module's.
_FLAT_MARKER_RE = re.compile(
    r"^\(?[A-Za-z]{1,3}\)[ \t]+\S"
    r"|^(?:[A-Z]{1,4}[-.])?\d{1,3}(?:\.\d{1,3})*[.)]?[ \t]+\S"
)
_CAPS_LINE_RE = re.compile(r"^[^a-z]{2,60}$")


def paragraphs_from_flat_text(text: str, start_index: int = 1
                              ) -> tuple[list[Para], bool]:
    """Paragraphs from text that may have lost its blank lines.

    Some PDFs -- and text pasted out of one -- arrive as an unbroken run of
    lines with no blank line anywhere. Blank lines are the primary signal and
    are always used where they exist; where a block runs on for many lines
    with none, the block is re-split at lines that open a numbered or lettered
    item, or at a short all-capitals line. The return flag says whether that
    happened, so the conversion report can tell the human that these
    paragraph breaks were *inferred* rather than read.
    """
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    lines_total = max(1, len(text.split("\n")))
    blanks = sum(1 for ln in text.split("\n") if not ln.strip())
    run_on = [b for b in blocks if len(b.split("\n")) >= 8]
    if not run_on or blanks / lines_total >= 0.05:
        return split_paragraphs(text, start_index), False

    paras: list[Para] = []
    i = start_index
    inferred = False
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 8:
            paras.append(Para(index=i, text=block))
            i += 1
            continue
        inferred = True
        buf: list[str] = []
        for ln in lines:
            starts = bool(_FLAT_MARKER_RE.match(ln.strip())) or bool(
                _CAPS_LINE_RE.match(ln.strip()))
            if starts and buf:
                paras.append(Para(index=i, text="\n".join(buf)))
                i += 1
                buf = []
            buf.append(ln)
        if buf:
            paras.append(Para(index=i, text="\n".join(buf)))
            i += 1
    return paras, inferred


# --- per-format readers ----------------------------------------------------

FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SETEXT_RE = re.compile(r"^(=+|-{3,})\s*$")


def _strip_md_inline(s: str) -> str:
    """Remove markdown emphasis markers. Removes markers, never words.

    `**C-4.2** An Employee ...` becomes `C-4.2 An Employee ...`, which is the
    same clause number the structure inference reads out of any other
    document -- so a house-convention document travels the same code path as
    a real one rather than a privileged one.
    """
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"\1", s, flags=re.DOTALL)
    s = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", s)
    s = re.sub(r"`([^`\n]+)`", r"\1", s)
    return s


def _extract_text(path: Path, fmt: str) -> Extracted:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    ex = Extracted(source_path=str(path), fmt=fmt, tool="read")
    meta_block = ""

    if fmt == "md":
        fm = FRONT_MATTER_RE.match(raw)
        if fm:
            meta_block = fm.group(1)
            raw = raw[fm.end():]
            ex.meta["_front_matter"] = meta_block
            try:
                import yaml

                loaded = yaml.safe_load(meta_block) or {}
                for k, v in loaded.items():
                    if isinstance(v, (datetime.date, datetime.datetime)):
                        ex.meta[str(k)] = v.isoformat()
                    elif isinstance(v, (str, int, float)):
                        ex.meta[str(k)] = str(v)
            except Exception as e:                       # pragma: no cover
                ex.warnings.append(f"front matter present but unreadable: {e}")

    text = normalise_text(raw)
    if fmt != "md":
        ex.paras, inferred = paragraphs_from_flat_text(text)
        if inferred:
            ex.warnings.append(
                "the file contains no blank lines, so paragraph breaks were "
                "INFERRED from lines that open a numbered or lettered item"
            )
        return ex

    # Markdown: headings are structure the format carries, so keep them typed.
    blocks = re.split(r"\n\s*\n", text)
    i = 1
    for block in blocks:
        block = block.strip("\n")
        if not block.strip():
            continue
        lines = block.split("\n")
        # a block may be a run of consecutive ATX headings / heading + text
        buf: list[str] = []
        for ln in lines:
            m = ATX_RE.match(ln.strip())
            if m:
                if buf:
                    ex.paras.append(Para(index=i, text=_strip_md_inline("\n".join(buf))))
                    i += 1
                    buf = []
                ex.paras.append(Para(index=i, text=_strip_md_inline(m.group(2)).strip(),
                                     kind="heading", level=len(m.group(1))))
                i += 1
                continue
            if buf and SETEXT_RE.match(ln.strip()) and len(buf) == 1:
                ex.paras.append(Para(index=i, text=_strip_md_inline(buf[0]).strip(),
                                     kind="heading",
                                     level=1 if ln.strip().startswith("=") else 2))
                i += 1
                buf = []
                continue
            buf.append(ln)
        if buf:
            ex.paras.append(Para(index=i, text=_strip_md_inline("\n".join(buf))))
            i += 1
    return ex


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CORE_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
}


def _docx_para_text(p: ET.Element) -> str:
    """Visible text of one w:p, with tabs as spaces and w:br as line breaks.

    Only `w:t` is read. `w:delText` (tracked deletion) and `w:instrText`
    (field codes) are therefore excluded for free: deleted text is not part of
    the document, and a field code is not text a reader sees.
    """
    out: list[str] = []
    for node in p.iter():
        tag = node.tag
        if tag == W + "t":
            out.append(node.text or "")
        elif tag == W + "tab":
            out.append(" ")
        elif tag in (W + "br", W + "cr"):
            out.append("\n")
        elif tag == W + "noBreakHyphen":
            out.append("-")
    return "".join(out)


def _extract_docx(path: Path) -> Extracted:
    ex = Extracted(source_path=str(path), fmt="docx", tool="zipfile+ElementTree")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ExtractionError(
            f"{path}: not a valid .docx (a .docx is a zip archive). If this is a "
            f"legacy binary .doc, convert it first: {UNSUPPORTED_SUFFIXES['.doc']}"
        ) from e
    with zf:
        names = set(zf.namelist())
        if "word/document.xml" not in names:
            raise ExtractionError(
                f"{path}: zip archive with no word/document.xml, so it is not a "
                f"Word document. Contents look like: {sorted(names)[:5]}"
            )
        root = ET.fromstring(zf.read("word/document.xml"))

        # core properties: the only metadata a .docx reliably carries
        if "docProps/core.xml" in names:
            try:
                core = ET.fromstring(zf.read("docProps/core.xml"))

                def _cp(q: str) -> str:
                    el = core.find(q, CORE_NS)
                    return (el.text or "").strip() if el is not None else ""

                for key, q in (("title", "dc:title"), ("subject", "dc:subject"),
                               ("creator", "dc:creator"),
                               ("last_modified_by", "cp:lastModifiedBy"),
                               ("created", "dcterms:created"),
                               ("modified", "dcterms:modified"),
                               ("version", "cp:version"),
                               ("category", "cp:category")):
                    v = _cp(q)
                    if v:
                        ex.meta[key] = v
            except ET.ParseError as e:                    # pragma: no cover
                ex.warnings.append(f"docProps/core.xml unreadable: {e}")

        body = root.find(W + "body")
        if body is None:                                   # pragma: no cover
            raise ExtractionError(f"{path}: word/document.xml has no w:body")

        in_table = {id(p) for tbl in body.iter(W + "tbl") for p in tbl.iter(W + "p")}
        n_auto = 0
        i = 1
        for p in body.iter(W + "p"):
            text = normalise_text(_docx_para_text(p))
            if not text.strip():
                continue
            pr = p.find(W + "pPr")
            style = ""
            if pr is not None:
                st = pr.find(W + "pStyle")
                if st is not None:
                    style = st.get(W + "val") or ""
            kind, level = "body", 0
            m = re.fullmatch(r"(?:Heading|heading)[ _-]?([1-9])", style)
            if m:
                # Word's Title style sits ABOVE Heading1, so heading levels are
                # shifted down one: otherwise a document's title and its first
                # section heading look like the same level and neither can be
                # told from the other.
                kind, level = "heading", int(m.group(1)) + 1
            elif style in ("Title", "Subtitle"):
                kind, level = "heading", 1 if style == "Title" else 2
            note = ""
            if pr is not None and pr.find(W + "numPr") is not None:
                note = "docx auto-numbered list item"
                n_auto += 1
            if id(p) in in_table:
                note = (note + "; " if note else "") + "inside a table"
            ex.paras.append(Para(index=i, text=text, kind=kind, level=level, note=note))
            i += 1

        if n_auto:
            ex.warnings.append(
                f"{n_auto} paragraph(s) use Word's automatic list numbering. Those "
                f"numbers live in word/numbering.xml, not in the text, so they are "
                f"NOT visible to extraction: clause numbers that appear in Word may "
                f"be absent here and will have to be synthesised. Check the report."
            )
        if in_table:
            ex.warnings.append(
                f"{len(in_table)} paragraph(s) come from tables; each cell becomes a "
                f"paragraph and the row/column relationship is lost."
            )
    if not ex.paras:
        raise ExtractionError(f"{path}: no paragraph text found in word/document.xml")
    return ex


def pdf_backends() -> dict[str, str]:
    """What is actually available for PDF text, probed rather than assumed."""
    found: dict[str, str] = {}
    exe = shutil.which("pdftotext")
    if exe:
        found["pdftotext"] = exe
    if importlib.util.find_spec("pypdf") is not None:
        found["pypdf"] = "python module pypdf"
    return found


_NO_PDF_BACKEND = (
    "no PDF text extractor is available. Install ONE of:\n"
    "  * poppler-utils, which provides `pdftotext` (preferred: it preserves "
    "reading order and paragraph breaks)\n"
    "        apt-get install poppler-utils   /   brew install poppler\n"
    "  * the pypdf Python package\n"
    "        pip install pypdf\n"
    "Then re-run. Nothing is guessed from a PDF we cannot read."
)


def _extract_pdf(path: Path) -> Extracted:
    backends = pdf_backends()
    if not backends:
        raise ExtractionError(f"{path}: {_NO_PDF_BACKEND}")
    ex = Extracted(source_path=str(path), fmt="pdf")

    if "pdftotext" in backends:
        proc = subprocess.run(
            # -layout keeps the blank line between paragraphs and the leading
            # indentation of sub-paragraphs; without it poppler reflows the
            # page and both signals are lost.
            [backends["pdftotext"], "-q", "-nopgbrk", "-eol", "unix", "-layout",
             str(path), "-"],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise ExtractionError(
                f"{path}: pdftotext failed (rc={proc.returncode}): "
                f"{(proc.stderr or '').strip()[:400]}"
            )
        text = proc.stdout
        ex.tool = "pdftotext"
    else:
        from pypdf import PdfReader                        # type: ignore

        reader = PdfReader(str(path))
        text = "\n\n".join((pg.extract_text() or "") for pg in reader.pages)
        ex.tool = "pypdf"
        try:
            info = reader.metadata or {}
            for key, src in (("title", "/Title"), ("creator", "/Author"),
                             ("created", "/CreationDate")):
                v = info.get(src)
                if v:
                    ex.meta[key] = str(v)
        except Exception:                                  # pragma: no cover
            pass
        ex.warnings.append(
            "extracted with pypdf; `pdftotext` (poppler-utils) preserves paragraph "
            "breaks better and is preferred where available."
        )

    if ex.tool == "pdftotext":
        pdfinfo = shutil.which("pdfinfo")
        if pdfinfo:
            try:
                pi = subprocess.run([pdfinfo, str(path)], capture_output=True,
                                    text=True, timeout=60)
                for line in (pi.stdout or "").splitlines():
                    k, _, v = line.partition(":")
                    key = {"Title": "title", "Author": "creator",
                           "CreationDate": "created"}.get(k.strip())
                    if key and v.strip():
                        ex.meta[key] = v.strip()
            except Exception:                              # pragma: no cover
                pass

    text = normalise_text(text)
    if len(text.replace("\n", "").strip()) < 20:
        raise ExtractionError(
            f"{path}: the PDF yielded almost no text ({len(text.strip())} chars). It "
            f"is most likely a scan with no text layer. OCR it first (e.g. "
            f"`ocrmypdf in.pdf out.pdf`) -- guessing at the text of a legal document "
            f"is not an option."
        )
    ex.paras, inferred = paragraphs_from_flat_text(
        isolate_page_furniture(_dehyphenate(text)))
    ex.warnings.append(
        "PDF paragraph breaks come from the text layer, and running headers, "
        "footers and page numbers survive it as paragraphs. Every paragraph is "
        "listed in the conversion report; check it."
    )
    if inferred:
        ex.warnings.append(
            "this PDF's text layer contains no blank lines, so paragraph breaks "
            "were INFERRED from lines that open a numbered or lettered item. A "
            "clause whose text merely continues on a new line may have been "
            "joined to its neighbour; check the clause table in the report."
        )
    return ex


_PAGE_NUM_LINE_RE = re.compile(
    r"^(?:page\s+\d+(?:\s+of\s+\d+)?|\d{1,3}|[-\u2013\u2014]\s*\d{1,3}\s*[-\u2013\u2014])$",
    re.I,
)


def isolate_page_furniture(text: str) -> str:
    """Put every running header, footer and page number in its own paragraph.

    Pagination is not part of a document's structure, but it lands in the
    text layer all the same -- and worse, a footer and the next page's header
    arrive with no blank line between them or the clause they interrupt, so a
    clause acquires `Page 2 of 9` in the middle of a sentence. Isolating such
    lines into their own paragraphs lets `lks.structure` classify them
    explicitly. They are NOT deleted here: the conversion report has to
    account for every paragraph, and a line this function guessed wrong about
    must still be visible to the human reading it.

    A line is furniture if it is a page number, or if it appears three or
    more times in the document and is short enough to be a running head.
    """
    lines = text.split("\n")
    counts: dict[str, int] = {}
    for ln in lines:
        key = ln.strip()
        if key:
            counts[key] = counts.get(key, 0) + 1
    out: list[str] = []
    for ln in lines:
        key = ln.strip()
        repeated = counts.get(key, 0) >= 3 and len(key) <= 80 and len(key.split()) <= 12
        if key and (_PAGE_NUM_LINE_RE.match(key) or repeated):
            out += ["", ln, ""]
        else:
            out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


_HYPHEN_BREAK_RE = re.compile(r"([A-Za-z]{2,})-\n([a-z]{2,})")


def _dehyphenate(text: str) -> str:
    """Rejoin a word split across lines by end-of-line hyphenation.

    Only `word-` + newline + lowercase continuation, so hyphenated compounds
    (`Client-Billable`, `pro-rated`) and any hyphen before a capital or a
    digit are left alone.
    """
    return _HYPHEN_BREAK_RE.sub(r"\1\2", text)


class _HTMLText(HTMLParser):
    FURNITURE = {"nav", "footer", "aside"}
    BLOCK = {"p", "div", "section", "article", "li", "tr", "td", "th", "blockquote",
             "pre", "dd", "dt", "figcaption", "header", "footer", "main", "table",
             "ul", "ol", "dl", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr"}
    DROP = {"script", "style", "head", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, int, list[str]]] = []   # (kind, level, chunks)
        self._drop = 0
        self._furniture = 0
        self._cur: tuple[str, int, list[str]] = ("body", 0, [])
        self.title = ""
        self._in_title = False

    def _flush(self) -> None:
        kind, level, chunks = self._cur
        text = "".join(chunks)
        if text.strip():
            if self._furniture and kind != "heading":
                kind = "furniture"
            self.blocks.append((kind, level, [text]))
        self._cur = ("body", 0, [])

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.DROP:
            self._drop += 1
            return
        if tag in self.FURNITURE:
            self._flush()
            self._furniture += 1
        if tag == "title":
            self._in_title = True
            return
        if tag in self.BLOCK:
            self._flush()
            if re.fullmatch(r"h[1-6]", tag):
                self._cur = ("heading", int(tag[1]), [])

    def handle_endtag(self, tag: str) -> None:
        if tag in self.DROP:
            self._drop = max(0, self._drop - 1)
            return
        if tag in self.FURNITURE:
            self._flush()
            self._furniture = max(0, self._furniture - 1)
        if tag == "title":
            self._in_title = False
            return
        if tag in self.BLOCK:
            self._flush()

    def handle_data(self, data: str) -> None:
        # <title> lives inside <head>, which is otherwise dropped whole
        if self._in_title:
            self.title += data
            return
        if self._drop:
            return
        self._cur[2].append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _extract_html(path: Path) -> Extracted:
    raw = path.read_text(encoding="utf-8", errors="replace")
    p = _HTMLText()
    p.feed(raw)
    p.close()
    ex = Extracted(source_path=str(path), fmt="html", tool="html.parser")
    if p.title.strip():
        ex.meta["title"] = normalise_text(_html.unescape(p.title)).strip()
    i = 1
    for kind, level, chunks in p.blocks:
        text = normalise_text(_html.unescape("".join(chunks)))
        # HTML source indentation is formatting, not content
        text = "\n".join(ln.strip() for ln in text.split("\n")).strip()
        if not text.strip():
            continue
        note = ("from an HTML <nav>, <footer> or <aside> element, which is page "
                "furniture rather than document text") if kind == "furniture" else ""
        ex.paras.append(Para(index=i, text=text, kind=kind, level=level, note=note))
        i += 1
    if not ex.paras:
        raise ExtractionError(f"{path}: no text content found in the HTML")
    return ex


# --- entry point -----------------------------------------------------------


def extract(path: str | Path) -> Extracted:
    """Read `path` into paragraphs plus format metadata."""
    path = Path(path)
    if not path.exists():
        raise ExtractionError(f"{path}: no such file")
    suffix = path.suffix.lower()
    if suffix in UNSUPPORTED_SUFFIXES:
        raise ExtractionError(
            f"{path}: {suffix} is not supported -- {UNSUPPORTED_SUFFIXES[suffix]}. "
            f"Supported: {', '.join(SUPPORTED_SUFFIXES)}"
        )
    if suffix in (".txt", ".text"):
        return _extract_text(path, "txt")
    if suffix in (".md", ".markdown"):
        return _extract_text(path, "md")
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in (".html", ".htm"):
        return _extract_html(path)
    raise ExtractionError(
        f"{path}: unrecognised extension {suffix or '(none)'}. Supported: "
        f"{', '.join(SUPPORTED_SUFFIXES)}. Known-but-unsupported: "
        f"{', '.join(sorted(UNSUPPORTED_SUFFIXES))}"
    )


def capabilities() -> dict[str, Any]:
    """What this installation can actually read, probed now."""
    pdf = pdf_backends()
    return {
        "text": {"suffixes": [".txt", ".md", ".markdown", ".text"], "backend": "builtin"},
        "docx": {"suffixes": [".docx"], "backend": "zipfile + xml.etree (stdlib)"},
        "html": {"suffixes": [".html", ".htm"], "backend": "html.parser (stdlib)"},
        "pdf": {
            "suffixes": [".pdf"],
            "available": sorted(pdf),
            "chosen": ("pdftotext" if "pdftotext" in pdf
                       else "pypdf" if "pypdf" in pdf else None),
            "remedy": None if pdf else _NO_PDF_BACKEND,
        },
        "unsupported": UNSUPPORTED_SUFFIXES,
    }
