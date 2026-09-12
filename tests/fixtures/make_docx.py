"""Generate the .docx test fixture with the standard library only.

A .docx is a zip of XML parts, so building one needs nothing but `zipfile`
(which is also how `lks.extract` reads one back). Generating the fixture here
rather than committing an opaque binary means the test can say exactly what
the input contains -- including the two things that matter for a real Word
document:

* headings carried by paragraph *styles* (`Heading1`, `Heading2`), which are
  the only section signal a docx gives, and
* a list whose numbers live in `numbering.xml` and therefore do **not**
  appear in the text, which is why some clause ids have to be synthesised.

Run directly to (re)write the fixture, or call `build()`.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "staff-handbook.docx"

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

# (style, text). style "" is body text; "NumberedList" carries w:numPr, whose
# number Word renders but does not store in the text.
CONTENT: list[tuple[str, str]] = [
    ("Title", "Remote Working Standard"),
    ("", "This Standard is effective from 1 February 2026 and applies to every "
         "Employee whose contract of employment permits remote working."),
    ("Heading1", "1. Eligibility"),
    ("", "1.1 An Employee is eligible to work remotely where their role has been "
         "designated as remote-eligible by the Employee’s department head."),
    ("", "1.2 An Employee in the first 3 months of employment may work remotely "
         "for no more than 2 days in any Payroll Week."),
    ("Heading1", "2. Equipment allowance"),
    ("", "2.1 An eligible Employee is entitled to a one-off equipment allowance "
         "of £350, payable on production of receipts."),
    ("", "2.2 By way of exception to 2.1, an Employee whose role requires a "
         "second display is entitled to a further £120."),
    ("Heading2", "Conditions"),
    ("NumberedList", "The allowance is payable once in any 36-month period."),
    ("NumberedList", "Equipment purchased with the allowance remains the "
                     "property of the Company."),
    ("Heading1", "3. Working hours"),
    ("", "3.1 An Employee working remotely must be contactable during the core "
         "hours of 10:00 to 16:00 local time."),
    ("", "An Employee who cannot be contactable during core hours must agree "
         "alternative hours with their line manager in advance."),
]

CORE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
  xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Remote Working Standard</dc:title>
  <dc:creator>People Operations</dc:creator>
  <cp:lastModifiedBy>People Operations</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">2025-11-03T09:14:00Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">2026-01-12T16:02:00Z</dcterms:modified>
  <cp:version>2.1</cp:version>
</cp:coreProperties>
"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels"
    ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Target="word/document.xml"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>
  <Relationship Id="rId2" Target="docProps/core.xml"
    Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"/>
</Relationships>
"""


def _para(style: str, text: str) -> str:
    props = []
    if style == "NumberedList":
        props.append('<w:pStyle w:val="ListParagraph"/>')
        props.append('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>')
    elif style:
        props.append(f'<w:pStyle w:val="{style}"/>')
    ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    # split on the deliberate soft break so the fixture also exercises w:br
    runs = "".join(
        f'<w:r><w:t xml:space="preserve">{escape(chunk)}</w:t></w:r>'
        if i == 0 else f"<w:r><w:br/><w:t>{escape(chunk)}</w:t></w:r>"
        for i, chunk in enumerate(text.split("\n"))
    )
    return f"<w:p>{ppr}{runs}</w:p>"


def build(path: str | Path = DEFAULT_PATH) -> Path:
    path = Path(path)
    body = "".join(_para(style, text) for style, text in CONTENT)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<w:document {W}><w:body>{body}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
        "</w:body></w:document>\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # fixed timestamps so regenerating the fixture is byte-reproducible
        for name, data in (("[Content_Types].xml", CONTENT_TYPES),
                           ("_rels/.rels", RELS),
                           ("word/document.xml", document),
                           ("docProps/core.xml", CORE_XML)):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 12, 16, 2, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return path


if __name__ == "__main__":
    print(build())
