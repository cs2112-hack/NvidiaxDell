#!/usr/bin/env python3
"""Fetch the public-domain / license-clear source documents into raw/real/.

Only public government texts are fetched (government-edicts doctrine: Georgia v.
Public.Resource.Org, 590 U.S. 255 (2020)). Everything else in the corpus is either
paraphrased facts or authored synthetic text -- see ../CORPUS.md.

Idempotent: skips files that already exist. Failures are reported, not fatal.
"""
import html, re, sys, urllib.request
from html.parser import HTMLParser
from pathlib import Path

REAL = Path(__file__).resolve().parent.parent / "raw" / "real"
UA = "corpus-build/1.0 (legal knowledge system demo; sheikhgaye28@gmail.com)"

SOURCES = {
    # file name: (url, marker regex to trim the boilerplate nav away)
    "ca_labor_code_246_sick_leave.txt": (
        "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=LAB&sectionNum=246", r"^246\."),
    "ca_labor_code_201_final_pay.txt": (
        "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=LAB&sectionNum=201", r"^201\."),
    "ca_labor_code_202_final_pay_quit.txt": (
        "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=LAB&sectionNum=202", r"^202\."),
    "ca_labor_code_227_3_vacation_payout.txt": (
        "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=LAB&sectionNum=227.3", r"^227\.3\."),
    "wa_rcw_49_46_210_sick_leave.txt": (
        "https://app.leg.wa.gov/RCW/default.aspx?cite=49.46.210", r"^Paid sick leave"),
    "co_crs_8_13_3_403_sick_leave.txt": (
        "https://law.justia.com/codes/colorado/title-8/labor-i/article-13-3/part-4/section-8-13-3-403/", r"8-13\.3-403"),
    "flsa_29_usc_207_overtime.txt": (
        "https://www.law.cornell.edu/uscode/text/29/207", r"\(a\)\s*Employees"),
}


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def to_text(raw: bytes) -> str:
    p = Text()
    p.feed(raw.decode("utf-8", "replace"))
    txt = html.unescape("".join(p.out))
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    txt = re.sub(r"\n\s*\n\s*\n+", "\n\n", txt)
    return "\n".join(l.strip() for l in txt.splitlines()).strip()


def main():
    ok = fail = 0
    for name, (url, marker) in SOURCES.items():
        dest = REAL / name
        if dest.exists():
            print(f"skip  {name}")
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            body = urllib.request.urlopen(req, timeout=30).read()
            txt = to_text(body)
            m = re.search(marker, txt, re.M)
            if m:
                txt = txt[m.start():]
            dest.write_text(f"SOURCE: {url}\nRETRIEVED: 2026-09-12\n"
                            f"LICENSE: public domain (US/state government edict)\n\n{txt}\n")
            print(f"ok    {name} ({len(txt)} chars)")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {name}: {e}", file=sys.stderr)
            fail += 1
    print(f"\n{ok} fetched, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
