#!/usr/bin/env python3
"""Pull a few short, clause-dense EX-10 exhibits from SEC EDGAR full-text search.

Free keyless JSON API (efts.sec.gov). SEC caps automated traffic at 10 req/s and
requires a descriptive User-Agent; this script sleeps 0.5s between requests.
Searches for the *clause* we want to encode, not the contract type.
"""
import json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_real import to_text  # reuse the html->text stripper

OUT = Path(__file__).resolve().parent.parent / "raw" / "real" / "edgar"
UA = "corpus-build/1.0 (legal knowledge system demo; sheikhgaye28@gmail.com)"
QUERIES = ['"shall not exceed the fees paid"', '"service credit"', '"limitation of liability"']
MAX_CHARS = 90_000   # keep exhibits short enough to encode by hand
WANT = 4


def get(url):
    time.sleep(0.5)
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read()


def search(q):
    url = "https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(
        {"q": q, "forms": "8-K", "dateRange": "custom",
         "startdt": "2023-01-01", "enddt": "2025-12-31"})
    return json.loads(get(url))["hits"]["hits"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    kept, seen = 0, set()
    for q in QUERIES:
        try:
            hits = search(q)
        except Exception as e:  # noqa: BLE001
            print(f"FAIL search {q}: {e}", file=sys.stderr)
            continue
        for h in hits:
            if kept >= WANT:
                print(f"\n{kept} exhibits saved to {OUT}")
                return 0
            acc, _, fname = h["_id"].partition(":")
            src = h.get("_source", {})
            if not re.match(r"EX-10", (src.get("file_type") or ""), re.I):
                continue
            cik = (src.get("ciks") or ["0"])[0].lstrip("0")
            key = (acc, fname)
            if key in seen:
                continue
            seen.add(key)
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{fname}"
            try:
                txt = to_text(get(url))
            except Exception as e:  # noqa: BLE001
                print(f"FAIL doc {url}: {e}", file=sys.stderr)
                continue
            if len(txt) > MAX_CHARS or len(txt) < 3_000:
                print(f"skip  {fname} ({len(txt)} chars)")
                continue
            co = re.sub(r"[^a-z0-9]+", "_", (src.get("display_names") or ["unknown"])[0].lower())[:40]
            dest = OUT / f"edgar_{co}_{acc}_{Path(fname).stem}.txt"
            dest.write_text(
                f"SOURCE: {url}\nFORM: {src.get('root_form')} {src.get('file_type')}\n"
                f"FILED: {src.get('file_date')}\nMATCHED QUERY: {q}\n"
                f"LICENSE: public SEC disclosure (EDGAR); underlying contract publicly filed\n\n{txt}\n")
            print(f"ok    {dest.name} ({len(txt)} chars)")
            kept += 1
    print(f"\n{kept} exhibits saved to {OUT}")
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main())
