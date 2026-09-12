#!/usr/bin/env python3
"""Rebuild the committed vector index from the corpus."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lks.vector import build_index

if __name__ == "__main__":
    m = build_index()
    print(json.dumps({k: v for k, v in m.items() if k != "corpus"}, indent=2))
    print(f"indexed {m['n_chunks']} chunks, dim {m['dim']}")
    print(f"corpus aggregate {m['corpus']['aggregate']}")
