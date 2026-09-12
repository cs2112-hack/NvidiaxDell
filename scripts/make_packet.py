#!/usr/bin/env python3
"""Write a blind review packet for a Catala module.

The packet is the ONLY thing an adversarial reviewer is given: the
authoritative corpus text plus the artefact with implementer voice removed.
Writing it to a file (rather than describing it) is what makes the blind-review
property checkable -- you can read reviewer/packets/<module>.md and confirm for
yourself that no design rationale leaked into it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lks.reviewer import build_catala_packet

def main() -> int:
    if len(sys.argv) < 2:
        print("usage: make_packet.py <module-stem> [...]", file=sys.stderr)
        return 2
    outdir = Path("reviewer/packets")
    outdir.mkdir(parents=True, exist_ok=True)
    for stem in sys.argv[1:]:
        path = Path("catala/modules") / f"{stem}.catala_en"
        if not path.exists():
            print(f"no such module: {path}", file=sys.stderr)
            return 1
        pkt = build_catala_packet(path)
        dest = outdir / f"{stem}.md"
        dest.write_text(pkt.render(), encoding="utf-8")
        print(f"{dest}: {len(pkt.source_clauses.splitlines())} lines of source, "
              f"{pkt.commentary_lines_removed} implementer lines removed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
