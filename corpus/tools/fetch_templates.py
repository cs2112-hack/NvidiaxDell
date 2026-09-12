#!/usr/bin/env python3
"""Fetch the CC BY 4.0 standard-form templates that the drafting component templates against.

Common Paper and Law Insider publish the full standard terms as HTML under CC BY 4.0, so
those are fetched as text. Bonterms and YC publish PDF/DOCX behind a download page; this
script records the links it finds there instead of guessing asset URLs -- download those
by hand if the demo needs them (YC's marked-up SAFE is CC BY-ND: use as-is, do not
distribute modified versions).
"""
import re, sys, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_real import to_text

OUT = Path(__file__).resolve().parent.parent / "raw" / "templates"
UA = "Mozilla/5.0 corpus-build/1.0 (legal knowledge system demo; sheikhgaye28@gmail.com)"

HTML = {
    "commonpaper_cloud_service_agreement.txt": (
        "https://commonpaper.com/standards/cloud-service-agreement/", "CC BY 4.0 (Common Paper)"),
    "commonpaper_mutual_nda.txt": (
        "https://commonpaper.com/standards/mutual-nda/", "CC BY 4.0 (Common Paper)"),
    "commonpaper_dpa.txt": (
        "https://commonpaper.com/standards/data-processing-agreement/", "CC BY 4.0 (Common Paper)"),
    "onenda.txt": (
        "https://www.lawinsider.com/standards/onenda", "CC BY 4.0 (oneNDA)"),
    "onesaas.txt": (
        "https://www.lawinsider.com/standards/onesaas", "CC BY 4.0 (oneSaaS)"),
}
LINK_PAGES = {
    "bonterms_download_links.txt": "https://bonterms.com/download-center/",
    "yc_safe_links.txt": "https://www.ycombinator.com/documents",
}


def get(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=45).read()


# CC BY-ND 4.0: download and use as-is; do not distribute modified versions.
YC_DOCS = {
    "yc_postmoney_safe_valuation_cap_only.docx":
        "https://bookface-static.ycombinator.com/assets/ycdc/Postmoney Safe - Valuation Cap Only - "
        "FINAL-f2a64add6d21039ab347ee2e7194141a4239e364ffed54bad0fe9cf623bf1691.docx",
    "yc_pro_rata_side_letter.docx":
        "https://bookface-static.ycombinator.com/assets/ycdc/Pro Rata Side "
        "Letter-d6dd8d827741862b18fba0f658da17fb4e787e5f2dda49584b9caea89bf42302.docx",
}


def docx_text(raw: bytes) -> str:
    """A .docx is a zip of XML; pull the paragraph text with stdlib only."""
    import io, zipfile
    xml = zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    txt = re.sub(r"<[^>]+>", "", xml)
    import html as _html
    return re.sub(r"\n{3,}", "\n\n", _html.unescape(txt)).strip()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for name, (url, lic) in HTML.items():
        dest = OUT / name
        if dest.exists():
            print(f"skip  {name}"); continue
        try:
            txt = to_text(get(url))
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {name}: {e}", file=sys.stderr); fail += 1; continue
        dest.write_text(f"SOURCE: {url}\nRETRIEVED: 2026-09-12\nLICENSE: {lic}\n\n{txt}\n")
        print(f"ok    {name} ({len(txt)} chars)"); ok += 1
    for name, url in YC_DOCS.items():
        dest = OUT / name
        if dest.exists():
            print(f"skip  {name}"); continue
        try:
            raw = get(urllib.parse.quote(url, safe=":/"))
            dest.write_bytes(raw)
            (OUT / (name[:-5] + ".txt")).write_text(
                f"SOURCE: {url}\nRETRIEVED: 2026-09-12\n"
                f"LICENSE: CC BY-ND 4.0 (Y Combinator) -- use as-is, do not distribute modified\n\n"
                + docx_text(raw) + "\n")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {name}: {e}", file=sys.stderr); fail += 1; continue
        print(f"ok    {name}"); ok += 1
    for name, url in LINK_PAGES.items():
        try:
            html = get(url).decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {name}: {e}", file=sys.stderr); fail += 1; continue
        links = sorted({u for u in re.findall(r'href="([^"]+\.(?:pdf|docx|doc))"', html, re.I)})
        (OUT / name).write_text(f"SOURCE PAGE: {url}\nRETRIEVED: 2026-09-12\n\n"
                                + "\n".join(links) + "\n")
        print(f"ok    {name} ({len(links)} links)"); ok += 1
    print(f"\n{ok} template artifacts, {fail} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
