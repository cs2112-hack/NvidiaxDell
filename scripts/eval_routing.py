#!/usr/bin/env python3
"""Measure the chat layer's routing against eval/routing.yaml.

Why this exists
---------------
Routing quality used to be asserted from a handful of hand-tried questions.
That cannot distinguish an improvement from a different set of mistakes, so
every knob in `lks.chat` (ROUTE_MARGIN, CATALA_THRESHOLD, VECTOR_THRESHOLD, the
route index's text) was unfalsifiable. This harness makes them falsifiable.

What it reports, and why each number is here
--------------------------------------------
* routing accuracy overall and per EXPECTED engine -- an aggregate alone hides
  that the router can be perfect on prose and useless on rules.
* a confusion matrix over engines -- the direction of an error matters. Sending
  a prose question to CATALA wastes the user's time; sending a rule question to
  VECTOR risks a quotation being read as the computed answer, which is the
  failure the two-engine invariant exists to prevent.
* citation precision and recall against the labelled clauses -- a right engine
  citing the wrong clause is not a right answer.
* scope accuracy, and the two asymmetric ambiguity errors: claiming a single
  scope where the label says candidates should have been reported
  (over-confident), and reporting candidates where one scope was right
  (under-confident). These are not interchangeable, so they are never summed.
* judgement safety -- for every case whose label names a judgement input, did
  the system refuse to compute and name the input a human must supply? A
  violation here is a correctness bug, not a quality metric, and is reported
  first.
* every failure, with the router's scores and candidate list, so a human can
  see what is wrong rather than a single number.

Engine semantics are defined in eval/routing.yaml's header. In short: a CATALA
part of any kind counts as CATALA, a VECTOR part of kind `quotation` counts as
VECTOR, and `caveat` parts are excluded because they are attached
deterministically from triage's `qualifies` field and carry no routing signal.

Usage
-----
    $PY scripts/eval_routing.py
    $PY scripts/eval_routing.py --json
    $PY scripts/eval_routing.py --margin 0.06 --catala-threshold 0.40
    $PY scripts/eval_routing.py --sweep margin
    $PY scripts/eval_routing.py --sweep catala-threshold --json

The threshold flags set the module-level constants in `lks.chat` for the
duration of the run. That is deliberate: the constants are the thing under
measurement, and a sweep that could not reach them would leave them hand-set.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EVAL_PATH = ROOT / "eval" / "routing.yaml"
ENGINES = ["CATALA", "VECTOR", "BOTH", "NONE"]

_CITE_TAIL = re.compile(r"\s*\([^()]*:\d+\)\s*$")


def norm_cite(c: str) -> str:
    """`MSA-SCH4 L-1.2 (004-msa-sla-credits.md:17)` -> `MSA-SCH4 L-1.2`.

    CATALA parts cite bare clause refs (the router's supporting clauses) while
    VECTOR parts cite the canonical `REF (file:line)` form. Comparing them
    against one labelled ref list needs one shape.
    """
    return _CITE_TAIL.sub("", c).strip()


def as_list(v: Any) -> list[str]:
    if v is None:
        return []
    return [v] if isinstance(v, str) else list(v)


# --- running one case ------------------------------------------------------


def predict(chat: Any, question: str) -> dict[str, Any]:
    ans = chat.answer(question)
    cat = [p for p in ans.parts if p.engine == "CATALA"]
    quo = [p for p in ans.parts if p.engine == "VECTOR" and p.kind == "quotation"]
    cav = [p for p in ans.parts if p.engine == "VECTOR" and p.kind == "caveat"]
    nocov = [p for p in ans.parts if p.engine == "NONE"]

    if cat and quo:
        engine = "BOTH"
    elif cat:
        engine = "CATALA"
    elif quo:
        engine = "VECTOR"
    else:
        engine = "NONE"

    head = cat[0] if cat else None
    cites: list[str] = []
    for p in cat + quo:
        for c in p.citations:
            c = norm_cite(c)
            if c not in cites:
                cites.append(c)

    return {
        "engine": engine,
        "kind": head.kind if head else (quo[0].kind if quo else (nocov[0].kind if nocov else None)),
        "scope": head.scope if head else None,
        "ambiguous": bool(head and head.kind == "ambiguous-route"),
        "candidates": [list(t) for t in (head.candidates if head else [])],
        "reported": list(head.reported) if head else [],
        "route_score": head.score if head else None,
        "vector_scores": [round(p.score, 4) for p in quo if p.score is not None],
        "citations": cites,
        "n_caveats": len(cav),
        "needs": (head.inputs or {}).get("missing") if head else None,
        "computed": bool(head and head.kind == "computed"),
        "text": "\n".join(p.text for p in ans.parts),
    }


def judge(case: dict[str, Any], got: dict[str, Any]) -> dict[str, Any]:
    """Score one case. Returns the per-case record the report is built from."""
    want_engine = case["engine"]
    want_scopes = as_list(case.get("scope"))
    want_clauses = set(case.get("clauses") or [])
    want_ambiguous = bool(case.get("ambiguous"))
    judgements = case.get("judgement") or []

    engine_ok = got["engine"] == want_engine

    # -- scope ------------------------------------------------------------
    # Checked only where the label names scopes. For an ambiguous case the
    # router is right if it reported candidates and the labelled scopes are
    # among them; naming one of them outright is the over-confident error.
    if not want_scopes:
        scope_ok = None
    elif want_ambiguous:
        scope_ok = got["ambiguous"] and bool(set(got["reported"]) & set(want_scopes))
    else:
        scope_ok = got["scope"] in want_scopes

    over_confident = bool(want_ambiguous and want_scopes and not got["ambiguous"]
                          and got["scope"] is not None)
    under_confident = bool(not want_ambiguous and want_scopes and got["ambiguous"])

    # -- citations ---------------------------------------------------------
    got_clauses = set(got["citations"])
    tp = len(want_clauses & got_clauses)
    cit = {
        "tp": tp,
        "fp": len(got_clauses - want_clauses),
        "fn": len(want_clauses - got_clauses),
        "recall": (tp / len(want_clauses)) if want_clauses else None,
        "precision": (tp / len(got_clauses)) if got_clauses else None,
        "missing": sorted(want_clauses - got_clauses),
    }

    # -- judgement safety --------------------------------------------------
    # Two obligations. (1) Never compute: with no inputs supplied the system
    # must not produce a figure for a question that turns on a judgement -- that
    # would be the machine deciding it. (2) Name the input: the reply must
    # identify at least one of the judgement inputs a human has to supply.
    if judgements:
        named_ji = [j for j in judgements if j in got["text"]]
        jsafe = {
            "silently_computed": got["computed"],
            "named": named_ji,
            "ok": (not got["computed"]) and bool(named_ji),
        }
    else:
        jsafe = None

    return {
        "id": case["id"],
        "question": case["question"],
        "tags": case.get("tags") or [],
        "pair": case.get("pair"),
        "want": {
            "engine": want_engine, "scope": want_scopes,
            "clauses": sorted(want_clauses), "ambiguous": want_ambiguous,
            "judgement": judgements,
        },
        "got": {k: v for k, v in got.items() if k != "text"},
        "engine_ok": engine_ok,
        "scope_ok": scope_ok,
        "over_confident": over_confident,
        "under_confident": under_confident,
        "citations": cit,
        "judgement_safety": jsafe,
        "why": case.get("why", ""),
        "passed": engine_ok and (scope_ok is not False) and (jsafe is None or jsafe["ok"])
                  and cit["recall"] in (None, 1.0) if False else None,
    }


def _pass(rec: dict[str, Any]) -> bool:
    """A case passes when the engine is right, the scope is right where one is
    labelled, no judgement was silently decided, and at least one labelled
    clause was cited. Full citation recall is reported as its own number rather
    than folded in here, so that a near-miss on a second clause does not read
    the same as a wrong engine."""
    if not rec["engine_ok"]:
        return False
    if rec["scope_ok"] is False:
        return False
    js = rec["judgement_safety"]
    if js is not None and not js["ok"]:
        return False
    if rec["want"]["clauses"] and rec["citations"]["tp"] == 0:
        return False
    return True


# --- aggregation -----------------------------------------------------------


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    for r in records:
        r["passed"] = _pass(r)

    n = len(records)
    per_engine: dict[str, dict[str, int]] = {}
    confusion = {w: {g: 0 for g in ENGINES} for w in ENGINES}
    for r in records:
        w = r["want"]["engine"]
        g = r["got"]["engine"]
        confusion[w][g] += 1
        b = per_engine.setdefault(w, {"n": 0, "engine_ok": 0, "passed": 0})
        b["n"] += 1
        b["engine_ok"] += int(r["engine_ok"])
        b["passed"] += int(r["passed"])

    per_tag: dict[str, dict[str, int]] = {}
    for r in records:
        for t in r["tags"]:
            b = per_tag.setdefault(t, {"n": 0, "passed": 0})
            b["n"] += 1
            b["passed"] += int(r["passed"])

    tp = sum(r["citations"]["tp"] for r in records)
    fp = sum(r["citations"]["fp"] for r in records)
    fn = sum(r["citations"]["fn"] for r in records)

    scoped = [r for r in records if r["scope_ok"] is not None]
    jcases = [r for r in records if r["judgement_safety"] is not None]
    amb_expected = [r for r in records if r["want"]["ambiguous"]]
    unamb_expected = [r for r in records if r["want"]["scope"] and not r["want"]["ambiguous"]]

    pairs: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r["pair"]:
            pairs.setdefault(r["pair"], []).append(r)

    return {
        "n": n,
        "engine_accuracy": sum(r["engine_ok"] for r in records) / n,
        "pass_rate": sum(r["passed"] for r in records) / n,
        "per_engine": {
            k: {**v,
                "engine_accuracy": v["engine_ok"] / v["n"],
                "pass_rate": v["passed"] / v["n"]}
            for k, v in per_engine.items()
        },
        "confusion": confusion,
        "per_tag": {
            k: {**v, "pass_rate": v["passed"] / v["n"]}
            for k, v in sorted(per_tag.items())
        },
        "citations": {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
        },
        "scope_accuracy": (
            sum(1 for r in scoped if r["scope_ok"]) / len(scoped) if scoped else None
        ),
        "scope_n": len(scoped),
        "ambiguity": {
            "expected_ambiguous": len(amb_expected),
            "over_confident": sum(1 for r in records if r["over_confident"]),
            "over_confident_rate": (
                sum(1 for r in amb_expected if r["over_confident"]) / len(amb_expected)
                if amb_expected else None
            ),
            "expected_single_scope": len(unamb_expected),
            "under_confident": sum(1 for r in records if r["under_confident"]),
            "under_confident_rate": (
                sum(1 for r in unamb_expected if r["under_confident"]) / len(unamb_expected)
                if unamb_expected else None
            ),
        },
        "judgement": {
            "n": len(jcases),
            "silently_computed": sum(
                1 for r in jcases if r["judgement_safety"]["silently_computed"]
            ),
            "input_named": sum(1 for r in jcases if r["judgement_safety"]["named"]),
            "safe": sum(1 for r in jcases if r["judgement_safety"]["ok"]),
        },
        "near_miss_pairs": {
            k: {
                "ids": [r["id"] for r in v],
                "both_pass": all(r["passed"] for r in v),
                "collapsed": len({(r["got"]["engine"], r["got"]["scope"]) for r in v}) == 1,
            }
            for k, v in sorted(pairs.items())
        },
        "caveats_total": sum(r["got"]["n_caveats"] for r in records),
    }


# --- rendering -------------------------------------------------------------


def bar(frac: float, width: int = 24) -> str:
    filled = int(round(frac * width))
    return "#" * filled + "." * (width - filled)


def pct(x: float | None) -> str:
    return "   n/a" if x is None else f"{100 * x:5.1f}%"


def render(agg: dict[str, Any], records: list[dict[str, Any]], settings: dict[str, Any],
           show_failures: int) -> str:
    o: list[str] = []
    w = o.append
    w("=" * 78)
    w("ROUTING EVALUATION  —  eval/routing.yaml")
    w("=" * 78)
    w(f"  cases {agg['n']}    settings: " + "  ".join(
        f"{k}={v}" for k, v in sorted(settings.items())))
    w("")
    w(f"  overall pass rate      {pct(agg['pass_rate'])}   {bar(agg['pass_rate'])}")
    w(f"  engine accuracy        {pct(agg['engine_accuracy'])}   {bar(agg['engine_accuracy'])}")
    sa = agg["scope_accuracy"]
    w(f"  scope accuracy         {pct(sa)}   {bar(sa or 0)}   ({agg['scope_n']} cases with a labelled scope)")
    c = agg["citations"]
    w(f"  citation precision     {pct(c['precision'])}   {bar(c['precision'])}   tp={c['tp']} fp={c['fp']}")
    w(f"  citation recall        {pct(c['recall'])}   {bar(c['recall'])}   fn={c['fn']}")
    w("")

    # judgement safety first: a violation is a correctness bug
    j = agg["judgement"]
    w("-- judgement safety (a human must decide; the machine must not) " + "-" * 14)
    flag = "  <-- INVARIANT VIOLATION" if j["silently_computed"] else ""
    w(f"  judgement cases                     {j['n']}")
    w(f"  silently computed anyway            {j['silently_computed']}{flag}")
    w(f"  judgement input named to the user   {j['input_named']}/{j['n']}")
    w(f"  fully safe                          {j['safe']}/{j['n']}")
    w("")

    w("-- accuracy by expected engine " + "-" * 47)
    w(f"  {'expected':10s} {'n':>4s}  {'engine':>7s}  {'pass':>7s}")
    for e in ENGINES:
        b = agg["per_engine"].get(e)
        if not b:
            continue
        w(f"  {e:10s} {b['n']:>4d}  {pct(b['engine_accuracy'])}  {pct(b['pass_rate'])}   "
          f"{bar(b['pass_rate'], 18)}")
    w("")

    w("-- confusion matrix (rows = expected, cols = routed) " + "-" * 25)
    w("  " + " " * 10 + "".join(f"{g:>9s}" for g in ENGINES) + "      total")
    for e in ENGINES:
        row = agg["confusion"][e]
        tot = sum(row.values())
        if not tot:
            continue
        cells = "".join(
            (f"{row[g]:>9d}" if row[g] else f"{'.':>9s}") for g in ENGINES
        )
        w(f"  {e:10s}{cells}  {tot:>9d}")
    w("")

    a = agg["ambiguity"]
    w("-- ambiguity handling (the two errors are not interchangeable) " + "-" * 15)
    w(f"  labelled ambiguous                  {a['expected_ambiguous']}")
    w(f"    named one scope anyway            {sum(1 for r in records if r['want']['ambiguous'] and r['over_confident'])}"
      f"   ({pct(a['over_confident_rate'])} over-confident)")
    w(f"  labelled with a single scope        {a['expected_single_scope']}")
    w(f"    reported candidates instead       {sum(1 for r in records if r['under_confident'])}"
      f"   ({pct(a['under_confident_rate'])} under-confident)")
    w("")

    w("-- near-miss pairs (must route differently) " + "-" * 34)
    for k, v in agg["near_miss_pairs"].items():
        mark = "ok  " if v["both_pass"] else "FAIL"
        coll = "  [COLLAPSED: both routed identically]" if v["collapsed"] else ""
        w(f"  {mark} {k:10s} {', '.join(v['ids'])}{coll}")
    w("")

    w("-- pass rate by category " + "-" * 53)
    for k, v in agg["per_tag"].items():
        w(f"  {k:24s} {v['passed']:>3d}/{v['n']:<3d}  {pct(v['pass_rate'])}  {bar(v['pass_rate'], 18)}")
    w("")

    fails = [r for r in records if not r["passed"]]
    w(f"-- failures ({len(fails)}) " + "-" * 62)
    if not fails:
        w("  none")
    for r in fails[:show_failures]:
        g, wa = r["got"], r["want"]
        w("")
        w(f"  {r['id']}  [{', '.join(r['tags'])}]")
        w(f"    Q  {r['question']}")
        w(f"    want  engine={wa['engine']:6s} scope={wa['scope'] or '-'}"
          f"{' AMBIGUOUS' if wa['ambiguous'] else ''}")
        w(f"          clauses={wa['clauses'] or '-'}")
        w(f"    got   engine={g['engine']:6s} kind={g['kind']} scope={g['scope'] or '-'}"
          + (f" score={g['route_score']:.4f}" if g["route_score"] is not None else ""))
        if g["candidates"]:
            w("          candidates=" + ", ".join(
                f"{k}:{s:.4f}" for k, s in g["candidates"][:4]))
        if g["reported"]:
            w("          reported to user=" + ", ".join(g["reported"]))
        if g["vector_scores"]:
            w(f"          vector scores={g['vector_scores']}")
        w(f"          cited={g['citations'] or '-'}")
        reasons = []
        if not r["engine_ok"]:
            reasons.append(f"wrong engine ({wa['engine']} -> {g['engine']})")
        if r["scope_ok"] is False:
            reasons.append("wrong scope")
        if r["over_confident"]:
            reasons.append("named a scope where candidates were required")
        if r["under_confident"]:
            reasons.append("reported candidates where one scope was right")
        js = r["judgement_safety"]
        if js and js["silently_computed"]:
            reasons.append("COMPUTED A JUDGEMENT QUESTION")
        if js and not js["named"]:
            reasons.append("did not name the judgement input")
        if wa["clauses"] and r["citations"]["tp"] == 0:
            reasons.append(f"cited none of {wa['clauses']}")
        w(f"    why fail  {'; '.join(reasons)}")
        w(f"    label     {r['why']}")
    if len(fails) > show_failures:
        w("")
        w(f"  ... {len(fails) - show_failures} more; --failures N or --json for all")
    w("")
    w(f"  caveat parts attached across the run: {agg['caveats_total']} "
      "(excluded from engine and citation scoring)")
    return "\n".join(o)


# --- sweeps ----------------------------------------------------------------

SWEEPS = {
    "margin": ("ROUTE_MARGIN",
               [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20]),
    "catala-threshold": ("CATALA_THRESHOLD",
                         [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]),
    "vector-threshold": ("VECTOR_THRESHOLD",
                         [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]),
    "lexical-weight": ("LEXICAL_WEIGHT",
                       [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]),
}


def run_once(chat: Any, cases: list[dict[str, Any]]) -> tuple[list[dict], dict]:
    records = [judge(c, predict(chat, c["question"])) for c in cases]
    return records, aggregate(records)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable results")
    ap.add_argument("--eval", default=str(EVAL_PATH))
    ap.add_argument("--backend", default="files", choices=["files", "mongo", "auto"])
    ap.add_argument("--failures", type=int, default=100, help="how many failures to print")
    ap.add_argument("--only", help="run only cases whose id or tag matches this")
    ap.add_argument("--margin", type=float)
    ap.add_argument("--catala-threshold", type=float)
    ap.add_argument("--vector-threshold", type=float)
    ap.add_argument("--lexical-weight", type=float)
    ap.add_argument("--sweep", choices=sorted(SWEEPS), action="append",
                    help="sweep a threshold and print the curve instead of one run")
    a = ap.parse_args()

    import yaml

    from lks import chat as chat_mod

    cases = yaml.safe_load(Path(a.eval).read_text())["cases"]
    if a.only:
        cases = [c for c in cases
                 if a.only == c["id"] or a.only in (c.get("tags") or [])]
        if not cases:
            print(f"no cases match {a.only!r}", file=sys.stderr)
            return 2

    overrides = {
        "ROUTE_MARGIN": a.margin,
        "CATALA_THRESHOLD": a.catala_threshold,
        "VECTOR_THRESHOLD": a.vector_threshold,
        "LEXICAL_WEIGHT": a.lexical_weight,
    }
    for k, v in overrides.items():
        if v is not None:
            setattr(chat_mod, k, v)

    chat = chat_mod.Chat.open(backend=a.backend)

    if a.sweep:
        curves: dict[str, Any] = {}
        for name in a.sweep:
            const, values = SWEEPS[name]
            saved = getattr(chat_mod, const, None)
            if saved is None:
                print(f"lks.chat has no {const}; skipping sweep {name}", file=sys.stderr)
                continue
            rows = []
            for v in values:
                setattr(chat_mod, const, v)
                _recs, agg = run_once(chat, cases)
                rows.append({
                    "value": v,
                    "pass_rate": agg["pass_rate"],
                    "engine_accuracy": agg["engine_accuracy"],
                    "scope_accuracy": agg["scope_accuracy"],
                    "citation_precision": agg["citations"]["precision"],
                    "citation_recall": agg["citations"]["recall"],
                    "over_confident": agg["ambiguity"]["over_confident"],
                    "under_confident": agg["ambiguity"]["under_confident"],
                })
            setattr(chat_mod, const, saved)
            curves[name] = {"constant": const, "baseline": saved, "rows": rows}

        if a.json:
            print(json.dumps({"sweeps": curves}, indent=2, sort_keys=True))
            return 0
        for name, cur in curves.items():
            print(f"\n== sweep {name}  ({cur['constant']}, currently {cur['baseline']}) ==")
            print(f"  {'value':>7s} {'pass':>7s} {'engine':>7s} {'scope':>7s} "
                  f"{'cit P':>7s} {'cit R':>7s} {'over':>5s} {'under':>6s}")
            best = max(cur["rows"], key=lambda r: r["pass_rate"])["pass_rate"]
            for r in cur["rows"]:
                mark = " <-- best" if r["pass_rate"] == best else ""
                cur_mark = "  (current)" if r["value"] == cur["baseline"] else ""
                print(f"  {r['value']:>7.3f} {pct(r['pass_rate'])} "
                      f"{pct(r['engine_accuracy'])} {pct(r['scope_accuracy'])} "
                      f"{pct(r['citation_precision'])} {pct(r['citation_recall'])} "
                      f"{r['over_confident']:>5d} {r['under_confident']:>6d}"
                      f"{mark}{cur_mark}")
        return 0

    records, agg = run_once(chat, cases)
    settings = {
        "ROUTE_MARGIN": chat_mod.ROUTE_MARGIN,
        "CATALA_THRESHOLD": chat_mod.CATALA_THRESHOLD,
        "VECTOR_THRESHOLD": chat_mod.VECTOR_THRESHOLD,
    }
    if hasattr(chat_mod, "LEXICAL_WEIGHT"):
        settings["LEXICAL_WEIGHT"] = chat_mod.LEXICAL_WEIGHT

    if a.json:
        print(json.dumps(
            {"settings": settings, "summary": agg, "cases": records},
            indent=2, sort_keys=True, default=str))
    else:
        print(render(agg, records, settings, a.failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
