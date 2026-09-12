#!/usr/bin/env python3
"""Execute a Catala scope. The adversarial reviewer's only way to run the
artefact -- so it never needs to open the module source.

    ./scripts/probe.py overtime HourPremium '{"ordinal":45,"grade":5, ...}'

Prints the scope's output as JSON, or, if Catala raised, a JSON object with
__error__ set to the error class (Conflict / NoValue / ...) plus the compiler
diagnostic. An error is a legitimate observation, not a crash: a Conflict on
inputs the document answers plainly is exactly the kind of break worth finding.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lks.catala_runner import CatalaError, run_scope

def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    stem, scope = sys.argv[1], sys.argv[2]
    inputs = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    path = Path("catala/modules") / f"{stem}.catala_en"
    if not path.exists():
        print(json.dumps({"__error__": "NoSuchModule", "module": str(path)}))
        return 1
    try:
        print(json.dumps(run_scope(path, scope, inputs), sort_keys=True))
    except CatalaError as e:
        print(json.dumps({"__error__": type(e).__name__,
                          "diagnostic": e.diagnostic[:1500]}, sort_keys=True))
        return 0
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
