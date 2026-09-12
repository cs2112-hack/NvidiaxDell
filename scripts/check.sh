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
if missing: print(f"   FAIL  not encoded ({len(missing)}): {missing[:8]}{' ...' if len(missing)>8 else ''}")
if dupes:  print(f"   FAIL  encoded in >1 module: {dupes}")
if extra:  print(f"   note  PROSE clauses also quoted in Catala: {extra[:5]}")
sys.exit(1 if (missing or dupes) else 0)
PYEOF
[ $? -ne 0 ] && rc=1

echo "== 4. clerk test (#[test] scope assertions) =="
# Under the build lock (lks.catala_runner.BUILD_LOCK), so a generation run
# never loads objects this rebuild is halfway through writing.
mkdir -p _build
if out=$(mkdir -p .run && flock -s .run/tree.lock flock _build/.lks-build.lock clerk test 2>&1); then
  echo "$out" | grep -E "PASSED|FAILED|tests" | sed 's/^/   /' | tail -6
else
  echo "$out" | tail -25 | sed 's/^/   /'; rc=1
fi

echo "== 5. counterexample regression (permanent, re-run forever) =="
"$PY" - <<'PYEOF'
import sys
from lks.reviewer import run_regression
from lks.counterexample import summary
rs = run_regression()
s = summary()
bad = [r for r in rs if not r.passed]
if not rs:
    print("   (no counterexamples recorded yet)")
else:
    for r in bad: print(f"   FAIL  {r.id}: expected {r.expected!r} got {r.actual!r} {r.detail[:80]}")
    print(f"   {len(rs)-len(bad)}/{len(rs)} counterexamples pass  (store: {s})")
sys.exit(1 if bad else 0)
PYEOF
[ $? -ne 0 ] && rc=1

echo "== 6. every scope is JSON-executable (no enum-typed outputs) =="
"$PY" scripts/check_executable.py || rc=1

echo "== 7. vector store (files + mongo, both pinned to corpus AND triage) =="
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

echo "== 8. exception-branch coverage =="
"$PY" - <<'PYEOF'
import sys
from pathlib import Path
from lks.reviewer import discover_scopes, exception_branches
from lks.catala_runner import CatalaError
errors = []
for f in sorted(Path('catala/modules').glob('*.catala_en')):
    for scope, qual in discover_scopes(f).items():
        for var in qual['output'] + qual['internal']:
            try:
                br = exception_branches(f, scope, var)
            except CatalaError as e:
                # A variable whose exception tree cannot be read is one nothing
                # downstream can map, trace or compare -- not one to skip.
                errors.append(f"{f.stem}.{scope}.{var}: {str(e).splitlines()[0][:100] if str(e) else 'CatalaError'}")
                continue
            if len(br) > 1:
                print(f"   {f.stem}.{scope}.{var}: {len(br)} branches")
for x in errors:
    print(f"   FAIL  exception tree unreadable: {x}")
sys.exit(1 if errors else 0)
PYEOF
[ $? -ne 0 ] && rc=1

echo "== 9. exposure engine (declared content, and every finding reproducible) =="
"$PY" - <<'PYEOF'
import sys
from lks import exposure, surface as sf, watchers
from lks.registry import load_registry

reg = load_registry()
doms, preds = sf.load_domains(), exposure.load_predicates()
problems = sf.validate_domains(doms, reg) + exposure.validate_predicates(preds, reg)
for x in problems:
    print(f"   FAIL  {x}")
print(f"   {len(doms)}/{len(reg)} scopes have a declared population and are therefore "
      f"visible; {len(preds)} predicates, all cited")

# Every exposure on the queue must still be reachable by executing the scope on
# its own recorded facts. An entry that no longer reproduces is either fixed --
# in which case it belongs closed rather than sitting in a lawyer's queue -- or
# the engine has drifted, and both are worth failing the gate for. Divergences
# are excluded because they are claims about a past decision, not about the
# corpus, and re-executing the corpus cannot confirm or refute one.
stale = []
for e in exposure.load_queue():
    if e.klass == exposure.Klass.DIVERGENCE:
        continue
    v = exposure.adjudicate(
        exposure.Attack(e.archetype, e.scope, e.facts, origin="gate"),
        registry=reg, domains=doms, predicates=preds,
    )
    if not v.landed or v.klass != e.klass:
        stale.append(f"{e.id} no longer reproduces ({v.why})")
for x in stale:
    print(f"   FAIL  {x}")
q = exposure.summary()
print(f"   {q['total']} on the queue ({q['by_class']}); {q['costed']} carry a computed figure")

divs = watchers.check_operations()
print(f"   {len(divs)} divergence(s) between the decisions on record and the policy")
sys.exit(1 if (problems or stale) else 0)
PYEOF
[ $? -ne 0 ] && rc=1

echo "== 10. rule registry matches the modules (catala/registry.yaml is derived) =="
"$PY" - <<'PYEOF'
import sys
from lks.registry import build_registry, load_registry
built = {k: v.to_dict() for k, v in build_registry().items()}
committed = {k: v.to_dict() for k, v in load_registry().items()}
drift = sorted(set(built) ^ set(committed)) + sorted(
    k for k in set(built) & set(committed) if built[k] != committed[k])
for k in drift:
    if k not in committed:
        print(f"   FAIL  {k}: declared by the modules, missing from the registry")
    elif k not in built:
        print(f"   FAIL  {k}: in the registry, no longer declared by any module")
    else:
        diff = [f for f in built[k] if built[k][f] != committed[k].get(f)]
        print(f"   FAIL  {k}: registry differs on {diff}")
if drift:
    print("   regenerate with scripts/build_registry.py -- the router answers from this file")
    sys.exit(1)
print(f"   ok    {len(built)} scopes, registry current")
PYEOF
[ $? -ne 0 ] && rc=1

echo
[ $rc -eq 0 ] && echo "GATE: PASS" || echo "GATE: FAIL"
exit $rc
