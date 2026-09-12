"""A PDF writer, in the standard library, because the deliverable is a PDF.

## Why this exists rather than a dependency

Nothing on this host can make a PDF except LibreOffice: no pandoc, no
weasyprint, no wkhtmltopdf, no LaTeX, no headless Chrome, and neither reportlab
nor fpdf in the virtualenv. So the choice was LibreOffice, a new dependency, or
this. Three properties decided it.

*It stays offline.* The README promises a cold checkout works with
`./scripts/start.sh` and nothing else. `pip install reportlab` breaks that
promise on a machine with no network, and it breaks it at the last step of the
pipeline, after an hour of model time has already been spent.

*It is byte-deterministic.* The same document renders to the same bytes,
always: there is no wall-clock timestamp in the output, `/CreationDate` is
derived from the document's own effective date, object numbers are assigned in
a fixed order, and zlib at a fixed level is reproducible. That matters because
this repo already treats reproducibility as the property that makes an artefact
citable -- `vectorstore/index/manifest.json` pins a corpus hash and the store
refuses to answer when it disagrees. A PDF whose bytes are stable can be hashed
into `report.md` and pinned the same way, so "this is the document the gates
passed" is checkable rather than asserted. LibreOffice embeds a creation
timestamp and cannot give us that.

*It puts clause layout under our control.* A legal document is mostly one
shape -- a bold clause marker, then a body block hanging off it, at a stable
indent, never split from its marker by a page break. That is four lines of code
here and a fight with an HTML importer there.

## What it deliberately does not do

No font embedding: the base-14 fonts are guaranteed present in every conforming
reader, so the file stays small and needs no font licence reasoning. No
hyphenation dictionary. No bidi, no vertical scripts, no shaping. Text is
encoded as WinAnsi, which covers Latin-1 plus the typographic characters legal
prose actually uses -- curly quotes, en and em dashes, and the pound sign,
which this corpus needs on every expense clause. A character outside WinAnsi is
transliterated where there is an obvious equivalent and replaced with `?`
otherwise, and `unmapped()` reports every one so a silent substitution in a
legal document is impossible.

## Layout model

`Layout` is a single-column flowing typesetter. Callers append blocks
(`heading`, `para`, `clause`, `table`, `rule`) and it breaks pages as it goes,
keeping a heading with the block that follows it and a clause marker with the
first line of its body. There is no float, no multi-column, and no reflow after
the fact: one pass, top to bottom, which is all a contract needs.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# --- font metrics ----------------------------------------------------------
#
# Adobe's base-14 advance widths, in 1/1000 em. These are the numbers every
# conforming reader uses for these fonts, so measuring with them is exact
# rather than approximate -- text wrapped here occupies the width the reader
# will give it. Only the glyphs legal prose uses are tabulated; `Font.width`
# falls back to a per-font default and `unmapped()` surfaces anything that had
# to be substituted.

_ASCII = (
    "space exclam quotedbl numbersign dollar percent ampersand quotesingle "
    "parenleft parenright asterisk plus comma hyphen period slash"
).split()

def _mk(name: str, widths: dict[str, int], default: int) -> "Font":
    return Font(name=name, widths=widths, default_width=default)


def _row(chars: str, widths: Sequence[int]) -> dict[str, int]:
    if len(chars) != len(widths):
        raise ValueError(f"{len(chars)} chars but {len(widths)} widths")
    return dict(zip(chars, widths))


_PUNCT = " !\"#$%&'()*+,-./"
_DIGITS = "0123456789"
_MID = ":;<=>?@"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BRACKET = "[\\]^_`"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_TAIL = "{|}~"

# Typographic extras beyond ASCII, shared shape across the Times family and
# the Helvetica family. These are the ones that appear in real contracts.
_TIMES_EXTRA = {
    "£": 500, "—": 1000, "–": 500, "‘": 333, "’": 333,
    "“": 444, "”": 444, "•": 350, "§": 500, "©": 760,
    "°": 400, "×": 564, "®": 760, "¶": 453, "€": 500,
    "½": 750, "¼": 750, "…": 1000, " ": 250,
}
_HELV_EXTRA = {
    "£": 556, "—": 1000, "–": 556, "‘": 222, "’": 222,
    "“": 333, "”": 333, "•": 350, "§": 556, "©": 737,
    "°": 400, "×": 584, "®": 737, "¶": 537, "€": 556,
    "½": 834, "¼": 834, "…": 1000, " ": 278,
}


@dataclass(frozen=True)
class Font:
    name: str
    widths: dict[str, int]
    default_width: int

    def width(self, text: str, size: float) -> float:
        w = self.widths
        d = self.default_width
        return sum(w.get(ch, d) for ch in text) * size / 1000.0


TIMES = _mk(
    "Times-Roman",
    {
        **_row(_PUNCT, [250, 333, 408, 500, 500, 833, 778, 333, 333, 333, 500, 564, 250, 333, 250, 278]),
        **_row(_DIGITS, [500] * 10),
        **_row(_MID, [278, 278, 564, 564, 564, 444, 921]),
        **_row(_UPPER, [722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889,
                        722, 722, 556, 722, 667, 556, 611, 722, 722, 944, 722, 722, 611]),
        **_row(_BRACKET, [333, 278, 333, 469, 500, 333]),
        **_row(_LOWER, [444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500, 278, 778,
                        500, 500, 500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444]),
        **_row(_TAIL, [480, 200, 480, 541]),
        **_TIMES_EXTRA,
    },
    500,
)

TIMES_BOLD = _mk(
    "Times-Bold",
    {
        **_row(_PUNCT, [250, 333, 555, 500, 500, 1000, 833, 333, 333, 333, 500, 570, 250, 333, 250, 278]),
        **_row(_DIGITS, [500] * 10),
        **_row(_MID, [333, 333, 570, 570, 570, 500, 930]),
        **_row(_UPPER, [722, 667, 722, 722, 667, 611, 778, 778, 389, 500, 778, 667, 944,
                        722, 778, 611, 778, 722, 556, 667, 722, 722, 1000, 722, 722, 667]),
        **_row(_BRACKET, [333, 278, 333, 581, 500, 333]),
        **_row(_LOWER, [500, 556, 444, 556, 444, 333, 500, 556, 278, 333, 556, 278, 833,
                        556, 500, 556, 556, 444, 389, 333, 556, 500, 722, 500, 500, 444]),
        **_row(_TAIL, [394, 220, 394, 520]),
        **_TIMES_EXTRA,
    },
    500,
)

TIMES_ITALIC = _mk(
    "Times-Italic",
    {
        **_row(_PUNCT, [250, 333, 420, 500, 500, 833, 778, 333, 333, 333, 500, 675, 250, 333, 250, 278]),
        **_row(_DIGITS, [500] * 10),
        **_row(_MID, [333, 333, 675, 675, 675, 500, 920]),
        **_row(_UPPER, [611, 611, 667, 722, 611, 611, 722, 722, 333, 444, 667, 556, 833,
                        667, 722, 611, 722, 611, 500, 556, 722, 611, 833, 611, 556, 556]),
        **_row(_BRACKET, [389, 278, 389, 422, 500, 333]),
        **_row(_LOWER, [500, 500, 444, 500, 444, 278, 500, 500, 278, 278, 444, 278, 722,
                        500, 500, 500, 500, 389, 389, 278, 500, 444, 667, 444, 444, 389]),
        **_row(_TAIL, [400, 275, 400, 541]),
        **_TIMES_EXTRA,
    },
    500,
)

HELV = _mk(
    "Helvetica",
    {
        **_row(_PUNCT, [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278]),
        **_row(_DIGITS, [556] * 10),
        **_row(_MID, [278, 278, 584, 584, 584, 556, 1015]),
        **_row(_UPPER, [667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
                        722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]),
        **_row(_BRACKET, [278, 278, 278, 469, 556, 333]),
        **_row(_LOWER, [556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833,
                        556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500]),
        **_row(_TAIL, [334, 260, 334, 584]),
        **_HELV_EXTRA,
    },
    556,
)

HELV_BOLD = _mk(
    "Helvetica-Bold",
    {
        **_row(_PUNCT, [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278]),
        **_row(_DIGITS, [556] * 10),
        **_row(_MID, [333, 333, 584, 584, 584, 611, 975]),
        **_row(_UPPER, [722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
                        722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]),
        **_row(_BRACKET, [333, 278, 333, 584, 556, 333]),
        **_row(_LOWER, [556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889,
                        611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500]),
        **_row(_TAIL, [389, 280, 389, 584]),
        **_HELV_EXTRA,
    },
    556,
)

COURIER = _mk("Courier", {}, 600)

FONTS: dict[str, Font] = {
    f.name: f for f in (TIMES, TIMES_BOLD, TIMES_ITALIC, HELV, HELV_BOLD, COURIER)
}
_FONT_KEYS = {name: f"F{i + 1}" for i, name in enumerate(FONTS)}


# --- WinAnsi encoding ------------------------------------------------------
#
# The base-14 fonts are used with WinAnsiEncoding, so a page's bytes are
# Latin-1 with Microsoft's substitutions in 0x80-0x9F. Legal prose needs
# exactly those substitutions: curly quotes and dashes come out of every word
# processor, and a contract rendered with `â€”` where an em dash belongs is not
# a document anyone will sign.

_WINANSI = {
    "€": 0x80, "‚": 0x82, "ƒ": 0x83, "„": 0x84, "…": 0x85,
    "†": 0x86, "‡": 0x87, "ˆ": 0x88, "‰": 0x89, "Š": 0x8A,
    "‹": 0x8B, "Œ": 0x8C, "Ž": 0x8E, "‘": 0x91, "’": 0x92,
    "“": 0x93, "”": 0x94, "•": 0x95, "–": 0x96, "—": 0x97,
    "˜": 0x98, "™": 0x99, "š": 0x9A, "›": 0x9B, "œ": 0x9C,
    "ž": 0x9E, "Ÿ": 0x9F,
}

# Where a character has no WinAnsi code but an unambiguous plain-text
# equivalent, use it rather than dropping information. A legal document that
# silently loses a character is worse than one that shows a rougher glyph.
_TRANSLITERATE = {
    "−": "-", "‐": "-", "‑": "-", "‒": "-", "―": "-",
    "­": "", "​": "", "﻿": "", "′": "'", "″": '"',
    "≤": "<=", "≥": ">=", "≠": "!=", "×": "x", "÷": "/",
    "→": "->", "⇒": "=>", "←": "<-", "·": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
}

_unmapped: dict[str, int] = {}


def unmapped() -> dict[str, int]:
    """Characters this process could not encode, and how often.

    Read after rendering. A non-empty result means the PDF shows `?` where the
    source had a character, which in a legal document is a defect and not a
    cosmetic one -- so it is counted and reported rather than swallowed.
    """
    return dict(_unmapped)


def reset_unmapped() -> None:
    _unmapped.clear()


def normalise(text: str) -> str:
    """Apply the transliterations, so measuring and encoding agree.

    Width measurement happens on the normalised string and so does encoding;
    if they disagreed, justified text would be mis-spaced.
    """
    if not any(ch in _TRANSLITERATE for ch in text):
        return text
    return "".join(_TRANSLITERATE.get(ch, ch) for ch in text)


def encode(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        o = ord(ch)
        if o == 0x0A or o == 0x0D:
            out.append(0x20)
        elif o < 0x80 or 0xA0 <= o <= 0xFF:
            out.append(o)
        elif ch in _WINANSI:
            out.append(_WINANSI[ch])
        else:
            _unmapped[ch] = _unmapped.get(ch, 0) + 1
            out.append(0x3F)  # '?'
    return bytes(out)


def _literal(text: str) -> bytes:
    """A PDF literal string for a *content stream*: WinAnsi, delimiters escaped.

    Only valid inside a page's content stream, where the bytes are interpreted
    through the font's `/WinAnsiEncoding`. Document metadata is a different
    string type with a different encoding -- see `_text_string`.
    """
    b = encode(text)
    b = b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + b + b")"


def _text_string(text: str) -> bytes:
    """A PDF *text string*, for `/Title`, `/Author`, `/Subject` and friends.

    These are not WinAnsi. The spec gives them PDFDocEncoding or UTF-16BE, and
    the two differ exactly where legal titles live: an em dash is 0x97 in
    WinAnsi and Scaron in PDFDocEncoding, so writing document metadata through
    `_literal` turned "Employment Terms — Annex C" into "Employment Terms Š
    Annex C" in every reader. Verified with `pdfinfo` against this writer's own
    output before it was fixed.

    ASCII goes out as a literal, which stays readable in a hex dump; anything
    else goes out as UTF-16BE with a byte-order mark, which every reader
    understands and which can represent any title at all.
    """
    if all(ord(c) < 0x80 for c in text):
        b = text.encode("ascii")
        b = b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        return b"(" + b + b")"
    return b"<" + (b"\xfe\xff" + text.encode("utf-16-be")).hex().encode("ascii") + b">"


# --- page canvas -----------------------------------------------------------


@dataclass
class Canvas:
    """One page's content stream, built as a list of operators."""

    width: float
    height: float
    ops: list[bytes] = field(default_factory=list)

    def text(
        self,
        x: float,
        y: float,
        s: str,
        font: Font = TIMES,
        size: float = 11.0,
        color: tuple[float, float, float] = (0, 0, 0),
        word_space: float = 0.0,
    ) -> None:
        """Draw one line of text with its baseline at `y`.

        `word_space` is PDF's `Tw`, which adds a fixed amount to every space
        glyph. That is how a justified line is set: measure the natural width,
        divide the slack by the number of spaces, and let the reader do the
        distribution. Doing it by positioning each word individually would
        produce the same picture and a much larger file.
        """
        if not s:
            return
        self.ops.append(b"BT")
        if color != (0, 0, 0):
            self.ops.append(b"%s %s %s rg" % tuple(_num(c) for c in color))
        self.ops.append(
            b"/%s %s Tf" % (_FONT_KEYS[font.name].encode(), _num(size))
        )
        if word_space:
            self.ops.append(b"%s Tw" % _num(word_space))
        self.ops.append(b"%s %s Td" % (_num(x), _num(y)))
        self.ops.append(_literal(s) + b" Tj")
        if word_space:
            self.ops.append(b"0 Tw")
        if color != (0, 0, 0):
            self.ops.append(b"0 0 0 rg")
        self.ops.append(b"ET")

    def line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        width: float = 0.5,
        color: tuple[float, float, float] = (0, 0, 0),
    ) -> None:
        self.ops.append(b"q")
        self.ops.append(b"%s %s %s RG" % tuple(_num(c) for c in color))
        self.ops.append(b"%s w" % _num(width))
        self.ops.append(b"%s %s m %s %s l S" % (_num(x0), _num(y0), _num(x1), _num(y1)))
        self.ops.append(b"Q")

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: tuple[float, float, float] | None = None,
        stroke: tuple[float, float, float] | None = None,
        line_width: float = 0.5,
    ) -> None:
        if fill is None and stroke is None:
            return
        self.ops.append(b"q")
        if fill is not None:
            self.ops.append(b"%s %s %s rg" % tuple(_num(c) for c in fill))
        if stroke is not None:
            self.ops.append(b"%s %s %s RG" % tuple(_num(c) for c in stroke))
            self.ops.append(b"%s w" % _num(line_width))
        self.ops.append(b"%s %s %s %s re" % (_num(x), _num(y), _num(w), _num(h)))
        if fill is not None and stroke is not None:
            self.ops.append(b"B")
        elif fill is not None:
            self.ops.append(b"f")
        else:
            self.ops.append(b"S")
        self.ops.append(b"Q")

    def stream(self) -> bytes:
        return b"\n".join(self.ops)


def _num(v: float) -> bytes:
    """Format a number for a content stream: short, and never in exponent form.

    Determinism depends on this: `repr(float)` varies in trailing digits across
    values that are visually identical, and an exponent form is not valid PDF
    syntax at all.
    """
    if v == int(v):
        return b"%d" % int(v)
    return (b"%.3f" % v).rstrip(b"0").rstrip(b".")


# --- the file --------------------------------------------------------------

PT = 1.0
MM = 72.0 / 25.4
A4 = (595.276, 841.890)
LETTER = (612.0, 792.0)


def build(
    pages: list[Canvas],
    *,
    title: str = "",
    author: str = "",
    subject: str = "",
    creation_date: str = "",
) -> bytes:
    """Assemble pages into a PDF file.

    `creation_date` is a PDF date string (`D:YYYYMMDDHHmmSS`) supplied by the
    caller, never read from the clock, because a timestamp is the one thing
    that would make otherwise identical renders differ. `render_document`
    derives it from the document's effective date.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_ids: dict[str, int] = {}
    for name in FONTS:
        font_ids[name] = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /%s "
            b"/Encoding /WinAnsiEncoding >>" % name.encode()
        )

    resources = b"<< /Font << " + b" ".join(
        b"/%s %d 0 R" % (_FONT_KEYS[n].encode(), font_ids[n]) for n in FONTS
    ) + b" >> >>"

    pages_id = len(objects) + 1 + 2 * len(pages)  # content + page objects follow
    page_ids: list[int] = []
    for canvas in pages:
        raw = canvas.stream()
        packed = zlib.compress(raw, 9)
        content_id = add(
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(packed)
            + packed
            + b"\nendstream"
        )
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] "
                b"/Resources %s /Contents %d 0 R >>"
                % (pages_id, _num(canvas.width), _num(canvas.height), resources, content_id)
            )
        )

    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    actual_pages_id = add(
        b"<< /Type /Pages /Count %d /Kids [%s] >>" % (len(page_ids), kids)
    )
    assert actual_pages_id == pages_id, "page tree id must match the /Parent written above"

    info_parts = [b"/Producer " + _text_string("lks \u2014 legal knowledge system")]
    if title:
        info_parts.append(b"/Title " + _text_string(title))
    if author:
        info_parts.append(b"/Author " + _text_string(author))
    if subject:
        info_parts.append(b"/Subject " + _text_string(subject))
    if creation_date:
        info_parts.append(b"/CreationDate " + _text_string(creation_date))
    info_id = add(b"<< " + b" ".join(info_parts) + b" >>")
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i
        out += body
        out += b"\nendobj\n"

    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog_id,
        info_id,
        xref,
    )
    return bytes(out)


# --- flowing layout -------------------------------------------------------


@dataclass
class Style:
    page: tuple[float, float] = A4
    margin_left: float = 25 * MM
    margin_right: float = 22 * MM
    margin_top: float = 22 * MM
    margin_bottom: float = 20 * MM

    body_font: Font = TIMES
    body_size: float = 10.5
    body_leading: float = 14.2

    marker_font: Font = TIMES_BOLD
    heading_font: Font = TIMES_BOLD
    chrome_font: Font = HELV
    mono_font: Font = COURIER

    h1_size: float = 16.0
    h2_size: float = 11.5
    h3_size: float = 10.5

    justify: bool = True
    """Justify body text, as printed contracts are.

    Safe without hyphenation only because the measure is wide and the type is
    small; `MAX_STRETCH` below refuses to justify a line that would need
    obvious rivers, and sets it ragged instead.
    """

    footer: str = ""
    header: str = ""


MAX_STRETCH = 3.2
"""Point of extra space per gap beyond which a line is set ragged instead.

Justification distributes a line's slack across its spaces. When a line has few
spaces and a lot of slack -- the last line before a long unbreakable token, or
a short line in a narrow cell -- the result is a row of visible gaps. Past this
threshold ragged-right is simply better looking, so the line is left alone.
"""


@dataclass
class Layout:
    """A single-column flowing typesetter.

    Blocks are appended in reading order and pages break as they fill. The
    only cleverness is `keep_with_next`, which holds a heading or a clause
    marker back to the next page rather than leaving it stranded at the foot
    of this one.
    """

    style: Style = field(default_factory=Style)
    pages: list[Canvas] = field(default_factory=list)
    y: float = 0.0
    page_no: int = 0
    _page_labels: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.pages:
            self.new_page()

    # -- geometry
    @property
    def left(self) -> float:
        return self.style.margin_left

    @property
    def right(self) -> float:
        return self.style.page[0] - self.style.margin_right

    @property
    def measure(self) -> float:
        return self.right - self.left

    @property
    def bottom(self) -> float:
        return self.style.margin_bottom

    @property
    def canvas(self) -> Canvas:
        return self.pages[-1]

    def new_page(self) -> None:
        self.pages.append(Canvas(*self.style.page))
        self.page_no += 1
        self.y = self.style.page[1] - self.style.margin_top
        if self.style.header:
            self.canvas.text(
                self.left, self.style.page[1] - self.style.margin_top + 10,
                self.style.header, self.style.chrome_font, 7.5, (0.45, 0.45, 0.45),
            )

    def space(self, h: float) -> None:
        if self.y - h < self.bottom:
            self.new_page()
        else:
            self.y -= h

    def need(self, h: float) -> None:
        """Break the page unless `h` points remain."""
        if self.y - h < self.bottom:
            self.new_page()

    # -- line breaking
    def wrap(self, text: str, font: Font, size: float, width: float) -> list[str]:
        """Greedy line breaking. A token longer than the measure is hard-split.

        Greedy rather than Knuth-Plass: the corpus's clause bodies are three to
        six lines and the difference is invisible at that length, while the
        failure mode of a paragraph algorithm -- a whole clause reflowing
        because one word changed -- would make the output less stable, which is
        the property this renderer is for.
        """
        text = normalise(text)
        words = text.split()
        if not words:
            return []
        lines: list[str] = []
        cur = ""
        for w in words:
            probe = w if not cur else cur + " " + w
            if font.width(probe, size) <= width or not cur:
                if font.width(probe, size) > width and not cur:
                    # single token wider than the measure: split it
                    piece = ""
                    for ch in w:
                        if font.width(piece + ch, size) > width and piece:
                            lines.append(piece)
                            piece = ch
                        else:
                            piece += ch
                    cur = piece
                    continue
                cur = probe
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def _draw_lines(
        self,
        lines: list[str],
        x: float,
        width: float,
        font: Font,
        size: float,
        leading: float,
        justify: bool,
        color: tuple[float, float, float] = (0, 0, 0),
    ) -> None:
        for i, ln in enumerate(lines):
            self.need(leading)
            last = i == len(lines) - 1
            ws = 0.0
            if justify and not last:
                gaps = ln.count(" ")
                if gaps:
                    slack = width - font.width(ln, size)
                    per = slack / gaps
                    if 0 < per <= MAX_STRETCH:
                        ws = per
            self.y -= leading
            self.canvas.text(x, self.y, ln, font, size, color, word_space=ws)

    # -- blocks
    def para(
        self,
        text: str,
        *,
        font: Font | None = None,
        size: float | None = None,
        leading: float | None = None,
        indent: float = 0.0,
        space_before: float = 0.0,
        space_after: float = 0.0,
        justify: bool | None = None,
        color: tuple[float, float, float] = (0, 0, 0),
    ) -> None:
        font = font or self.style.body_font
        size = size or self.style.body_size
        leading = leading or self.style.body_leading
        if space_before:
            self.space(space_before)
        lines = self.wrap(text, font, size, self.measure - indent)
        just = self.style.justify if justify is None else justify
        self._draw_lines(
            lines, self.left + indent, self.measure - indent,
            font, size, leading, just, color,
        )
        if space_after:
            self.space(space_after)

    def heading(self, text: str, level: int = 2, *, space_before: float = 0.0) -> None:
        st = self.style
        size = {1: st.h1_size, 2: st.h2_size, 3: st.h3_size}.get(level, st.h2_size)
        lead = size * 1.28
        self.space(space_before or (14 if level <= 2 else 10))
        # Keep the heading with at least two lines of what follows it.
        self.need(lead + 2 * st.body_leading)
        self._draw_lines(
            self.wrap(text, st.heading_font, size, self.measure),
            self.left, self.measure, st.heading_font, size, lead, False,
        )
        self.space(level == 1 and 8 or 4)

    def clause(self, marker: str, body: str, *, hang: float | None = None) -> None:
        """A numbered clause: bold marker, body hanging off a fixed indent.

        The marker and the body's first line are laid out as one unit so a page
        never breaks between `**C-4.2**` and the sentence it introduces -- which
        is the single most important typographic rule for a contract, because a
        clause number stranded at the foot of a page reads as a missing clause.
        """
        st = self.style
        hang = st.body_size * 3.4 if hang is None else hang
        self.space(7)
        self.need(2 * st.body_leading)
        lines = self.wrap(body, st.body_font, st.body_size, self.measure - hang)
        if not lines:
            lines = [""]
        self.y -= st.body_leading
        self.canvas.text(self.left, self.y, marker, st.marker_font, st.body_size)
        first = lines[0]
        ws = 0.0
        if st.justify and len(lines) > 1:
            gaps = first.count(" ")
            if gaps:
                per = (self.measure - hang - st.body_font.width(first, st.body_size)) / gaps
                if 0 < per <= MAX_STRETCH:
                    ws = per
        self.canvas.text(
            self.left + hang, self.y, first, st.body_font, st.body_size, word_space=ws
        )
        self._draw_lines(
            lines[1:], self.left + hang, self.measure - hang,
            st.body_font, st.body_size, st.body_leading, st.justify,
        )

    def rule(self, *, space: float = 8.0, color: tuple[float, float, float] = (0.7, 0.7, 0.7)) -> None:
        self.space(space)
        self.need(2)
        self.canvas.line(self.left, self.y, self.right, self.y, 0.5, color)
        self.space(space)

    def table(
        self,
        rows: list[list[str]],
        widths: list[float],
        *,
        header: bool = True,
        size: float | None = None,
        leading: float | None = None,
        zebra: bool = False,
    ) -> None:
        """A simple grid. `widths` are fractions of the measure.

        Cells wrap. A row is drawn as a unit, so a row never splits across a
        page; a row taller than a page is the caller's problem and will
        overflow rather than silently lose lines.
        """
        st = self.style
        size = size or st.body_size - 1.0
        leading = leading or size * 1.32
        total = sum(widths) or 1.0
        cols = [self.measure * w / total for w in widths]
        pad = 4.0

        for r, row in enumerate(rows):
            font = st.marker_font if (header and r == 0) else st.body_font
            cells = [
                self.wrap(str(c), font, size, cols[i] - 2 * pad)
                for i, c in enumerate(row[: len(cols)])
            ]
            height = max((len(c) for c in cells), default=1) * leading + pad
            self.need(height + 2)
            top = self.y
            if zebra and not (header and r == 0) and r % 2 == 0:
                self.canvas.rect(
                    self.left, top - height, self.measure, height, fill=(0.965, 0.965, 0.965)
                )
            x = self.left
            for i, lines in enumerate(cells):
                yy = top - leading
                for ln in lines:
                    self.canvas.text(x + pad, yy, ln, font, size)
                    yy -= leading
                x += cols[i]
            self.y = top - height
            if header and r == 0:
                self.canvas.line(self.left, self.y, self.right, self.y, 0.7, (0.3, 0.3, 0.3))
            elif r < len(rows) - 1:
                self.canvas.line(self.left, self.y, self.right, self.y, 0.25, (0.82, 0.82, 0.82))
        self.space(4)

    def kv(self, label: str, value: str, *, label_width: float = 118.0) -> None:
        st = self.style
        self.need(st.body_leading)
        lines = self.wrap(value, st.body_font, st.body_size, self.measure - label_width)
        self.y -= st.body_leading
        self.canvas.text(
            self.left, self.y, label, st.chrome_font, st.body_size - 1.0, (0.35, 0.35, 0.35)
        )
        if lines:
            self.canvas.text(self.left + label_width, self.y, lines[0], st.body_font, st.body_size)
        self._draw_lines(
            lines[1:], self.left + label_width, self.measure - label_width,
            st.body_font, st.body_size, st.body_leading, False,
        )

    def mono(self, text: str, *, size: float = 8.2, indent: float = 10.0) -> None:
        """Pre-formatted text: never wrapped, never justified, clipped to the
        measure. Used for compiler diagnostics and input vectors, where a
        reflowed line is a changed line."""
        lead = size * 1.24
        for raw in normalise(text).splitlines() or [""]:
            self.need(lead)
            self.y -= lead
            ln = raw
            while self.style.mono_font.width(ln, size) > self.measure - indent and len(ln) > 1:
                ln = ln[:-1]
            self.canvas.text(self.left + indent, self.y, ln, self.style.mono_font, size)

    def bullet(self, text: str, *, marker: str = "•", indent: float = 12.0) -> None:
        st = self.style
        hang = indent + 9.0
        lines = self.wrap(text, st.body_font, st.body_size, self.measure - hang)
        if not lines:
            return
        self.need(st.body_leading)
        self.y -= st.body_leading
        self.canvas.text(self.left + indent, self.y, marker, st.body_font, st.body_size)
        self.canvas.text(self.left + hang, self.y, lines[0], st.body_font, st.body_size)
        self._draw_lines(
            lines[1:], self.left + hang, self.measure - hang,
            st.body_font, st.body_size, st.body_leading, False,
        )

    def page_break(self) -> None:
        self.new_page()

    # -- finishing
    def paginate(self, *, first_page_number: bool = False) -> None:
        """Stamp footers. Called last, because it needs the total page count."""
        total = len(self.pages)
        for i, canvas in enumerate(self.pages, start=1):
            if i == 1 and not first_page_number:
                continue
            label = f"{i} of {total}"
            st = self.style
            if st.footer:
                canvas.text(
                    st.margin_left, st.margin_bottom - 12, st.footer,
                    st.chrome_font, 7.5, (0.45, 0.45, 0.45),
                )
            w = st.chrome_font.width(label, 7.5)
            canvas.text(
                st.page[0] - st.margin_right - w, st.margin_bottom - 12, label,
                st.chrome_font, 7.5, (0.45, 0.45, 0.45),
            )

    def to_bytes(self, **info: Any) -> bytes:
        return build(self.pages, **info)

    def write(self, path: str | Path, **info: Any) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.to_bytes(**info))
        return p


def pdf_date(iso_date: str) -> str:
    """A PDF date string from a plain `YYYY-MM-DD`, at midnight UTC.

    Derived from the document's own effective date rather than the clock, so
    two renders of the same document are byte-identical. An unparseable date
    yields an empty string and `/CreationDate` is then omitted entirely, which
    is valid and still deterministic.
    """
    s = (iso_date or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        y, m, d = s[:4], s[5:7], s[8:10]
        if y.isdigit() and m.isdigit() and d.isdigit():
            return f"D:{y}{m}{d}000000Z"
    return ""
