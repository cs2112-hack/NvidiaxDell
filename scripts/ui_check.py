#!/usr/bin/env python3
"""Put the web interface through every state it can reach, in a real headless
browser, and fail if the layout breaks.

    . scripts/env.sh
    $PY scripts/ui_check.py                          # every scenario, every width
    $PY scripts/ui_check.py --only check-,tour-      # scenarios whose names start so
    $PY scripts/ui_check.py --widths 1440,400 --shots .run/ui
    $PY scripts/ui_check.py --api http://127.0.0.1:8765 --record   # refresh replies

Why it exists. The risk check looked right until its agents ran: the status dot
carried the class `run`, the past-drafts list styled `.run` as a full-width
grid, and mid-job the dot swelled into a pill that ran over the top bar and the
agent cards. No single rule was wrong. The states an interface reaches only
while a job is running are exactly the ones nobody looks at before shipping, so
this drives the interface into each of them with scripted job events and
measures the result, instead of trusting a screenshot taken while idle.

Three things are checked:

  1. app.css, statically: no class is both styled on its own and used as the
     modifier of another class. That is the collision above, found without a
     browser.
  2. Each scenario's steps run without a script error, a missing element, or a
     request the scenario did not expect.
  3. The layout holds at each width: nothing scrolls sideways, the top bar keeps
     its height and its controls do not overlap, dots stay dots, chips stay on
     one line, fixed marks keep their size, and nothing spills out of its box.

Jobs and drafting runs are scripted here, so a scenario means the same thing on
every machine and never touches the local model. The read-only endpoints (ask,
scope, clause, state, queue, agent, scopes) are replayed from
tests/ui/api_snapshot.json, recorded from a live server with --record, so the
answers on screen are real answers.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import copy
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DRIVER = ROOT / "tests" / "ui" / "driver.js"
SNAPSHOT = ROOT / "tests" / "ui" / "api_snapshot.json"
WIDTHS = (1440, 1024, 760, 400)
READ_ONLY = ("/api/state", "/api/agent", "/api/exposure/queue", "/api/exposure/scopes",
             "/api/ask", "/api/scope", "/api/clause")


# --- 1. the static check --------------------------------------------------

def css_collisions(css: str) -> list[str]:
    """Classes styled on their own that are also used as a modifier.

    `.run { display: grid }` styles anything carrying `run`. `.dot.run` uses
    `run` as a modifier of `.dot`. An element given both gets the grid: that is
    the whole bug, and it is visible in the stylesheet alone.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    standalone: dict[str, str] = {}
    modifier: dict[str, str] = {}
    for m in re.finditer(r"([^{}]+)\{", css):
        selectors = m.group(1).strip()
        if not selectors or selectors.startswith("@") or re.fullmatch(r"(from|to|[\d.]+%)", selectors):
            continue
        for sel in selectors.split(","):
            sel = sel.strip()
            plain = sel
            while re.search(r"\([^()]*\)", plain):
                plain = re.sub(r"\([^()]*\)", "", plain)
            plain = re.sub(r"\[[^\]]*\]", "", plain)
            plain = re.sub(r"::?[\w-]+", "", plain)
            compounds = [c for c in re.split(r"[\s>+~]+", plain) if c]
            for i, compound in enumerate(compounds):
                classes = re.findall(r"\.([A-Za-z_][\w-]*)", compound)
                # `textarea.field` narrows .field to one element; it modifies no class.
                qualified = bool(re.match(r"[A-Za-z*#]", compound))
                if len(classes) >= 2:
                    for c in classes[1:]:
                        modifier.setdefault(c, sel)
                elif len(classes) == 1 and not qualified and i == len(compounds) - 1:
                    standalone.setdefault(classes[0], sel)
    return [
        f".{c} modifies another class in `{where}` and is styled on its own in "
        f"`{standalone[c]}`: an element given the modifier also takes that style"
        for c, where in sorted(modifier.items()) if c in standalone
    ]


# --- 2. scripted data ------------------------------------------------------

SWEEP = [
    ("operations", "Reconcile what we did against what we wrote"),
    ("rivalries", "Execute both sides of every rivalry"),
    ("reverify", "Re-execute every finding on the queue"),
    ("fleet", "Send the fleet against the rules"),
]
TARGETS = [
    ("Overtime.HourPremium", "auditor"), ("ClaimWindow.ClaimAdmissible", "contractor"),
    ("ServiceCredits.ServiceCredit", "customer"), ("Accommodation.NightlyAccommodation", "employment"),
    ("Availability.AvailabilityPercentage", "regulator"), ("Clawback.RecoverableCommission", "auditor"),
]
ROLE = {
    "auditor": "Auditor reconciling what the policy says against what it pays",
    "contractor": "Disgruntled contractor or leaver chasing what they are owed",
    "customer": "Counsel for a customer under the services agreement",
    "employment": "Plaintiff's employment lawyer looking for an unpaid entitlement",
    "regulator": "Regulator testing whether the policy can be complied with",
}
# Real text from a fleet round, so line lengths are what the model writes.
NARRATIVE = (
    "A Grade 6 incident engineer works a night hour on a public holiday during a critical incident, "
    "at ordinal 50 (exceeding 48). The documents grant overtime under C-5.2 and a holiday rate under "
    "C-7. C-8.1 mandates taking the 'highest applicable multiplier.' C-7.2 defines the holiday rate as "
    "max(2.0, 1.75) = 2.0. C-5.2 grants 1.5. The highest is 2.0. However, C-6 states the night premium "
    "is cumulative. The policy fails to define whether the night premium stacks with the "
    "*substitutionary* holiday rate or only the overtime rate."
)
UNBROKEN = "availability_percentage_measured_across_the_calendar_month_excluding_scheduled_maintenance_windows"
LONG_SCOPE = "Accommodation.NightlyAccommodationForStaffTravellingOnCompanyBusinessOutsideTheirHomeRegion"
LONG_REQUEST = (
    "A remote working equipment allowance policy for England and Wales. Employees who work from home "
    "on at least 2 days in a normal working week receive a one-off equipment allowance of £300 and a "
    "monthly utilities allowance of £25. Part-time employees receive the monthly allowance pro rata to "
    "their contracted weekly hours out of 37.5. The one-off allowance is repayable in full if the "
    "employee leaves within 6 months of receiving it, and half of it is repayable if they leave after "
    "6 months but within 12 months."
)
G1_DIAGNOSTIC = (
    "┌─[ERROR]─ 1/5 ─\n│\n│  Syntax error at \"(\":\n│  » expected a binary operator continuing the "
    "expression, or a keyword\n│    ending the expression and starting the next item.\n│\n├─➤ "
    "generated/remote-working-allowance-3/RemoteWorkingAllowance.catala_en:101.62-63:\n│ 101 │   "
    "definition six_month_anniversary equals first_day_of_month_following(payment_date + 6 month)"
)


def ev(t: float, kind: str, **data) -> dict:
    return {"type": kind, "t": t, **data}


def job(jid: str, kind: str, *, status: str = "running", events=(), lines=(), params=None,
        result=None, error: str = "", seconds: float = 300.0, progress=None, script=None, label: str = "") -> dict:
    return {
        "id": jid, "kind": kind, "label": label or {"sweep": "autonomous sweep", "fleet": "run the attack fleet",
                                                    "generate": "generate a document", "letter": "draft the demand letter",
                                                    "close": "propose a fix and measure it",
                                                    "doc-watch": "contradiction check"}[kind],
        "params": params or {}, "status": status, "error": error, "progress": progress or {},
        "result": result, "started": time.time() - seconds, "seconds": seconds,
        "events": list(events), "lines": list(lines), "script": script or [],
    }


def payload(j: dict, since: int = 0, since_events: int = 0, brief: bool = False) -> dict:
    out = {k: j[k] for k in ("id", "kind", "label", "params", "status", "error", "progress", "result", "started", "seconds")}
    out["n_lines"] = len(j["lines"])
    out["n_events"] = len(j["events"])
    if not brief:
        out["lines"] = j["lines"][since:]
        out["events"] = j["events"][since_events:]
    return out


def sweep_prelude(rounds: int, stale: bool = False) -> list[dict]:
    e = [ev(0, "plan", stages=[{"key": k, "title": t} for k, t in SWEEP], rounds=rounds, scopes=[])]
    e += [ev(0, "stage", key="operations", title=SWEEP[0][1], status="running"),
          ev(0.9, "stage", key="operations", title=SWEEP[0][1], status="done",
             summary="14 decisions executed: 8 agree with the policy, 8 diverge, 0 new on the queue", recorded=[]),
          ev(0.9, "stage", key="rivalries", title=SWEEP[1][1], status="running"),
          ev(1.8, "stage", key="rivalries", title=SWEEP[1][1], status="done",
             summary="1 pair of rules executed on both sides, 0 contradictions"),
          ev(1.8, "stage", key="reverify", title=SWEEP[2][1], status="running", total=9)]
    for i in range(1, 10):
        ok = not (stale and i == 7)
        e.append(ev(1.9, "verify", id=f"EXP-{i:04d}", klass="DIVERGENCE", scope=TARGETS[i % 6][0], ok=ok,
                    why="the record still diverges from the policy" if ok else "the record no longer diverges", n=i, of=9))
    e.append(ev(1.9, "stage", key="reverify", title=SWEEP[2][1], status="done",
                summary=f"{8 if stale else 9} of 9 still reproduce"))
    return e


def fleet_start(rounds: int, t: float = 2.0) -> list[dict]:
    return [ev(t, "stage", key="fleet", title=SWEEP[3][1], status="running", note="loading the model's weights",
               total=rounds, model="qwen3.6:27b-q4_K_M", isolation="sandbox-unproven")]


def attack(n: int, of: int, phase: str, t: float, *, plain: bool = True, long: bool = False) -> list[dict]:
    scope, arch = TARGETS[(n - 1) % len(TARGETS)]
    if long:
        scope = LONG_SCOPE
    base = {"n": n, "of": of, "scope": scope, "archetype": arch, "role": ROLE[arch]}
    out = [ev(t, "round", phase="proposing", **base)]
    if phase == "proposing":
        return out
    if phase in ("malformed", "unanswered"):
        final = {"klass": "", "recorded": "", "predicate": "", "amount": None, "amount_basis": "",
                 "why": "no JSON object in the reply" if phase == "malformed" else "the model did not answer within 900s"}
        if plain:
            final["plain_why"] = ("The model took too long to reply (it was probably busy with another job), so this attempt was skipped."
                                  if phase == "unanswered" else "The agent's reply could not be read, so nothing was executed.")
        out.append(ev(t + 60, "round", phase=phase, **final, **base))
        return out
    facts = {"ordinal": 50, "grade": 6, "is_public_holiday": True, "is_night_hour": True, "hourly_base_rate": 42.5}
    if long:
        facts[UNBROKEN] = 98.5
    out.append(ev(t + 200, "round", phase="adjudicating", narrative=NARRATIVE + (" " + UNBROKEN if long else ""),
                  contends="The documents fail to say whether the night premium stacks.",
                  citations=["EMP-ANNEX-C C-5.2", "EMP-ANNEX-C C-7.2", "EMP-ANNEX-C C-8.1"], facts=facts, **base))
    if phase == "adjudicating":
        return out
    final = {"klass": "", "recorded": "", "predicate": "", "amount": None, "amount_basis": ""}
    final.update({
        "died": {"why": "the scope computed an answer and no predicate fired",
                 "plain_why": "the rule gave an answer, and it is not one that any known claim against the company relies on"},
        "known": {"why": "the outcome matches EXP-0009, already on the queue", "klass": "ADVERSE",
                  "plain_why": "this lands on a risk already on the list (EXP-0009)"},
        "landed": {"why": "predicate overtime_premium_unpaid fired on the executed outputs", "klass": "ADVERSE",
                   "recorded": "EXP-0009", "amount": 1.25,
                   "plain_why": "the rule's own answer supports a claim against the company",
                   "amount_basis": "policy computes 1.25x on an hour the company pays at 0; difference 1.25 on one employee-hour"},
    }[phase])
    if not plain:
        final.pop("plain_why", None)
    out.append(ev(t + 201, "round", phase=phase, **final, **base))
    return out


def sweep_job(jid: str, rounds: int, phases: list[str], *, status: str = "running", plain: bool = True,
              long: bool = False, failed: str = "", kind: str = "sweep", stale: bool = False) -> dict:
    events = sweep_prelude(rounds, stale) if kind == "sweep" else []
    events += fleet_start(rounds)
    t = 120.0
    for i, phase in enumerate(phases, 1):
        events += attack(i, rounds, phase, t, plain=plain, long=long)
        t += 300
    done = sum(1 for p in phases if p not in ("proposing", "adjudicating"))
    if status == "done":
        events.append(ev(t, "stage", key="fleet", title=SWEEP[3][1], status="done",
                         summary=f"{rounds} attacks across {rounds} rules; {phases.count('landed')} new on the queue"))
    if failed:
        events.append(ev(t, "failed", error=failed))
    lines = [f"{k}: done" for k, _ in SWEEP[:3]] + [f"  {i}  {p}" for i, p in enumerate(phases, 1)]
    return job(jid, kind, status=status, events=events, lines=lines, params={"rounds": rounds, "scopes": []},
               error=failed, seconds=t, progress={"stage": "fleet", "done": done, "total": rounds},
               result={"recorded": [], "found": [], "stale": [], "contradictions": 0} if status == "done" else None)


def gen_events(stages: list[tuple]) -> list[dict]:
    return [ev(t, "stage", key=k, title=title, status=s, summary=summary) for t, k, title, s, summary in stages]


GEN_PARAMS = {"request": LONG_REQUEST[:200], "effective_date": None, "catala_attempts": 3, "screen_rounds": 2,
              "no_roundtrip": False, "emit_failed_pdf": False}
G_RETRIEVE = [(0, "retrieve", "Retrieve precedent", "running", ""),
              (3, "retrieve", "Retrieve precedent", "done", "11 passages from 3 documents; 2 Catala modules encode them")]
G_DRAFT = [(3, "draft-1", "Draft the document", "running", ""),
           (310, "draft-1", "Draft the document", "done", "RWA-POL “Remote Working Equipment and Utilities Allowance Policy”, 12 clauses")]
G_CATALA_FAIL = [(310, "catala-1-1", "Catala gate — attempt 1", "running", ""),
                 (620, "catala-1-1", "Catala gate — attempt 1", "failed", "G1: the module does not compile; " + G1_DIAGNOSTIC.replace("\n", " "))]
G_CATALA_OK = [(620, "catala-1-2", "Catala gate — attempt 2", "running", ""),
               (900, "catala-1-2", "Catala gate — attempt 2", "done", "G1 typechecks; G2 412 vectors, 0 runtime errors; G3 every branch reached; G4 34 quotations match")]
G_SCREEN_FAIL = [(900, "screen-1", "Review screen — round 1", "running", ""),
                 (1400, "screen-1", "Review screen — round 1", "failed", "blocked: 2 confirmed findings (pro rata base unstated; repayment on death not excluded)")]
G_REDRAFT = [(1400, "draft-2", "Redraft the document", "running", "")]
ISSUED = {"name": "sabbatical-policy", "passed": True, "doc_id": "SAB-POL", "title": "Sabbatical Leave Policy",
          "failure_stage": "", "failure_reason": "", "seconds": 2140,
          "pdf_url": "/api/generate/pdf?name=sabbatical-policy", "run_url": "/api/generate/run?name=sabbatical-policy"}
NOT_ISSUED = {"name": "remote-working-allowance-5", "passed": False, "doc_id": "RWA-POL",
              "title": "Remote Working Equipment and Utilities Allowance Policy", "failure_stage": "roundtrip",
              "failure_reason": ("G5: the independent re-encoding computes the monthly allowance before pro-rating, the draft after; "
                                 "the two disagree on 38 of 412 vectors, first at contracted_weekly_hours = 22.5"),
              "seconds": 2711, "pdf_url": None, "run_url": "/api/generate/run?name=remote-working-allowance-5"}


def gen_job(jid: str, stages: list[tuple], *, status: str = "running", result=None, error: str = "",
            script=None, seconds: float | None = None) -> dict:
    events = gen_events(stages)
    return job(jid, "generate", status=status, events=events, lines=[f"{s[2]}: {s[3]} {s[4]}".strip() for s in stages],
               params=GEN_PARAMS, result=result, error=error, script=script,
               seconds=seconds if seconds is not None else (stages[-1][0] if stages else 5),
               progress={"stage": stages[-1][1] if stages else "retrieve", "name": "remote-working-allowance-6"})


NOW = time.time()
RUNS = [
    {"name": "remote-working-allowance-6", "finished": False, "passed": False, "modified": NOW - 300},
    {"name": "sabbatical-policy", "finished": True, "passed": True, "doc_id": "SAB-POL", "title": "Sabbatical Leave Policy",
     "request": "A sabbatical policy.", "failure_stage": "", "failure_reason": "", "catala_iterations": 2,
     "screen_rounds": 1, "seconds": 2140, "has_pdf": True, "modified": NOW - 3600},
    {"name": "remote-working-allowance-5", "finished": True, "passed": False, "doc_id": "RWA-POL",
     "title": "Remote Working Equipment and Utilities Allowance Policy", "request": LONG_REQUEST[:300],
     "failure_stage": "screen", "failure_reason": NOT_ISSUED["failure_reason"], "catala_iterations": 3,
     "screen_rounds": 2, "seconds": 2711, "has_pdf": False, "modified": NOW - 7200},
    {"name": "a-request-long-enough-that-its-workspace-name-is-one-unbroken-slug-for-ninety-characters-x",
     "finished": True, "passed": False, "doc_id": "", "title": "", "request": LONG_REQUEST[:300],
     "failure_stage": "aborted", "failure_reason": "stopped by hand", "catala_iterations": 0, "screen_rounds": 0,
     "seconds": 800, "has_pdf": False, "modified": NOW - 90000},
]


def finding(kind: str, agent: str, summary: str, **extra) -> dict:
    return {"kind": kind, "agent": agent, "summary": summary, "clause_ids": ["RWA-3.2", "RWA-4.1"], "agreed_with": [],
            "quote": "", "verified_by": "", "discard_reason": "", "fix": "", **extra}


RUN_RECORDS = {
    "remote-working-allowance-5": {
        "run": {
            "workspace": "generated/remote-working-allowance-5", "prompt": LONG_REQUEST, "passed": False,
            "failure_stage": "screen", "failure_reason": NOT_ISSUED["failure_reason"], "pdf": None,
            "doc_id": "RWA-POL", "title": "Remote Working Equipment and Utilities Allowance Policy",
            "catala_iterations": 3, "screen_rounds": 2, "seconds": 2711, "model": "qwen3.6:27b-q4_K_M",
            "gates": {"ok": True, "gates": [
                {"id": "G1", "name": "typecheck", "ok": False, "skipped": False, "detail": "the module does not compile",
                 "evidence": {"diagnostic": G1_DIAGNOSTIC}},
                {"id": "G2", "name": "totality", "ok": True, "skipped": False,
                 "detail": "412 vectors driven onto every threshold; 0 ScopeConflict, 0 NoApplicableRule; 18 rejected by assertion_contracted_weekly_hours_positive_and_not_more_than_full_time_hours"},
                {"id": "G3", "name": "dead branch", "ok": True, "skipped": False, "detail": "every exception branch reached"},
                {"id": "G4", "name": "fidelity", "ok": True, "skipped": False, "detail": "34 quotations match the document"},
            ]},
            "g5": {"id": "G5", "name": "roundtrip", "ok": False, "skipped": False, "detail": NOT_ISSUED["failure_reason"]},
            "screens": [{
                "passed": False, "verdict": "blocked: 1 confirmed finding",
                "agents": [
                    {"agent": "screen-logic", "ok": True, "findings": [{}, {}], "error": "", "enforcement": "sandbox-unproven", "seconds": 412.3},
                    {"agent": "screen-language", "ok": False, "findings": [], "error": "the reply was not JSON", "enforcement": "sandbox-unproven", "seconds": 600},
                    {"agent": "screen-consistency", "ok": True, "findings": [{}], "error": "", "enforcement": "sandbox-unproven", "seconds": 388},
                ],
                "confirmed": [finding("PRO_RATA_BASE", "screen-logic", "The pro rata base is stated as 37.5 hours in RWA-3.2 and as the contractual full-time week in RWA-4.1.",
                                      verified_by="executed RemoteWorkingAllowance.MonthlyAllowance at 22.5 hours: 15.00 against 13.50",
                                      quote="pro rata to their contracted weekly hours out of 37.5", agreed_with=["screen-consistency"])],
                "agreed": [],
                "open_questions": [finding("UNDEFINED_TERM", "screen-language", "Whether “normal working week” excludes weeks of annual leave is a judgement the draft leaves open.")],
                "discarded": [finding("HALLUCINATED_QUOTE", "screen-consistency", "Quotes a clause RWA-9.9 that the draft does not contain.",
                                      discard_reason="the quotation is not in the document", fix=UNBROKEN)],
            }],
            "context": [{"ref": "EMP-ANNEX-C C-10.1", "kind": "prose", "score": 0.4405},
                        {"ref": "EXP-POL E-4.2", "kind": "rule", "score": None}],
        },
        "report": "# Remote Working Equipment and Utilities Allowance Policy\n\n" + ("Not issued. " * 40) + "\n\n```\n" + G1_DIAGNOSTIC + "\n```\n",
        "summary": {"name": "remote-working-allowance-5", "has_pdf": False},
    },
    "sabbatical-policy": {
        "run": {"prompt": "A sabbatical policy.", "passed": True, "failure_stage": "", "failure_reason": "",
                "doc_id": "SAB-POL", "title": "Sabbatical Leave Policy", "catala_iterations": 2, "screen_rounds": 1,
                "seconds": 2140, "model": "qwen3.6:27b-q4_K_M", "gates": {"ok": True, "gates": []}, "g5": None,
                "screens": [], "context": []},
        "report": "", "summary": {"name": "sabbatical-policy", "has_pdf": True},
    },
}

LETTER = ("Dear Sirs,\n\nWe act for a Grade 6 incident engineer employed by your company. " + NARRATIVE + "\n\n"
          "Our client is owed 1.25 times the hourly base rate for every such hour, computed under your own "
          "clause C-8.1.\n\nYours faithfully,\n" + UNBROKEN)


# --- 3. scenarios ------------------------------------------------------------

def _gen_live() -> dict:
    return gen_job("g-live", G_RETRIEVE[:1], seconds=1, script=[
        {"polls": 2, "events": gen_events(G_RETRIEVE[1:] + G_DRAFT[:1])},
        {"polls": 4, "events": gen_events(G_DRAFT[1:] + G_CATALA_FAIL)},
        {"polls": 6, "events": gen_events(G_CATALA_OK)},
        {"polls": 8, "events": gen_events([(900, "screen-1", "Review screen — round 1", "running", ""),
                                           (1300, "screen-1", "Review screen — round 1", "done", "passed: no confirmed findings"),
                                           (1300, "roundtrip-1", "Roundtrip (G5) — attempt 1", "running", "")])},
        {"polls": 10, "events": gen_events([(2100, "roundtrip-1", "Roundtrip (G5) — attempt 1", "done", "structure and behaviour agree on 412 vectors")])
         + [ev(2140, "done", passed=True)], "status": "done", "result": ISSUED, "seconds": 2140},
    ])


def _sweep_live() -> dict:
    j = sweep_job("s-live", 2, [], status="running")
    j["events"] = sweep_prelude(2)[:3]
    j["script"] = [
        {"polls": 2, "events": sweep_prelude(2)[3:] + fleet_start(2)},
        {"polls": 4, "events": attack(1, 2, "proposing", 120)},
        {"polls": 6, "events": attack(1, 2, "died", 120)[1:] + attack(2, 2, "proposing", 420)},
        {"polls": 8, "events": attack(2, 2, "adjudicating", 420)[1:]},
        {"polls": 10, "events": attack(2, 2, "landed", 420)[2:]
         + [ev(700, "stage", key="fleet", title=SWEEP[3][1], status="done", summary="2 attacks across 2 rules; 1 new on the queue")],
         "status": "done", "result": {"recorded": [], "found": ["EXP-0009"], "stale": [], "contradictions": 0}, "seconds": 700},
    ]
    return j


def _letter_job() -> dict:
    return job("l-1", "letter", params={"id": "EXP-0008"}, seconds=1,
               events=[ev(0, "stage", key="draft", title="Draft the letter from the other side", status="running")],
               script=[{"polls": 3, "events": [ev(95, "stage", key="draft", title="Draft the letter from the other side", status="done",
                                                  summary="a 4-paragraph letter; every figure taken from the executed finding")],
                        "status": "done", "result": {"id": "EXP-0008", "letter": LETTER}, "seconds": 95}])


def _close_job() -> dict:
    return job("c-1", "close", params={"id": "EXP-0008"}, seconds=1,
               events=[ev(0, "stage", key="propose", title="Propose the smallest closing edit", status="running")],
               script=[{"polls": 3, "events": [
                   ev(80, "stage", key="propose", title="Propose the smallest closing edit", status="done", summary="1 exception added"),
                   ev(80, "stage", key="measure", title="Apply it, rebuild, and measure every map and test", status="running"),
                   ev(140, "stage", key="measure", title="Apply it, rebuild, and measure every map and test", status="failed",
                      summary="closes EXP-0008 but opens 2 regions of SILENCE")],
                   "status": "done", "seconds": 140, "result": {
                       "ok": False, "edit": {"clause_amendment": "S-7.4 No commission is recoverable where the contract churned because of the Company's material breach.",
                                             "rationale": NARRATIVE, "old": "definition recoverable_amount equals commission_paid",
                                             "new": "exception definition recoverable_amount under condition terminated_for_company_material_breach consequence equals $0 " + UNBROKEN},
                       "report": ["EXP-0008 closed", "2 new regions where the policy is silent", "31 of 31 counterexamples still pass"]}}])


ASK_CREDIT = "How much service credit do we owe at 98.5% uptime?"


def _facts_steps() -> list:
    return [["fill", "#askfield", ASK_CREDIT], ["submit", "#askform"], ["wait", ".missing .factsform"], ["check", "facts form"],
            ["fillFact", "Availability percentage", "98.5"], ["fillFact", "Monthly service charge", "10000"],
            ["fillFact", "Arrears days", "0"], ["fillFact", "Invoice undisputed", "true"],
            ["click", ".factsform button[type=submit]"], ["wait", ".out-v", "2,000"], ["check", "computed answer"]]


SCENARIOS: list[dict] = [
    # risk check
    {"name": "check-idle", "hash": "check"},
    {"name": "check-proposing", "hash": "check", "jobs": [sweep_job("a1", 6, ["proposing"])]},
    {"name": "check-adjudicating", "hash": "check", "jobs": [sweep_job("a2", 6, ["died", "adjudicating"])]},
    {"name": "check-attacks-finishing", "hash": "check", "jobs": [sweep_job("a3", 6, ["died", "known", "landed", "proposing"])],
     "steps": [["check", "live"], ["click", ".atk.p-known"], ["wait", ".now-top", "an earlier attack"], ["check", "earlier attack"],
               ["click", "button", "Follow the latest attack"], ["wait", ".mini-step.s-running"]]},
    {"name": "check-bad-replies", "hash": "check", "jobs": [sweep_job("a4", 4, ["malformed", "unanswered", "died", "proposing"])],
     "steps": [["click", ".atk.p-unanswered"], ["wait", ".mini-verdict", "did not answer in time"],
               ["wait", ".now-why", "took too long"], ["check", "unanswered attack"]]},
    {"name": "check-finished", "hash": "check",
     "jobs": [sweep_job("a5", 6, ["died", "known", "landed", "died", "died", "died"], status="done", stale=True)],
     "steps": [["wait", ".launch-last", "Last check finished"], ["check", "after the agents finished"],
               ["click", ".finding"], ["wait", "#drawer .figure"], ["check", "finding drawer"], ["key", "Escape"], ["gone", "#drawer"]]},
    {"name": "check-finished-old-server", "hash": "check",
     "jobs": [sweep_job("a6", 3, ["died", "malformed", "died"], status="done", plain=False)],
     "steps": [["wait", ".now-why", "no known claim"]]},
    {"name": "check-failed", "hash": "check",
     "jobs": [sweep_job("a7", 6, ["died", "adjudicating"], status="failed", failed="ReadTimeout: the model stopped answering after 900s")],
     "steps": [["wait", ".notice.is-bad", "The check stopped"], ["wait", ".mini-verdict", "Stopped before a verdict"]]},
    {"name": "check-single-rule", "hash": "check", "jobs": [sweep_job("a8", 3, ["died", "died", "died"], status="done", kind="fleet")]},
    {"name": "check-long-content", "hash": "check",
     "jobs": [sweep_job("a9", 12, ["died"] * 5 + ["landed", "known", "malformed", "died", "died", "adjudicating"], long=True)],
     "steps": [["click", "summary", "The facts the rule engine ran"], ["check", "long facts open"]]},
    {"name": "check-run-live", "hash": "check", "on_run": {"sweep": _sweep_live}, "budget": 90000,
     "steps": [["click", "button", "Start risk check"], ["wait", ".status-text", "Risk check"], ["wait", ".agent.is-on"],
               ["check", "an agent is drafting"], ["wait", ".atk.p-died"], ["check", "the first attack finished"],
               ["wait", ".atk.p-landed"], ["wait", ".launch-last", "Last check finished"], ["wait", ".status-text", "Local model"],
               ["check", "after the agents finished"]]},
    {"name": "check-letter", "hash": "check", "jobs": [sweep_job("b1", 3, ["died", "died", "died"], status="done")],
     "on_run": {"letter": _letter_job}, "budget": 60000,
     "steps": [["click", ".finding"], ["click", "#drawer button", "Write the demand letter"], ["wait", "#agentjob .step.s-running"],
               ["check", "letter being written"], ["wait", "#agentjob .letter"], ["check", "letter written"]]},
    {"name": "check-fix", "hash": "check", "jobs": [sweep_job("b2", 3, ["died", "died", "died"], status="done")],
     "on_run": {"close": _close_job}, "budget": 60000,
     "steps": [["click", ".finding"], ["click", "#drawer button", "Propose a fix"], ["wait", "#agentjob", "What the re-run showed"],
               ["check", "fix measured"]]},

    # drafting
    {"name": "draft-idle", "hash": "draft"},
    {"name": "draft-no-runs", "hash": "draft", "runs": []},
    {"name": "draft-old-server", "hash": "draft",
     "api": {"GET /api/generate/runs": (404, {"error": "No endpoint GET /api/generate/runs."})},
     "steps": [["wait", "#draftruns .notice", "Restart it"], ["disabled", "#draftbtn"]]},
    {"name": "draft-validation", "hash": "draft",
     "steps": [["click", "#draftbtn"], ["wait", "#drafterror", "sentence"], ["check", "empty request refused"]]},
    {"name": "draft-start", "hash": "draft", "on_run": {"generate": _gen_live}, "budget": 90000,
     "steps": [["fill", "#draftrequest", LONG_REQUEST], ["fill", "#draftdate", "2026-10-01"], ["click", ".adv summary"],
               ["submit", "#draftform"], ["wait", ".status-text", "Drafting"], ["wait", "#draftpipe .step.s-running"],
               ["check", "drafting"], ["wait", "#draftpipe .sub.s-blocked"], ["check", "an encoding attempt failed"],
               ["wait", ".verdict.is-issued"], ["check", "issued"], ["click", "button", "Read the full record"],
               ["wait", "#drawertitle", "Sabbatical"]]},
    {"name": "draft-retrying", "hash": "draft",
     "jobs": [gen_job("g2", G_RETRIEVE + G_DRAFT + G_CATALA_FAIL + G_CATALA_OK + G_SCREEN_FAIL + G_REDRAFT)],
     "steps": [["wait", ".step-loop", "Rewritten"], ["check", "redrafting"]]},
    {"name": "draft-issued", "hash": "draft",
     "jobs": [gen_job("g3", G_RETRIEVE + G_DRAFT + G_CATALA_OK, status="done", result=ISSUED, seconds=2140)],
     "steps": [["wait", ".verdict.is-issued a", "Open the PDF"]]},
    {"name": "draft-not-issued", "hash": "draft",
     "jobs": [gen_job("g4", G_RETRIEVE + G_DRAFT + G_CATALA_OK, status="done", result=NOT_ISSUED, seconds=2711)],
     "steps": [["wait", ".verdict.is-not", "the roundtrip check"]]},
    {"name": "draft-job-failed", "hash": "draft",
     "jobs": [gen_job("g5", G_RETRIEVE + G_DRAFT[:1], status="failed", error="RuntimeError: Ollama returned 500: model runner has unexpectedly stopped")],
     "steps": [["wait", "#draftout .notice.is-bad", "Ollama"]]},
    {"name": "draft-run-record", "hash": "draft",
     "steps": [["click", ".draftrun", "Remote Working Equipment"], ["wait", "#drawer .gates"], ["check", "run record"],
               ["click", "#drawer summary", "The full report"], ["check", "report open"]]},

    # ask (real replies, replayed)
    {"name": "ask-empty", "hash": "ask"},
    {"name": "ask-quote", "hash": "ask",
     "steps": [["click", ".example", "Who decides commission disputes?"], ["wait", ".passage"], ["check", "quoted answer"],
               ["click", ".passage .cite-quiet"], ["wait", "#drawer .law"], ["check", "clause drawer"], ["key", "Escape"]]},
    {"name": "ask-needs-facts", "hash": "ask",
     "steps": _facts_steps() + [["click", "button", "Change the facts"], ["wait", ".trace .factsform"], ["check", "changing facts"]]},
    {"name": "ask-slotfill", "hash": "ask",
     "api": {"POST /api/slotfill": (200, {"facts": {"availability_percentage": 98.5, "monthly_service_charge": 10000.0},
                                         "omitted": ["arrears_days", "invoice_undisputed"], "error": ""})},
     "steps": [["fill", "#askfield", ASK_CREDIT], ["submit", "#askform"], ["click", ".describe summary"],
               ["fill", ".describe textarea", "Uptime was 98.5% last month and the charge is 10,000."],
               ["click", ".describe button", "Fill in the facts"], ["wait", ".field.was-filled"], ["wait", ".describe .hint", "left for you"],
               ["check", "agent filled facts"]]},
    {"name": "ask-bad-fact", "hash": "ask",
     "steps": [["fill", "#askfield", ASK_CREDIT], ["submit", "#askform"], ["fillFact", "Availability percentage", "98.5"],
               ["fillFact", "Monthly service charge", "10000"], ["fillFact", "Arrears days", "two"], ["fillFact", "Invoice undisputed", "true"],
               ["click", ".factsform button[type=submit]"], ["wait", ".factsform .form-error", "whole number"]]},
    {"name": "ask-no-coverage", "hash": "ask",
     "steps": [["fill", "#askfield", "What is the capital of France?"], ["submit", "#askform"], ["wait", ".turn .a-meta", "part"],
               ["check", "no coverage"]]},
    {"name": "ask-error", "hash": "ask", "api": {"POST /api/ask": (500, {"error": "RuntimeError: clerk is not on PATH"})},
     "steps": [["fill", "#askfield", "Who decides commission disputes?"], ["submit", "#askform"], ["wait", ".turn .notice.is-bad", "clerk"],
               ["wait", "button", "Try again"], ["check", "error"]]},
    {"name": "ask-long-question", "hash": "ask",
     "steps": [["fill", "#askfield", "Who decides commission disputes? " + UNBROKEN + " " + LONG_REQUEST], ["submit", "#askform"],
               ["wait", ".turn .a-meta", "part"], ["check", "long question"]]},
    {"name": "ask-documents", "hash": "ask",
     "steps": [["click", ".doc-link", "Master Services"], ["wait", "#drawer .clause-row"], ["check", "document drawer"],
               ["click", "#drawer .clause-row"], ["wait", "#drawer .law"], ["click", "#drawerback"], ["wait", "#drawer .clause-row"]]},

    # the tour
    {"name": "tour-first-visit", "hash": None, "toured": False, "budget": 90000, "steps": [["tourWalk"]]},
    {"name": "tour-keyboard", "hash": "check", "steps": [
        ["click", ".tour-btn"], ["wait", ".tour-card"], ["key", "ArrowRight"], ["key", "ArrowRight"], ["sleep", 700],
        ["wait", ".tour-card .eyebrow", "3 of"], ["key", "ArrowLeft"], ["wait", ".tour-card .eyebrow", "2 of"],
        ["key", "Escape"], ["gone", ".tour-card"]]},
    {"name": "tour-old-link", "hash": "welcome", "steps": [["wait", ".tour-card", "Rules are executed"], ["click", "button", "Skip the tour"], ["gone", ".tour-card"]]},
    {"name": "tour-during-a-check", "hash": None, "toured": False, "budget": 90000,
     "jobs": [sweep_job("t1", 6, ["died", "landed", "adjudicating"])], "steps": [["tourWalk"]]},
]


# --- the document watch (lks.doc_watch), in every state its status can take ----

def iso_ago(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


WATCH_BASE = {"running": True, "interval": 30, "scans": 1204, "last_scan": NOW - 12, "last_error": "",
              "waiting_on": "", "attackers": 2, "model": "qwen3.6:27b-q4_K_M", "mongo": "connected"}


def wdoc(key: str, doc_id: str, title: str, status: str, *, source: str = "index", n: int = 24,
         last_check: dict | None = None, error: str = "", attempts: int = 0, check_id: str = "", changed: float = 3600) -> dict:
    d = {"key": key, "doc_id": doc_id, "title": title, "source": source, "fingerprint": "5c1f0a9e2b7d",
         "status": status, "attempts": attempts, "error": error, "n_clauses": n, "first_seen": iso_ago(changed + 900),
         "changed_at": iso_ago(changed), "retry_after": 0}
    if last_check:
        d["last_check"] = last_check
    if check_id:
        d["check_id"] = check_id
    if status in ("checked", "incomplete"):
        d["checked_at"] = iso_ago(changed - 400)
    return d


def wfinding(cid: str, ref: str, status: str = "single", *, quote: str, corpus_quote: str, facts: str, summary: str,
             reason: str = "", doc_title: str = "Travel Policy 2026", corpus_title: str = "") -> dict:
    return {"clause_id": cid, "corpus_ref": ref, "quote": quote, "corpus_quote": corpus_quote, "facts": facts,
            "summary": summary, "raised_by": [0, 1] if status == "corroborated" else [0], "status": status,
            "discard_reason": reason, "doc_title": doc_title if status != "discarded" else "",
            "corpus_title": corpus_title if status != "discarded" else ""}


def wcheck(cid: str, key: str, doc_id: str, title: str, *, findings=(), discarded=(), complete: bool = True,
           errors=(), ago: float = 3000, seconds: float = 412.3, n: int = 18, docs: int = 6, clauses: int = 312) -> dict:
    findings, discarded = list(findings), list(discarded)
    both = sum(1 for f in findings if f["status"] == "corroborated")
    if not complete:
        verdict = f"INCOMPLETE: {len(errors)} attacker run(s) gave no usable reply"
    elif findings:
        verdict = f"{len(findings)} possible contradiction(s), {both} corroborated by two attackers"
    else:
        verdict = f"no contradiction found against {docs} document(s), {len(discarded)} claim(s) discarded"
    return {"id": cid, "key": key, "doc_id": doc_id, "title": title, "fingerprint": "5c1f0a9e2b7d",
            "started": NOW - ago, "finished": NOW - ago + seconds, "seconds": seconds, "complete": complete,
            "errors": list(errors), "verdict": verdict, "n_clauses": n, "n_docs_compared": docs,
            "n_clauses_compared": clauses, "batches": (n + 4) // 5, "attackers": 2, "model": "qwen3.6:27b-q4_K_M",
            "findings": findings, "discarded": discarded,
            "attacks_tried": ["compared claim deadlines", "compared receipt thresholds", "compared approval owners"]}


def brief(c: dict) -> dict:
    return {"verdict": c["verdict"], "findings": len(c["findings"]),
            "corroborated": sum(1 for f in c["findings"] if f["status"] == "corroborated"), "complete": c["complete"]}


F_DEADLINE = wfinding("T-4.1", "EXP-POL E-3.1", "corroborated",
                      quote="Travel expenses must be claimed within 90 days of the end of the trip.",
                      corpus_quote="Claims must be submitted within 60 days of the expense being incurred.",
                      facts="An employee returns from a 10-day trip and claims a hotel bill 75 days after it was paid.",
                      summary="The travel policy admits the claim; the expense policy says it is out of time.",
                      corpus_title="Expense Reimbursement Policy")
F_RECEIPT = wfinding("T-5.2", "EXP-POL E-3.2", "single",
                     quote="No receipt is needed for items under GBP 40.",
                     corpus_quote="Receipts are required for any single item above GBP 25.",
                     facts="A GBP 32 taxi fare without a receipt.",
                     summary="The travel policy waives the receipt; the expense policy requires one.",
                     corpus_title="Expense Reimbursement Policy")
F_DISCARDED = wfinding("T-2.1", "NDA N-2.1", "discarded", quote="Trips must be approved in advance.",
                       corpus_quote="Trips need no approval.", facts="", summary="Approval rules differ.",
                       reason="quote does not appear in the existing clause")
CHECK_FLAGGED = wcheck("c0ffee000001", "intake:66e3a1f0c2", "TRAVEL-2026", "Travel Policy 2026",
                       findings=[F_DEADLINE, F_RECEIPT], discarded=[F_DISCARDED], ago=900, seconds=512.8)
CHECK_CLEAN = wcheck("c0ffee000002", "index:DATA-RET", "DATA-RET", "Data Retention and Deletion Standard",
                     discarded=[F_DISCARDED], ago=86000, seconds=388.1, n=17)
CHECK_INCOMPLETE = wcheck("c0ffee000003", "intake:66e3a1f0c3", "BONUS-2026", "Discretionary Bonus Scheme 2026",
                          complete=False, errors=["batch 2, attacker 1: the model did not answer within 900s",
                                                  "batch 3, attacker 2: reply is not valid JSON"], ago=7200, seconds=1802.0, n=12)
CHECK_LEFT = {"id": "c0ffee000004", "key": "intake:66e3a1f0c4", "doc_id": "DRAFT-X", "title": "",
              "verdict": "the document left the base before it was checked", "started": NOW - 100000,
              "finished": NOW - 100000, "seconds": 0.0}

WATCH_QUIET = {**WATCH_BASE, "counts": {"baseline": 6},
               "documents": [wdoc(f"index:{d}", d, t, "baseline", n=n, changed=200000) for d, t, n in [
                   ("EMP-ANNEX-C", "Employment Terms — Annex C: Working Time, Overtime and Leave", 26),
                   ("EXP-POL", "Expense Reimbursement Policy", 24), ("COMM-PLAN", "Sales Commission Plan — FY26", 25),
                   ("MSA-SCH4", "Master Services Agreement — Schedule 4: Service Levels and Service Credits", 20),
                   ("NDA", "Mutual Non-Disclosure Agreement", 22), ("DATA-RET", "Data Retention and Deletion Standard", 17)]],
               "checks": []}
WATCH_ACTIVE = {**WATCH_BASE, "counts": {"checking": 1, "pending": 1, "checked": 2, "incomplete": 1, "unreadable": 1, "removed": 1, "baseline": 4},
                "documents": [
                    wdoc("intake:66e3a1f0c5", "LEAVE-2026", "Family Leave Policy 2026", "checking", source="intake", n=21, changed=60),
                    wdoc("intake:66e3a1f0c6", "INTAKE-3a1f0c6", "", "pending", source="intake", n=9, changed=45,
                         error="the model did not answer within 900s", attempts=1),
                    wdoc("intake:66e3a1f0c2", "TRAVEL-2026", "Travel Policy 2026", "checked", source="intake", n=18,
                         last_check=brief(CHECK_FLAGGED), check_id=CHECK_FLAGGED["id"], attempts=1, changed=1400),
                    wdoc("intake:66e3a1f0c3", "BONUS-2026", "Discretionary Bonus Scheme 2026", "incomplete", source="intake", n=12,
                         last_check=brief(CHECK_INCOMPLETE), check_id=CHECK_INCOMPLETE["id"], attempts=3,
                         error="batch 2, attacker 1: the model did not answer within 900s", changed=9000),
                    wdoc("index:DATA-RET", "DATA-RET", "Data Retention and Deletion Standard", "checked", n=17,
                         last_check=brief(CHECK_CLEAN), check_id=CHECK_CLEAN["id"], attempts=1, changed=90000),
                    wdoc("intake:66e3a1f0c7", "INTAKE-3a1f0c7", "", "unreadable", source="intake", n=0,
                         error="the row has no `text`", changed=3000),
                    wdoc("intake:66e3a1f0c4", "DRAFT-X", "Superseded draft", "removed", source="intake", n=4, changed=100000),
                ] + WATCH_QUIET["documents"][:4],
                "checks": [CHECK_FLAGGED, CHECK_INCOMPLETE, CHECK_CLEAN, CHECK_LEFT]}
WATCH_JOB = job("w-live", "doc-watch", label="contradiction check: Family Leave Policy 2026",
                params={"key": "intake:66e3a1f0c5", "doc_id": "LEAVE-2026"}, seconds=190,
                lines=["  batch 1/5 · attacker 1/2 · attempt 1 (5 clauses vs 14 rivals)", "    2 claim(s)",
                       "  batch 1/5 · attacker 2/2 · attempt 1 (5 clauses vs 14 rivals)", "    unusable reply: timeout",
                       "  batch 1/5 · attacker 2/2 · attempt 2 (5 clauses vs 14 rivals)"])
LONG = "the_employee_must_notify_the_line_manager_in_writing_before_the_first_day_of_absence_without_exception"
WATCH_LONG = {**WATCH_ACTIVE, "last_error": "ServerSelectionTimeoutError: localhost:27017: [Errno 111] Connection refused, Timeout: 30s, Topology Description: " + LONG,
              "documents": [wdoc("intake:" + "f" * 24, "A-DOCUMENT-ID-THAT-IS-ALSO-VERY-LONG-2026-" + LONG[:30],
                                 "A title long enough to wrap onto several lines in the narrowest layout " + LONG, "unreadable",
                                 source="intake", n=0, error="ValueError: " + LONG * 2)] + WATCH_ACTIVE["documents"],
              "checks": [wcheck("c0ffee000009", "intake:" + "f" * 24, "LONG-2026", "Long Policy " + LONG,
                                findings=[{**F_DEADLINE, "quote": LONG + " " + F_DEADLINE["quote"], "summary": LONG}], ago=500)] + WATCH_ACTIVE["checks"]}

SCENARIOS += [
    {"name": "watch-old-server", "hash": "watch", "watch": (404, {"error": "No endpoint GET /api/watch."}),
     "steps": [["wait", "#watchnotice .notice", "Restart it"], ["wait", "#watchstatus", "Not available"], ["check", "no endpoint"]]},
    {"name": "watch-turned-off", "hash": "watch",
     "watch": (503, {"error": "the contradiction watch is not running in this server (LKS_DOC_WATCH=0)"}),
     "steps": [["wait", "#watchnotice .notice", "LKS_DOC_WATCH=0"], ["check", "turned off"]]},
    {"name": "watch-first-start", "hash": "watch",
     "watch": (200, {**WATCH_BASE, "scans": 0, "last_scan": 0, "counts": {}, "documents": [], "checks": []}),
     "steps": [["wait", "#watchstatus", "Starting"], ["wait", "#watchdocs", "No documents tracked yet"], ["check", "first start"]]},
    {"name": "watch-baseline", "hash": "watch",
     "steps": [["wait", "#watchstatus", "Watching"], ["wait", "#watchdocs .badge", "Baseline"], ["wait", "#watchchecks", "No checks yet"],
               ["check", "all baseline"]]},
    {"name": "watch-active", "hash": "watch", "watch": (200, WATCH_ACTIVE), "jobs": [WATCH_JOB],
     "steps": [["wait", "#watchstatus", "Checking a document"], ["wait", ".status-text", "Checking a new document"],
               ["wait", "#watchpipe .step.s-running", "Look for contradictions"], ["wait", "#watchnow .watch-lines"],
               ["check", "a check in progress"],
               ["click", "#watchchecks .finding", "Travel Policy 2026"], ["wait", "#drawer .wpair-head.is-both"],
               ["check", "possible contradictions"], ["click", "#drawer summary", "Discarded claims"], ["key", "Escape"],
               ["click", "#watchchecks .finding", "Discretionary Bonus"], ["wait", "#drawer .verdict.is-pending"], ["key", "Escape"],
               ["click", "#watchdocs button", "Check now"], ["wait", "#watchnotice .notice", "queued"], ["check", "queued a baseline document"],
               ["fill", "#watchfilter", "attention"], ["wait", "#watchdocs", "Could not be read"], ["check", "needs attention"]]},
    {"name": "watch-mongo-down", "hash": "watch",
     "watch": (200, {**WATCH_BASE, "mongo": "unavailable: ServerSelectionTimeoutError",
                     "last_error": "ServerSelectionTimeoutError: localhost:27017: [Errno 111] Connection refused",
                     "checks": [], "checks_error": "ServerSelectionTimeoutError: localhost:27017"}),
     "steps": [["wait", "#watchstatus", "Cannot reach MongoDB"], ["wait", "#watchdocs", "MongoDB is not reachable"], ["check", "mongo down"]]},
    {"name": "watch-waiting-for-model", "hash": "watch",
     "watch": (200, {**WATCH_QUIET, "waiting_on": "the local model: qwen3.6:27b-q4_K_M is not loaded (ollama is not running)",
                     "counts": {"pending": 1, "baseline": 6},
                     "documents": [wdoc("intake:66e3a1f0c8", "TRAVEL-2027", "Travel Policy 2027", "pending", source="intake", changed=20)]
                     + WATCH_QUIET["documents"]}),
     "steps": [["wait", "#watchstatus", "Waiting for the model"], ["check", "waiting for the model"]]},
    # Captured from a real server: the watch will not take a baseline before the index exists.
    {"name": "watch-waiting-for-index", "hash": "watch",
     "watch": (200, {**WATCH_BASE, "scans": 0, "last_scan": 0.0,
                     "waiting_on": "an index to take the baseline from (scripts/sync_mongo.py)",
                     "counts": {}, "documents": [], "checks": []}),
     "steps": [["wait", "#watchstatus", "Waiting for the index"], ["wait", "#watchdocs", "once the document index has been built"],
               ["check", "waiting for the index"]]},
    # Also from the real code: a scan during `sync_mongo.py` is not believed.
    {"name": "watch-index-syncing", "hash": "watch",
     "watch": (200, {**WATCH_QUIET, "waiting_on": "an index sync in progress (31 of 49 chunks present)"}),
     "steps": [["wait", "#watchstatus", "Waiting for the index to finish updating"], ["wait", "#watchstatus", "31 of 49"],
               ["check", "index syncing"]]},
    {"name": "watch-waiting-unknown", "hash": "watch",
     "watch": (200, {**WATCH_QUIET, "waiting_on": "OperationFailure: not authorized on lks to execute command { find: \"watch_state\" }"}),
     "steps": [["wait", "#watchstatus .watch-state", "Waiting"], ["wait", "#watchstatus .notice", "not authorized"], ["check", "unknown wait"]]},
    {"name": "watch-long-content", "hash": "watch", "watch": (200, WATCH_LONG),
     "steps": [["click", "#watchchecks .finding", "Long Policy"], ["wait", "#drawer .wpair"], ["check", "long finding"], ["key", "Escape"],
               ["check", "long page"]]},
    {"name": "live-watch", "hash": "watch", "live": True, "settle": 4000, "steps": [["check", "the real watch page"]]},
    # stop on a state, for screenshots
    {"name": "watch-shot-drawer", "hash": "watch", "watch": (200, WATCH_ACTIVE), "jobs": [WATCH_JOB],
     "steps": [["click", "#watchchecks .finding", "Travel Policy 2026"], ["wait", "#drawer .wpair"], ["check", "drawer open"]]},
    {"name": "tour-at-watch", "hash": None, "toured": False, "budget": 60000, "watch": (200, WATCH_ACTIVE),
     "steps": [["wait", ".tour-card"]] + [["click", "[data-tour-next]"], ["sleep", 400]] * 7 + [["sleep", 9000], ["check", "tour at the watch"]]},
]


# Stops partway through the tour, so a screenshot shows a step with its example
# played out. Layout is checked here as everywhere.
SCENARIOS += [
    {"name": "tour-at-ask", "hash": None, "toured": False,
     "steps": [["wait", ".tour-card"], ["click", "[data-tour-next]"], ["sleep", 500], ["click", "[data-tour-next]"],
               ["sleep", 6000], ["check", "tour at Ask"]]},
    {"name": "tour-at-draft", "hash": None, "toured": False, "budget": 60000,
     "steps": [["wait", ".tour-card"]] + [["click", "[data-tour-next]"], ["sleep", 400]] * 6
              + [["sleep", 10000], ["check", "tour at Draft"]]},
]


# Watch a real server: `--api http://127.0.0.1:8765 --only live-`.
SCENARIOS += [
    {"name": "live-check", "hash": "check", "live": True, "settle": 4000, "steps": [["check", "the real risk check page"]]},
    {"name": "live-draft", "hash": "draft", "live": True, "settle": 4000, "steps": [["check", "the real draft page"]]},
    {"name": "live-ask", "hash": "ask", "live": True, "settle": 3000, "steps": [["check", "the real ask page"]]},
]


# Real jobs and a real drafting record, captured from a running server, so the
# interface is also held to what the backend actually sends rather than only to
# what these scripts believe it sends. Refresh by saving newer ones here.
REAL_JOBS = ROOT / "tests" / "ui" / "real_jobs.json"
REAL_RUNS = ROOT / "tests" / "ui" / "real_runs.json"


def _real_job(raw: dict) -> dict:
    j = {k: raw[k] for k in ("id", "kind", "label", "params", "status", "error", "progress", "result", "started", "seconds")}
    j.update(events=list(raw.get("events", [])), lines=list(raw.get("lines", [])), script=[])
    return j


if REAL_JOBS.exists():
    _jobs = json.loads(REAL_JOBS.read_text(encoding="utf-8"))
    if "quick" in _jobs:
        SCENARIOS.append({"name": "check-real-quick", "hash": "check", "jobs": [_real_job(_jobs["quick"])],
                          "steps": [["wait", "#checkpipe .step.s-skipped", "no attack rounds"], ["check", "a real quick check"]]})
    if "three_attacks" in _jobs:
        SCENARIOS.append({"name": "check-real-attacks", "hash": "check", "jobs": [_real_job(_jobs["three_attacks"])],
                          "steps": [["wait", ".atk.p-died"], ["click", ".atk.p-died"], ["wait", ".now-why"], ["wait", ".claim"],
                                    ["check", "a real finished attack"]]})
    if "six_attacks" in _jobs and "quick" in _jobs:
        SCENARIOS.append({"name": "check-real-history", "hash": "check",
                          "jobs": [_real_job(_jobs["six_attacks"]), _real_job(_jobs["quick"])],
                          "steps": [["wait", ".launch-last", "Last check finished"], ["check", "latest of two real checks"]]})
if REAL_RUNS.exists():
    _runs = json.loads(REAL_RUNS.read_text(encoding="utf-8"))
    SCENARIOS.append({"name": "draft-real-record", "hash": "draft", "runs": _runs["runs"], "run_records": _runs["records"],
                      "steps": [["click", ".draftrun", "Remote Working Equipment"], ["wait", "#drawer .gates"], ["wait", "#drawer .rf"],
                                ["check", "a real two-round review"], ["click", "#drawer summary", "Discarded"], ["check", "discarded findings open"]]})


# --- 4. the stand-in server ---------------------------------------------------

class Run:
    def __init__(self, scenario: dict):
        self.sc = scenario
        self.jobs = [copy.deepcopy(j) for j in scenario.get("jobs", [])]
        self.polls: dict[str, int] = {}
        self.unexpected: list[str] = []
        self.lock = threading.Lock()


class Harness:
    def __init__(self, scenarios: list[dict], api: str | None = None, record: bool = False):
        self.scenarios = {s["name"]: s for s in scenarios}
        self.api = api.rstrip("/") if api else None
        self.record = record
        self.snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
        self.runs: dict[str, Run] = {}
        self.lock = threading.Lock()
        harness = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a): pass
            def do_GET(self): harness.handle(self, "GET")
            def do_POST(self): harness.handle(self, "POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()

    def url(self, name: str, width: int, attempt: str = "") -> str:
        sc = self.scenarios[name]
        run_id = f"{name}@{width}{attempt}"
        tail = f"#{sc['hash']}" if sc.get("hash") else ""
        return f"http://127.0.0.1:{self.port}/?{urlencode({'__run': run_id})}{tail}"

    @staticmethod
    def send(req, status: int, body, ctype: str = "application/json", headers: dict | None = None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        req.send_response(status)
        req.send_header("Content-Type", ctype)
        req.send_header("Content-Length", str(len(data)))
        req.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            req.send_header(k, v)
        req.end_headers()
        req.wfile.write(data)

    def handle(self, req, method: str):
        u = urlparse(req.path)
        q = dict(parse_qsl(u.query))
        body = None
        if method == "POST":
            n = int(req.headers.get("Content-Length") or 0)
            raw = req.rfile.read(n) if n else b""
            body = json.loads(raw) if raw else {}
        if not u.path.startswith("/api/"):
            return self.static(req, u.path, q)
        cookie = req.headers.get("Cookie") or ""
        m = re.search(r"ui_run=([^;]+)", cookie)
        run = self.runs.get(urllib.request.unquote(m.group(1))) if m else None
        if run is None:
            return self.send(req, 400, {"error": "no harness run for this browser"})
        status, out = self.api_reply(run, method, u.path, q, body)
        self.send(req, status, out)

    def static(self, req, path: str, q: dict):
        if path in ("/", "/index.html"):
            run_id = q.get("__run", "")
            name = run_id.split("@")[0]
            sc = self.scenarios.get(name)
            if sc is None:
                return self.send(req, 404, b"unknown scenario", "text/plain")
            with self.lock:
                self.runs[run_id] = Run(sc)
            page = (WEB / "index.html").read_text(encoding="utf-8")
            scenario = json.dumps({"name": name, "steps": sc.get("steps", []), "settle": sc.get("settle", 1500)}).replace("</", "<\\/")
            toured = "true" if sc.get("toured", True) else "false"
            # Headless virtual time does not advance CSS transitions, so a moving
            # element would be measured where it started. The layout is checked
            # where things come to rest; the scripted animations still run.
            head = ("<style>*,*::before,*::after{transition:none!important;animation:none!important}</style>"
                    f"<script>window.__UI_SCENARIO__={scenario};window.__UI_ERRORS__=[];"
                    "addEventListener('error',function(e){__UI_ERRORS__.push(e.message+' ('+(e.filename||'').split('/').pop()+':'+e.lineno+')')});"
                    "addEventListener('unhandledrejection',function(e){__UI_ERRORS__.push('unhandled rejection: '+((e.reason&&e.reason.message)||e.reason))});"
                    f"try{{localStorage.clear();if({toured})localStorage.setItem('ross.toured','1')}}catch(e){{}}</script>")
            page = page.replace('<script src="/icons.js"></script>', head + '<script src="/icons.js"></script>', 1)
            page = page.replace('<script src="/app.js"></script>', '<script src="/app.js"></script><script src="/__ui_driver__.js"></script>', 1)
            return self.send(req, 200, page.encode(), "text/html; charset=utf-8",
                             {"Set-Cookie": f"ui_run={quote(run_id)}; Path=/"})
        if path == "/__ui_driver__.js":
            return self.send(req, 200, DRIVER.read_bytes(), "application/javascript; charset=utf-8")
        target = (WEB / path.lstrip("/")).resolve()
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            return self.send(req, 404, b"not found", "text/plain")
        ctype = {".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".png": "image/png", ".jpg": "image/jpeg", ".html": "text/html; charset=utf-8"}.get(target.suffix, "application/octet-stream")
        return self.send(req, 200, target.read_bytes(), ctype)

    def api_reply(self, run: Run, method: str, path: str, q: dict, body):
        key = f"{method} {path}"
        override = run.sc.get("api", {}).get(key)
        if override is not None:
            return override(body, q) if callable(override) else override
        if run.sc.get("live"):
            # A live scenario watches a real server, jobs and all. It only reads.
            if method != "GET":
                run.unexpected.append(f"{key} (a live scenario must not change the server)")
                return 403, {"error": "live scenarios are read-only"}
            if not self.api:
                run.unexpected.append(f"{key} (a live scenario needs --api URL)")
                return 503, {"error": "no live server"}
            try:
                return forward(self.api, method, path, q, body)
            except OSError as e:
                run.unexpected.append(f"{key} (the live server did not answer: {e})")
                return 503, {"error": str(e)}
        with run.lock:
            if key == "GET /api/exposure/jobs":
                return 200, {"jobs": [payload(j, brief=True) for j in sorted(run.jobs, key=lambda j: -j["started"])]}
            if key in ("GET /api/exposure/job", "GET /api/generate/job"):
                jid = q.get("id")
                if not jid:
                    live = next((j for j in run.jobs if j["status"] == "running"), None)
                    return 200, {"running": payload(live, brief=True) if live else None}
                j = next((j for j in run.jobs if j["id"] == jid), None)
                if j is None:
                    return 404, {"error": f"no job {jid!r}"}
                n = run.polls[jid] = run.polls.get(jid, 0) + 1
                for step in j["script"]:
                    if step.get("_done") or step["polls"] > n:
                        continue
                    step["_done"] = True
                    j["events"].extend(step.get("events", []))
                    j["lines"].extend(step.get("lines", []))
                    for f in ("status", "result", "error", "progress", "seconds"):
                        if f in step:
                            j[f] = step[f]
                return 200, payload(j, int(q.get("since") or 0), int(q.get("since_events") or 0))
            if key in ("POST /api/exposure/run", "POST /api/generate"):
                kind = "generate" if path == "/api/generate" else (body or {}).get("kind")
                live = next((j for j in run.jobs if j["status"] == "running"), None)
                if live:
                    return 409, {"error": f"{live['label']} is already running. The local model serves one request at a time."}
                factory = run.sc.get("on_run", {}).get(kind)
                if factory is None:
                    run.unexpected.append(f"{key} {json.dumps(body)} (the scenario does not start a {kind} job)")
                    return 400, {"error": f"this scenario does not start a {kind} job"}
                j = factory()
                j["started"] = time.time()
                run.jobs.append(j)
                return 200, payload(j)
        if key == "GET /api/watch":
            return run.sc.get("watch", (200, WATCH_QUIET))
        if key == "POST /api/watch/recheck":
            return 200, {"key": (body or {}).get("key", ""), "status": "pending"}
        if key == "GET /api/generate/runs":
            return 200, {"runs": run.sc.get("runs", RUNS)}
        if key == "GET /api/generate/run":
            rec = run.sc.get("run_records", RUN_RECORDS).get(q.get("name", ""))
            return (200, rec) if rec else (409, {"error": "this run has not finished, or its record is unreadable"})
        if path.startswith(READ_ONLY):
            return self.read_only(run, method, path, q, body)
        run.unexpected.append(key)
        return 404, {"error": f"No endpoint {method} {path}."}

    def read_only(self, run: Run, method: str, path: str, q: dict, body):
        k = f"{method} {path}" + (f"?{urlencode(sorted(q.items()))}" if q else "") + (f" {json.dumps(body, sort_keys=True)}" if body else "")
        if k in self.snapshot and not self.record:
            status, out = self.snapshot[k]
            return status, out
        if self.api:
            try:
                status, out = forward(self.api, method, path, q, body)
            except OSError as e:
                run.unexpected.append(f"{k} (the live server did not answer: {e})")
                return 503, {"error": str(e)}
            if self.record:
                with self.lock:
                    self.snapshot[k] = [status, out]
            return status, out
        run.unexpected.append(f"{k} (no recorded reply; record one with --api URL --record)")
        return 503, {"error": f"no recorded reply for {k}"}

    def save_snapshot(self):
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(dict(sorted(self.snapshot.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def forward(api: str, method: str, path: str, q: dict, body):
    url = api + path + (f"?{urlencode(q)}" if q else "")
    data = json.dumps(body).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


# --- 5. the browser -------------------------------------------------------------

def find_chrome() -> str | None:
    env = os.environ.get("UI_CHROME")
    if env and Path(env).exists():
        return env
    for pattern in ("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell",
                    "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"):
        hits = sorted(glob.glob(os.path.expanduser(pattern)))
        if hits:
            return hits[-1]
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def browse(chrome: str, url: str, width: int, budget: int, png: Path | None = None) -> str:
    height = 860 if width < 600 else 900
    with tempfile.TemporaryDirectory(prefix="ui-check-") as profile:
        cmd = [chrome]
        if "headless_shell" not in chrome:
            cmd.append("--headless=new")
        cmd += ["--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--mute-audio",
                f"--user-data-dir={profile}", f"--window-size={width},{height}", f"--virtual-time-budget={budget}"]
        cmd += [f"--screenshot={png}"] if png else ["--dump-dom"]
        cmd.append(url)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        return p.stdout


def run_one(harness: Harness, chrome: str, name: str, width: int, shots: Path | None) -> tuple[str, int, list[str]]:
    sc = harness.scenarios[name]
    budget = sc.get("budget", 45000)
    try:
        dom = browse(chrome, harness.url(name, width), width, budget)
    except subprocess.TimeoutExpired:
        return name, width, ["the browser did not finish within 240s"]
    m = re.search(r'<pre id="__ui_report__"[^>]*>(.*?)</pre>', dom, re.S)
    problems: list[str] = []
    if not m:
        problems.append("the driver never reported: a script error stopped it, or the scenario outlasted its time budget")
    else:
        rep = json.loads(html.unescape(m.group(1)))
        problems += [f"step: {x}" for x in rep["failures"]]
        problems += [f"script error: {x}" for x in dict.fromkeys(rep["errors"])]
        problems += rep["problems"]
    run = harness.runs.get(f"{name}@{width}")
    if run:
        problems += [f"unexpected request: {x}" for x in dict.fromkeys(run.unexpected)]
    if shots is not None:
        shots.mkdir(parents=True, exist_ok=True)
        try:
            browse(chrome, harness.url(name, width, "-shot"), width, budget, png=shots / f"{name}@{width}.png")
        except subprocess.TimeoutExpired:
            pass
    return name, width, problems


def run_all(chrome: str, *, only: list[str] | None = None, widths=WIDTHS, shots: Path | None = None,
            api: str | None = None, record: bool = False, workers: int = 6, quiet: bool = False) -> list[tuple[str, int, list[str]]]:
    # Live scenarios watch whatever a real server is doing, so they run only when
    # asked for by name and given a server; the default run stays deterministic.
    chosen = [s for s in SCENARIOS
              if (not only and not s.get("live")) or (only and any(s["name"].startswith(o) for o in only) and (api or not s.get("live")))]
    harness = Harness(SCENARIOS, api=api, record=record)
    results = []
    try:
        tasks = [(s["name"], w) for s in chosen for w in widths]
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_one, harness, chrome, n, w, shots) for n, w in tasks]
            for f in cf.as_completed(futures):
                name, width, problems = f.result()
                results.append((name, width, problems))
                if not quiet:
                    mark = "ok  " if not problems else "FAIL"
                    print(f"  {mark} {name} @ {width}px" + ("" if not problems else "\n" + "\n".join(f"         - {p}" for p in problems[:12])))
        if record:
            harness.save_snapshot()
    finally:
        harness.close()
    return sorted(results)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated scenario name prefixes")
    ap.add_argument("--widths", default=",".join(map(str, WIDTHS)))
    ap.add_argument("--shots", type=Path, help="also save a screenshot of every scenario here")
    ap.add_argument("--api", help="a live server to fetch read-only replies from, e.g. http://127.0.0.1:8765")
    ap.add_argument("--record", action="store_true", help="save the live replies to tests/ui/api_snapshot.json")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for s in SCENARIOS:
            print(s["name"])
        return 0

    failed = 0
    print("CSS: state classes")
    collisions = css_collisions((WEB / "app.css").read_text(encoding="utf-8"))
    for c in collisions:
        print(f"  FAIL {c}")
    if not collisions:
        print("  ok   no class is both a modifier and a style of its own")
    failed += len(collisions)

    chrome = find_chrome()
    if not chrome:
        print("Browser: no headless Chromium found (set UI_CHROME). The scenarios did not run.")
        return 1 if failed else 2
    if args.record and not args.api:
        ap.error("--record needs --api")
    widths = [int(w) for w in args.widths.split(",") if w]
    if args.record:
        widths = widths[:1]
    only = [o for o in (args.only or "").split(",") if o]
    print(f"Browser: {len(widths)} width(s), {chrome}")
    t0 = time.time()
    results = run_all(chrome, only=only, widths=widths, shots=args.shots, api=args.api, record=args.record, workers=args.workers)
    bad = [r for r in results if r[2]]
    failed += len(bad)
    print(f"\n{len(results) - len(bad)} of {len(results)} scenario runs held their layout, in {time.time() - t0:.0f}s"
          + (f"; replies saved to {SNAPSHOT.relative_to(ROOT)}" if args.record else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
