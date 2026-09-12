#!/usr/bin/env python3
"""Run the adversarial review loop unattended, on the local model.

    scripts/review_loop.py --module overtime --rounds 40
    scripts/review_loop.py --all --quiet-rounds 25
    scripts/review_loop.py --status
    scripts/review_loop.py --module overtime --details   # add the technical detail

The brief's acceptance criterion is that "the reviewer has failed to break it
across a sustained run". Until now that was a thing a person did for as long as
they had patience for, which is why no component could honestly be called done.
This makes it a measurement: the loop keeps attacking until it has gone
`--quiet-rounds` consecutive attempts without finding a defect, and it records
how many attempts that took so the claim has a number behind it.

Two properties make an unattended loop safe to trust:

  * The model never decides whether it broke anything. A proposed finding is
    accepted only if re-executing the scope on the model's own inputs really
    does contradict the expected value it derived from the clause, and only if
    it cited clauses. Everything else is discarded.

  * Attacks already tried are fed back into each round, so the loop explores
    instead of proposing its favourite fact pattern forever. The log persists
    between runs, so stopping and resuming does not reset the search.

And two that make the claim honest. Only an attempt that actually ran the rule
counts towards a quiet run. An earlier version counted every round, so a
module where the model never produced a usable answer "went quiet" after
fifteen failures and was reported as having survived; the version after that
still counted a reviewer saying NO_BREAK_FOUND or AMBIGUITY, neither of which
runs anything, so a model that proposed nothing fifteen times signed a module
off. And a sign-off belongs to the version of the rule it was earned on
(`lks.reviewer.rule_fingerprint`): when the rule or the clauses it quotes
change, the clean run starts again, and a defect found later withdraws it.

Output is written for someone who does not read Catala. Every attempt says
what happened in a sentence; a defect gets a full write-up in
`reviewer/reports/`; every attempt, with its technical detail, is appended to
`reviewer/rounds/<module>.jsonl`.
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lks import agents, llm, plain
from lks.registry import build_registry, load_registry
from lks.reviewer import REPORTS, record_findings, rule_fingerprint

STATE = Path("reviewer/loop-state.json")
ROUNDS = Path("reviewer/rounds")
STALL_LIMIT = 3
"""Attempts in a row the model does not answer before a module is abandoned."""

LABEL = {
    "defect": "DEFECT FOUND",
    "held": "NO PROBLEM",
    "no_break": "NOTHING TO TEST",
    "ambiguity": "DOCUMENT UNCLEAR",
    "rejected": "CLAIM DID NOT CHECK OUT",
    "no_answer": "NO ANSWER",
    "error": "CHECKER ERROR",
}


def say(text: str = "", indent: int = 0) -> None:
    pad = " " * indent
    if not text:
        print(flush=True)
        return
    print(textwrap.fill(text, width=88, initial_indent=pad, subsequent_indent=pad,
                        break_long_words=False, break_on_hyphens=False), flush=True)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"modules": {}}


def save_state(state: dict, module: str | None = None) -> None:
    """Write the state. With `module`, only that module's entry is written,
    over what is on disk now and under a lock.

    Each run loads the whole file when it starts and used to write the whole
    file back, so two runs on different modules erased each other's progress:
    whichever saved last put back its own stale copy of the other module."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    ROUNDS.mkdir(parents=True, exist_ok=True)
    with open(ROUNDS / ".state.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if module is not None:
            current = load_state()
            current.setdefault("modules", {})[module] = state["modules"][module]
            state = current
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, STATE)


def log_round(module: str, record: dict) -> None:
    ROUNDS.mkdir(parents=True, exist_ok=True)
    with (ROUNDS / f"{module}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def regenerate_packet(module: str) -> tuple[bool, str]:
    """(whether the packet was written, and why not if it was not)."""
    r = subprocess.run(
        [sys.executable, "scripts/make_packet.py", module],
        capture_output=True, text=True,
    )
    why = (r.stderr or r.stdout).strip().splitlines()
    return r.returncode == 0, (why[-1][:200] if why else f"exit {r.returncode}")


def scopes_for(module: str) -> list[tuple[str, str, str]]:
    reg = load_registry() or build_registry()
    out = []
    for key, e in sorted(reg.items()):
        if e.module.lower() == module.lower():
            out.append((key, e.path, e.scope))
    return out


def new_entry() -> dict:
    return {"tried": {}, "accepted": [], "rounds_total": 0, "longest_quiet_streak": 0,
            "ambiguities": 0, "malformed": 0, "errors": 0, "held": 0, "no_break": 0,
            "rejected": 0, "ambiguity_notes": [], "fingerprint": ""}


def module_path(module: str) -> Path:
    return Path("catala/modules") / f"{module}.catala_en"


def score(outcome: str, entry: dict, quiet: int, stalled: int) -> tuple[int, int]:
    """Fold one attempt into the run and the module's record: (quiet, stalled).

    Only `held` extends a clean run: the rule was executed on the reviewer's
    situation and gave the answer the reviewer derived from the clauses.
    `no_break` and `ambiguity` execute nothing, so they neither extend a clean
    run nor end one. A defect ends it, and withdraws a sign-off already earned
    on this version of the rule, because it shows the rule did not hold up."""
    if outcome == "defect":
        entry["longest_quiet_streak"] = 0
        return 0, 0
    if outcome == "held":
        entry["held"] += 1
        quiet += 1
        entry["longest_quiet_streak"] = max(entry["longest_quiet_streak"], quiet)
        return quiet, 0
    if outcome in ("no_break", "ambiguity"):
        entry["no_break" if outcome == "no_break" else "ambiguities"] += 1
        return quiet, 0
    if outcome == "rejected":
        entry["rejected"] += 1
        return quiet, 0
    if outcome == "error":
        # the checker failed, not the model: counted apart, and not a stall
        entry["errors"] += 1
        return quiet, stalled
    entry["malformed"] += 1
    return quiet, stalled + 1


def attempt(module: str, path: str, key: str, scope: str, packet: str,
            entry: dict, model: str) -> tuple[str, list[str], dict]:
    """One attempt: (outcome, lines to show, record to log)."""
    # per scope: the prompt calls these the situations "already tried on this
    # rule", and one list for every scope in a module made that untrue
    tried_here = entry["tried"].setdefault(scope, [])
    res = agents.propose_attack(packet, key, path, scope, model=model, seed=None,
                                already_tried=tried_here)
    record = {"tokens": res.usage.completion_tokens}
    if not res.ok:
        record.update(kind=res.kind, error=res.error)
        return "no_answer", [plain.failed_attempt(res.kind, res.error)], record

    f = res.value
    record["finding"] = f
    fact = str(f.get("fact_pattern", "")).strip()
    if fact:
        tried_here.append(fact[:150])
    tried = [f"Situation tried: {fact[:220]}{'…' if len(fact) > 220 else ''}"] if fact else []

    if f["verdict"] == "NO_BREAK_FOUND":
        return "no_break", ["The reviewer did not propose a situation to test, so the rule "
                            "was not run. This does not count towards sign-off."] + tried, record

    if f["verdict"] == "AMBIGUITY":
        what = str(f.get("headline") or fact).strip()
        entry["ambiguity_notes"].append({"scope": scope, "note": what,
                                         "citations": f.get("citations") or []})
        return "ambiguity", [
            "The reviewer thinks the document itself does not settle this situation, "
            "so a lawyer should look at the wording. Nothing was recorded against the rule, "
            "and because the rule was not run, this does not count towards sign-off.",
            f"In the reviewer's words: {what}" if what else "",
            f"Clauses: {plain.names(f.get('citations') or [])}" if f.get("citations") else "",
        ], record

    out = record_findings([f], round=entry["rounds_total"] + 1)
    if out.accepted:
        ce = out.accepted[0]
        entry["accepted"].append(ce.id)
        record["accepted"] = ce.id
        rows = plain.comparison(ce.expected, ce.observed)
        lines = [f"{ce.id}: {ce.headline or plain.break_summary(ce)}"]
        lines += [f"The document requires: {plain.names([f'{r} {w}' for r, w, _ in rows])}",
                  f"The system gives:      {plain.names([f'{r} {g}' for r, _, g in rows])}",
                  f"Clauses: {plain.names(ce.citations)}",
                  f"Full write-up: {REPORTS / (ce.id + '.md')}"]
        return "defect", lines, record
    why = out.rejected[0][1] if out.rejected else "It was not accepted."
    record["rejected"] = why
    if out.held:
        return "held", [why] + tried, record
    return "rejected", ["The reviewer's claim could not be confirmed, so nothing was "
                        "recorded. This is a mistake by the reviewer, not a problem "
                        "with the rule.", f"Why: {why}"], record


def show_details(record: dict) -> None:
    f = record.get("finding") or {}
    detail = {k: f.get(k) for k in ("verdict", "inputs", "expected", "citations") if k in f}
    if record.get("error"):
        detail["error"] = record["error"]
    if detail:
        for line in json.dumps(detail, indent=2, default=str).splitlines():
            print(f"      {line}", flush=True)


def run_module(module: str, rounds: int, quiet_target: int, model: str,
               state: dict, details: bool = False) -> dict:
    """Attack one module until it goes quiet or the attempt budget runs out.

    One run per module at a time: two runs on the same module would each count
    their own clean run and write it over the other's."""
    name = plain.rule_name(module, None)
    ROUNDS.mkdir(parents=True, exist_ok=True)
    with open(ROUNDS / f".{module}.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            say(f"Another review of {name} is already running, so this one was skipped.")
            return {}
        on_disk = load_state().get("modules", {}).get(module)
        if on_disk is not None:
            state.setdefault("modules", {})[module] = on_disk
        return _run_module(module, name, rounds, quiet_target, model, state, details)


def _run_module(module: str, name: str, rounds: int, quiet_target: int, model: str,
                state: dict, details: bool) -> dict:
    ok, why = regenerate_packet(module)
    if not ok:
        say(f"Could not prepare the review material for {name}, so it was skipped: {why}")
        return {}
    packet = (Path("reviewer/packets") / f"{module}.md").read_text()
    targets = scopes_for(module)
    if not targets:
        say(f"{name} has no rules registered for review; skipped.")
        return {}

    entry = state["modules"].setdefault(module, new_entry())
    for k, v in new_entry().items():
        entry.setdefault(k, v)
    if not isinstance(entry["tried"], dict):
        entry["tried"] = {}     # an earlier version kept one list for every scope
    fingerprint = rule_fingerprint(module_path(module))
    if entry["fingerprint"] != fingerprint:
        if entry["fingerprint"] or entry["longest_quiet_streak"]:
            say(f"{name}, or the clauses it quotes, is not the version last reviewed, so its "
                f"earlier clean run no longer counts and earlier situations will be tried "
                f"again.")
        entry.update(fingerprint=fingerprint, longest_quiet_streak=0, tried={})
    counts = {k: 0 for k in LABEL}
    quiet = stalled = broken = 0
    t0 = time.monotonic()
    interrupted = False

    say(f"── {name}: {len(targets)} rule{'s' if len(targets) != 1 else ''} "
        + "─" * max(4, 60 - len(name)))
    for n in range(1, rounds + 1):
        # carried on from where the last run stopped: restarting at the first
        # scope every run left the later scopes of a large module unattacked
        key, path, scope = targets[entry["rounds_total"] % len(targets)]
        say()
        say(f"Attempt {n} of {rounds} · {plain.rule_name(module, scope)}")
        started = time.monotonic()
        try:
            outcome, lines, record = attempt(module, path, key, scope, packet, entry, model)
        except KeyboardInterrupt:
            interrupted = True
            say("Stopped by you.", 2)
            break
        except Exception as e:                                  # noqa: BLE001
            outcome, record = "error", {"error": f"{type(e).__name__}: {e}"}
            lines = [f"The checker itself hit a problem on this attempt, so it was skipped: "
                     f"{type(e).__name__}: {str(e)[:160]}"]
        secs = time.monotonic() - started
        entry["rounds_total"] += 1
        counts[outcome] += 1

        say(f"{LABEL[outcome]}  ({plain.duration(secs)})", 2)
        for line in lines:
            if line:
                say(line, 4)
        if details:
            show_details(record)

        quiet, stalled = score(outcome, entry, quiet, stalled)
        broken = broken + 1 if outcome == "error" else 0

        log_round(module, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "module": module, "scope": scope, "attempt": n,
                           "outcome": outcome, "plain": [l for l in lines if l],
                           "seconds": round(secs, 1), **record})
        save_state(state, module)

        if quiet >= quiet_target:
            say()
            say(f"{name} held up for {quiet} attempts in a row, which is the bar for "
                f"sign-off. Stopping here.")
            break
        if stalled >= STALL_LIMIT:
            say()
            say(f"The last {stalled} attempts got no usable answer from the model, so "
                f"{name} was abandoned rather than keep waiting. Check `lks agent status`, "
                f"and whether another job is using the model.")
            break
        if broken >= STALL_LIMIT:
            say()
            say(f"The checker itself failed on the last {broken} attempts, so {name} was "
                f"stopped. The fault is in the checker or the rule, not the model: "
                f"{str(record.get('error', ''))[:200]}")
            break

    wall = time.monotonic() - t0
    entry["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry["last_wall_seconds"] = round(wall, 1)
    save_state(state, module)
    summarise(name, counts, entry, quiet_target, wall)
    if interrupted:
        raise KeyboardInterrupt
    return entry


def summarise(name: str, counts: dict, entry: dict, quiet_target: int, wall: float) -> None:
    total = sum(counts.values())
    say()
    say(f"Summary for {name}: {total} attempt{'s' if total != 1 else ''} in "
        f"{plain.duration(wall)}")
    found = entry["accepted"][-counts["defect"]:] if counts["defect"] else []
    rows = [
        (counts["defect"], "defect found", "defects found",
         f": {plain.names(found)}, written up in {REPORTS}/" if found else ""),
        (counts["ambiguity"], "place where the document itself may be unclear",
         "places where the document itself may be unclear", " (not counted towards sign-off)"),
        (counts["held"], "attempt where the rule gave the answer the document requires",
         "attempts where the rule gave the answer the document requires", ""),
        (counts["no_break"], "attempt where the reviewer proposed nothing to test",
         "attempts where the reviewer proposed nothing to test",
         " (not counted towards sign-off)"),
        (counts["rejected"], "claim that did not check out (a reviewer mistake, not a rule problem)",
         "claims that did not check out (reviewer mistakes, not rule problems)", ""),
        (counts["no_answer"], "attempt with no usable answer from the model",
         "attempts with no usable answer from the model", ""),
        (counts["error"], "attempt the checker itself could not complete",
         "attempts the checker itself could not complete", ""),
    ]
    for num, one, many, extra in rows:
        if num:
            say(f"{num} {one if num == 1 else many}{extra}", 2)
    best = entry["longest_quiet_streak"]
    if best >= quiet_target:
        say(f"Signed off: at some point it held up for {best} attempts in a row "
            f"(the bar is {quiet_target}).", 2)
    else:
        say(f"Not signed off yet: it needs {quiet_target} attempts in a row with no "
            f"defect, and its best run so far is {best}.", 2)


def show_status(state: dict, quiet_target: int) -> None:
    mods = state.get("modules", {})
    if not mods:
        print("The review loop has not run yet.")
        return
    print(f"{'Document area':<22}{'Attempts':>9}{'Defects':>9}{'Unclear':>9}"
          f"{'Best clean run':>16}  Status")
    for m, e in sorted(mods.items()):
        try:
            current = rule_fingerprint(module_path(m))
        except Exception:                                       # noqa: BLE001
            current = None
        earned = e.get("longest_quiet_streak", 0)
        stale = e.get("fingerprint") != current
        streak = 0 if stale else earned
        status = ("signed off" if streak >= quiet_target
                  else f"needs {quiet_target - streak} more clean attempts in a row")
        if stale and earned:
            status += "; its earlier clean run was on a different version of the rule"
        if e.get("accepted"):
            status += f"; defects: {', '.join(e['accepted'][-4:])}"
        print(f"{plain.rule_name(m, None):<22}{e.get('rounds_total', 0):>9}"
              f"{len(e.get('accepted', [])):>9}{e.get('ambiguities', 0):>9}"
              f"{f'{streak} of {quiet_target}':>16}  {status}")


ISOLATION = {
    agents.Enforcement.SANDBOX: "the reviewer runs in a sandbox holding only the documents "
                                "and the rules",
    agents.Enforcement.UNPROVEN: "the reviewer is asked not to read the implementers' notes, "
                                 "and sees a copy of the rules with those notes removed; the "
                                 "sandbox that would enforce this has not been proven yet",
    agents.Enforcement.PROMPT: "the reviewer is asked not to read the implementers' notes, "
                               "and sees a copy of the rules with those notes removed; "
                               "nothing else enforces it",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", action="append", help="repeatable")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--rounds", type=int, default=20, help="attempt budget per module")
    ap.add_argument("--quiet-rounds", type=int, default=15,
                    help="attempts in a row without a defect before a module is signed off")
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--details", action="store_true",
                    help="also print each attempt's inputs, expected answer and errors")
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
        print(f"The model is not available, so the review cannot run: {e}", file=sys.stderr)
        return 1

    say("Adversarial review: a reviewer looks for situations where a rule gives a "
        "different answer from the document it is meant to follow. Nothing the reviewer "
        "claims is believed until the rule has been run on that situation.")
    say(f"Model: {a.model}", 2)
    say(f"Isolation: {ISOLATION.get(agents.REVIEWER.enforcement(), agents.REVIEWER.enforcement())}", 2)
    say("An attempt normally takes one to two minutes, and longer while the model is "
        "busy with other work.", 2)
    say("Loading the model…", 2)
    try:
        llm.warm(a.model)
    except llm.ModelUnavailable as e:
        say(f"The model did not load ({e}). Attempts will still be made.", 2)

    try:
        for m in modules:
            say()
            run_module(m, a.rounds, a.quiet_rounds, a.model, state, a.details)
    except KeyboardInterrupt:
        say()
        say("Stopped. Progress so far is saved and the next run carries on from it.")

    print()
    show_status(state, a.quiet_rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
