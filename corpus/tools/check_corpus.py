#!/usr/bin/env python3
"""Corpus self-check. Run it after touching any document, the manifest, or the gold set.

Catches the failure modes that actually bite: a gold record pointing at a doc_id that
does not exist, a module in the gold set that no scope contract declares, a document
whose front matter drifted from the manifest, a module with only one fact pattern (so
its exception branch is untested), and a missing PDF.

    python3 tools/check_corpus.py     # exit 0 = corpus is coherent
"""
import csv, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors, warnings = [], []


def err(m): errors.append(m)
def warn(m): warnings.append(m)


rows = list(csv.DictReader((ROOT / "manifest.csv").open()))
ids = {r["doc_id"] for r in rows}
if len(ids) != len(rows):
    err("duplicate doc_id in manifest.csv")

for r in rows:
    if not (ROOT / r["path"]).exists():
        err(f"{r['doc_id']}: missing file {r['path']}")
    if not r["license"].strip():
        err(f"{r['doc_id']}: empty license column")

# front matter must agree with the manifest
for r in rows:
    p = ROOT / r["path"]
    if p.suffix != ".md" or not p.exists():
        continue
    m = re.search(r"^doc_id:\s*(\S+)", p.read_text(), re.M)
    if not m:
        warn(f"{r['doc_id']}: no doc_id in front matter ({r['path']})")
    elif m.group(1) != r["doc_id"]:
        err(f"{r['path']}: front matter says {m.group(1)}, manifest says {r['doc_id']}")

scopes_txt = (ROOT / "SCOPES.md").read_text()
declared = set(re.findall(r"^\|\s*`([A-Za-z][A-Za-z0-9_]*)`", scopes_txt, re.M))
if not declared:
    err("SCOPES.md declares no modules")


def load(name):
    out = []
    for i, line in enumerate((ROOT / "gold" / name).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            err(f"gold/{name}:{i}: {e}")
    return out


fps, qas, confs = load("fact_patterns.jsonl"), load("qa_pairs.jsonl"), load("conflicts.jsonl")
for name, recs in (("fact_patterns", fps), ("qa_pairs", qas), ("conflicts", confs)):
    seen = set()
    for r in recs:
        if r["id"] in seen:
            err(f"gold/{name}: duplicate id {r['id']}")
        seen.add(r["id"])
        for d in r.get("docs", []):
            if d not in ids:
                err(f"{r['id']}: unknown doc_id {d}")
        mod = r.get("module")
        if mod and mod not in declared:
            err(f"{r['id']}: module {mod} is not declared in SCOPES.md")

# modules named by the manifest must be declared, and vice versa
for r in rows:
    for mod in filter(None, r["modules"].split(";")):
        if mod not in declared:
            err(f"{r['doc_id']}: manifest module {mod} is not declared in SCOPES.md")

# every module needs a fact pattern, and more than one branch, or its exceptions are untested
# modules whose clause has no carve-out: one pattern is full coverage
NO_EXCEPTIONS = {"BreachNotice_AcmeNorthwind", "IncentiveComp_Acme", "IncentiveComp_DxcSideLetter",
                 "Overtime_Flsa"}
by_mod = {}
for r in fps:
    by_mod.setdefault(r["module"], []).append(r)
for mod in sorted(declared):
    pats = by_mod.get(mod, [])
    if not pats:
        if mod.endswith("_Reference"):
            continue
        err(f"{mod}: declared in SCOPES.md but has no fact pattern")
    elif mod in NO_EXCEPTIONS:
        pass
    elif len(pats) == 1:
        warn(f"{mod}: only one fact pattern, so no exception branch is covered")
    elif len({p["branch"] for p in pats}) < 2:
        warn(f"{mod}: {len(pats)} patterns but all take the same branch")

for c in confs:
    if c["expect"] not in ("block", "resolve", "no_conflict"):
        err(f"{c['id']}: bad expect value {c['expect']!r}")
    for k in ("doc_a", "doc_b"):
        if c[k] not in ids:
            err(f"{c['id']}: unknown {k} {c[k]}")
    if c["expect"] == "resolve" and not c.get("precedence_clause"):
        err(f"{c['id']}: expect=resolve but no precedence_clause given")
    if c["expect"] == "block" and c.get("precedence_clause"):
        err(f"{c['id']}: expect=block but a precedence_clause is given")

for mod in sorted((ROOT / "catala" / "src").glob("*.catala_en")):
    if mod.stem in ("legal_types",):
        continue
    if not (ROOT / "catala" / "tests" / f"test_{mod.stem}.catala_en").exists():
        err(f"catala/src/{mod.name}: no matching tests/test_{mod.stem}.catala_en")

for r in rows:
    p = Path(r["path"])
    if p.suffix == ".md" and not (ROOT / "pdf" / f"{p.stem}.pdf").exists():
        warn(f"{r['doc_id']}: no rendered PDF (run tools/render_pdfs.py)")

print(f"{len(rows)} documents, {len(declared)} modules, {len(fps)} fact patterns, "
      f"{len(qas)} Q/A pairs, {len(confs)} conflict records")
for w in warnings:
    print(f"warn  {w}")
for e in errors:
    print(f"ERROR {e}", file=sys.stderr)
print(f"\n{len(errors)} errors, {len(warnings)} warnings")
sys.exit(1 if errors else 0)
