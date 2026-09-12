#!/usr/bin/env python3
"""Fetch real litigation / discovery documents (Meta-related) as demo PDFs.

Two sources, both public:
  * DocumentCloud public documents (court exhibits, complaints, leaked internal
    Facebook emails from the Six4Three case) -- fetched by explicit document id so
    the set is reproducible rather than search-order dependent.
  * Government-published PDFs (FTC, state AG) -- US government works.

These are demo/retrieval assets: real, long, messy, scanned-or-exported legal PDFs
of the kind the system has to ingest. They are NOT encoded into Catala.
Underlying internal documents remain third-party material; court records are public.
"""
import json, sys, urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "raw" / "discovery"
UA = "corpus-build/1.0 (legal knowledge system demo; sheikhgaye28@gmail.com)"

# DocumentCloud id -> local basename
DC_DOCS = {
    "6429802":  "fb_six4three_internal_emails_engineers_concern",
    "7010041":  "fb_instagram_internal_emails",
    "24397755": "meta_instagram_multistate_ag_complaint_redacted_2024",
    "23869675": "kadrey_v_meta_complaint",
    "23869695": "kadrey_v_meta_complaint_exhibits",
    "25881475": "kadrey_v_meta_motion_for_summary_judgment",
}
DIRECT = {
    "ftc_v_facebook_amended_complaint_redacted":
        "https://www.ftc.gov/system/files/documents/cases/ecf_75-1_ftc_v_facebook_public_redacted_fac.pdf",
}


def get(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90).read()


def save(name, url, meta):
    pdf = OUT / f"{name}.pdf"
    if pdf.exists():
        print(f"skip  {pdf.name}")
        return True
    try:
        body = get(url)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL  {name}: {e}", file=sys.stderr)
        return False
    if not body.startswith(b"%PDF"):
        print(f"FAIL  {name}: not a PDF ({body[:20]!r})", file=sys.stderr)
        return False
    pdf.write_bytes(body)
    (OUT / f"{name}.source.txt").write_text(
        "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n")
    print(f"ok    {pdf.name} ({len(body)//1024} KB)")
    return True


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    for doc_id, name in DC_DOCS.items():
        try:
            d = json.loads(get(f"https://api.www.documentcloud.org/api/documents/{doc_id}/"))
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {name}: metadata: {e}", file=sys.stderr)
            continue
        url = f"https://s3.documentcloud.org/documents/{doc_id}/{d['slug']}.pdf"
        try:  # DocumentCloud publishes extracted text alongside the PDF
            txt_url = f"https://s3.documentcloud.org/documents/{doc_id}/{d['slug']}.txt"
            (OUT / f"{name}.txt").write_text(get(txt_url).decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            print(f"warn  {name}: no extracted text: {e}", file=sys.stderr)
        ok += save(name, url, {
            "SOURCE": f"https://www.documentcloud.org/documents/{doc_id}/",
            "PDF": url, "TITLE": d.get("title", ""), "PAGES": d.get("page_count", ""),
            "DC_SOURCE_FIELD": d.get("source", ""), "DESCRIPTION": (d.get("description") or "").replace("\n", " "),
            "RETRIEVED": "2026-09-12",
            "LICENSE": "public court record / publicly released document; underlying internal "
                       "material is third-party. Internal demo use.",
        })
    for name, url in DIRECT.items():
        try:  # DocumentCloud publishes extracted text alongside the PDF
            txt_url = f"https://s3.documentcloud.org/documents/{doc_id}/{d['slug']}.txt"
            (OUT / f"{name}.txt").write_text(get(txt_url).decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            print(f"warn  {name}: no extracted text: {e}", file=sys.stderr)
        ok += save(name, url, {"SOURCE": url, "RETRIEVED": "2026-09-12",
                               "LICENSE": "US government work (public domain)"})
    print(f"\n{ok} discovery documents in {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
