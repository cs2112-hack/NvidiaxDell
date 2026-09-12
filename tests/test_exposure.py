#!/usr/bin/env python3
"""Tests for the adversarial legal exposure engine.

    . scripts/env.sh && $PY tests/test_exposure.py

Plain asserts and a `__main__` that runs them all, matching tests/test_extract.py.
There is no pytest in this checkout.

The engine's whole claim is that no unverified agent output reaches a human, so
the load-bearing tests here are the ones that try to get something through the
adjudicator that should not get through:

  * `test_attack_outside_the_domain_dies` -- facts describing nobody
  * `test_narrative_does_not_persuade_the_adjudicator` -- a confident story
    attached to facts that compute correctly
  * `test_predicate_booleans_are_type_safe` -- the truthiness bug that let a
    boolean agree with a string, in the one place it would manufacture findings
  * `test_counterfactual_restores_the_module` -- the engine edits real files

If one of those ever passes something, the queue is no longer worth working and
the rest of the numbers in this system stop meaning anything.
"""
from __future__ import annotations

import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lks import exposure, remedy, watchers
from lks import surface as sf
from lks.registry import load_registry

OT = "Overtime.HourPremium"
SC = "ServiceCredits.ServiceCredit"


def ot_facts(**over):
    f = dict(ordinal=45, grade=3, is_public_holiday=False, is_critical_incident=False,
             is_night=False, has_standing_shift_allowance=False)
    f.update(over)
    return f


# --- the declared content ---------------------------------------------------

def test_domains_and_predicates_are_clean():
    reg = load_registry()
    problems = sf.validate_domains(sf.load_domains(), reg)
    problems += exposure.validate_predicates(exposure.load_predicates(), reg)
    assert not problems, "\n".join(problems)


def test_every_narrowing_carries_a_note():
    """A narrowing suppresses findings, so an unexplained one is a silent
    reduction in what the engine can see."""
    for key, sd in sf.load_domains().items():
        for name, d in sd.inputs.items():
            if d.type in ("boolean", "enum", "struct") or d.realisable is None:
                continue
            assert d.note.strip(), f"{key}.{name} narrows the domain with no note"
        for c in sd.coherence:
            assert c.note.strip(), f"{key}: coherence {c.holds!r} has no note"


def test_every_predicate_cites():
    for p in exposure.load_predicates():
        assert p.citations, f"{p.id} asserts an outcome is bad in law and cites nothing"
        assert p.theory.strip(), f"{p.id} states no theory"


# --- what the adjudicator refuses to let through ---------------------------

def test_attack_outside_the_domain_dies():
    a = exposure.Attack("x", OT, ot_facts(ordinal=500))
    v = exposure.adjudicate(a)
    assert not v.landed
    assert "describe nobody" in v.why, v.why
    assert "168" in v.why, "the reason must quote the declared domain, not just refuse"


def test_attack_off_schema_dies():
    v = exposure.adjudicate(exposure.Attack("x", OT, {"ordinal": 45}))
    assert not v.landed and "not the inputs the scope takes" in v.why
    v = exposure.adjudicate(exposure.Attack("x", OT, ot_facts(invented=1)))
    assert not v.landed and "unexpected ['invented']" in v.why


def test_attack_on_an_unmapped_scope_dies():
    """No declared population means the engine cannot tell a real case from an
    impossible one, so it declines rather than guessing."""
    v = exposure.adjudicate(exposure.Attack("x", "WorkingTimeDefs.NightHours",
                                            {"hour_of_day": 3}))
    assert not v.landed and "realisable domain" in v.why


def test_narrative_does_not_persuade_the_adjudicator():
    """The one that matters. A fact pattern that computes correctly dies, no
    matter how certain the proposing role was about it."""
    a = exposure.Attack(
        "plaintiff's employment lawyer", OT, ot_facts(),
        narrative="This is a flagrant underpayment and the tribunal will award "
                  "the full 1.5 multiplier plus aggravated damages.",
        citations=["EMP-ANNEX-C C-4.2", "EMP-ANNEX-C C-8.1"],
    )
    v = exposure.adjudicate(a)
    assert not v.landed, "a narrative and citations must not carry a finding"
    assert v.why == exposure.DIED_NO_BAD_OUTCOME
    assert exposure.record(a, v, store=tempfile.mkdtemp()) is None


def test_predicate_booleans_are_type_safe():
    """`accrues_toil = true` must not be satisfied by the string '1' or by 1.

    This is the truthiness failure `catala_runner._as_bool` was written for,
    landing in the one place where it would manufacture findings rather than
    suppress them."""
    atom = exposure.Atom("accrues_toil = true").compile()
    assert atom.holds({"accrues_toil": True})
    assert not atom.holds({"accrues_toil": False})
    assert not atom.holds({"accrues_toil": "1"})
    assert not atom.holds({"accrues_toil": 1})
    assert not atom.holds({}), "a missing value must not satisfy a condition"
    num = exposure.Atom("multiplier = 0.0").compile()
    assert num.holds({"multiplier": "0.0"}), "Catala emits decimals as strings"
    assert not num.holds({"multiplier": False}), "false must not equal 0.0"


def test_a_predicate_with_no_conditions_is_rejected():
    p = exposure.Predicate(id="X", scope=OT, title="t", archetype="a", theory="t",
                           citations=["EMP-ANNEX-C C-4.1"])
    assert not p.fires({"anything": 1}), "an empty conjunction must not fire"
    assert any("fire on everything" in x for x in exposure.validate_predicates([p]))


# --- what it does let through ----------------------------------------------

def test_known_adverse_reading_lands_and_is_costed():
    a = exposure.Attack("plaintiff's employment lawyer", OT, ot_facts(grade=5))
    v = exposure.adjudicate(a)
    assert v.landed and v.klass == exposure.Klass.ADVERSE
    assert v.predicate == "EXP-P001"
    assert v.amount == Decimal("1.25"), v.amount
    assert "contended to be 1.25" in v.amount_basis
    refs = {r for g in v.governing for r in g["clause_refs"]}
    assert "EMP-ANNEX-C C-5.1" in refs, refs


def test_contended_value_may_reference_another_output():
    a = exposure.Attack("customer's counsel", SC, dict(
        availability_percentage=94.0, monthly_service_charge=20000.0,
        arrears_days=45, invoice_undisputed=True))
    v = exposure.adjudicate(a)
    assert v.landed and v.predicate == "EXP-P004"
    assert v.amount == Decimal("0.5"), v.amount
    assert "credit_cap_percentage" in v.amount_basis


def test_a_refusal_on_realisable_facts_lands():
    """The corpus refusing to answer about someone who exists is a finding the
    compiler reaches on its own."""
    a = exposure.Attack("auditor", "Commission.CommissionPayable", dict(
        net_booked_revenue=500000.0, new_logo_share=0.2, quota=0.0,
        commencement_date="2024-01-01", annual_base_salary=100000.0))
    v = exposure.adjudicate(a)
    assert v.landed and v.klass == exposure.Klass.REFUSAL
    assert "assertion" in v.diagnostic.lower()


def test_traced_outputs_are_the_scopes_own_not_a_callees():
    """The adjudicator, the map and the watcher read these values. A scope that
    calls another used to come back carrying the callee's variables."""
    from lks.catala_runner import run_scope, run_scope_traced, values_agree
    path = "catala/modules/overtime.catala_en"
    facts = {"hours": [{"hour_of_week": 45, "is_public_holiday": True,
                        "is_critical_incident": False, "is_night": True}],
             "base_hourly_rate": 100.0, "grade": 3, "has_standing_shift_allowance": False}
    traced, _ = run_scope_traced(path, "WeeklyOvertime", facts)
    for callee_only in ("multiplier", "night_premium", "total_rate", "input", "output"):
        assert callee_only not in traced, f"HourPremium's {callee_only} leaked into WeeklyOvertime"
    for name, value in run_scope(path, "WeeklyOvertime", facts).items():
        assert values_agree(value, traced[name]), (name, value, traced[name])


def test_a_structural_finding_keeps_only_citations_that_name_real_clauses():
    got = exposure.checked_citations(
        ["COMM-PLAN S-99.9", "S-2.1", "made up", "COMM-PLAN S-2.1"],
        "Commission.CommissionPayable", [])
    assert got == ["COMM-PLAN S-2.1"], got


def test_two_recorders_never_share_an_exposure_id():
    store = Path(tempfile.mkdtemp())

    def finding(headline):
        return exposure.Exposure(id="", klass=exposure.Klass.SILENCE, scope=OT,
                                 archetype="auditor", headline=headline, facts={})

    first = exposure.create_exposure(finding("first"), store)
    ids = iter(["EXP-0001", "EXP-0002"])     # the second recorder scanned before the first wrote
    real = exposure._next_id
    exposure._next_id = lambda _store: next(ids)
    try:
        second = exposure.create_exposure(finding("second"), store)
    finally:
        exposure._next_id = real
    assert (first.id, second.id) == ("EXP-0001", "EXP-0002")
    assert [e.headline for e in exposure.load_queue(store)] == ["first", "second"]


def test_the_queue_deduplicates_by_hole_not_by_facts():
    store = Path(tempfile.mkdtemp())
    first = None
    for ordinal in (45, 46, 47):
        a = exposure.Attack("p", OT, ot_facts(grade=5, ordinal=ordinal))
        v = exposure.adjudicate(a)
        assert v.landed
        got = exposure.record(a, v, store=store)
        if first is None:
            first, e = got, got
            assert got is not None
        else:
            assert got is None, "the same legal hole must not be queued twice"
    assert len(exposure.load_queue(store)) == 1


# --- the map ---------------------------------------------------------------

def test_borders_come_from_the_compiler():
    """Every probe value is a clause's own threshold, its neighbour, or an end
    of the declared domain -- never a number this module chose."""
    reg = load_registry()
    bs = sf.borders(OT, reg[OT])
    texts = {b.text for b in bs}
    assert "ordinal > 40" in texts and "ordinal > 48" in texts and "grade >= 5" in texts
    for b in bs:
        assert b.clause_refs or b.label, f"{b.text} is attributable to nothing"
    sd = sf.load_domains()[OT]
    values, reasons = sf.probe_values(sd, bs, reg[OT])
    assert set(values["ordinal"]) >= {39, 40, 41, 47, 48, 49}
    assert all(sd.inputs["ordinal"].contains(v) for v in values["ordinal"])
    assert any("EMP-ANNEX-C C-4.1" in r for r in reasons["ordinal"])


def test_regions_are_grouped_by_the_provisions_that_governed():
    surf = sf.partition(OT)
    assert surf.regions and surf.cells_run > 100
    for r in surf.regions:
        sigs = {c.signature for c in r.cells}
        assert len(sigs) == 1, f"{r.id} mixes {len(sigs)} decision signatures"
    assert len({r.signature for r in surf.regions}) == len(surf.regions)


def test_coherence_removes_cases_nobody_is_in():
    """Without it the grid manufactures claims submitted before the expense
    existed, the scope correctly refuses them, and the map reports the refusals
    as findings of its own making."""
    surf = sf.partition("ClaimWindow.ClaimAdmissible")
    assert surf.incoherent > 0, "the constraint should be excluding something"
    for r in surf.regions:
        if r.outcome != sf.OUTCOME_REFUSED:
            continue
        assert "submission_date >= expense_incurred_date" not in r.cells[0].diagnostic


def test_locate_finds_the_region_a_real_decision_falls_in():
    surf = sf.partition(OT)
    r = sf.locate(surf, ot_facts(grade=5, ordinal=45))
    assert r is not None and r.outcome == sf.OUTCOME_COMPUTED
    assert "EMP-ANNEX-C C-5.1" in r.clause_refs
    assert sf.locate(surf, {"nothing": "relevant"}) is None


def test_undetermined_amount_is_computed_from_neighbours():
    surf = sf.partition("Commission.CommissionPayable")
    sd = sf.load_domains()["Commission.CommissionPayable"]
    refused = surf.by_outcome(sf.OUTCOME_REFUSED)
    assert refused, "the quota=0 refusal should be on the map"
    amount, basis = exposure.undetermined_amount(surf, refused[0], sd)
    assert amount is not None and "ranges" in basis and "undetermined" in basis


# --- the watchers ----------------------------------------------------------

def test_operations_finds_the_seeded_divergences_and_nothing_else():
    divs = watchers.check_operations()
    by_id = {}
    for d in divs:
        by_id.setdefault(d.decision.id, []).append(d.field)
    assert set(by_id) == {"OPS-0003", "OPS-0004", "OPS-0007", "OPS-0009",
                          "OPS-0012", "OPS-0014"}, sorted(by_id)
    assert "total_rate" in by_id["OPS-0003"]
    ot3 = next(d for d in divs if d.decision.id == "OPS-0003" and d.field == "total_rate")
    assert "EMP-ANNEX-C C-8.1" in ot3.clause_refs


def test_divergence_comparison_is_money_aware():
    """Exports write money as quoted strings; the policy emits numbers. A
    comparison that fell through to string equality would report every single
    decision as a divergence and the watcher would be unusable."""
    d = watchers.Decision(
        id="T-1", scope="GroundTransport.TaxiFare", subject="s", decided_on="2026-01-01",
        facts=dict(journey_cost=85.0, is_airport_journey=False,
                   journey_start_hour=19, journey_start_minute=30),
        actual={"reimbursable_amount": "60.00"},
    )
    assert not watchers.check_operations([d]), "60.00 and 60.0 are the same money"


def test_divergence_direction_uses_the_declared_polarity():
    """A larger recoverable clawback favours the employer, and a larger
    overtime rate favours the employee. Reading the comparison alone reports the
    company recovering a time-barred sum as generosity, which is backwards."""
    divs = {(d.decision.id, d.field): d for d in watchers.check_operations()}
    claw = divs[("OPS-0014", "recoverable_amount")]
    assert claw.direction == "more" and claw.favours == "company"
    assert claw.generosity == "less", "recovering more than permitted is a claim against us"
    ot = divs[("OPS-0004", "total_rate")]
    assert ot.direction == "more" and ot.generosity == "more"

    undeclared = watchers.Divergence(claw.decision, "x", 5, 3)
    assert undeclared.generosity == "", "no declared polarity means no claim about who gained"


def test_population_lands_real_decisions_on_the_map():
    surf = watchers.populate(sf.partition(OT))
    assert sum(r.population for r in surf.regions) >= 4
    peopled = [r for r in surf.regions if r.population]
    assert peopled and all(r.outcome == sf.OUTCOME_COMPUTED for r in peopled)


def test_a_holding_requiring_the_current_outcome_is_degenerate():
    """The commonest way a formalised ruling is wrong: the ratio is read right
    and the direction of the contention is copied from the wrong side. Caught
    by execution, because it cannot be caught by reading."""
    h = watchers.ProposedHolding(
        id="T-HOLD", scope=OT, title="t", holding="h",
        when=["grade >= 5", "ordinal > 40", "multiplier = 0.0"],
        citations=["Some v Other [2026] EAT 1"],
        contends_output="multiplier", contends_value=0.0,
    )
    imp = watchers.holding_impact(h)
    assert imp.cells_caught > 0 and imp.cells_hit == 0
    assert imp.degenerate, "it requires exactly the outcome those cases already have"
    assert "forbids nothing" in imp.summary()

    h.contends_value = 1.25
    imp2 = watchers.holding_impact(h)
    assert imp2.cells_hit > 0 and not imp2.degenerate and not imp2.inverted
    assert "indefensible" in imp2.summary()


def test_a_holding_contending_against_the_claimant_is_inverted():
    """HOLD-0001 as first drafted: the ruling gives grade 5+ the overtime rate,
    and the constraint contended for the employer's 0.0. Most caught cases
    DIFFER from 0.0, so an equality test called them indefensible; every one of
    them moves against the claimant, which is what exposes the direction."""
    h = watchers.ProposedHolding(
        id="T-INV", scope=OT, title="t", holding="h",
        when=["grade >= 5", "ordinal > 40"],
        citations=["Some v Other [2026] EAT 1"],
        contends_output="multiplier", contends_value=0.0,
    )
    imp = watchers.holding_impact(h)
    assert imp.contends_against > 0 and imp.cells_hit == 0
    assert imp.inverted and not imp.degenerate
    assert "against the claimant" in imp.summary()
    assert "indefensible" not in imp.summary()


def test_the_proposed_holdings_bite_in_the_claimants_favour():
    hs = watchers.load_proposed_holdings()
    assert hs
    for h in hs:
        imp = watchers.holding_impact(h)
        assert imp.cells_hit > 0, imp.summary()
        assert not imp.inverted and not imp.contends_against, imp.summary()


def test_the_map_cache_is_invalidated_by_a_dependency():
    """MealPerDiem `> Using ExpenseDefs`. Keyed on its own bytes alone, an edit
    to ExpenseDefs served the old map -- and the counterfactual reads through
    that cache, so it reported that the edit moved nothing."""
    with tempfile.TemporaryDirectory() as td:
        mods = Path(td)
        for f in (ROOT / "catala/modules").glob("*.catala_en"):
            (mods / f.name).write_bytes(f.read_bytes())
        meal, defs, other = (mods / "mealperdiem.catala_en",
                             mods / "expensedefs.catala_en", mods / "retention.catala_en")
        before_meal, before_other = sf.source_hash(meal), sf.source_hash(other)
        defs.write_text(defs.read_text() + "\n")
        assert sf.source_hash(meal) != before_meal, "a dependency's edit must change the key"
        assert sf.source_hash(other) == before_other, "an unrelated module's edit must not"


def test_declared_rivalries_agree():
    cs = exposure.check_rivalries()
    assert not cs, "\n".join(c.headline for c in cs)


# --- the remedy ------------------------------------------------------------

def test_splice_refuses_anything_but_a_unique_match():
    src = "alpha\nbeta\nalpha\n"
    assert remedy.splice(src, "beta", "gamma") == "alpha\ngamma\nalpha\n"
    for bad, why in (("alpha", "occurs 2 times"), ("delta", "does not occur")):
        try:
            remedy.splice(src, bad, "x")
            raise AssertionError(f"expected a refusal for {bad!r}")
        except ValueError as e:
            assert why in str(e), str(e)


def test_an_edit_interrupted_by_a_crash_is_put_back():
    from lks import catala_runner as cr
    d = Path(tempfile.mkdtemp())
    target = d / "module.catala_en"
    target.write_text("the adopted policy\n")
    real = cr.EDIT_JOURNAL
    cr.EDIT_JOURNAL = d / "journal"
    try:
        cr.begin_edit(target, target.read_bytes())
        target.write_text("a what-if nobody adopted\n")      # and the editor dies here
        assert cr.restore_interrupted_edits(rebuild=False) == [str(target.resolve())]
        assert target.read_text() == "the adopted policy\n"
        assert not list(cr.EDIT_JOURNAL.glob("*")), "the journal outlived the restore"
    finally:
        cr.EDIT_JOURNAL = real


def test_another_process_waits_for_an_edit_to_be_put_back():
    import subprocess
    import time
    from lks import catala_runner as cr
    ready = Path(tempfile.mkdtemp()) / "ready"
    holder = subprocess.Popen([sys.executable, "-c",
        f"import sys, time\nsys.path.insert(0, {str(ROOT / 'src')!r})\n"
        f"from pathlib import Path\nfrom lks.catala_runner import tree_lock\n"
        f"with tree_lock(exclusive=True):\n"
        f"    Path({str(ready)!r}).write_text('1')\n    time.sleep(3)\n"])
    try:
        while not ready.exists():
            assert holder.poll() is None, "the editing process died before its edit"
            time.sleep(0.05)
        t = time.monotonic()
        with cr.tree_lock(exclusive=False):
            waited = time.monotonic() - t
        assert waited >= 2.0, f"read the tree {waited:.1f}s into another process's edit"
    finally:
        holder.wait(timeout=30)


def test_counterfactual_restores_the_module():
    """The engine edits the real tree, because Catala resolves dependencies
    through objects keyed by source path. Leaving an edit behind would be the
    worst possible outcome of asking a question."""
    path = ROOT / "catala/modules/overtime.catala_en"
    before = path.read_text()
    new = remedy.splice(before, "consequence equals 1.25", "consequence equals 1.30")
    cf = remedy.counterfactual(path, new, scopes=[OT])
    assert path.read_text() == before, "the module was not put back"
    assert cf.typecheck_ok, cf.typecheck_diagnostic
    assert cf.regressions_broken, "changing C-4.1's rate must break recorded counterexamples"
    assert not cf.ok, "an edit that breaks the regression suite is not ok"
    assert cf.diffs and cf.diffs[0].touched > 0
    worst = cf.diffs[0].worst
    assert worst is not None and worst.delta == Decimal("0.05")


def test_counterfactual_restores_the_module_even_when_the_edit_is_broken():
    path = ROOT / "catala/modules/overtime.catala_en"
    before = path.read_text()
    cf = remedy.counterfactual(path, before + "\n```catala\nthis is not catala\n```\n",
                               scopes=[OT])
    assert path.read_text() == before, "the module was not put back after a bad edit"
    assert not cf.ok


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
