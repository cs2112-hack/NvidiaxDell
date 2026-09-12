#!/usr/bin/env bash
# Full gate for the Catala layer. Run this before claiming a module is done.
#   ./scripts/check.sh                 -> check every module
#   ./scripts/check.sh overtime        -> check one module by stem
cd "$(dirname "$0")/.." || exit 1
. scripts/env.sh
set -o pipefail
rc=0

if [ -n "$1" ]; then MODS="catala/modules/$1.catala_en"; else MODS=$(ls catala/modules/*.catala_en 2>/dev/null); fi
[ -z "$MODS" ] && { echo "no modules found"; exit 1; }

echo "== 1. typecheck (--check-invariants) =="
for m in $MODS; do
  if out=$(catala typecheck "$m" --check-invariants 2>&1); then
    echo "   ok    $m"
  else
    echo "   FAIL  $m"; echo "$out" | sed 's/^/         /'; rc=1
  fi
done

echo "== 2. literate fidelity (quotations match the corpus) =="
"$PY" - <<'PYEOF'
import sys
from lks.literate import check_fidelity, parse_literate
from pathlib import Path
ps = check_fidelity()
if ps:
    for p in ps: print(f"   FAIL  {p}")
    sys.exit(1)
n_q = n_b = n_u = 0
for f in sorted(Path('catala/modules').glob('*.catala_en')):
    lf = parse_literate(f)
    n_q += len(lf.quotations); n_b += len(lf.blocks); n_u += len(lf.unattributed)
print(f"   ok    {n_q} quotations, {n_b} blocks, {n_u} declared NO-CLAUSE")
PYEOF
[ $? -ne 0 ] && rc=1

echo "== 3. RULE/HYBRID coverage (every rule clause encoded somewhere) =="
"$PY" - <<'PYEOF'
import sys
from lks.triage import load_ledger, Label
from lks.literate import encoded_refs
led = load_ledger(); enc = encoded_refs()
need = {r for r, d in led.items() if d.label in (Label.RULE, Label.HYBRID)}
missing = sorted(need - set(enc))
dupes = {r: fs for r, fs in enc.items() if len(set(fs)) > 1}
extra = sorted(r for r in enc if r in led and led[r].label is Label.PROSE)
print(f"   {len(need)-len(missing)}/{len(need)} RULE+HYBRID clauses encoded")
if missing: print(f"   pending ({len(missing)}): {missing[:8]}{' ...' if len(missing)>8 else ''}")
if dupes:  print(f"   FAIL  encoded in >1 module: {dupes}"); sys.exit(1)
if extra:  print(f"   note  PROSE clauses also quoted in Catala: {extra[:5]}")
PYEOF
[ $? -ne 0 ] && rc=1

echo "== 4. clerk test (#[test] scope assertions) =="
if out=$(clerk test 2>&1); then
  echo "$out" | grep -E "PASSED|FAILED|tests" | sed 's/^/   /' | tail -6
else
  echo "$out" | tail -25 | sed 's/^/   /'; rc=1
fi

echo "== 5. counterexample regression (permanent, re-run forever) =="
"$PY" - <<'PYEOF'
from lks.reviewer import run_regression
from lks.counterexample import summary
rs = run_regression()
s = summary()
if not rs:
    print("   (no counterexamples recorded yet)")
else:
    bad = [r for r in rs if not r.passed]
    for r in bad: print(f"   FAIL  {r.id}: expected {r.expected!r} got {r.actual!r} {r.detail[:80]}")
    print(f"   {len(rs)-len(bad)}/{len(rs)} counterexamples pass  (store: {s})")
PYEOF

echo "== 6. vector store (files + mongo, both pinned to corpus AND triage) =="
"$PY" - <<'PYEOF'
from lks.vector import VectorStore, IndexStaleError, IndexMissingError
try:
    s = VectorStore.open()
    m = s.manifest
    print(f"   ok    files: {m['n_chunks']} chunks, {m['labels']}, "
          f"corpus {m['corpus']['aggregate']}, triage {m['triage']['aggregate']}")
except (IndexStaleError, IndexMissingError) as e:
    print(f"   FAIL  files: {e}")
    raise SystemExit(1)
try:
    from lks.mongo_store import MongoVectorStore, MongoUnavailable
    ms = MongoVectorStore.open()
    st = ms.status()
    print(f"   ok    mongo: {st['n_chunks']} chunks via {st['search_path']}")
    ms.close()
except MongoUnavailable:
    print("   skip  mongo: not running (start the container, then scripts/sync_mongo.py)")
except IndexStaleError as e:
    print(f"   FAIL  mongo: {e}")
    raise SystemExit(1)
PYEOF
[ $? -ne 0 ] && rc=1

echo "== 7. exception-branch coverage =="
"$PY" - <<'PYEOF'
from pathlib import Path
from lks.reviewer import discover_scopes, exception_branches
from lks.catala_runner import CatalaError
for f in sorted(Path('catala/modules').glob('*.catala_en')):
    for scope, qual in discover_scopes(f).items():
        for var in qual['output'] + qual['internal']:
            try:
                br = exception_branches(f, scope, var)
            except CatalaError:
                continue
            if len(br) > 1:
                print(f"   {f.stem}.{scope}.{var}: {len(br)} branches")
PYEOF

echo
[ $rc -eq 0 ] && echo "GATE: PASS" || echo "GATE: FAIL"
exit $rc
