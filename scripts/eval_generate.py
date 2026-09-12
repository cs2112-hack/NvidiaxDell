#!/usr/bin/env python3
"""Measure the generation pipeline against the corpus.

    . scripts/env.sh
    $PY scripts/eval_generate.py mutants [--per-module 12] [--screen 0] [--screen-originals 0]
    $PY scripts/eval_generate.py regenerate --doc EMP-ANNEX-C [--no-roundtrip]
    $PY scripts/eval_generate.py regenerate --all
    $PY scripts/eval_generate.py report          # writes eval/GENERATION-RESULTS.md

Two evaluations, because the pipeline has two halves and one number cannot
separate them.

## V1 — mutants (does the checking catch planted defects?)

`lks.mutate` plants the defect shapes this corpus has actually produced into
the 19 committed modules, which are known to be correct. Each mutant is then
classified, in this order, and every class is reported:

    stillborn     does not compile. G1 catches it, which measures nothing about
                  the rule, so it leaves the denominator.
    untestable    the mutated scope has no battery (a list or structure input),
                  so whether it changed behaviour cannot be established.
    equivalent    `compare_encodings` against the original finds no difference
                  on any boundary vector. Nothing to detect; leaves the denominator.
    live          behaviourally different from the original. The denominator.

and each live mutant is then put to the cheap gates (killed if G2 or G3 report
a failure the unmutated module did not have) and, optionally, to the logic
reviewer (killed only if a *confirmed* finding lands on inputs where the mutant
and the original actually disagree -- a confirmed finding elsewhere is about
the corpus, not the mutant, and counting it would inflate the kill rate).

The live count is also the ceiling for G5: a perfect re-encoder reproduces the
original module, and comparing against the original is exactly what classified
the mutant live.

`--screen-originals N` runs the logic reviewer on N *unmutated* modules. A
confirmed finding there is either a false positive or a real defect in the
committed corpus. They are listed with their evidence, not silently counted as
either.

## V2 — leave-one-out regeneration (can the whole pipeline rebuild a document?)

For a corpus document D: exclude D from retrieval entirely (its chunks, its
text, its encodings), give the pipeline a brief of the kind a person would
write -- D's title, its section headings and the figures it states -- and run
it. Scored on whether it issued, where it failed if not, and, where it produced
a module, `compare_encodings` against each committed module that encodes D.

Clause-level triage agreement is deliberately NOT reported. A regenerated
document numbers its own clauses, so its K-3.1 has no relationship to the
ledger's C-3.1, and a per-id agreement figure would be a number about nothing.
The label *distribution* is reported instead, next to D's.

The brief leaks D's structure and figures. That is intended: V2 asks whether
the pipeline can produce a sound, executable document from a realistic
request, not whether a model can guess an overtime policy's numbers.
"""
from __future__ import annotations

import argparse
import collections
import functools
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Progress is usually read from a redirected log; unflushed, a two-hour run
# looks hung for its first hour.
print = functools.partial(print, flush=True)  # noqa: A001

OUT = ROOT / "eval" / "generation"
RESULTS_MD = ROOT / "eval" / "GENERATION-RESULTS.md"
SCRATCH = OUT / "mutants"


# --- shared --------------------------------------------------------------


def module_documents() -> dict[str, str]:
    """module path -> the corpus document path it encodes (first by clause count)."""
    from lks.registry import load_registry
    from lks.segment import load_corpus

    docs = {d.doc_id: d.source_path for d in load_corpus()}
    counts: dict[str, collections.Counter] = {}
    for e in load_registry().values():
        for ref in e.encodes:
            counts.setdefault(e.path, collections.Counter())[ref.split()[0]] += 1
    return {p: docs[c.most_common(1)[0][0]] for p, c in counts.items() if c}


def mutated_scope(m) -> str | None:
    lines = Path(m.module).read_text(encoding="utf-8").splitlines()
    for i in range(m.span[0], -1, -1):
        hit = re.match(r"^scope\s+([A-Z]\w*)", lines[i])
        if hit:
            return hit.group(1)
    return None


def _failure_signature(report) -> tuple[set, set]:
    g2 = report.get("G2")
    g3 = report.get("G3")
    g2s = {(f.get("scope"), f.get("kind")) for f in (g2.evidence.get("failures", []) if g2 else [])}
    g3s = {(d["scope"], d["variable"], d["branch"]) for d in (g3.evidence.get("dead_branches", []) if g3 else [])}
    return g2s, g3s


# --- V1 ------------------------------------------------------------------


def cmd_mutants(a) -> int:
    from lks import gates, llm, mutate, screen
    from lks.agents import SCREEN_LOGIC, run_role
    from lks.catala_runner import CatalaError, run_scope, values_agree
    from lks.draft import compare_encodings
    from lks.segment import parse_document

    OUT.mkdir(parents=True, exist_ok=True)
    pairs = module_documents()
    rows: list[dict] = []
    fp_rows: list[dict] = []
    screen_budget = a.screen
    t0 = time.monotonic()

    for mod_path, doc_path in sorted(pairs.items()):
        if a.module and Path(mod_path).stem.lower() != a.module.lower():
            continue
        muts = mutate.sample(mutate.enumerate_mutants(mod_path), a.per_module, seed=a.seed)
        if not muts:
            continue
        base_report, _ = gates.run_cheap_gates(mod_path, doc_path, cap=a.cap)
        base_g2, base_g3 = _failure_signature(base_report)
        doc = parse_document(doc_path)
        doc_md = Path(doc_path).read_text(encoding="utf-8")
        print(f"\n## {Path(mod_path).name} ({len(muts)} mutants) against {Path(doc_path).name}")

        for m in muts:
            row = {"id": m.id, "module": Path(mod_path).name, "operator": m.operator,
                   "line": m.line, "change": m.describe()}
            path = mutate.write_mutant(m, SCRATCH)
            if not gates.g1_typecheck(path).ok:
                row["class"] = "stillborn"
                rows.append(row)
                print(f"  {m.id:<44} stillborn")
                continue

            scope = mutated_scope(m)
            try:
                cmp = compare_encodings(mod_path, path, scope_map={scope: scope} if scope else None,
                                        cap=a.cap)
                differs = bool(cmp.behaviour_diffs) or any(not d.equal for d in cmp.tree_diffs)
                if differs:
                    row["class"] = "live"
                    row["behaviour_diffs"] = len(cmp.behaviour_diffs)
                elif cmp.battery_size == 0 or cmp.errors:
                    row["class"] = "untestable"
                    row["why"] = "; ".join(cmp.errors)[:200]
                else:
                    row["class"] = "equivalent"
            except CatalaError as e:
                row["class"] = "untestable"
                row["why"] = e.diagnostic[:200]

            if row["class"] != "live":
                rows.append(row)
                print(f"  {m.id:<44} {row['class']}")
                continue

            rep, _ = gates.run_cheap_gates(path, doc_path, cap=a.cap)
            g2s, g3s = _failure_signature(rep)
            new_g2, new_g3 = g2s - base_g2, g3s - base_g3
            row["killed_by_gates"] = bool(new_g2 or new_g3)
            row["gate_kill"] = (["G2:" + "/".join(map(str, x)) for x in sorted(new_g2)]
                                + ["G3:" + x[2] for x in sorted(new_g3)])[:4]

            if not row["killed_by_gates"] and screen_budget > 0:
                screen_budget -= 1
                ifaces = screen.scope_interfaces(path)
                packet = screen.logic_packet(doc_md, path, ifaces)
                res = run_role(SCREEN_LOGIC, packet, model=a.model, seed=a.seed + len(rows))
                row["screen_ok"] = res.ok
                row["killed_by_screen"] = False
                if res.ok:
                    try:
                        findings, _ = screen._parse("logic", res.value)
                    except ValueError:
                        findings = []
                    for f in findings:
                        screen.verify(f, doc=doc, module=path, ifaces=ifaces, corpus_refs=set(),
                                      exercised_clauses=set())
                        if f.status != screen.CONFIRMED:
                            continue
                        try:
                            orig = run_scope(mod_path, f.scope, f.inputs)
                        except CatalaError as e:
                            orig = {"__error__": type(e).__name__}
                        obs = f.observed if isinstance(f.observed, dict) else {}
                        common = set(orig) & set(obs)
                        if not common or not all(values_agree(orig[k], obs[k]) for k in common):
                            row["killed_by_screen"] = True
                            row["screen_evidence"] = f.verified_by[:300]
                            break
                else:
                    row["screen_error"] = res.error[:200]
            rows.append(row)
            verdict = ("killed by gates " + ",".join(row["gate_kill"])) if row["killed_by_gates"] else (
                "killed by screen" if row.get("killed_by_screen") else
                ("survived screen" if "killed_by_screen" in row else "survived gates"))
            print(f"  {m.id:<44} live -> {verdict}")

    if a.screen_originals:
        for mod_path, doc_path in sorted(pairs.items())[: a.screen_originals]:
            doc = parse_document(doc_path)
            ifaces = screen.scope_interfaces(mod_path)
            packet = screen.logic_packet(Path(doc_path).read_text(encoding="utf-8"),
                                         Path(mod_path), ifaces)
            res = run_role(SCREEN_LOGIC, packet, model=a.model, seed=a.seed)
            confirmed = []
            if res.ok:
                try:
                    findings, _ = screen._parse("logic", res.value)
                except ValueError:
                    findings = []
                for f in findings:
                    screen.verify(f, doc=doc, module=Path(mod_path), ifaces=ifaces,
                                  corpus_refs=set(), exercised_clauses=set())
                    if f.status == screen.CONFIRMED:
                        confirmed.append({"clause_ids": f.clause_ids, "summary": f.summary,
                                          "evidence": f.verified_by})
            fp_rows.append({"module": Path(mod_path).name, "ok": res.ok,
                            "error": res.error[:200], "confirmed": confirmed})
            print(f"  original {Path(mod_path).name}: {len(confirmed)} confirmed finding(s)")

    payload = {"generated": time.strftime("%Y-%m-%d %H:%M"), "seed": a.seed,
               "per_module": a.per_module, "cap": a.cap, "screen_budget": a.screen,
               "seconds": round(time.monotonic() - t0), "mutants": rows, "originals": fp_rows}
    (OUT / "mutants.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'mutants.json'}")
    return 0


# --- V2 ------------------------------------------------------------------

FIGURE_RE = re.compile(
    r"(?:£|GBP\s?|\$)\s?\d[\d,]*(?:\.\d+)?"
    r"|\d+(?:\.\d+)?\s?%"
    r"|\d+(?:\.\d+)?\s+times"
    r"|\d+(?:\.\d+)?\s+(?:business |calendar |working )?(?:hours?|days?|weeks?|months?|years?|minutes?)"
    r"|Grade\s+\d+"
)


def build_brief(doc) -> str:
    """A request a person would plausibly write for this document."""
    sections: list[str] = []
    for c in doc.clauses:
        if c.section_title not in sections:
            sections.append(c.section_title)
    figures: list[str] = []
    for c in doc.clauses:
        for f in FIGURE_RE.findall(c.body):
            f = " ".join(f.split())
            if f not in figures:
                figures.append(f)
    return (
        f"Draft a document titled \"{doc.title}\" for {doc.jurisdiction}, owned by "
        f"{doc.owner}, effective {doc.effective_date}. It should cover: "
        + "; ".join(sections) + ". "
        + ("The figures it must state include: " + ", ".join(figures[:25]) + "."
           if figures else "")
    )


def cmd_regenerate(a) -> int:
    from lks import generate as gen
    from lks.draft import compare_encodings
    from lks.registry import load_registry
    from lks.segment import load_corpus
    from lks.triage import load_ledger

    OUT.mkdir(parents=True, exist_ok=True)
    docs = {d.doc_id: d for d in load_corpus()}
    targets = sorted(docs) if a.all else [a.doc]
    reg = load_registry()
    ledger = load_ledger()
    for doc_id in targets:
        if doc_id not in docs:
            print(f"no such document {doc_id}; have {sorted(docs)}", file=sys.stderr)
            return 2
        d = docs[doc_id]
        brief = build_brief(d)
        # A fresh directory per run. Reusing one per document compared a rerun
        # that failed before encoding against the previous run's module.
        ws = OUT / "workspaces" / f"{doc_id}-{time.strftime('%Y%m%d-%H%M%S')}"
        print(f"\n#### {doc_id}: leave-one-out regeneration\nbrief: {brief}\n")
        limits = gen.Limits(roundtrip=not a.no_roundtrip, screen_rounds=a.screen_rounds,
                            catala_attempts=a.catala_attempts)
        r = gen.generate(brief, limits=limits, model=a.model, seed=a.seed, workspace=ws,
                         exclude_docs=(doc_id,))
        row = {"doc_id": doc_id, "brief": brief, "passed": r.passed,
               "failure_stage": r.failure_stage, "failure_reason": r.failure_reason,
               "catala_iterations": r.catala_iterations, "screen_rounds": r.screen_rounds,
               "seconds": round(r.seconds), "workspace": str(ws),
               "leak_check": sorted({c.doc_id for c in r.context} & {doc_id}),
               "comparisons": []}

        module = next(iter(sorted(ws.glob("*.catala_en"))), None)
        if module is not None and r.gate_report and r.gate_report.get("G1") and r.gate_report.get("G1").ok:
            committed = sorted({e.path for e in reg.values()
                                if any(x.split()[0] == doc_id for x in e.encodes)})
            for path in committed:
                smap = gen._scope_map(Path(path), module)
                if not smap:
                    row["comparisons"].append({"committed": Path(path).name, "matched_scopes": 0})
                    continue
                try:
                    cmp = compare_encodings(path, module, scope_map=smap, cap=a.cap)
                    row["comparisons"].append({
                        "committed": Path(path).name, "scope_map": smap,
                        "matched_scopes": len(smap), "converged": cmp.converged,
                        "battery": cmp.battery_size,
                        "behaviour_diffs": len(cmp.behaviour_diffs),
                        "structural_diffs": sum(1 for t in cmp.tree_diffs if not t.equal),
                        "errors": cmp.errors[:3],
                    })
                except Exception as e:                      # noqa: BLE001
                    row["comparisons"].append({"committed": Path(path).name,
                                               "error": f"{type(e).__name__}: {e}"[:200]})

        tri = ws / "triage.yaml"
        if tri.exists():
            import yaml
            got = collections.Counter(v["label"] for v in (yaml.safe_load(tri.read_text())
                                                            .get("decisions") or {}).values())
            row["labels_generated"] = dict(got)
        row["labels_ledger"] = dict(collections.Counter(
            dec.label.value for ref, dec in ledger.items() if ref.split()[0] == doc_id))
        (OUT / f"regenerate-{doc_id}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        print(f"\n{doc_id}: {'ISSUED' if r.passed else 'NOT ISSUED at ' + r.failure_stage}"
              f" — wrote {OUT / f'regenerate-{doc_id}.json'}")
    return 0


# --- report --------------------------------------------------------------


def cmd_report(_a) -> int:
    L = ["# Document generation — measured against the corpus", "",
         "Reproduce with `scripts/eval_generate.py`. Method and definitions are in that "
         "script's docstring and `docs/GENERATION.md`.", ""]
    mpath = OUT / "mutants.json"
    if mpath.exists():
        data = json.loads(mpath.read_text())
        rows = data["mutants"]
        cls = collections.Counter(r["class"] for r in rows)
        live = [r for r in rows if r["class"] == "live"]
        by_gates = sum(1 for r in live if r.get("killed_by_gates"))
        screened = [r for r in live if "killed_by_screen" in r]
        by_screen = sum(1 for r in screened if r["killed_by_screen"])
        surv = len(live) - by_gates - by_screen
        L += ["## V1 — planted defects", "",
              f"{len(rows)} mutants of the committed modules (seed {data['seed']}, "
              f"up to {data['per_module']} per module), {data['seconds']}s.", "",
              "| class | count | meaning |", "|---|---:|---|",
              f"| stillborn | {cls['stillborn']} | does not compile; excluded |",
              f"| untestable | {cls['untestable']} | mutated scope has no battery; excluded |",
              f"| equivalent | {cls['equivalent']} | no behavioural difference; excluded |",
              f"| **live** | **{len(live)}** | the denominator, and the ceiling for G5 |", "",
              "| of the live mutants | count | rate |", "|---|---:|---:|",
              f"| killed by G2/G3 (no model) | {by_gates} | {by_gates / len(live):.0%} |" if live else "| killed by G2/G3 | 0 | — |",
              f"| sent to the logic reviewer | {len(screened)} | |",
              f"| killed by the logic reviewer | {by_screen} | "
              + (f"{by_screen / len(screened):.0%} of those screened |" if screened else "— |"),
              f"| survived everything run on them | {surv} | "
              + (f"{surv / len(live):.0%} |" if live else "— |"), ""]
        ops = collections.defaultdict(lambda: collections.Counter())
        for r in rows:
            ops[r["operator"]][r["class"]] += 1
            if r["class"] == "live" and r.get("killed_by_gates"):
                ops[r["operator"]]["gates"] += 1
        L += ["| operator | stillborn | untestable | equivalent | live | killed by gates |",
              "|---|---:|---:|---:|---:|---:|"]
        for op, c in sorted(ops.items()):
            L.append(f"| {op} | {c['stillborn']} | {c['untestable']} | {c['equivalent']} | "
                     f"{c['live']} | {c['gates']} |")
        survivors = [r for r in live if not r.get("killed_by_gates") and not r.get("killed_by_screen")]
        if survivors:
            L += ["", "### Live mutants the cheap gates did not catch", "",
                  "These are what the gates cannot see: a well-formed, total rule with every "
                  "branch reachable that computes the wrong thing. Catching them is the logic "
                  "reviewer's job and G5's.", ""]
            L += [f"- `{r['id']}` — {r['change']}" for r in survivors[:40]]
        if data.get("originals"):
            L += ["", "### Logic reviewer on unmutated modules", ""]
            for o in data["originals"]:
                L.append(f"- {o['module']}: " + (f"{len(o['confirmed'])} confirmed" if o["ok"]
                                                  else f"no usable reply ({o['error']})"))
                for c in o["confirmed"]:
                    L.append(f"  - {c['clause_ids']}: {c['summary']} — {c['evidence']}")
        L.append("")

    regen = sorted(OUT.glob("regenerate-*.json"))
    if regen:
        L += ["## V2 — leave-one-out regeneration", "",
              "| document | issued | failed at | encodings | screen rounds | minutes | "
              "matched committed scopes | converged |",
              "|---|---|---|---:|---:|---:|---|---|"]
        for p in regen:
            r = json.loads(p.read_text())
            comps = r.get("comparisons") or []
            matched = ", ".join(f"{c['committed']}:{c.get('matched_scopes', 0)}" for c in comps) or "—"
            conv = ", ".join(f"{c['committed']}:{'yes' if c.get('converged') else 'no'}"
                             for c in comps if c.get("matched_scopes")) or "—"
            L.append(f"| {r['doc_id']} | {'yes' if r['passed'] else 'no'} | "
                     f"{r['failure_stage'] or '—'} | {r['catala_iterations']} | "
                     f"{r['screen_rounds']} | {r['seconds'] / 60:.0f} | {matched} | {conv} |")
        L += ["", "Label distribution, generated vs ledger (per-clause agreement is undefined "
                  "for a regenerated document; see the script docstring):", ""]
        for p in regen:
            r = json.loads(p.read_text())
            L.append(f"- {r['doc_id']}: generated {r.get('labels_generated', {})} — "
                     f"ledger {r.get('labels_ledger', {})}")
        L.append("")
    if len(L) <= 4:
        L.append("No results yet. Run `mutants` or `regenerate` first.")
    RESULTS_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {RESULTS_MD}")
    return 0


def main() -> int:
    from lks import llm

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mutants", help="V1: plant defects in committed modules and measure detection")
    m.add_argument("--per-module", type=int, default=12)
    m.add_argument("--module", help="only this module (file stem)")
    m.add_argument("--screen", type=int, default=0,
                   help="send up to N gate-surviving live mutants to the logic reviewer (model time)")
    m.add_argument("--screen-originals", type=int, default=0,
                   help="run the logic reviewer on N unmutated modules (false-positive check)")
    m.add_argument("--cap", type=int, default=600)
    m.add_argument("--seed", type=int, default=20260912)
    m.add_argument("--model", default=llm.DEFAULT_MODEL)
    m.set_defaults(fn=cmd_mutants)

    g = sub.add_parser("regenerate", help="V2: leave-one-out regeneration of corpus documents")
    who = g.add_mutually_exclusive_group(required=True)
    who.add_argument("--doc")
    who.add_argument("--all", action="store_true")
    g.add_argument("--no-roundtrip", action="store_true")
    g.add_argument("--screen-rounds", type=int, default=2)
    g.add_argument("--catala-attempts", type=int, default=3)
    g.add_argument("--cap", type=int, default=600)
    g.add_argument("--seed", type=int, default=20260912)
    g.add_argument("--model", default=llm.DEFAULT_MODEL)
    g.set_defaults(fn=cmd_regenerate)

    r = sub.add_parser("report", help="write eval/GENERATION-RESULTS.md from saved results")
    r.set_defaults(fn=cmd_report)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
