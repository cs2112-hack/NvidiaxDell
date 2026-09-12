#!/usr/bin/env python3
"""Repair the line numbers in literate citations from the corpus.

Clause line numbers shift whenever a corpus document is edited, and a stale
line number is a real defect -- it sends a reader to the wrong place -- but
it is not a defect anyone should fix by hand. The clause *text* is never
touched here: if the quoted text has drifted, that is a substantive change
and check_fidelity must keep failing until a human looks at it.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lks.literate import CITATION_RE
from lks.segment import load_corpus

def main() -> int:
    clauses = {}
    for doc in load_corpus("corpus"):
        for c in doc.clauses:
            clauses[c.ref] = c

    fixed = total = 0
    for f in sorted(Path("catala/modules").glob("*.catala_en")):
        lines = f.read_text(encoding="utf-8").splitlines()
        changed = False
        for i, line in enumerate(lines):
            m = CITATION_RE.match(line)
            if not m:
                continue
            total += 1
            doc_id, clause_id, fname, lno = m.groups()
            c = clauses.get(f"{doc_id} {clause_id}")
            if c is None:
                print(f"  {f.name}:{i+1}: {doc_id} {clause_id} not in corpus — left alone")
                continue
            want_file = Path(c.source_path).name
            if fname != want_file or int(lno) != c.line_start:
                lines[i] = f"| {doc_id} {clause_id} ({want_file}:{c.line_start})"
                print(f"  {f.name}:{i+1}: {clause_id} {fname}:{lno} -> {want_file}:{c.line_start}")
                fixed += 1
                changed = True
        if changed:
            f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{fixed} citation(s) repaired out of {total}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
