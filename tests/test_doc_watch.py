#!/usr/bin/env python3
"""Tests for the contradiction watch.

    . scripts/env.sh && $PY tests/test_doc_watch.py

Plain asserts and a `__main__`, matching the other test files. No model is
called: what has to be right is what happens to a reply, and when a document
counts as having arrived. The one test that needs MongoDB uses a throwaway
database and is skipped when MongoDB is not running.

The load-bearing ones:

  * `test_resync_wakes_nothing` -- start.sh re-syncs every chunk on every start
  * `test_hallucinated_quote_is_discarded` -- a contradiction the text does not contain
  * `test_failed_attacker_makes_the_check_incomplete` -- a search that did not
    happen must not read as a search that found nothing
"""
from __future__ import annotations

import hashlib
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lks import doc_watch as dw


def chunk(doc_id, cid, text, title=None):
    return {"doc_id": doc_id, "clause_id": cid, "section_id": cid.rsplit(".", 1)[0],
            "section_title": "", "doc_title": title or doc_id, "text": text}


BASE_ROWS = [
    chunk("EXP-POL", "E-3.1", "Claims must be submitted within 60 days of the expense being incurred."),
    chunk("EXP-POL", "E-3.2", "Receipts are required for any single item above GBP 25."),
    chunk("NDA", "N-2.1", "Confidential Information must be kept secret for five years."),
]


def new_doc(*clauses):
    return dw.BaseDoc(key="intake:abc", doc_id="TRAVEL-2026", title="Travel Policy 2026",
                      source="intake",
                      clauses=[dw.BaseClause("TRAVEL-2026", cid, cid.rsplit(".", 1)[0], "",
                                             "Travel Policy 2026", text) for cid, text in clauses])


def fake_embed(clauses):
    """Bag of hashed words: deterministic, and similar texts score similarly."""
    for c in clauses:
        v = np.zeros(64, dtype=np.float32)
        for w in c.text.lower().split():
            v[int(hashlib.md5(w.strip(".,").encode()).hexdigest(), 16) % 64] += 1
        c.embedding = list(v / (np.linalg.norm(v) or 1))


def fake_call(replies):
    """Replies in call order; a string is a failed run with that error."""
    it = iter(replies)
    calls = []

    def call(role, prompt, **kw):
        calls.append(prompt)
        r = next(it, {"findings": [], "attacks_tried": []})
        if isinstance(r, str):
            return SimpleNamespace(ok=False, error=r, value=None)
        return SimpleNamespace(ok=True, error="", value=r)
    call.calls = calls
    return call


GOOD = {"clause_id": "T-1.1", "corpus_ref": "EXP-POL E-3.1",
        "quote": "within 30 days", "corpus_quote": "within 60 days",
        "facts": "An employee submits a travel claim on day 45.",
        "summary": "Travel says 30 days, the expense policy says 60."}


def run(doc, replies, **kw):
    base = dw.docs_from_chunks(BASE_ROWS) + [doc]
    return dw.check_document(doc, base, call=fake_call(replies), embed=fake_embed,
                             emit=lambda _s: None, **kw)


# --- arrival ---------------------------------------------------------------

def test_first_scan_is_baseline():
    ups = dw.plan_scan(dw.docs_from_chunks(BASE_ROWS), {}, first_scan=True)
    assert {u["status"] for u in ups.values()} == {dw.BASELINE}
    assert set(ups) == {"index:EXP-POL", "index:NDA"}


def test_resync_wakes_nothing():
    docs = dw.docs_from_chunks(BASE_ROWS)
    state = {k: {"_id": k, **u, "status": dw.CHECKED}
             for k, u in dw.plan_scan(docs, {}, first_scan=True).items()}
    # a re-sync re-inserts the same rows, in another order, with embeddings attached
    resynced = dw.docs_from_chunks([{**r, "embedding": [0.1]} for r in reversed(BASE_ROWS)])
    assert dw.plan_scan(resynced, state, first_scan=False) == {}


def test_new_and_changed_documents_are_pending():
    docs = dw.docs_from_chunks(BASE_ROWS)
    state = {k: {"_id": k, **u} for k, u in dw.plan_scan(docs, {}, first_scan=True).items()}
    rows = [dict(r) for r in BASE_ROWS] + [chunk("LEAVE", "L-1.1", "25 days of annual leave.")]
    rows[0]["text"] = "Claims must be submitted within 90 days."
    ups = dw.plan_scan(dw.docs_from_chunks(rows), state, first_scan=False)
    assert ups["index:LEAVE"]["status"] == dw.PENDING
    assert ups["index:EXP-POL"]["status"] == dw.PENDING
    assert "index:NDA" not in ups


def test_removed_document_is_marked():
    docs = dw.docs_from_chunks(BASE_ROWS)
    state = {k: {"_id": k, **u} for k, u in dw.plan_scan(docs, {}, first_scan=True).items()}
    ups = dw.plan_scan(dw.docs_from_chunks(BASE_ROWS[:2]), state, first_scan=False)
    assert set(ups) == {"index:NDA"} and ups["index:NDA"]["status"] == dw.REMOVED


def test_document_back_unchanged_is_not_new():
    """A scan inside a re-sync sees documents vanish; coming back unchanged,
    they must not be attacked as arrivals."""
    docs = dw.docs_from_chunks(BASE_ROWS)
    state = {k: {"_id": k, **u} for k, u in dw.plan_scan(docs, {}, first_scan=True).items()}
    state["index:NDA"]["status"] = dw.CHECKED
    for k, u in dw.plan_scan([], state, first_scan=False).items():
        state[k].update(u)
    back = dw.plan_scan(docs, state, first_scan=False)
    assert {k: u["status"] for k, u in back.items()} == {
        "index:EXP-POL": dw.BASELINE, "index:NDA": dw.CHECKED}


def test_rule_clause_from_source_is_part_of_the_document():
    """The index leaves RULE clauses out. They are read from the file, and a
    change to one alone is a change to the document."""
    import tempfile
    head = ("---\ndoc_id: EXP-POL\ntitle: Expenses\nversion: '1'\n"
            "effective_date: 2026-01-01\njurisdiction: England and Wales\nowner: Finance\n---\n\n"
            "## E-3 Claims\n\n**E-3.1** Claims must be submitted within 60 days of the expense "
            "being incurred.\n\n")
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "exp.md"
        src.write_text(head + "**E-3.2** Receipts are required for any single item above GBP 25.\n")
        rows = [{**BASE_ROWS[0], "source_path": str(src)}]      # E-3.2, a RULE, is not indexed
        doc = dw.complete_from_source(dw.docs_from_chunks(rows)[0])
        assert [c.clause_id for c in doc.clauses] == ["E-3.1", "E-3.2"]
        src.write_text(head + "**E-3.2** Receipts are required for any single item above GBP 250.\n")
        assert dw.complete_from_source(dw.docs_from_chunks(rows)[0]).fingerprint != doc.fingerprint


def test_intake_in_house_convention():
    text = ("---\ndoc_id: TRAVEL-2026\ntitle: Travel Policy\nversion: '1'\n"
            "effective_date: 2026-01-01\njurisdiction: England and Wales\nowner: Finance\n---\n\n"
            "## T-1 Claims\n\n**T-1.1** Travel claims must be submitted within 30 days.\n\n"
            "**T-1.2** Economy class is required for flights under six hours.\n")
    d = dw.doc_from_intake({"_id": "665f", "text": text})
    assert not d.error, d.error
    assert d.doc_id == "TRAVEL-2026" and [c.clause_id for c in d.clauses] == ["T-1.1", "T-1.2"]


def test_intake_plain_text_is_converted():
    text = ("Travel Policy\n\n1. Claims\n\n1.1 Travel claims must be submitted within 30 days "
            "of return.\n\n1.2 Receipts are required for every item.\n\n2. Flights\n\n"
            "2.1 Economy class is required for flights under six hours.\n")
    d = dw.doc_from_intake({"_id": "6660", "text": text, "doc_id": "TRAVEL-PLAIN"})
    assert not d.error, d.error
    assert d.doc_id == "TRAVEL-PLAIN" and len(d.clauses) >= 3


def test_empty_intake_is_unreadable_not_retried():
    d = dw.doc_from_intake({"_id": "6661", "text": "  "})
    ups = dw.plan_scan([d], {}, first_scan=False)
    assert ups[d.key]["status"] == dw.UNREADABLE
    assert dw.plan_scan([d], {d.key: {**ups[d.key]}}, first_scan=False) == {}


# --- the check ---------------------------------------------------------------

def test_real_contradiction_survives():
    doc = new_doc(("T-1.1", "Travel claims must be submitted within 30 days of return."))
    r = run(doc, [{"findings": [GOOD], "attacks_tried": []}], attackers=1)
    assert r.complete and len(r.findings) == 1 and not r.discarded
    assert r.findings[0].status == dw.SINGLE
    assert r.n_docs_compared == 2


def test_nothing_to_compare_is_not_a_clean_result():
    doc = new_doc(("T-1.1", "Travel claims must be submitted within 30 days of return."))
    r = dw.check_document(doc, [doc], call=fake_call([]), embed=fake_embed, emit=lambda _s: None)
    assert not r.complete and r.verdict().startswith("INCOMPLETE: the base holds no other")


def test_hallucinated_quote_is_discarded():
    doc = new_doc(("T-1.1", "Travel claims must be submitted within 30 days of return."))
    bad_new = {**GOOD, "quote": "within 14 days"}
    bad_old = {**GOOD, "corpus_quote": "within 10 days"}
    r = run(doc, [{"findings": [bad_new, bad_old]}], attackers=1)
    assert not r.findings
    assert [f.discard_reason for f in r.discarded] == [
        "quote does not appear in the new clause", "quote does not appear in the existing clause"]


def test_citing_the_document_against_itself_is_discarded():
    doc = new_doc(("T-1.1", "Claims within 30 days."), ("T-1.2", "Claims within 45 days."))
    own = {**GOOD, "corpus_ref": "TRAVEL-2026 T-1.2", "quote": "within 30 days",
           "corpus_quote": "within 45 days"}
    r = run(doc, [{"findings": [own]}], attackers=1)
    assert not r.findings and "not a clause of another document" in r.discarded[0].discard_reason


def test_two_attackers_on_one_pair_corroborate():
    doc = new_doc(("T-1.1", "Travel claims must be submitted within 30 days of return."))
    r = run(doc, [{"findings": [GOOD]}, {"findings": [{**GOOD, "summary": "worded differently"}]}],
            attackers=2)
    assert len(r.findings) == 1
    assert r.findings[0].status == dw.CORROBORATED and r.findings[0].raised_by == [0, 1]


def test_failed_attacker_makes_the_check_incomplete():
    doc = new_doc(("T-1.1", "Travel claims must be submitted within 30 days of return."))
    r = run(doc, [{"findings": []}, "timeout", "timeout"], attackers=2)
    assert not r.complete and len(r.errors) == 1
    assert r.verdict().startswith("INCOMPLETE")


def test_retry_rescues_an_attacker():
    doc = new_doc(("T-1.1", "Travel claims must be submitted within 30 days of return."))
    r = run(doc, ["truncated", {"findings": [GOOD]}], attackers=1)
    assert r.complete and len(r.findings) == 1


def test_every_document_in_the_base_is_scored():
    """A clause's nearest rival is found wherever it is in the base, and the
    whole base -- not a pre-selected document -- is what gets scored."""
    doc = new_doc(("T-1.1", "Confidential Information must be kept secret for two years."))
    call = fake_call([{"findings": []}])
    base = dw.docs_from_chunks(BASE_ROWS) + [doc]
    dw.check_document(doc, base, call=call, embed=fake_embed, emit=lambda _s: None,
                      attackers=1, neighbours=1)
    assert "NDA N-2.1" in call.calls[0]


def test_batches_cover_every_clause():
    doc = new_doc(*[(f"T-1.{i}", f"Clause number {i} about claims.") for i in range(1, 13)])
    call = fake_call([])
    base = dw.docs_from_chunks(BASE_ROWS) + [doc]
    r = dw.check_document(doc, base, call=call, embed=fake_embed, emit=lambda _s: None, attackers=1)
    assert r.batches == 3 and len(call.calls) == 3
    for i in range(1, 13):
        assert any(f"## T-1.{i}\n" in p or f"## T-1.{i} " in p for p in call.calls), i


# --- against a real MongoDB ------------------------------------------------

def test_watcher_end_to_end_on_mongo():
    try:
        from lks.mongo_store import MongoUnavailable, connect
        client = connect(timeout_ms=1500)
    except Exception as e:                                      # noqa: BLE001
        return f"MongoDB not reachable ({type(e).__name__})"
    name = "lks_test_doc_watch"
    client.drop_database(name)
    db = client[name]
    real = dw.check_document
    try:
        w = dw.Watcher(db_name=name, check_model=False, launcher=dw.inline_launcher)
        w.launcher = lambda label, params, body: (body(lambda _s: None), True)[1]
        assert w.tick() is None and "baseline" in w.waiting_on  # no index yet: no baseline taken
        assert db[dw.STATE].count_documents({}) == 0

        db[dw.CHUNKS].insert_many([{"_id": f"{r['doc_id']} {r['clause_id']}", **r} for r in BASE_ROWS])
        assert w.tick() is None                                 # first scan: baseline only
        assert db[dw.STATE].count_documents({"status": dw.BASELINE}) == 2

        db["index_meta"].insert_one({"_id": "current", "n_chunks": 3})
        db[dw.CHUNKS].delete_one({"_id": "NDA N-2.1"})          # a scan inside a sync
        assert w.tick() is None and "sync in progress" in w.waiting_on
        assert db[dw.STATE].count_documents({"status": dw.REMOVED}) == 0
        db[dw.CHUNKS].insert_one({"_id": "NDA N-2.1", **BASE_ROWS[2]})
        assert w.tick() is None and not w.waiting_on

        db[dw.CHUNKS].delete_many({})                           # a re-sync
        db[dw.CHUNKS].insert_many([{"_id": f"{r['doc_id']} {r['clause_id']}", **r} for r in BASE_ROWS])
        assert w.tick() is None

        text = ("---\ndoc_id: TRAVEL-2026\ntitle: Travel Policy 2026\nversion: '1'\n"
                "effective_date: 2026-01-01\njurisdiction: England and Wales\nowner: Finance\n---\n\n"
                "## T-1 Claims\n\n**T-1.1** Travel claims must be submitted within 30 days of return.\n")
        oid = db[dw.INTAKE].insert_one({"text": text}).inserted_id
        dw.check_document = lambda target, base, **kw: real(
            target, base, call=fake_call([{"findings": [GOOD]}]), embed=fake_embed, attackers=1,
            emit=kw.get("emit", print), heartbeat=kw.get("heartbeat", lambda: None))

        key = w.tick()
        assert key == f"intake:{oid}", key
        row = db[dw.STATE].find_one({"_id": key})
        assert row["status"] == dw.CHECKED, row
        check = db[dw.CHECKS].find_one({"_id": row["check_id"]})
        assert check["findings"][0]["corpus_ref"] == "EXP-POL E-3.1"
        assert w.tick() is None                                 # checked stays checked

        assert w.requeue(key) and w.tick() == key               # an explicit re-check runs again
    finally:
        dw.check_document = real
        client.drop_database(name)
        client.close()


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    failed: list[tuple[str, str]] = []
    skipped = 0
    for fn in TESTS:
        try:
            note = fn()
            if note:
                skipped += 1
                print(f"skip {fn.__name__}  ({note})")
            else:
                print(f"ok   {fn.__name__}")
        except Exception:
            failed.append((fn.__name__, traceback.format_exc()))
            print(f"FAIL {fn.__name__}")
    print()
    for name, tb in failed:
        print(f"--- {name} ---\n{tb}")
    print(f"{len(TESTS) - len(failed) - skipped} passed, {skipped} skipped, "
          f"{len(failed)} failed, of {len(TESTS)} tests")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
