#!/usr/bin/env python3
"""Tests for document generation.

    . scripts/env.sh && $PY tests/test_generate.py

Plain asserts and a `__main__` that runs them all, matching tests/test_exposure.py.
None of these needs the local model: every agent reply is injected, because the
parts of the pipeline that must be right are what happens *to* a reply --
whether a planted defect is caught, whether a hallucinated finding is thrown
away, whether a failed document is withheld.

The load-bearing tests, the ones that would mean the pipeline issues something
it should not:

  * `test_dead_branch_blocks_and_derived_does_not` -- G3 blocking on proof, not
    on its own blind spot
  * `test_assertion_is_a_domain_statement_not_a_failure` and
    `test_assertion_false_cannot_pass_vacuously` -- G2 cannot be gamed from
    either side
  * `test_hallucinated_quote_is_discarded` and
    `test_confirmed_finding_blocks_alone` -- the screen's two halves
  * `test_failed_pipeline_issues_no_pdf` -- the refusal
  * `test_stray_fence_never_reaches_the_document` -- a defect this pipeline
    actually printed into a PDF once
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lks import agents, gates, generate, llm, mutate, pdf, render, screen
from lks.segment import load_corpus, parse_document

TMP = Path(tempfile.mkdtemp(prefix="lks-generate-test-"))

DOC = """---
doc_id: ONCALL-POL
title: "On-Call Allowance Policy — Test"
version: "1.0"
effective_date: 2026-10-01
jurisdiction: "England and Wales"
owner: "People Operations"
---

# On-Call Allowance Policy

## K-1 Definitions

**K-1.1** "On-Call Shift" means a continuous period during which an Employee
is required to be available to respond to incidents.

## K-2 Allowance

**K-2.1** An Employee is entitled to an allowance of £40 for each On-Call Shift.

**K-2.2** By way of exception to K-2.1, where an On-Call Shift falls on a
public holiday the allowance is £80.

## K-3 Callouts

**K-3.1** An Employee is entitled to £25 for each callout in an On-Call
Shift, for no more than 4 callouts in that shift.
"""

GOOD = """# On-Call Allowance Policy

> Module OnCall

```catala-metadata
declaration scope ShiftPay:
  input is_public_holiday content boolean
  input callouts content integer
  output allowance content money
  output callout_pay content money
```

## K-2 Allowance

| ENCODES: K-2.1

```catala
scope ShiftPay:
  label base definition allowance equals $40
```

| ENCODES: K-2.2

```catala
scope ShiftPay:
  exception base definition allowance under condition is_public_holiday
  consequence equals $80
```

## K-3 Callouts

| ENCODES: K-3.1

```catala
scope ShiftPay:
  assertion callouts >= 0
  label capped definition callout_pay equals $25 * (decimal of callouts)
  exception capped definition callout_pay under condition callouts > 4
  consequence equals $100
```
"""


def _write(name: str, text: str) -> Path:
    d = TMP / hashlib.sha256(text.encode()).hexdigest()[:8]
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


def _doc() -> Path:
    return _write("document.md", DOC)


def _module(text: str = GOOD) -> Path:
    doc = parse_document(_doc())
    assembled, name, _ = generate.assemble_module(text, doc)
    return _write(f"{name}.catala_en", assembled)


# --- pdf -----------------------------------------------------------------


def test_pdf_is_byte_deterministic():
    doc = parse_document(_doc())
    a = render.render_document(doc, TMP / "a.pdf")
    b = render.render_document(doc, TMP / "b.pdf")
    assert a.sha256 == b.sha256, "the same document must render to the same bytes"
    assert a.unmapped == {}, a.unmapped


def test_pdf_metadata_is_a_text_string_not_winansi():
    # An em dash is 0x97 in WinAnsi and Scaron in PDFDocEncoding. Writing
    # metadata through the content-stream encoder printed "Test Š" in readers.
    assert pdf._text_string("A — B").startswith(b"<feff"), "non-ASCII metadata must be UTF-16BE"
    assert pdf._text_string("plain") == b"(plain)"
    data = (TMP / "a.pdf").read_bytes() if (TMP / "a.pdf").exists() else b""
    if not data:
        test_pdf_is_byte_deterministic()
        data = (TMP / "a.pdf").read_bytes()
    assert b"\x97" not in data.split(b"/Title", 1)[1][:200], "title must not be WinAnsi-encoded"


def test_unmappable_character_is_reported_not_swallowed():
    pdf.reset_unmapped()
    pdf.encode("price ₹100")                           # rupee sign: not in WinAnsi
    assert pdf.unmapped().get("₹") == 1


def test_failed_render_is_stamped_on_every_page():
    doc = parse_document(Path(load_corpus()[0].source_path))
    prov = render.Provenance(prompt="x", failed=True, failure_reason="G2")
    rr = render.render_document(doc, TMP / "failed.pdf", provenance=prov)
    raw = (TMP / "failed.pdf").read_bytes()
    import zlib, re as _re
    streams = [zlib.decompress(m.group(1)) for m in
               _re.finditer(rb"stream\n(.*?)\nendstream", raw, _re.S)]
    stamped = sum(1 for s in streams if b"FAILED GATE" in s)
    assert stamped == rr.pages, f"{stamped} of {rr.pages} pages stamped"


# --- extraction and assembly --------------------------------------------


def test_stray_fence_never_reaches_the_document():
    for raw in (f"Here is the document:\n\n```markdown\n{DOC}```",
                f"```markdown\n{DOC}```\n\nLet me know if you need changes.",
                f"Preamble.\n{DOC}```"):
        out = generate.extract_markdown(raw)
        assert "```" not in out, "a fence was left inside the document"
        assert out.startswith("---"), out[:40]


def test_encoder_reply_is_unwrapped_from_every_realistic_shape():
    shapes = {
        "bare": GOOD,
        "catala fence after preamble": f"Here is the module:\n\n```catala\n{GOOD}```",
        "plain fence with sign-off": f"```\n{GOOD}```\n\nLet me know if anything should change.",
        "markdown fence": f"```markdown\n{GOOD}\n```\n",
        "catala_en fence, no title": "```catala_en\n" + GOOD.split("\n", 2)[2] + "```",
    }
    doc = parse_document(_doc())
    for label, raw in shapes.items():
        out = generate.extract_catala(raw)
        assert "> Module OnCall" in out, label
        assert not out.lstrip().startswith("```") and not out.rstrip().endswith("```\n```"), label
        opens = sum(1 for ln in out.splitlines() if ln.startswith("```") and ln.strip() != "```")
        closes = sum(1 for ln in out.splitlines() if ln.strip() == "```")
        assert opens == closes, f"{label}: {opens} opens, {closes} closes"
        text, name, _ = generate.assemble_module(out, doc)
        assert gates.g1_typecheck(_write(f"{name}.catala_en", text)).ok, label


def test_encodes_markers_expand_to_verbatim_quotations():
    doc = parse_document(_doc())
    text, name, notes = generate.assemble_module(GOOD, doc)
    assert name == "OnCall" and not notes
    r = gates.g4_quotation_fidelity(_write(f"{name}.catala_en", text), _doc())
    assert r.ok, r.evidence
    assert r.evidence["quoted_clauses"] == ["K-2.1", "K-2.2", "K-3.1"]


def test_unknown_clause_in_encodes_is_named_by_g4():
    doc = parse_document(_doc())
    text, name, notes = generate.assemble_module(GOOD.replace("ENCODES: K-3.1", "ENCODES: K-9.9"), doc)
    assert any("K-9.9" in n for n in notes)
    r = gates.g4_quotation_fidelity(_write(f"{name}.catala_en", text), _doc())
    assert not r.ok and any("K-9.9" in p for p in r.evidence["problems"])


def test_module_colliding_with_the_corpus_is_renamed():
    doc = parse_document(_doc())
    text, name, notes = generate.assemble_module(GOOD.replace("Module OnCall", "Module Overtime"),
                                                 doc, reserved={"Overtime"})
    assert name == "GenOvertime" and "> Module GenOvertime" in text and notes


# --- gates ---------------------------------------------------------------


def test_out_of_tree_module_that_uses_a_corpus_module_executes():
    # `run_scope_traced` once lacked the project include path, so every traced
    # vector of such a module failed with "Required module not found" and the
    # gates reported a failure that was the harness's, not the module's.
    src = (ROOT / "catala/modules/accommodation.catala_en").read_text(encoding="utf-8")
    assert "> Using ExpenseDefs" in src
    path = _write("accommodation.catala_en", src)
    run = gates.execute_battery(path, cap=120)
    errors = [e for sr in run.scopes for e in sr.errors]
    assert not any("Required module not found" in e["diagnostic"] for e in errors), errors[:1]
    assert gates.g2_totality(run).ok


def test_good_module_passes_every_cheap_gate():
    rep, run = gates.run_cheap_gates(_module(), _doc())
    assert rep.ok, rep.summary()
    assert run.size >= 8


def test_type_error_fails_g1_and_is_not_reported_as_total():
    rep, _ = gates.run_cheap_gates(_module(GOOD.replace("equals $40", "equals 40")), _doc())
    assert not rep.get("G1").ok
    assert not rep.get("G2").ok and "not reached" in rep.get("G2").detail


def test_dead_branch_blocks_and_derived_does_not():
    dead = GOOD.replace(
        "exception base definition allowance under condition is_public_holiday",
        "exception base definition allowance under condition callouts > 10 and callouts < 5",
    )
    rep, _ = gates.run_cheap_gates(_module(dead), _doc())
    g3 = rep.get("G3")
    assert not g3.ok and g3.evidence["dead_branches"], g3.detail

    rep, _ = gates.run_cheap_gates(_module(DERIVED), _doc())
    g3 = rep.get("G3")
    assert g3.ok, "a branch the battery cannot aim at must not block: " + g3.detail
    assert g3.evidence["untested_branches"] and "NOT EXERCISED" in g3.detail


DERIVED = GOOD.replace("  output callout_pay content money\n",
                       "  output callout_pay content money\n  internal doubled content integer\n") \
              .replace("  assertion callouts >= 0\n",
                       "  assertion callouts >= 0\n  definition doubled equals callouts * 1000\n") \
              .replace("under condition is_public_holiday", "under condition doubled > 999999")
"""K-2.2's exception conditioned on a derived value no vector reaches: G3
passes it as NOT EXERCISED."""


def test_inoperative_clause_is_refuted_only_where_every_branch_was_taken():
    # G3 passes DERIVED, and the screen used to refute "K-2.2 is inoperative"
    # with "every branch fired" -- about the one branch that never had.
    path = _module(DERIVED)
    rep, run = gates.run_cheap_gates(path, _doc())
    assert rep.get("G3").ok
    ex = gates.exercised_clauses(path, run)
    assert "K-2.2" not in ex and {"K-2.1", "K-3.1"} <= ex, ex
    doc = parse_document(_doc())
    f = screen.Finding(agent="language", kind="INOPERATIVE_CLAUSE", clause_ids=["K-2.2"],
                       quote="public holiday", summary="never applies")
    screen.verify(f, doc=doc, module=path, ifaces={}, corpus_refs=set(), exercised_clauses=ex)
    assert f.status == screen.JUDGEMENT, (f.status, f.verified_by)

    good = _module()
    _, run = gates.run_cheap_gates(good, _doc())
    assert gates.exercised_clauses(good, run) == {"K-2.1", "K-2.2", "K-3.1"}


SHADOWED = GOOD.replace(
    "  exception base definition allowance under condition is_public_holiday\n",
    "  exception base definition allowance under condition not is_public_holiday\n"
    "  consequence equals $40\n"
    "  exception base definition allowance under condition is_public_holiday\n",
)
"""An unconditional base both of whose exceptions together cover every case."""


def test_a_shadowed_base_case_is_repaired_in_the_encoding_not_the_document():
    rep, _ = gates.run_cheap_gates(_module(SHADOWED), _doc())
    dead = rep.get("G3").evidence["dead_branches"]
    assert dead and all(d["unconditional"] for d in dead), dead

    drafts = []
    base = _fake([SHADOWED, GOOD])

    def call(role, prompt, *, model, seed, validate=None):
        if role.name == "drafter":
            drafts.append(prompt)
        return base(role, prompt, model=model, seed=seed, validate=validate)

    r = generate.generate("an on-call policy", workspace=TMP / "run-shadowed", emit=lambda _s: None,
                          call=call, limits=generate.Limits(roundtrip=False))
    assert r.passed and r.catala_iterations == 2, (r.failure_stage, r.failure_reason)
    assert len(drafts) == 1, "no redraft can remove a base case the encoder wrote"


REPAY = """# Repayment

> Module Repay

```catala-metadata
declaration scope Repayment:
  input payment_date content date
  input termination_date content date
  output repayment content money
```

| NO-CLAUSE: test fixture

```catala
scope Repayment:
  date round down
  label full definition repayment equals $300
  label half exception full definition repayment
    under condition termination_date > Date.add_round_down of payment_date, 6 month
    consequence equals $150
  label none exception half definition repayment
    under condition termination_date > Date.add_round_down of payment_date, 12 month
    consequence equals $0
```
"""


def test_date_offsets_are_read_as_the_compiler_prints_them():
    from lks.catala_runner import exception_tree
    from lks.draft import collect_date_offsets
    trees = exception_tree(ROOT / "catala/modules/NdaSurvival.catala_en", "SurvivalEnd",
                           "agreement_end_date")
    assert {(30, "day"), (2, "year")} <= collect_date_offsets(trees)


def test_a_date_window_is_inside_the_battery():
    # Run 4's "repay half between 6 and 12 months" was never executed: no
    # offset was collected, and G3 called the branch "derived" because it read
    # `months` as a variable.
    path = _write("Repay.catala_en", REPAY)
    assert gates.g1_typecheck(path).ok
    run = gates.execute_battery(path)
    g3 = gates.g3_dead_branches(run)
    assert g3.evidence["covered"] == g3.evidence["total"] == 3, g3.detail
    assert gates.g2_totality(run).ok

    dead = REPAY.replace("12 month", "3 month")      # "none" now always overrides "half"
    run = gates.execute_battery(_write("Repay.catala_en", dead))
    g3 = gates.g3_dead_branches(run)
    assert not g3.ok and any("half" in d["branch"] for d in g3.evidence["dead_branches"]), g3.detail


def test_assertion_is_a_domain_statement_not_a_failure():
    # callouts probes 0; forbid it, and the rejected vectors must not fail G2
    guarded = GOOD.replace("assertion callouts >= 0", "assertion callouts >= 1")
    rep, run = gates.run_cheap_gates(_module(guarded), _doc())
    g2 = rep.get("G2")
    assert g2.ok, g2.detail
    assert g2.evidence["rejected_by_assertion"] > 0


def test_assertion_false_cannot_pass_vacuously():
    rep, _ = gates.run_cheap_gates(_module(GOOD.replace("assertion callouts >= 0", "assertion false")), _doc())
    g2 = rep.get("G2")
    assert not g2.ok and g2.evidence["error_class"] == "AssertionsRejectBattery", g2.detail


def test_small_battery_is_not_failed_for_being_small():
    # A floor of 8 absolute vectors failed seven correct committed modules.
    small = GOOD.replace("callouts > 4", "is_public_holiday")
    rep, run = gates.run_cheap_gates(_module(small), _doc())
    assert rep.get("G2").ok, rep.get("G2").detail


def test_conflict_is_caught_at_a_boundary():
    conflict = GOOD.replace(
        "  exception capped definition callout_pay under condition callouts > 4\n  consequence equals $100\n",
        "  exception capped definition callout_pay under condition callouts > 4\n  consequence equals $100\n"
        "  exception capped definition callout_pay under condition callouts >= 4\n  consequence equals $99\n",
    )
    rep, _ = gates.run_cheap_gates(_module(conflict), _doc())
    g2 = rep.get("G2")
    assert not g2.ok and g2.evidence["error_class"] == "ScopeConflict", g2.detail
    assert g2.evidence["failing_vector"]["callouts"] >= 5


# --- screen --------------------------------------------------------------


def _screen(replies: dict) -> screen.ScreenResult:
    module = _module()
    doc = parse_document(_doc())

    def call(role, prompt, *, model, seed):
        v = replies.get(role.name, {"findings": []})
        if isinstance(v, Exception):
            return agents.RoleResult(role.name, False, error=str(v), enforcement="prompt")
        return agents.RoleResult(role.name, True, value=v, enforcement="prompt",
                                 usage=llm.Usage(0, 0, 0.0))

    ws = TMP / "screen"
    ws.mkdir(exist_ok=True)
    return screen.run_screen(doc=doc, document_md=DOC, module=module, workspace=ws,
                             corpus_context=[], corpus_refs=set(),
                             exercised_clauses={"K-2.1", "K-2.2", "K-3.1"},
                             emit=lambda _s: None, call=call)


INPUTS = {"is_public_holiday": False, "callouts": 5}


def test_confirmed_finding_blocks_alone():
    r = _screen({"screen-logic": {"findings": [{
        "verdict": "BREAK", "clause_ids": ["K-3.1"], "scope": "ShiftPay", "inputs": INPUTS,
        "expected": {"callout_pay": 125}, "summary": "five callouts"}]}})
    assert len(r.confirmed) == 1 and not r.passed
    assert "the rule produced" in r.confirmed[0].verified_by


def test_finding_matching_the_rule_is_refuted():
    r = _screen({"screen-logic": {"findings": [{
        "verdict": "BREAK", "clause_ids": ["K-3.1"], "scope": "ShiftPay", "inputs": INPUTS,
        "expected": {"callout_pay": 100}, "summary": "capped at 4"}]}})
    assert r.passed and r.all_findings[0].status == screen.REFUTED


def test_invented_input_is_discarded_before_execution():
    r = _screen({"screen-logic": {"findings": [{
        "verdict": "BREAK", "clause_ids": ["K-3.1"], "scope": "ShiftPay",
        "inputs": {**INPUTS, "salary": 10}, "expected": {"callout_pay": 1}, "summary": "x"}]}})
    f = r.all_findings[0]
    assert f.status == screen.DISCARDED and "invented" in f.discard_reason and r.passed


def test_hallucinated_quote_is_discarded():
    r = _screen({"screen-language": {"findings": [{
        "kind": "UNQUANTIFIED_STANDARD", "clause_ids": ["K-2.1"],
        "quote": "a reasonable allowance", "summary": "x"}]}})
    assert r.all_findings[0].status == screen.DISCARDED and r.passed


def test_lone_judgement_is_an_open_question_and_does_not_block():
    r = _screen({"screen-language": {"findings": [{
        "kind": "AMBIGUOUS_REFERENT", "clause_ids": ["K-2.2"], "quote": "public holiday",
        "summary": "whose holiday"}]}})
    assert r.passed and len(r.open_questions) == 1


def test_two_reviewers_on_one_clause_block():
    r = _screen({
        "screen-language": {"findings": [{"kind": "UNDEFINED_TERM", "clause_ids": ["K-2.2"],
                                          "quote": "public holiday", "summary": "undefined"}]},
        "screen-consistency": {"findings": [{"kind": "UNDECIDED_CASE", "clause_ids": ["K-2.2", "K-3.1"],
                                             "quote": "public holiday", "summary": "whose calendar"}]},
    })
    assert not r.passed and len(r.agreed) == 2


def test_one_reviewer_cannot_agree_with_itself():
    r = _screen({"screen-language": {"findings": [
        {"kind": "UNDEFINED_TERM", "clause_ids": ["K-2.2"], "quote": "public holiday", "summary": "a"},
        {"kind": "AMBIGUOUS_REFERENT", "clause_ids": ["K-2.2"], "quote": "public holiday", "summary": "b"}]}})
    assert r.passed and not r.agreed


def test_defined_term_refutes_undefined_term():
    r = _screen({"screen-language": {"findings": [{
        "kind": "UNDEFINED_TERM", "clause_ids": ["K-2.1"], "quote": "On-Call Shift", "summary": "x"}]}})
    assert r.all_findings[0].status == screen.REFUTED


def test_a_reviewer_that_never_replies_blocks_the_screen():
    r = _screen({"screen-consistency": RuntimeError("ModelRefused: empty completion")})
    assert not r.passed and len(r.incomplete) == 1
    assert r.agents[2].attempts == 2, "an unusable reply must be retried once"


# --- the pipeline, with every agent injected -----------------------------


def _fake(encodings: list[str], screens: list[dict] | None = None):
    state = {"enc": 0, "scr": 0}
    screens = screens or []

    def call(role, prompt, *, model, seed, validate=None):
        if role.name.startswith("screen-"):
            idx = min(state["scr"] // 3, len(screens) - 1) if screens else -1
            state["scr"] += 1
            value = (screens[idx].get(role.name) if idx >= 0 else None) or {"findings": []}
            return agents.RoleResult(role.name, True, value=value, enforcement="prompt")
        if role.name == "drafter":
            raw = DOC
        elif role.name == "encoder":
            raw = encodings[min(state["enc"], len(encodings) - 1)]
            state["enc"] += 1
        elif role.name == "reencoder":
            raw = GOOD
        else:
            raise AssertionError(role.name)
        try:
            v = validate(raw) if validate else raw
        except Exception as e:                          # noqa: BLE001
            return agents.RoleResult(role.name, False, error=str(e), enforcement="prompt")
        return agents.RoleResult(role.name, True, value=v, enforcement="prompt")
    return call


def test_pipeline_repairs_an_encoding_and_issues():
    ws = TMP / "run-ok"
    r = generate.generate("an on-call policy", workspace=ws, emit=lambda _s: None,
                          call=_fake([GOOD.replace("equals $40", "equals 40"), GOOD]))
    assert r.passed, r.failure_reason
    assert r.catala_iterations == 2 and r.pdf and r.pdf.exists()
    for name in ("report.md", "run.json", "context.json", "triage.yaml", "document.md"):
        assert (ws / name).exists(), name


BLOCKED = {"screen-logic": {"findings": [{
    "verdict": "BREAK", "clause_ids": ["K-3.1"], "scope": "ShiftPay", "inputs": INPUTS,
    "expected": {"callout_pay": 125}, "summary": "cap"}]}}


def test_pipeline_redrafts_after_a_blocked_screen():
    fixed = GOOD.replace("callouts > 4", "callouts > 5")
    r = generate.generate("an on-call policy", workspace=TMP / "run-redraft", emit=lambda _s: None,
                          call=_fake([GOOD, fixed], screens=[BLOCKED, {}]),
                          limits=generate.Limits(roundtrip=False))
    assert r.passed and r.screen_rounds == 2, (r.failure_stage, r.failure_reason)
    h = r.confirmed_history[0]
    assert h["outcome"].startswith("resolved, re-verified"), h["outcome"]


def test_a_break_a_later_round_merely_missed_still_blocks():
    # Same words, same code: round 2's reviewers were silent, nothing was
    # fixed. This used to issue, with "Resolved by redrafting" in the PDF.
    ws = TMP / "run-unresolved"
    r = generate.generate("an on-call policy", workspace=ws, emit=lambda _s: None,
                          call=_fake([GOOD], screens=[BLOCKED, {}]),
                          limits=generate.Limits(roundtrip=False))
    assert not r.passed and r.failure_stage == "screen", (r.failure_stage, r.failure_reason)
    assert "still reproduce" in r.failure_reason
    assert r.confirmed_history[0]["outcome"].startswith("STILL REPRODUCES")
    assert not (ws / "document.pdf").exists()


DEAD = GOOD.replace(
    "exception base definition allowance under condition is_public_holiday",
    "exception base definition allowance under condition callouts > 10 and callouts < 5",
)


def test_a_redraft_after_a_dead_branch_gets_its_own_encoding_attempts():
    # The redraft used to inherit the old draft's attempt count: dead, then two
    # broken encodings, failed "after 3 attempts" with the rewritten document
    # tried twice and the good encoding never reached.
    bad = GOOD.replace("equals $40", "equals 40")
    r = generate.generate("an on-call policy", workspace=TMP / "run-dead-budget",
                          emit=lambda _s: None, call=_fake([DEAD, bad, bad, GOOD]),
                          limits=generate.Limits(roundtrip=False))
    assert r.passed, (r.failure_stage, r.failure_reason)
    assert r.catala_iterations == 4
    assert [a.stage for a in r.attempts].count("draft-r11") == 1


def test_dead_branch_redrafts_are_bounded():
    drafts = []
    base = _fake([DEAD])

    def call(role, prompt, *, model, seed, validate=None):
        if role.name == "drafter":
            drafts.append(prompt)
        return base(role, prompt, model=model, seed=seed, validate=validate)

    r = generate.generate("an on-call policy", workspace=TMP / "run-dead-bounded",
                          emit=lambda _s: None, call=call,
                          limits=generate.Limits(roundtrip=False))
    lim = generate.Limits()
    assert not r.passed and r.failure_stage == "catala", (r.failure_stage, r.failure_reason)
    assert len(drafts) == 1 + lim.dead_branch_redrafts, len(drafts)
    assert r.catala_iterations == lim.dead_branch_redrafts + lim.catala_attempts


def test_an_incomplete_screen_screens_the_same_draft_again():
    # A reviewer that timed out used to trigger a redraft from a brief with no
    # items in it.
    base = _fake([GOOD])
    drafts, failures = [], {"n": 0}

    def call(role, prompt, *, model, seed, validate=None):
        if role.name == "drafter":
            drafts.append(prompt)
        if role.name == "screen-consistency" and failures["n"] < 2:
            failures["n"] += 1
            return agents.RoleResult(role.name, False, error="ModelTimedOut: 900s",
                                     enforcement="prompt")
        return base(role, prompt, model=model, seed=seed, validate=validate)

    r = generate.generate("an on-call policy", workspace=TMP / "run-incomplete",
                          emit=lambda _s: None, call=call,
                          limits=generate.Limits(roundtrip=False))
    assert r.passed, (r.failure_stage, r.failure_reason)
    assert len(drafts) == 1, "an incomplete screen must not redraft"
    assert r.screen_rounds == 2 and r.screens[0].incomplete and r.screens[1].passed
    assert r.catala_iterations == 1, "the same draft needs no new encoding"


def test_an_incomplete_last_round_fails_as_incomplete():
    base = _fake([GOOD])

    def call(role, prompt, *, model, seed, validate=None):
        if role.name == "screen-language":
            return agents.RoleResult(role.name, False, error="ModelTimedOut: 900s",
                                     enforcement="prompt")
        return base(role, prompt, model=model, seed=seed, validate=validate)

    r = generate.generate("an on-call policy", workspace=TMP / "run-incomplete-last",
                          emit=lambda _s: None, call=call,
                          limits=generate.Limits(roundtrip=False, screen_rounds=2))
    assert not r.passed and r.failure_stage == "screen"
    assert r.failure_reason.startswith("incomplete after 2 round(s)"), r.failure_reason


def test_a_consistency_judgement_is_not_refuted_by_the_rules_own_answer():
    # Run 4 dropped "no rule for exactly 37.5 hours" because the encoding
    # happened to give the answer the reviewer guessed.
    r = _screen({"screen-consistency": {"findings": [{
        "kind": "UNDECIDED_CASE", "clause_ids": ["K-3.1"], "scope": "ShiftPay",
        "inputs": INPUTS, "expected": {"callout_pay": 100}, "summary": "five callouts"}]}})
    f = r.all_findings[0]
    assert f.status == screen.JUDGEMENT, (f.status, f.verified_by, f.discard_reason)
    assert r.open_questions == [f] and r.passed
    assert f.observed["callout_pay"] == 100, f.observed


def test_a_consistency_judgement_with_unusable_inputs_is_kept():
    r = _screen({"screen-consistency": {"findings": [{
        "kind": "INTERNAL_CONTRADICTION", "clause_ids": ["K-3.1"], "scope": "ShiftPay",
        "inputs": {**INPUTS, "salary": 1}, "expected": {"callout_pay": 1}, "summary": "x"}]}})
    assert r.all_findings[0].status == screen.JUDGEMENT


def test_catala_waits_for_a_build_in_progress():
    # clerk rewrote `_build` while G2 executed an out-of-tree module from it,
    # and every vector failed with "implementation mismatch on Stdlib_en".
    import fcntl
    import threading
    import time
    from lks import catala_runner

    module = _module()
    catala_runner.BUILD_LOCK.parent.mkdir(parents=True, exist_ok=True)
    out: dict = {}

    def check():
        out["ok"] = catala_runner.typecheck(module).ok
        out["finished"] = time.monotonic()

    with open(catala_runner.BUILD_LOCK, "a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)          # a build in progress
        th = threading.Thread(target=check)
        th.start()
        time.sleep(2.0)
        released = time.monotonic()
        fcntl.flock(fh, fcntl.LOCK_UN)
    th.join(120)
    assert out.get("ok"), out
    assert out["finished"] >= released, "catala ran while a build held the lock"


def test_a_stale_build_is_rebuilt_and_the_run_retried():
    # A process that does not take the build lock can still leave `_build`
    # inconsistent; the loader's "implementation mismatch" is the build's
    # fault, never the module's, and must not reach a gate as a CatalaError.
    import subprocess as sp
    from lks import catala_runner

    tools = catala_runner.toolchain()
    calls: list[tuple[str, str]] = []
    real = sp.run

    def fake(args, **_kw):
        calls.append((Path(args[0]).name, args[1]))
        if calls == [("catala", "interpret")]:
            return sp.CompletedProcess(args, 123, "", 'While loading compiled module from '
                                       '"ExpenseDefs.cmxs":\n  implementation mismatch on Stdlib_en')
        return sp.CompletedProcess(args, 0, "{}", "")

    sp.run = fake
    try:
        proc = catala_runner._run([tools["catala"], "interpret", "x.catala_en"])
    finally:
        sp.run = real
    assert proc.returncode == 0
    assert calls == [("catala", "interpret"), ("clerk", "build"), ("catala", "interpret")], calls


def test_a_used_workspace_is_refused():
    ws = TMP / "run-reused"
    ws.mkdir()
    (ws / "document.pdf").write_bytes(b"from an earlier run that passed")
    try:
        generate.generate("an on-call policy", workspace=ws, emit=lambda _s: None,
                          call=_fake([GOOD]), limits=generate.Limits(roundtrip=False))
    except generate.WorkspaceInUse:
        return
    raise AssertionError("a workspace holding another run's PDF must not be reused")


def test_failed_pipeline_issues_no_pdf():
    ws = TMP / "run-fail"
    r = generate.generate("an on-call policy", workspace=ws, emit=lambda _s: None,
                          call=_fake([GOOD.replace("equals $40", "equals 40")]),
                          limits=generate.Limits(catala_attempts=2))
    assert not r.passed and r.failure_stage == "catala"
    assert r.pdf is None and not (ws / "document.pdf").exists(), "a failed document must not be issued"
    assert "NOT ISSUED" in (ws / "report.md").read_text()


def _stage_log(ev_list):
    """Every `running` must be closed by `done` or `failed` for the same key."""
    open_: dict[str, int] = {}
    for e in ev_list:
        if e["status"] == "running":
            assert e["key"] not in open_, f"{e['key']} started twice"
            open_[e["key"]] = 1
        else:
            assert open_.pop(e["key"], None), f"{e['key']} {e['status']} without running"
    assert not open_, f"stages never closed: {sorted(open_)}"
    return [(e["key"], e["status"]) for e in ev_list if e["status"] != "running"]


def test_pipeline_reports_structured_stages():
    events: list[dict] = []
    r = generate.generate("an on-call policy", workspace=TMP / "run-stages", emit=lambda _s: None,
                          call=_fake([GOOD.replace("equals $40", "equals 40"), GOOD]),
                          on_stage=events.append)
    assert r.passed
    closed = _stage_log(events)
    assert closed == [("retrieve", "done"), ("draft-1", "done"), ("catala-1-1", "failed"),
                      ("catala-1-2", "done"), ("screen-1", "done"), ("roundtrip-1", "done"),
                      ("pdf", "done")], closed
    assert all(set(e) == {"key", "title", "status", "summary"} for e in events)


def test_failed_pipeline_reports_the_pdf_stage_as_failed():
    events: list[dict] = []
    generate.generate("an on-call policy", workspace=TMP / "run-stages-fail", emit=lambda _s: None,
                      call=_fake([GOOD.replace("equals $40", "equals 40")]),
                      limits=generate.Limits(catala_attempts=1), on_stage=events.append)
    closed = _stage_log(events)
    assert closed[-1] == ("pdf", "failed") and "not issued" in events[-1]["summary"]


def test_a_broken_observer_cannot_break_a_run():
    def explode(_ev):
        raise RuntimeError("display crashed")
    r = generate.generate("an on-call policy", workspace=TMP / "run-observer", emit=lambda _s: None,
                          call=_fake([GOOD]), on_stage=explode,
                          limits=generate.Limits(roundtrip=False))
    assert r.passed


def test_the_model_never_dates_a_rule():
    md, note = generate.settle_effective_date(DOC, "an on-call policy")
    assert 'effective_date: "TO BE CONFIRMED"' in md and "2026-10-01" in note
    assert parse_document(_write("document.md", md)).effective_date == "TO BE CONFIRMED"

    kept, note = generate.settle_effective_date(DOC, "an on-call policy effective 2026-10-01")
    assert kept == DOC and not note

    forced, _ = generate.settle_effective_date(DOC, "an on-call policy", "2027-01-01")
    assert "effective_date: 2027-01-01" in forced and "2026-10-01" not in forced


def test_pipeline_writes_the_settled_date_and_prints_it():
    ws = TMP / "run-date"
    r = generate.generate("an on-call policy", workspace=ws, emit=lambda _s: None,
                          call=_fake([GOOD]), limits=generate.Limits(roundtrip=False))
    assert r.passed
    assert parse_document(ws / "document.md").effective_date == "TO BE CONFIRMED"
    import subprocess
    text = subprocess.run(["pdftotext", "-l", "1", str(ws / "document.pdf"), "-"],
                          capture_output=True, text=True).stdout
    if text:                                        # pdftotext is optional
        assert "TO BE CONFIRMED" in text


def test_a_deliberation_that_eats_the_budget_is_repeated_without_reasoning():
    base = _fake([GOOD])
    seen: list[tuple[str, bool]] = []

    def call(role, prompt, *, model, seed, validate=None):
        seen.append((role.name, role.think))
        if role.name == "drafter" and role.think:
            return agents.RoleResult(role.name, False, enforcement="prompt",
                                     error="the token budget (8192) was consumed by reasoning "
                                           "and no answer was produced.")
        return base(role, prompt, model=model, seed=seed, validate=validate)

    ws = TMP / "run-fallback"
    r = generate.generate("an on-call policy", workspace=ws, emit=lambda _s: None, call=call,
                          limits=generate.Limits(roundtrip=False))
    assert r.passed, (r.failure_stage, r.failure_reason)
    assert seen[:2] == [("drafter", True), ("drafter", False)], seen[:3]
    stages = [a.stage for a in r.attempts]
    assert "draft-r1" in stages and "draft-r1-direct" in stages, stages


def test_other_failures_are_not_retried_without_reasoning():
    calls = []

    def call(role, prompt, *, model, seed, validate=None):
        calls.append(role.think)
        return agents.RoleResult(role.name, False, enforcement="prompt", error="ModelUnavailable: down")

    r = generate.generate("an on-call policy", workspace=TMP / "run-nofallback", emit=lambda _s: None,
                          call=call, limits=generate.Limits(convention_attempts=1))
    assert not r.passed and r.failure_stage == "draft"
    assert calls == [True], calls


def test_encoder_prompt_keeps_the_whole_document_last_and_fits():
    precedent = [generate.ContextItem(ref="m", kind="encoding", score=0.5, doc_id="X",
                                      doc_title="X", text="x" * 20000)]
    budget = generate._prompt_budget_chars(agents.ENCODER)

    fresh = generate._encoder_prompt(DOC, precedent)
    assert fresh.rstrip().endswith(DOC.rstrip()), "the document must come last"
    assert len(fresh) <= budget, (len(fresh), budget)

    repair = generate._encoder_prompt(DOC, precedent, previous=GOOD * 3,
                                      brief="error: syntax\n" * 5000)
    assert repair.rstrip().endswith(DOC.rstrip())
    assert "house style" not in repair, "a repair must not resend the precedent module"
    assert len(repair) <= budget, (len(repair), budget)


def test_drafter_prompt_fits_and_keeps_the_request_and_previous_draft():
    ctx = [generate.ContextItem(ref="D", kind="document", score=0.5, doc_id="D", doc_title="D",
                                text="precedent line\n" * 2000) for _ in range(2)]
    ctx += [generate.ContextItem(ref=f"D C-{i}", kind="prose", score=0.4, doc_id="D",
                                 doc_title="D", text="clause " * 80) for i in range(8)]
    previous = DOC * 8
    p = generate._drafter_prompt("REQUEST-MARKER: an on-call policy", ctx,
                                 previous=previous, brief="fix this\n" * 2000)
    assert len(p) <= generate._prompt_budget_chars(agents.DRAFTER), len(p)
    assert "REQUEST-MARKER" in p and previous in p
    assert p.index("REQUEST-MARKER") > p.find("# Precedent"), "precedent is what truncation may drop"
    assert p.rstrip().endswith("so references to it stay valid.")


def test_triage_does_not_call_a_rule_hybrid_for_the_letters_in_an_input_name():
    # "in good faith" was matched by its first word, so any input containing
    # "in" -- `termination_date` in a real run -- made a date rule HYBRID.
    module = _module(GOOD.replace("callouts", "callouts_in_shift"))
    labels = generate.derive_triage(parse_document(_doc()), module)
    assert labels["K-3.1"]["label"] == "RULE", labels["K-3.1"]
    assert labels["K-1.1"]["label"] == "PROSE"


def test_g5_does_not_pass_on_the_scopes_it_happened_to_match():
    two = GOOD.replace(
        "  output callout_pay content money\n",
        "  output callout_pay content money\n\ndeclaration scope Standby:\n"
        "  input nights content integer\n  output standby_pay content money\n",
    ) + "\n| NO-CLAUSE: test fixture\n\n```catala\nscope Standby:\n  definition standby_pay equals $10\n```\n"
    original = _module(two)
    assert gates.g1_typecheck(original).ok
    g5 = gates.g5_roundtrip(original, _module(), scope_map={"ShiftPay": "ShiftPay"}, cap=60)
    assert not g5.ok and g5.evidence["unmatched_scopes"] == ["Standby"], g5.detail
    assert "UNTESTED" in g5.detail

    reverse = gates.g5_roundtrip(_module(), original, scope_map={"ShiftPay": "ShiftPay"}, cap=60)
    assert reverse.ok, reverse.detail
    assert reverse.evidence["not_in_module"] == ["Standby.standby_pay"]


def test_reencoder_prompt_ends_with_the_specification():
    p = generate._reencoder_prompt(DOC)
    assert p.rstrip().endswith(DOC.rstrip()) and "Date.add_round_down of" in p


def test_a_pinned_boolean_cannot_hide_a_real_difference():
    # At cap 240 the battery pins is_public_holiday to true, where C-7.1 governs
    # every hour, and a mutant paying a Grade 3 employee's 41st hour 0.0 instead
    # of 1.25 was once classified equivalent to the original.
    from lks.draft import compare_encodings
    m = next(x for x in mutate.enumerate_mutants(ROOT / "catala/modules/overtime.catala_en")
             if x.operator == "and_to_or" and "grade >= 5 or" in x.after)
    path = mutate.write_mutant(m, TMP / "pinned")
    res = compare_encodings(ROOT / "catala/modules/overtime.catala_en", path,
                            scope_map={"HourPremium": "HourPremium"}, cap=240)
    assert res.behaviour_diffs, "a real difference was hidden by a pinned input"
    assert not res.converged


def test_an_execution_failure_is_untested_not_agreement():
    import lks.draft as draft
    from lks.catala_runner import CatalaError
    real = draft.run_scope

    def broken(path, scope, inputs=None):
        raise CatalaError("x", stderr="Required module not found: ExpenseDefs")

    draft.run_scope = broken
    try:
        res = draft.compare_encodings(_module(), _module(), scope_map={"ShiftPay": "ShiftPay"}, cap=40)
    finally:
        draft.run_scope = real
    assert not res.behaviour_diffs
    assert any("UNTESTED" in e for e in res.errors), res.errors
    assert not res.converged, "vectors nobody could run must not read as agreement"


def test_leave_one_out_really_excludes_the_document():
    items, _ = generate.retrieve("overtime premium for hours over 48 in a payroll week",
                                 exclude_docs=("EMP-ANNEX-C",))
    assert items and all(i.doc_id != "EMP-ANNEX-C" for i in items)


# --- mutants -------------------------------------------------------------


def test_boundary_mutants_compile_and_are_single_site():
    ms = [m for m in mutate.enumerate_mutants(ROOT / "catala/modules/overtime.catala_en")
          if m.operator == "flip_comparison"][:3]
    assert ms
    for m in ms:
        src = Path(m.module).read_text(encoding="utf-8")
        out = m.apply(src)
        changed = [i for i, (a, b) in enumerate(zip(src.splitlines(), out.splitlines())) if a != b]
        assert len(changed) == 1, m.id
        assert gates.g1_typecheck(mutate.write_mutant(m, TMP / "mutants")).ok, m.id


def test_assertions_are_never_mutated():
    for p in (ROOT / "catala/modules").glob("*.catala_en"):
        for m in mutate.enumerate_mutants(p):
            assert not m.before.strip().startswith("assertion"), m.id


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok    {t.__name__}")
        except Exception as e:                          # noqa: BLE001
            failed += 1
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
