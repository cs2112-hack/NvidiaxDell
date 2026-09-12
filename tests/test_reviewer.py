#!/usr/bin/env python3
"""Tests for the adversarial reviewer's harness, and the model client under it.

    . scripts/env.sh && $PY tests/test_reviewer.py

Plain asserts and a `__main__` that runs them all, matching tests/test_exposure.py.
No model is needed: the harness runs against the real compiled rules, and the
client against a fake Ollama on a local port.

The harness exists so that nothing the reviewer says becomes a permanent test
unless the rule really disagrees with the document. Most tests here are a way a
claim used to get through:

  * `test_misspelt_input_is_turned_away_not_recorded` -- Catala refusing to
    parse the facts was accepted as an observation
  * `test_expected_naming_a_result_the_rule_lacks_is_turned_away` -- compared
    as "no match", which is a break
  * `test_bare_value_does_not_match_an_unrelated_result` -- `True` matched
    `accrues_toil` on a claim about the multiplier

The rest are the ways every round used to die: a reply that ran to the token
limit, and a timeout that escaped as an unhandled exception.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from lks import agents, llm, plain
from lks.catala_runner import run_scope
from lks.counterexample import Counterexample, load_all
from lks.interface import check_facts, scope_io
from lks.reviewer import AGREES, DISAGREES, UNCLEAR, compare, record_findings

OVERTIME = "catala/modules/overtime.catala_en"


def ce_yaml(ce_id: str) -> dict:
    return yaml.safe_load((ROOT / "tests/counterexamples" / f"{ce_id}.yaml").read_text())


def finding(**over) -> dict:
    f = {
        "verdict": "BREAK", "component": "catala",
        "target": {"module": "overtime", "path": OVERTIME, "scope": "HourPremium"},
        "fact_pattern": "An hour worked, for a test.",
        "inputs": dict(ce_yaml("CE-0001")["inputs"]),
        "expected": {"multiplier": 3.0},
        "citations": ["EMP-ANNEX-C C-4.1"],
        "source_reasoning": "For a test.",
        "headline": "Test headline.",
    }
    f.update(over)
    return f


def record(f: dict):
    with tempfile.TemporaryDirectory() as d:
        store, reports = Path(d) / "ce", Path(d) / "reports"
        out = record_findings([f], round=0, store=store, reports=reports)
        files = sorted(p.name for p in store.glob("*.yaml")) if store.exists() else []
        page = next(iter(sorted(reports.glob("*.md"))), None) if reports.exists() else None
        return out, files, page.read_text() if page else ""


# --- the harness -----------------------------------------------------------

def test_misspelt_input_is_turned_away_not_recorded():
    f = finding()
    f["inputs"]["ordinl"] = f["inputs"].pop("ordinal")
    out, files, _ = record(f)
    assert not out.accepted and not files, "a misspelt input became a permanent test"
    why = out.rejected[0][1]
    assert "ordinl" in why and "ordinal" in why, why


def test_wrong_type_is_turned_away():
    f = finding()
    f["inputs"]["is_night"] = "yes"
    out, files, _ = record(f)
    assert not files
    assert "true or false" in out.rejected[0][1], out.rejected[0][1]


def test_expected_naming_a_result_the_rule_lacks_is_turned_away():
    out, files, _ = record(finding(expected={"multiplyer": 3.0}))
    assert not files
    assert "does not produce" in out.rejected[0][1], out.rejected[0][1]


def test_bare_expected_on_a_rule_with_several_results_is_turned_away():
    out, files, _ = record(finding(expected=True))
    assert not files
    assert "which of the rule" in out.rejected[0][1], out.rejected[0][1]


def test_claim_the_rule_already_satisfies_is_held_not_recorded():
    real = run_scope(OVERTIME, "HourPremium", ce_yaml("CE-0001")["inputs"])
    out, files, _ = record(finding(expected={"total_rate": real["total_rate"]}))
    assert out.held and not out.accepted and not files
    assert "already gives" in out.rejected[0][1], out.rejected[0][1]


def test_real_disagreement_is_recorded_with_a_readable_report():
    out, files, page = record(finding())
    assert len(out.accepted) == 1 and len(files) == 1, out.rejected
    ce = out.accepted[0]
    assert ce.expected == {"multiplier": 3.0}
    assert ce.headline == "Test headline."
    for phrase in ("## In short", "The document requires", "The system gives",
                   "| multiplier | 3 | 2 |", "permanent test"):
        assert phrase in page, f"report is missing {phrase!r}"


def test_bare_value_does_not_match_an_unrelated_result():
    actual = {"multiplier": 2.0, "accrues_toil": True}
    assert compare(True, actual) == UNCLEAR
    assert compare({"accrues_toil": True}, actual) == AGREES
    assert compare({"multiplier": 3.0}, actual) == DISAGREES
    assert compare({"multiplyer": 2.0}, actual) == UNCLEAR
    assert compare({"__error__": "ScopeConflict"},
                   {"__error__": "ScopeConflict", "diagnostic": "…"}) == AGREES
    assert compare({"multiplier": 2.0}, {"__error__": "NoApplicableRule"}) == DISAGREES
    assert compare("2026-02-28", {"retention_end_date": "2026-02-28"}) == AGREES


def test_every_recorded_counterexample_names_its_results():
    bare = [ce.id for ce in load_all() if ce.component == "catala"
            and not isinstance(ce.expected, dict)]
    assert not bare, f"these can pass on a coincidence: {bare}"


def test_scope_interface_knows_optional_inputs_and_enumerations():
    io = scope_io("catala/modules/retention.catala_en", "RetentionEnd")
    assert "most_recent_engagement_date" in io.inputs
    assert "most_recent_engagement_date" not in io.required
    assert "EmployeeRecord" in io.enums["data_class"]
    assert check_facts(io, ce_yaml("CE-0029")["inputs"]) == []
    assert any("one of" in p for p in check_facts(io, {**ce_yaml("CE-0029")["inputs"],
                                                       "data_class": "Payroll"}))


def test_fleet_proposals_are_shaped_by_the_scope():
    from lks import fleet
    from lks.registry import load_registry
    schema = fleet.attack_schema(load_registry()["Overtime.HourPremium"])
    facts = schema["properties"]["facts"]
    assert set(facts["required"]) == set(facts["properties"]) == {
        "ordinal", "grade", "is_public_holiday", "is_critical_incident", "is_night",
        "has_standing_shift_allowance"}
    assert facts["properties"]["is_night"] == {"type": "boolean"}
    assert schema["properties"]["narrative"]["maxLength"] <= 800


def test_expected_value_of_the_wrong_type_is_turned_away():
    for expected in ({"total_rate": None}, {"accrues_toil": "maybe"},
                     {"multiplier": "2.0x"}, {"__error__": "Bogus"}):
        out, files, _ = record(finding(expected=expected))
        assert not out.accepted and not files, f"{expected} became a permanent test"
        assert "cannot be compared" in out.rejected[0][1], out.rejected[0][1]


def test_citation_of_a_clause_that_does_not_exist_is_turned_away():
    for citations in (["NOT A CLAUSE"], ["EMP-ANNEX-C C-99.9"],
                      ["EMP-ANNEX-C C-4.1", "C-99.9"]):
        out, files, _ = record(finding(citations=citations))
        assert not out.accepted and not files, f"{citations} established an answer"
        assert "not a clause" in out.rejected[0][1], out.rejected[0][1]
    # a bare id, a sub-paragraph, and a definition the packet leaves out all cite real clauses
    out, files, _ = record(finding(citations=["C-4.1", "EMP-ANNEX-C C-7.2(a)",
                                              "EMP-ANNEX-C C-2.1"]))
    assert len(files) == 1, out.rejected
    assert out.accepted[0].citations == ["EMP-ANNEX-C C-4.1", "EMP-ANNEX-C C-7.2(a)",
                                         "EMP-ANNEX-C C-2.1"]


def test_every_recorded_counterexample_would_still_be_accepted():
    """The gates must turn away bad claims, not the breaks already confirmed."""
    from lks.interface import check_results
    from lks.reviewer import clause_refs, normalise_citations
    for ce in load_all():
        path, scope = ce.target["path"], ce.target["scope"]
        assert check_results(scope_io(path, scope), ce.expected) == [], ce.id
        _, why = normalise_citations(ce.citations, *clause_refs(path))
        assert not why, f"{ce.id}: {why}"


def test_finding_about_another_component_is_not_recorded_unexecuted():
    out, files, _ = record(finding(component="chat", observed={"total_rate": 1}))
    assert not out.accepted and not files, "a claim nothing executed was recorded"


def test_implementer_notes_in_the_gutter_do_not_reach_the_packet():
    from lks.reviewer import build_catala_packet, strip_implementer_commentary
    src = ("# Title\n\n"
           "| EMP-ANNEX-C C-4.1 (001-employment-terms-annex-c.md:55)\n|\n"
           "| An Employee is entitled.\n\n"
           "| NOTE: Reading (A), adopted here.\n|\n| More of the implementer's reasoning.\n\n"
           "| EMP-ANNEX-C C-4.2 (001-employment-terms-annex-c.md:59)\n"
           "| NO-CLAUSE: chosen because I read it so\n| and the rest of why\n\n"
           "```catala\nscope X:\n  # why this line\n  definition a equals 1\n```\n")
    out, _ = strip_implementer_commentary(src)
    for kept in ("| An Employee is entitled.", "| NO-CLAUSE\n", "definition a equals 1"):
        assert kept in out, f"lost {kept!r}"
    for gone in ("adopted here", "implementer's reasoning", "I read it so", "rest of why",
                 "why this line"):
        assert gone not in out, f"{gone!r} reached the packet"
    for module in ("mealperdiem", "accommodation", "expensedefs"):
        artefact = build_catala_packet(f"catala/modules/{module}.catala_en").artefact
        assert "NOTE:" not in artefact, f"{module}'s notes reached its packet"


def test_headings_keep_their_clause_ids_and_lose_their_words():
    from lks.reviewer import strip_implementer_commentary
    out, _ = strip_implementer_commentary(
        "# Sales Commission Plan\n\n## S-6.1 The gateway is a fact about the absence\n\n"
        "## N-5.3 and N-5.4 applying to the same information\n")
    assert out.splitlines() == ["#", "", "## S-6.1", "", "## N-5.3 N-5.4"], out


def test_the_packet_sets_out_the_definitions_and_sections_its_clauses_rely_on():
    from lks.reviewer import build_catala_packet
    source = build_catala_packet(OVERTIME).source_clauses
    quoted, _, related = source.partition("### Not quoted by the artefact")
    assert related, "the overtime packet sets out nothing beyond what it quotes"
    for ref in ("C-2.1", "C-2.2", "C-2.3", "C-2.4", "C-2.5", "C-2.6"):
        assert f"### EMP-ANNEX-C {ref} " in related, f"{ref} is missing from the packet"
    assert "### EMP-ANNEX-C C-4.1 " in quoted and "### EMP-ANNEX-C C-4.1 " not in related
    assert "EMP-ANNEX-C C-9.1 " not in source, "a clause nothing relies on was added"


def test_a_withdrawn_counterexample_id_is_not_reused():
    from lks.counterexample import record_counterexample
    with tempfile.TemporaryDirectory() as d:
        store = Path(d)
        (store / "CE-0007.withdrawn").write_text("withdrawn\n")
        ce = record_counterexample("catala", "A test.", ["EMP-ANNEX-C C-4.1"], "",
                                   {"x": 1}, {"x": 2}, store=store)
        assert ce.id == "CE-0008", ce.id
    assert (ROOT / "tests/counterexamples/CE-0032.withdrawn").exists()


def _review_loop():
    import importlib.util
    spec = importlib.util.spec_from_file_location("review_loop", ROOT / "scripts/review_loop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_only_an_attempt_that_ran_the_rule_counts_towards_sign_off():
    loop = _review_loop()
    entry, quiet, stalled = loop.new_entry(), 0, 0
    for outcome in ["no_break"] * 20 + ["ambiguity"] * 20 + ["rejected"] * 5:
        quiet, stalled = loop.score(outcome, entry, quiet, stalled)
    assert quiet == 0 and entry["longest_quiet_streak"] == 0, "nothing was run, yet it went quiet"
    for _ in range(3):
        quiet, stalled = loop.score("held", entry, quiet, stalled)
    quiet, stalled = loop.score("no_break", entry, quiet, stalled)
    assert quiet == 3 and entry["longest_quiet_streak"] == 3
    quiet, stalled = loop.score("defect", entry, quiet, stalled)
    assert quiet == 0 and entry["longest_quiet_streak"] == 0, "a defect left the sign-off standing"


def test_a_checker_failure_is_not_counted_as_the_model_stalling():
    loop = _review_loop()
    entry = loop.new_entry()
    assert loop.score("error", entry, 2, 1) == (2, 1)
    assert entry["errors"] == 1 and entry["malformed"] == 0


def test_two_runs_on_different_modules_keep_each_others_progress():
    loop = _review_loop()
    with tempfile.TemporaryDirectory() as d:
        loop.STATE, loop.ROUNDS = Path(d) / "state.json", Path(d) / "rounds"
        alpha = {"modules": {"alpha": {**loop.new_entry(), "rounds_total": 3}}}
        beta = {"modules": {"beta": {**loop.new_entry(), "rounds_total": 5}}}
        loop.save_state(alpha, "alpha")
        loop.save_state(beta, "beta")       # loaded before alpha's progress was saved
        on_disk = loop.load_state()["modules"]
        assert on_disk["alpha"]["rounds_total"] == 3, "beta's save erased alpha's progress"
        assert on_disk["beta"]["rounds_total"] == 5


def test_sign_off_is_tied_to_the_version_of_the_rule():
    from lks.reviewer import rule_fingerprint
    original = Path(OVERTIME).read_text(encoding="utf-8")
    assert "## C-8 Precedence\n\n" in original and "if ordinal > 40 then 0.0 else 1.0" in original
    with tempfile.TemporaryDirectory() as d:
        copy = Path(d) / "overtime.catala_en"
        copy.write_text(original, encoding="utf-8")
        before = rule_fingerprint(copy)
        copy.write_text(original.replace(
            "## C-8 Precedence\n\n",
            "## C-8 Precedence\n\nAn explanation reworded.\n\n| NOTE: adopted here.\n\n"),
            encoding="utf-8")
        assert rule_fingerprint(copy) == before, "rewording commentary undid a sign-off"
        copy.write_text(original.replace("if ordinal > 40 then 0.0 else 1.0",
                                         "if ordinal > 41 then 0.0 else 1.0"), encoding="utf-8")
        assert rule_fingerprint(copy) != before, "a changed rule kept its sign-off"
    uses = rule_fingerprint("catala/modules/mealperdiem.catala_en")
    assert uses == rule_fingerprint("catala/modules/mealperdiem.catala_en")


# --- plain English ---------------------------------------------------------

def test_values_read_as_words():
    assert plain.value(True) == "yes"
    assert plain.value(2.0) == "2"
    assert plain.value(140000.0, "money") == "140,000.00"
    assert plain.value("2026-02-28") == "28 February 2026"
    assert plain.value({"years": 6, "months": 0, "days": 0}) == "6 years"
    assert plain.value({"__error__": "ScopeConflict"}).startswith("no answer")
    assert plain.rule_name("overtime", "HourPremium") == "Overtime: hour premium"


def test_summary_of_a_recorded_break_uses_executed_values():
    ce = Counterexample.from_dict(ce_yaml("CE-0029"))
    s = plain.break_summary(ce)
    assert "28 February 2026" in s and "1 March 2026" in s, s


def test_attack_that_found_nothing_does_not_read_as_a_failure():
    from lks import exposure
    s = plain.attack_result(exposure.DIED_NO_BAD_OUTCOME)
    assert "died" not in s and "predicate" not in s, s


# --- the model client, against a fake Ollama -------------------------------

class FakeOllama(BaseHTTPRequestHandler):
    plan: dict = {}

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        plan = type(self).plan
        try:
            if plan.get("status"):
                self.send_response(plan["status"])
                self.end_headers()
                self.wfile.write(b'{"error": "boom"}')
                return
            time.sleep(plan.get("delay_first", 0))
            self.send_response(200)
            self.end_headers()
            for piece in plan.get("pieces", []):
                self.wfile.write((json.dumps({"response": piece, "done": False}) + "\n").encode())
                self.wfile.flush()
                time.sleep(plan.get("gap", 0))
            self.wfile.write((json.dumps({"response": "", "done": True,
                                          "done_reason": plan.get("reason", "stop"),
                                          "eval_count": len(plan.get("pieces", [])),
                                          "prompt_eval_count": 7}) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


def with_fake(plan: dict, fn):
    FakeOllama.plan = plan
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old = llm.OLLAMA_HOST
    llm.OLLAMA_HOST = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        return fn()
    finally:
        llm.OLLAMA_HOST = old
        srv.shutdown()
        srv.server_close()


def test_client_stops_reading_when_the_json_is_complete():
    plan = {"pieces": ['{"verdict": ', '"NO_BREAK_FOUND"}'] + [" "] * 40, "gap": 0.1}
    t0 = time.monotonic()
    reply = with_fake(plan, lambda: llm.generate("x", as_json=True, timeout=5))
    assert time.monotonic() - t0 < 2, "the client waited for the budget to run out"
    assert reply.json() == {"verdict": "NO_BREAK_FOUND"}


def test_silence_is_a_timeout_not_a_crash():
    try:
        with_fake({"pieces": ["{}"], "delay_first": 3},
                  lambda: llm.generate("x", as_json=True, timeout=1))
    except llm.ModelTimedOut:
        return
    raise AssertionError("a silent model did not raise ModelTimedOut")


def test_role_failures_are_reported_not_raised():
    res = with_fake({"status": 500}, lambda: agents.run_role(agents.TRIAGE, "x"))
    assert not res.ok and res.kind == "unavailable", (res.kind, res.error)
    res = with_fake({"pieces": ['{"label": "RU'], "reason": "length"},
                    lambda: agents.run_role(agents.TRIAGE, "x"))
    assert not res.ok and res.kind == "truncated", (res.kind, res.error)
    assert "ran out of room" in plain.failed_attempt(res.kind)


# --- runner ----------------------------------------------------------------

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for t in tests:
        try:
            t()
            print(f"  ok    {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"  FAIL  {t.__name__}: {str(e)[:200]}")
        except Exception as e:                                  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {str(e)[:200]}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
