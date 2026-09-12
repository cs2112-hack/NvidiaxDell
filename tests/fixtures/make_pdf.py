"""Generate the .pdf test fixture with the standard library only.

Writing a PDF by hand is a few hundred bytes of object graph plus a content
stream of `Tj` operators, so the fixture needs no dependency and no binary
blob checked in that nobody can read. It deliberately includes the two things
that make real PDFs awkward:

* a running header and a `Page n of m` footer on every page, which arrive in
  the text layer as paragraphs and must be recognised as furniture rather
  than attached to a clause, and
* a clause whose sentence is split across a page boundary, and a word broken
  by an end-of-line hyphen.

Run directly to (re)write the fixture, or call `build()`.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "supplier-terms.pdf"

HEADER = "SUPPLIER TERMS OF PURCHASE — CONFIDENTIAL"

# ("h", text) is a heading-shaped line, ("p", text) a paragraph.
CONTENT: list[tuple[str, str]] = [
    ("h", "SUPPLIER TERMS OF PURCHASE"),
    ("p", "These Terms are effective from 1 June 2025 and apply to every purchase "
          "order issued by the Company."),
    ("h", "1. ORDERS"),
    ("p", "1.1 A purchase order is an offer by the Company to buy the goods it "
          "describes on these Terms, and no other terms apply."),
    ("p", "1.2 A purchase order is accepted when the Supplier despatches any part "
          "of the goods it describes or acknowledges it in writing, whichever is "
          "earlier."),
    ("h", "2. PRICE AND PAYMENT"),
    ("p", "2.1 The price for the goods is the price stated in the purchase order "
          "and is exclusive of value added tax."),
    ("p", "2.2 The Company shall pay each undisputed invoice within 45 days of the "
          "end of the month in which the invoice is received."),
    ("p", "2.3 Where the Company fails to pay an undisputed invoice within 45 days, "
          "the Supplier may charge interest at 2% per annum above the base rate of "
          "the Bank of England, calculated daily from the due date until payment."),
    ("h", "3. DELIVERY"),
    ("p", "3.1 The Supplier shall deliver the goods to the delivery address stated "
          "in the purchase order on the delivery date stated in it."),
    ("p", "3.2 Where the Supplier delivers more than 5 working days after the "
          "delivery date, the Company may reject the goods and recover any "
          "pre-payment made in respect of them."),
    ("p", "3.3 Delivery is not complete until the goods have been unloaded at the "
          "delivery address and the Company has signed for them."),
    ("h", "4. WARRANTY"),
    ("p", "4.1 The Supplier warrants that the goods conform to their specification "
          "for a period of 12 months from delivery."),
    ("p", "4.2 Where goods do not conform, the Company may require the Supplier to "
          "replace them at the Supplier's own cost, including the cost of "
          "collection and redelivery."),
    ("p", "4.3 The remedy in 4.2 is in addition to any other remedy available to "
          "the Company."),
    ("h", "5. INDEMNITY AND INSURANCE"),
    ("p", "5.1 The Supplier shall indemnify the Company against any claim brought "
          "by a third party alleging that the goods infringe that third party's "
          "intellectual property rights."),
    ("p", "5.2 The Supplier shall maintain product liability insurance of not less "
          "than GBP 5,000,000 in respect of any one claim, and shall produce the "
          "certificate of insurance to the Company on request."),
    ("h", "6. TERMINATION"),
    ("p", "6.1 The Company may cancel a purchase order in whole or in part at any "
          "time before delivery by written notice to the Supplier."),
    ("p", "6.2 Where the Company cancels a purchase order under 6.1, it shall pay "
          "the Supplier the cost of work properly performed before cancellation, "
          "and no other sum is payable in respect of the cancellation."),
    ("p", "6.3 Either party may terminate any agreement constituted by a purchase "
          "order immediately where the other party becomes insolvent, has an "
          "administrator appointed or ceases to carry on business."),
]

PAGE_W, PAGE_H = 595, 842
LEFT, TOP, BOTTOM = 64, 760, 300
LEADING, PARA_GAP, WRAP = 14, 16, 78


def _esc(s: str) -> str:
    out = s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    # WinAnsi: the pound sign is octal 243
    return out.replace("£", r"\243").replace("—", r"\227") \
              .replace("’", r"\222")


def _layout() -> list[list[tuple[float, str]]]:
    pages: list[list[tuple[float, str]]] = []
    cur: list[tuple[float, str]] = []
    y = TOP
    for kind, text in CONTENT:
        lines = textwrap.wrap(text, WRAP) or [""]
        need = LEADING * len(lines) + PARA_GAP
        if y - need < BOTTOM:
            pages.append(cur)
            cur, y = [], TOP
        for ln in lines:
            cur.append((y, ln))
            y -= LEADING
        y -= PARA_GAP
        if kind == "h":
            y -= 2
    pages.append(cur)
    return pages


def _content_stream(page_lines: list[tuple[float, str]], n: int, total: int) -> str:
    ops = ["BT", "/F1 10 Tf"]
    for y, line in [(812.0, HEADER)] + page_lines + [(48.0, f"Page {n} of {total}")]:
        ops.append(f"1 0 0 1 {LEFT} {y:.1f} Tm ({_esc(line)}) Tj")
    ops.append("ET")
    return "\n".join(ops)


def build(path: str | Path = DEFAULT_PATH) -> Path:
    path = Path(path)
    pages = _layout()
    total = len(pages)
    objs: list[str] = []           # 1-indexed object bodies

    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(total))
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {total} >>")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                "/Encoding /WinAnsiEncoding >>")
    for i, page in enumerate(pages):
        stream = _content_stream(page, i + 1, total)
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>")
        objs.append(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n"
                    f"{stream}\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{xref}\n%%EOF\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


if __name__ == "__main__":
    print(build())
