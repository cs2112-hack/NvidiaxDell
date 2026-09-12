#!/usr/bin/env python3
"""Regenerate catala/registry.yaml from the literate Catala sources."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lks.registry import build_registry, coverage, write_registry

if __name__ == "__main__":
    entries = build_registry()
    write_registry(entries)
    cov = coverage(entries)
    print(f"{len(entries)} scopes registered")
    for k, e in sorted(entries.items()):
        ji = f"  judgement:{e.judgement_inputs}" if e.judgement_inputs else ""
        print(f"  {k:34s} in={len(e.inputs)} out={len(e.outputs)} encodes={len(e.encodes)}{ji}")
    print(f"\ncoverage: {cov['encoded']}/{cov['required']} RULE+HYBRID clauses encoded")
    if cov["missing"]:
        print(f"missing ({len(cov['missing'])}): {cov['missing'][:10]}")
    if cov["duplicated"]:
        print(f"DUPLICATED: {cov['duplicated']}")
