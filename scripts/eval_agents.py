#!/usr/bin/env python3
"""Measure the local model on each role it is asked to play.

Why this exists: "we added a local agent" is not a claim anyone should accept
without a number. Two of the four roles have ground truth already sitting in
the repository -- 134 adjudicated triage labels, and 31 counterexamples whose
expected values were derived from clause text and confirmed by re-execution --
so the model's fitness for those roles is measurable rather than asserted.

    scripts/eval_agents.py triage [--limit N] [--model M]
    scripts/eval_agents.py slotfill
    scripts/eval_agents.py reviewer [--rounds N] [--module STEM]
    scripts/eval_agents.py all

Triage is the headline number. It is a three-way classification over clauses
whose correct labels were adjudicated clause by clause with stated reasons, so
accuracy here says whether the model understands the RULE / PROSE / HYBRID
distinction at all -- and HYBRID recall says whether it can spot the case that
matters most, a computation gated on a judgement nobody has defined.
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lks import agents, llm
from lks.registry import build_registry, load_registry
from lks.segment import load_corpus
from lks.triage import load_ledger


def bar(n: int, total: int, width: int = 28) -> str:
    filled = int(round(width * n / total)) if total else 0
    return "#" * filled + "." * (width - filled)


def eval_triage(limit: int | None, model: str) -> dict:
    ledger = load_ledger()
    clauses = [c for d in load_corpus("corpus") for c in d.clauses]
    if limit:
        # take a spread across documents rather than the first N, which would
        # all be one document's preamble
        step = max(1, len(clauses) // limit)
        clauses = clauses[::step][:limit]

    rows, usage = [], llm.Usage()
    t0 = time.monotonic()
    for i, c in enumerate(clauses, 1):
        truth = ledger.get(c.ref)
        if truth is None:
            continue
        res = agents.triage_clause(c, model=model)
        usage = usage + res.usage
        got = res.value["label"] if res.ok else "ERROR"
        rows.append({
            "ref": c.ref,
            "truth": truth.label.value,
            "got": got,
            "ok": got == truth.label.value,
            "reason": (res.value.get("reason") if res.ok else res.error)[:150],
            "judgement_inputs": (res.value.get("judgement_inputs") if res.ok else []),
            "truth_judgement": list(truth.judgement_inputs),
        })
        done = sum(1 for r in rows if r["ok"])
        print(f"\r  {i}/{len(clauses)} [{bar(i, len(clauses))}] "
              f"{done}/{len(rows)} correct", end="", flush=True)
    print()

    labels = ["RULE", "PROSE", "HYBRID"]
    matrix = Counter((r["truth"], r["got"]) for r in rows)
    correct = sum(1 for r in rows if r["ok"])

    print(f"\n  accuracy  {correct}/{len(rows)} = {100*correct/max(1,len(rows)):.1f}%")
    print(f"  wall      {time.monotonic()-t0:.0f}s    {usage}")

    print("\n  confusion (rows = adjudicated truth, columns = model)")
    print(f"    {'':8s}" + "".join(f"{l:>8s}" for l in labels) + f"{'ERROR':>8s}")
    for t in labels:
        n_t = sum(v for (tt, _), v in matrix.items() if tt == t)
        cells = "".join(f"{matrix.get((t, g), 0):>8d}" for g in labels)
        err = sum(v for (tt, gg), v in matrix.items() if tt == t and gg not in labels)
        recall = 100 * matrix.get((t, t), 0) / n_t if n_t else 0.0
        print(f"    {t:8s}{cells}{err:>8d}   n={n_t}  recall {recall:.0f}%")

    hyb = [r for r in rows if r["truth"] == "HYBRID"]
    if hyb:
        named = sum(1 for r in hyb if r["got"] == "HYBRID" and r["judgement_inputs"])
        print(f"\n  HYBRID clauses: {len(hyb)}; found as HYBRID with a judgement "
              f"input named: {named}")

    wrong = [r for r in rows if not r["ok"]]
    if wrong:
        print(f"\n  {len(wrong)} disagreement(s):")
        for r in wrong[:20]:
            print(f"    {r['ref']:22s} truth {r['truth']:6s} model {r['got']:6s}")
            print(f"      {r['reason']}")
    return {"n": len(rows), "correct": correct, "rows": rows,
            "accuracy": correct / max(1, len(rows))}


SLOT_CASES = [
    ("Overtime.HourPremium",
     "A grade 5 employee worked the 50th hour of the week on a public holiday, "
     "at night, and it was not a critical incident. No standing shift allowance.",
     {"ordinal": 50, "grade": 5, "is_public_holiday": True,
      "is_critical_incident": False, "is_night": True,
      "has_standing_shift_allowance": False}),
    ("Overtime.HourPremium",
     "A grade 3 employee worked their 41st hour this week.",
     {"ordinal": 41, "grade": 3}),
    ("ServiceCredits.ServiceCredit",
     "Availability was 98.5% last month and the monthly service charge is 10,000. "
     "The customer is not in arrears and the invoice is undisputed.",
     {"availability_percentage": 98.5, "monthly_service_charge": 10000.0,
      "arrears_days": 0, "invoice_undisputed": True}),
]


def eval_slotfill(model: str) -> dict:
    """Slot filling, and the property that matters more than accuracy: the
    model must LEAVE OUT a fact the question does not state rather than invent
    one, because an invented fact silently changes a legal answer."""
    reg = load_registry() or build_registry()
    from lks.catala_runner import json_schema
    ok = invented = 0
    for key, question, expect in SLOT_CASES:
        e = reg[key]
        in_schema, _ = json_schema(e.path, e.scope)
        types = {}
        defs = in_schema.get("definitions", {})
        root = defs.get(in_schema.get("$ref", "").split("/")[-1], {})
        for n, spec in (root.get("properties") or {}).items():
            r = spec.get("$ref", "")
            types[n] = r.split("/")[-1] if r else spec.get("type", "unknown")

        res = agents.extract_facts(question, types, model=model)
        print(f"\n  {key}")
        print(f"    Q: {question[:96]}")
        if not res.ok:
            print(f"    FAILED: {res.error[:150]}")
            continue
        got = res.value["facts"]
        extra = {k: v for k, v in got.items() if k not in expect}
        missing = {k: v for k, v in expect.items() if k not in got}
        wrong = {k: (got[k], expect[k]) for k in expect if k in got and got[k] != expect[k]}
        if extra:
            invented += 1
            print(f"    INVENTED facts the question does not state: {extra}")
        if wrong:
            print(f"    wrong values: {wrong}")
        if missing:
            print(f"    not extracted: {list(missing)}")
        if not extra and not wrong and not missing:
            ok += 1
            print(f"    exact: {got}")
        elif not extra and not wrong:
            print(f"    safe but incomplete (omitting is the correct failure)")
    print(f"\n  exact {ok}/{len(SLOT_CASES)}; cases with invented facts: {invented}")
    return {"exact": ok, "n": len(SLOT_CASES), "invented": invented}


def eval_reviewer(rounds: int, module: str, model: str) -> dict:
    """Can the model produce a finding the harness will ACCEPT?

    Acceptance is the bar, not plausibility: the harness re-executes the scope
    on the model's own inputs and rejects the finding unless the observed value
    genuinely differs from the expected one the model derived from the clause.
    """
    from lks.reviewer import record_findings
    packet_path = Path("reviewer/packets") / f"{module}.md"
    if not packet_path.exists():
        print(f"  no packet for {module}; run scripts/make_packet.py {module}")
        return {}
    packet = packet_path.read_text()
    reg = load_registry() or build_registry()
    scopes = [k for k in reg if reg[k].module.lower() == module.lower()]
    if not scopes:
        print(f"  no scope registered for module {module}")
        return {}
    key = scopes[0]
    e = reg[key]

    tried, accepted, malformed, verdicts = [], [], 0, Counter()
    for n in range(1, rounds + 1):
        res = agents.propose_attack(
            packet, key, e.path, e.scope, model=model, seed=None, already_tried=tried,
        )
        if not res.ok:
            malformed += 1
            print(f"  round {n}: malformed — {res.error[:110]}")
            continue
        f = res.value
        verdicts[f["verdict"]] += 1
        print(f"  round {n}: {f['verdict']}  {str(f.get('fact_pattern',''))[:88]}")
        tried.append(str(f.get("fact_pattern", ""))[:160])
        if f["verdict"] != "BREAK":
            continue
        out = record_findings([f], round=99)
        if out.accepted:
            accepted.append(out.accepted[0].id)
            print(f"    ACCEPTED as {out.accepted[0].id}")
        else:
            why = out.rejected[0][1] if out.rejected else "not accepted"
            print(f"    rejected: {why[:130]}")
    print(f"\n  {rounds} rounds: verdicts {dict(verdicts)}, malformed {malformed}, "
          f"accepted {len(accepted)} {accepted}")
    return {"rounds": rounds, "accepted": accepted, "malformed": malformed,
            "verdicts": dict(verdicts)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["triage", "slotfill", "reviewer", "all"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--module", default="overtime")
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        llm.require_model(a.model)
    except llm.ModelUnavailable as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"model {a.model}")
    print(f"isolation: {agents.TRIAGE.enforcement()}"
          f"{' (OpenShell absent — roles are only asked not to look)' if not agents.openshell_available() else ''}")
    print("warming the weights…", end=" ", flush=True)
    print(llm.warm(a.model))

    out = {}
    if a.what in ("triage", "all"):
        print("\n== triage: 3-way classification against the adjudicated ledger ==")
        out["triage"] = eval_triage(a.limit, a.model)
    if a.what in ("slotfill", "all"):
        print("\n== slotfill: extract stated facts, invent nothing ==")
        out["slotfill"] = eval_slotfill(a.model)
    if a.what in ("reviewer", "all"):
        print(f"\n== reviewer: can it produce a finding the harness accepts? ==")
        out["reviewer"] = eval_reviewer(a.rounds, a.module, a.model)
    if a.json:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
