#!/usr/bin/env python3
"""Run the adversarial review loop unattended, on the local model.

    scripts/review_loop.py --module overtime --rounds 40
    scripts/review_loop.py --all --quiet-rounds 25
    scripts/review_loop.py --status

The brief's acceptance criterion is that "the reviewer has failed to break it
across a sustained run". Until now that was a thing a person did for as long as
they had patience for, which is why no component could honestly be called done.
This makes it a measurement: the loop keeps attacking until it has gone
`--quiet-rounds` consecutive rounds without producing a finding the harness
accepts, and it records how many rounds that took so the claim has a number
behind it.

Two properties make an unattended loop safe to trust:

  * The model never decides whether it broke anything. A proposed finding is
    accepted only if re-executing the scope on the model's own inputs really
    does contradict the expected value it derived from the clause, and only if
    it cited clauses. Everything else is discarded.

  * Attacks already tried are fed back into each round, so the loop explores
    instead of proposing its favourite fact pattern forever. The log persists
    between runs, so stopping and resuming does not reset the search.
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lks import agents, llm
from lks.registry import build_registry, load_registry
from lks.reviewer import record_findings

STATE = Path("reviewer/loop-state.json")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"modules": {}}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def regenerate_packet(module: str) -> bool:
    r = subprocess.run(
        [sys.executable, "scripts/make_packet.py", module],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def scopes_for(module: str) -> list[tuple[str, str, str]]:
    reg = load_registry() or build_registry()
    out = []
    for key, e in sorted(reg.items()):
        if e.module.lower() == module.lower():
            out.append((key, e.path, e.scope))
    return out


def run_module(module: str, rounds: int, quiet_target: int, model: str,
               state: dict) -> dict:
    """Attack one module until it goes quiet or the round budget runs out."""
    if not regenerate_packet(module):
        print(f"  cannot build a packet for {module}; skipping")
        return {}
    packet = (Path("reviewer/packets") / f"{module}.md").read_text()
    targets = scopes_for(module)
    if not targets:
        print(f"  no registered scope for {module}; skipping")
        return {}

    entry = state["modules"].setdefault(module, {
        "tried": [], "accepted": [], "rounds_total": 0,
        "longest_quiet_streak": 0, "ambiguities": 0, "malformed": 0,
    })
    quiet = 0
    t0 = time.monotonic()

    for n in range(1, rounds + 1):
        key, path, scope = targets[(n - 1) % len(targets)]
        res = agents.propose_attack(
            packet, key, path, scope, model=model, seed=None,
            already_tried=entry["tried"],
        )
        entry["rounds_total"] += 1

        if not res.ok:
            entry["malformed"] += 1
            quiet += 1
            print(f"  {n:>3}  {scope:<24} malformed      {res.error[:60]}")
            continue

        f = res.value
        verdict = f["verdict"]
        fact = str(f.get("fact_pattern", ""))[:150]
        if fact:
            entry["tried"].append(fact)

        if verdict == "NO_BREAK_FOUND":
            quiet += 1
            print(f"  {n:>3}  {scope:<24} no break       quiet {quiet}/{quiet_target}")
        elif verdict == "AMBIGUITY":
            entry["ambiguities"] += 1
            quiet += 1
            print(f"  {n:>3}  {scope:<24} ambiguity      {fact[:56]}")
        else:
            out = record_findings([f], round=90)
            if out.accepted:
                ce = out.accepted[0]
                entry["accepted"].append(ce.id)
                quiet = 0
                print(f"  {n:>3}  {scope:<24} BREAK -> {ce.id}  {fact[:44]}")
            else:
                why = out.rejected[0][1] if out.rejected else "not accepted"
                quiet += 1
                print(f"  {n:>3}  {scope:<24} rejected       {why[:58]}")

        entry["longest_quiet_streak"] = max(entry["longest_quiet_streak"], quiet)
        save_state(state)
        if quiet >= quiet_target:
            print(f"  -- {module} went quiet: {quiet} consecutive rounds with no "
                  f"accepted finding")
            break

    entry["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry["last_wall_seconds"] = round(time.monotonic() - t0, 1)
    save_state(state)
    return entry


def show_status(state: dict, quiet_target: int) -> None:
    mods = state.get("modules", {})
    if not mods:
        print("The loop has not run yet.")
        return
    print(f"{'module':<22}{'rounds':>8}{'breaks':>8}{'ambig':>7}"
          f"{'quiet':>7}  status")
    for m, e in sorted(mods.items()):
        streak = e.get("longest_quiet_streak", 0)
        status = ("quiet — survived a sustained run" if streak >= quiet_target
                  else f"needs {quiet_target - streak} more quiet rounds")
        if e.get("accepted"):
            status += f"  (found {len(e['accepted'])}: {', '.join(e['accepted'][-4:])})"
        print(f"{m:<22}{e.get('rounds_total',0):>8}{len(e.get('accepted',[])):>8}"
              f"{e.get('ambiguities',0):>7}{streak:>7}  {status}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", action="append", help="repeatable")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--rounds", type=int, default=20, help="round budget per module")
    ap.add_argument("--quiet-rounds", type=int, default=15,
                    help="consecutive rounds with no accepted finding before a "
                         "module counts as having survived")
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    state = load_state()
    if a.status:
        show_status(state, a.quiet_rounds)
        return 0

    modules = a.module or []
    if a.all:
        reg = load_registry() or build_registry()
        modules = sorted({Path(e.path).stem for e in reg.values()})
    if not modules:
        print("Name a module with --module, or use --all.", file=sys.stderr)
        return 2

    try:
        llm.require_model(a.model)
    except llm.ModelUnavailable as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    enforcement = agents.REVIEWER.enforcement()
    print(f"model {a.model}   isolation {enforcement}")
    if enforcement == agents.Enforcement.PROMPT:
        print("  NOTE: OpenShell is not present, so the reviewer is only ASKED not "
              "to read the implementer's reasoning. Its packet is still stripped, "
              "but the isolation is not enforced by the filesystem.")
    print("warming the weights…", end=" ", flush=True)
    print(llm.warm(a.model))

    for m in modules:
        print(f"\n== {m} ==")
        run_module(m, a.rounds, a.quiet_rounds, a.model, state)

    print()
    show_status(state, a.quiet_rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
