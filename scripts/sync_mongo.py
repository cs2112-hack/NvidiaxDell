#!/usr/bin/env python3
"""Load the committed vector index into MongoDB.

Run after `build_index.py`, and after any `git checkout` that changes the
corpus or the index -- the store refuses to answer until you do, rather than
serving another commit's index.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lks.mongo_store import MongoUnavailable, MongoVectorStore, sync

if __name__ == "__main__":
    try:
        print(json.dumps(sync(), indent=2))
        s = MongoVectorStore.open()
        print(json.dumps(s.status(), indent=2))
        s.close()
    except MongoUnavailable as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
