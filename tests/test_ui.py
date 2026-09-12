#!/usr/bin/env python3
"""Tests for the web interface's layout.

    . scripts/env.sh && $PY tests/test_ui.py

Plain asserts and a `__main__` that runs them all, matching tests/test_exposure.py.

The stylesheet check needs nothing. The scenario check drives the real
interface in a headless Chromium through every job state (see
scripts/ui_check.py for why) and is skipped, saying so, when no browser is
installed. It never touches the local model or a live server: jobs are
scripted, and read-only replies come from tests/ui/api_snapshot.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ui_check  # noqa: E402


def test_detector_catches_the_bug_that_shipped():
    # The status dot was `.dot.run`; the past-drafts list was `.run`.
    css = ".dot { width: 6px } .dot.run { background: gold } .run { display: grid; width: 100% }"
    found = ui_check.css_collisions(css)
    assert len(found) == 1 and ".run" in found[0], found


def test_detector_ignores_descendants_and_prefixed_states():
    css = (".s-running > .step-gutter .step-mark { color: red } .mini-step.s-running .step-mark { color: red } "
           ".legend i.p-died { background: green } .atk.p-died { background: green } "
           ".btn-primary:hover:not(:disabled) { color: white } .nav-item[aria-current=\"page\"] { color: white }")
    assert ui_check.css_collisions(css) == []


def test_stylesheet_has_no_state_class_collisions():
    found = ui_check.css_collisions((ui_check.WEB / "app.css").read_text(encoding="utf-8"))
    assert not found, "\n".join(found)


def test_every_scenario_holds_its_layout():
    chrome = ui_check.find_chrome()
    if not chrome:
        print("    skipped: no headless Chromium (set UI_CHROME)")
        return
    results = ui_check.run_all(chrome, quiet=True)
    bad = [f"{name} @ {width}px: " + "; ".join(problems[:4]) for name, width, problems in results if problems]
    assert not bad, f"{len(bad)} of {len(results)} runs broke:\n" + "\n".join(bad)


if __name__ == "__main__":
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}\n{e}")
    sys.exit(1 if failed else 0)
