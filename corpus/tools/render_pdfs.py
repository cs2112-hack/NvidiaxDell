#!/usr/bin/env python3
"""Render corpus markdown documents to typeset legal PDFs for the demo.

pandoc (markdown -> HTML with the legal template) + headless Chrome (HTML -> PDF).
Both already on the box; no LaTeX, no new dependency.
"""
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = Path(__file__).resolve().parent / "legal.html"
OUT = ROOT / "pdf"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SRC_DIRS = ["raw/synthetic", "raw/real", "raw/templates"]


def main():
    OUT.mkdir(exist_ok=True)
    only = sys.argv[1:]
    docs = [p for d in SRC_DIRS for p in sorted((ROOT / d).glob("*.md"))
            if not only or p.stem in only]
    if not docs:
        print("no markdown documents found", file=sys.stderr)
        return 1
    for md in docs:
        html = OUT / f".{md.stem}.html"
        pdf = OUT / f"{md.stem}.pdf"
        subprocess.run(["pandoc", str(md), "--from", "markdown+yaml_metadata_block",
                        "--to", "html5", "--standalone", "--template", str(TPL), "-o", str(html)], check=True)
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        "--virtual-time-budget=3000", f"--print-to-pdf={pdf}",
                        html.resolve().as_uri()],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        html.unlink()
        print(f"ok    {pdf.name} ({pdf.stat().st_size // 1024} KB)")
    print(f"\n{len(docs)} PDFs in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
