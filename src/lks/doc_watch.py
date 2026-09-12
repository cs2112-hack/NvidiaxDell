"""The contradiction watch: every document that arrives in MongoDB is attacked
against the whole document base, around the clock.

## What counts as a document arriving

Two collections, because documents reach MongoDB two ways:

    lks.chunks      the materialised index (`scripts/sync_mongo.py`). A document
                    is its `doc_id`; it arrives when a merge is re-indexed.
    lks.documents   the intake. Anything may insert a raw document here --
                    `{"text": "...", "doc_id": "...", "title": "..."}`, only
                    `text` required -- and it is segmented into clauses by the
                    same converter `lks convert` uses.

Nothing may be inserted into `lks.chunks` by hand: `MongoVectorStore.verify`
counts its rows against the index manifest, and a stray row refuses every chat
answer. The intake is the door for everything that is not a merged corpus file.

## Why arrival is a fingerprint and not an insert event

`sync_mongo.py` deletes and re-inserts every chunk, and `start.sh` runs it on
every start. An insert-driven watcher would re-attack the entire corpus each
morning. Instead each document is reduced to a hash of its clause ids and
texts, and a document is new when its key is unseen and changed when its hash
moves. A re-sync of the same corpus changes nothing and wakes nothing, and the
base is not read at all while a sync is half done (`index_unsettled`).

The first scan against an empty state records what is already there as the
baseline and attacks none of it: the watch is for what comes next. A baseline
document can still be put through the check with `lks watch check <key>`.

## The check

The existing screen's consistency reviewer (`lks.screen`) attacks a draft
against the handful of corpus clauses a retriever picked for it. That is the
shape reused here, with the retrieval made exhaustive: every clause of the new
document is scored against every clause of every other document in the base,
and each clause is put to the attackers alongside its nearest rivals from
anywhere in it. No document is skipped because it looked unrelated as a whole.

Rule clauses are not in `lks.chunks` (they are answered by Catala, not
quoted), yet a rate or a deadline is precisely what a new document contradicts.
So for an indexed document whose source file is on disk, its full clause list
is read from the file -- for its fingerprint as much as for the check, or a
merge that moved only a rate would never count as a change.

Then, the discipline every agent in this repo is held to:

* **Blind attackers.** Two (`LKS_WATCH_ATTACKERS`) independent runs per batch,
  different seeds, neither seeing the other.
* **Mechanical validation before belief.** A finding must name a clause of the
  new document and a clause of a *different* document, and both quotes must be
  found verbatim in the clauses they cite. A hallucinated contradiction fails
  here and is recorded as discarded, with the reason.
* **Consensus on location, not wording.** A finding raised on the same pair of
  clauses by two attackers is `corroborated`; one raised by one is `single`.
  Neither is a legal conclusion. Both are for a person.
* **An incomplete search is not a clean result.** If an attacker cannot give a
  usable reply for a batch, the check is incomplete, the document goes back in
  the queue, and after `MAX_ATTEMPTS` it is recorded as incomplete rather than
  as having no contradictions.

## Around the clock

`lks serve` -- the web server `start.sh` brings up -- starts a `Watcher`
thread. It scans every `LKS_WATCH_INTERVAL` seconds and survives MongoDB or the
model being down. The check itself runs as an ordinary server job, so it takes
its turn on the local model with the fleet and the drafter and shows in the
interface as the running job. `lks watch run` is the same loop without the web
server. Documents are claimed atomically, so both may run at once.
"""
from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import llm
from .agents import Role, run_role
from .model import content_hash
from .screen import _norm

REPO = Path(__file__).resolve().parents[2]

CHUNKS = "chunks"
INTAKE = "documents"
STATE = "watch_state"
CHECKS = "contradiction_checks"
META_KEY = "__meta__"

INTERVAL = int(os.environ.get("LKS_WATCH_INTERVAL", "30"))
ATTACKERS = int(os.environ.get("LKS_WATCH_ATTACKERS", "2"))
NEIGHBOURS = int(os.environ.get("LKS_WATCH_NEIGHBOURS", "4"))
"""Nearest clauses from the rest of the base each new clause is read against."""
BATCH = 5
"""New clauses per prompt. With NEIGHBOURS rivals each, a batch fits the
role's context and token budget with room to spare."""
MAX_CONTEXT = 14
MAX_ATTEMPTS = 3
STALE_CLAIM_SECONDS = 40 * 60
"""A claim whose heartbeat is older than this was abandoned by a process that
died. Longer than one model call may legitimately stay silent (fifteen minutes)."""

PENDING, CHECKING, CHECKED, INCOMPLETE = "pending", "checking", "checked", "incomplete"
BASELINE, UNREADABLE, REMOVED = "baseline", "unreadable", "removed"

CORROBORATED, SINGLE, DISCARDED = "corroborated", "single", "discarded"


# --- the document base ----------------------------------------------------

@dataclass
class BaseClause:
    doc_id: str
    clause_id: str
    section_id: str
    section_title: str
    doc_title: str
    text: str
    embedding: list[float] | None = None

    @property
    def ref(self) -> str:
        return f"{self.doc_id} {self.clause_id}"

    def embed_text(self) -> str:
        # identical to `lks.vector.Chunk.embed_text`, so an intake clause and an
        # indexed one are scored in the same space
        return f"{self.doc_title} :: {self.section_id} {self.section_title} :: {self.text}"


@dataclass
class BaseDoc:
    key: str
    """`index:<doc_id>` or `intake:<_id>`: stable across re-syncs, and distinct
    when an intake row reuses an indexed doc_id."""
    doc_id: str
    title: str
    source: str
    clauses: list[BaseClause] = field(default_factory=list)
    source_path: str = ""
    error: str = ""
    raw_hash: str = ""

    @property
    def fingerprint(self) -> str:
        if self.error:
            return self.raw_hash
        return content_hash("|".join(sorted(
            f"{c.clause_id}={content_hash(c.text)}" for c in self.clauses)))


def docs_from_chunks(rows: list[dict[str, Any]]) -> list[BaseDoc]:
    by_doc: dict[str, BaseDoc] = {}
    for r in rows:
        d = by_doc.get(r["doc_id"])
        if d is None:
            d = by_doc[r["doc_id"]] = BaseDoc(
                key=f"index:{r['doc_id']}", doc_id=r["doc_id"],
                title=r.get("doc_title", ""), source="index",
                source_path=r.get("source_path", ""))
        d.clauses.append(BaseClause(
            doc_id=r["doc_id"], clause_id=r["clause_id"],
            section_id=r.get("section_id", ""), section_title=r.get("section_title", ""),
            doc_title=r.get("doc_title", ""), text=r.get("text", ""),
            embedding=r.get("embedding")))
    return sorted(by_doc.values(), key=lambda d: d.key)


def doc_from_intake(row: dict[str, Any]) -> BaseDoc:
    """Segment a raw intake document into clauses.

    A document already in the house convention is parsed as it stands; anything
    else goes through `lks.structure.convert_file`, the converter a person runs
    with `lks convert`. A document that yields no clauses is recorded as
    unreadable, not retried every scan.
    """
    from .segment import parse_document

    oid = str(row["_id"])
    text = str(row.get("text") or row.get("markdown") or row.get("body") or "")
    doc = BaseDoc(key=f"intake:{oid}", doc_id=str(row.get("doc_id") or f"INTAKE-{oid[-8:]}"),
                  title=str(row.get("title") or ""), source="intake",
                  raw_hash=content_hash(text + "|" + str(row.get("doc_id", ""))))
    if not text.strip():
        doc.error = "the row has no `text`"
        return doc
    try:
        with tempfile.TemporaryDirectory(prefix="lks-intake-") as tmp:
            src = Path(tmp) / f"{oid}.md"
            src.write_text(text, encoding="utf-8")
            try:
                parsed = parse_document(src)
            except Exception:                                   # noqa: BLE001
                from .structure import convert_file
                overrides = {k: str(row[k]) for k in ("doc_id", "title") if row.get(k)}
                conv = convert_file(src, out_dir=Path(tmp) / "out", overrides=overrides)
                parsed = parse_document(conv.converted_path)
    except Exception as e:                                      # noqa: BLE001
        doc.error = f"{type(e).__name__}: {e}"
        return doc
    if not row.get("doc_id"):
        doc.doc_id = parsed.doc_id if parsed.doc_id != "NEEDS-HUMAN-INPUT" else doc.doc_id
    doc.title = doc.title or parsed.title
    doc.clauses = [
        BaseClause(doc_id=doc.doc_id, clause_id=c.clause_id, section_id=c.section_id,
                   section_title=c.section_title, doc_title=doc.title, text=c.body)
        for c in parsed.clauses
    ]
    return doc


_SOURCE_CACHE: dict[str, tuple[tuple[int, int], Any]] = {}
"""Parsed source files by path, with the (mtime, size) they were parsed at.
Every scan completes every indexed document; an unchanged file is not re-parsed."""


def complete_from_source(doc: BaseDoc) -> BaseDoc:
    """Add the clauses the index leaves out (RULE clauses), from the file the
    chunks came from, when that file is still on disk and still this document.

    Clauses keep the file's order; an indexed clause keeps the row the index
    holds, embedding included."""
    if doc.source != "index" or not doc.source_path:
        return doc
    from .segment import parse_document

    path = Path(doc.source_path)
    path = path if path.is_absolute() else REPO / path
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        hit = _SOURCE_CACHE.get(str(path))
        if hit is None or hit[0] != stamp:
            hit = _SOURCE_CACHE[str(path)] = (stamp, parse_document(path))
        parsed = hit[1]
    except Exception:                                           # noqa: BLE001
        return doc
    if parsed.doc_id != doc.doc_id:
        return doc
    indexed = {c.clause_id: c for c in doc.clauses}
    clauses = [indexed.pop(c.clause_id, None) or BaseClause(
                   doc_id=doc.doc_id, clause_id=c.clause_id, section_id=c.section_id,
                   section_title=c.section_title, doc_title=doc.title, text=c.body)
               for c in parsed.clauses]
    return BaseDoc(key=doc.key, doc_id=doc.doc_id, title=doc.title, source=doc.source,
                   clauses=clauses + list(indexed.values()), source_path=doc.source_path)


class BaseUnsettled(RuntimeError):
    """The index is part-way through a sync; the base cannot be read as it stands."""


def index_unsettled(db, rows: list[dict[str, Any]]) -> str:
    """Why the chunks just read are not a whole index, or "" when they are.

    `sync_mongo.py` deletes every chunk, re-inserts them, then rewrites the
    metadata. Read inside that window, every indexed document vanishes (and
    comes back as new) or half of one does (and its fingerprint moves). The
    metadata says how many chunks a finished sync leaves, so a read holding any
    other number is not believed. With no metadata there is no sync to be in.
    """
    from .mongo_store import META_COLL

    meta = db[META_COLL].find_one({"_id": "current"})
    if meta is None:
        return ""
    have, want = len({r["_id"] for r in rows}), meta.get("n_chunks")
    return "" if have == want else f"an index sync in progress ({have} of {want} chunks present)"


_INTAKE_CACHE: dict[tuple[str, str], BaseDoc] = {}
"""Parsed intake rows, by (_id, content). A scan runs every few seconds and
conversion is not free; a row that has not changed is not re-segmented."""


def read_base(db) -> list[BaseDoc]:
    rows = list(db[CHUNKS].find({}))
    wait = index_unsettled(db, rows)
    if wait:
        raise BaseUnsettled(wait)
    chunks = [complete_from_source(d) for d in docs_from_chunks(rows)]
    intake = []
    for r in db[INTAKE].find({}):
        k = (str(r["_id"]), content_hash(repr(sorted((k, str(v)) for k, v in r.items()))))
        if k not in _INTAKE_CACHE:
            _INTAKE_CACHE[k] = doc_from_intake(r)
        intake.append(_INTAKE_CACHE[k])
    return chunks + intake


# --- scanning -------------------------------------------------------------

def plan_scan(docs: list[BaseDoc], state: dict[str, dict[str, Any]],
              first_scan: bool) -> dict[str, dict[str, Any]]:
    """The state updates one scan implies. Pure, so the arrival rules are
    testable without a database.

    Unseen -> pending (baseline on the very first scan). Fingerprint moved ->
    pending again, whatever it was. Gone from the base -> removed. Back unchanged
    -> whatever it was before it went.
    """
    now = _now()
    updates: dict[str, dict[str, Any]] = {}
    seen = set()
    for d in docs:
        seen.add(d.key)
        prev = state.get(d.key)
        if prev is not None and prev.get("fingerprint") == d.fingerprint:
            if prev.get("status") == REMOVED:
                # back unchanged (a checkout and back again): it takes up where
                # it left off rather than arriving a second time
                back = prev.get("removed_from") or PENDING
                updates[d.key] = {"status": PENDING if back == CHECKING else back,
                                  "changed_at": now}
            continue
        status = UNREADABLE if d.error else (BASELINE if first_scan else PENDING)
        updates[d.key] = {
            "doc_id": d.doc_id, "title": d.title, "source": d.source,
            "fingerprint": d.fingerprint, "status": status, "attempts": 0,
            "error": d.error, "n_clauses": len(d.clauses),
            "first_seen": (prev or {}).get("first_seen", now), "changed_at": now,
            "retry_after": 0,
        }
    for key, prev in state.items():
        if key not in seen and prev.get("status") != REMOVED:
            updates[key] = {"status": REMOVED, "removed_from": prev.get("status"),
                            "changed_at": now}
    return updates


# --- the attackers --------------------------------------------------------

CROSS_CONTRADICTION = Role(
    name="watch-contradiction",
    purpose="Find where a document that has just arrived contradicts a document "
            "already in the base.",
    reads=["the new document's clauses", "the nearest clauses of every other document"],
    forbidden=["docs", "src", "tests", "triage", "catala"],
    temperature=0.4,
    num_predict=2400,
    think=False,
    system=(
        "A new document has just been added to a company's document base. You "
        "are one of several independent attackers, and you will not see what "
        "the others find. You do not approve documents; there is no 'looks "
        "fine' outcome. If you find nothing, return an empty findings list and "
        "list what you tried.\n\n"
        "Your target is CONTRADICTION BETWEEN DOCUMENTS: a clause of the new "
        "document and a clause of an existing document that, applied to the "
        "same facts, require incompatible things or give different answers -- "
        "a different rate, deadline, threshold, entitlement, owner or "
        "permission -- where neither says which one prevails. Two clauses about "
        "different subjects, or one that expressly overrides, refines or "
        "defers to the other, are NOT a contradiction.\n\n"
        "For every finding: `clause_id` is the new document's clause, "
        "`corpus_ref` is the existing clause exactly as headed "
        "('EMP-ANNEX-C C-4.1'). `quote` is copied exactly from the new clause "
        "and `corpus_quote` exactly from the existing one; a finding whose "
        "quotes are not found verbatim is discarded unread. `facts` is one "
        "concrete situation both clauses reach, and `summary` says what each "
        "requires in it. Report at most 4 findings, the most consequential "
        "first, and keep every field short.\n\n"
        "Reply with JSON only."
    ),
)


def reply_schema(clause_ids: list[str], refs: list[str]) -> dict[str, Any]:
    def text(n: int) -> dict[str, Any]:
        return {"type": "string", "maxLength": n}

    item = {
        "type": "object",
        "properties": {
            "clause_id": {"type": "string", "enum": clause_ids},
            "corpus_ref": {"type": "string", "enum": refs},
            "quote": text(240),
            "corpus_quote": text(240),
            "facts": text(300),
            "summary": text(300),
        },
        "required": ["clause_id", "corpus_ref", "quote", "corpus_quote", "facts", "summary"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "attacks_tried": {"type": "array", "maxItems": 6, "items": text(100)},
            "findings": {"type": "array", "maxItems": 4, "items": item},
        },
        "required": ["attacks_tried", "findings"],
        "additionalProperties": False,
    }


def packet(target: BaseDoc, clauses: list[BaseClause], rivals: list[BaseClause]) -> str:
    rows = [f"# The new document: {target.title or target.doc_id} ({target.doc_id})", ""]
    for c in clauses:
        rows += [f"## {c.clause_id} — {c.section_title}".rstrip(" —"), c.text.strip(), ""]
    rows += ["# Existing clauses from other documents closest to these", ""]
    for c in rivals:
        rows += [f"## {c.ref} — {c.doc_title}", c.text.strip(), ""]
    return "\n".join(rows)


@dataclass
class WatchFinding:
    clause_id: str
    corpus_ref: str
    quote: str
    corpus_quote: str
    facts: str
    summary: str
    raised_by: list[int] = field(default_factory=list)
    status: str = SINGLE
    discard_reason: str = ""
    doc_title: str = ""
    corpus_title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckResult:
    key: str
    doc_id: str
    title: str
    fingerprint: str
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    complete: bool = True
    errors: list[str] = field(default_factory=list)
    n_clauses: int = 0
    n_docs_compared: int = 0
    n_clauses_compared: int = 0
    batches: int = 0
    attackers: int = 0
    model: str = ""
    findings: list[WatchFinding] = field(default_factory=list)
    discarded: list[WatchFinding] = field(default_factory=list)
    attacks_tried: list[str] = field(default_factory=list)

    @property
    def corroborated(self) -> list[WatchFinding]:
        return [f for f in self.findings if f.status == CORROBORATED]

    def verdict(self) -> str:
        if not self.complete:
            if not self.batches:
                return f"INCOMPLETE: {'; '.join(self.errors)}"
            return f"INCOMPLETE: {len(self.errors)} attacker run(s) gave no usable reply"
        if not self.findings:
            return (f"no contradiction found against {self.n_docs_compared} document(s), "
                    f"{len(self.discarded)} claim(s) discarded")
        return (f"{len(self.findings)} possible contradiction(s), "
                f"{len(self.corroborated)} corroborated by two attackers")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "doc_id": self.doc_id, "title": self.title,
            "fingerprint": self.fingerprint, "started": self.started,
            "finished": self.finished, "seconds": round(self.finished - self.started, 1),
            "complete": self.complete, "errors": self.errors, "verdict": self.verdict(),
            "n_clauses": self.n_clauses, "n_docs_compared": self.n_docs_compared,
            "n_clauses_compared": self.n_clauses_compared, "batches": self.batches,
            "attackers": self.attackers, "model": self.model,
            "findings": [f.to_dict() for f in self.findings],
            "discarded": [f.to_dict() for f in self.discarded],
            "attacks_tried": self.attacks_tried[:40],
        }


def embed_missing(clauses: list[BaseClause]) -> None:
    todo = [c for c in clauses if not c.embedding]
    if not todo:
        return
    from .vector import MODEL_DIR, _load_model

    vecs = np.asarray(_load_model(REPO / MODEL_DIR).encode([c.embed_text() for c in todo]),
                      dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.where(norms == 0, 1, norms)
    for c, v in zip(todo, vecs):
        c.embedding = [float(x) for x in v]


def nearest(target: list[BaseClause], others: list[BaseClause], k: int) -> list[list[int]]:
    """For each target clause, the indices of its k nearest clauses in `others`,
    scored exhaustively: every clause of the base is compared, none pre-filtered."""
    mat = np.asarray([c.embedding for c in others], dtype=np.float32)
    out = []
    for c in target:
        scores = mat @ np.asarray(c.embedding, dtype=np.float32)
        out.append([int(i) for i in np.argsort(-scores)[:k]])
    return out


def verify(f: WatchFinding, target: BaseDoc, by_ref: dict[str, BaseClause]) -> WatchFinding:
    new = {c.clause_id: c for c in target.clauses}
    old = by_ref.get(f.corpus_ref)
    if f.clause_id not in new:
        f.status, f.discard_reason = DISCARDED, f"{f.clause_id!r} is not a clause of the new document"
    elif old is None:
        f.status, f.discard_reason = DISCARDED, f"{f.corpus_ref!r} is not a clause of another document"
    elif old.doc_id == target.doc_id:
        f.status, f.discard_reason = DISCARDED, "cites the document against itself"
    elif not f.quote or _norm(f.quote) not in _norm(new[f.clause_id].text):
        f.status, f.discard_reason = DISCARDED, "quote does not appear in the new clause"
    elif not f.corpus_quote or _norm(f.corpus_quote) not in _norm(old.text):
        f.status, f.discard_reason = DISCARDED, "quote does not appear in the existing clause"
    elif not f.summary:
        f.status, f.discard_reason = DISCARDED, "says nothing about what conflicts"
    if f.status != DISCARDED and old is not None:
        f.doc_title, f.corpus_title = target.title, old.doc_title
    return f


def _parse(value: Any, attacker: int) -> tuple[list[WatchFinding], list[str]]:
    if not isinstance(value, dict) or not isinstance(value.get("findings", []), list):
        raise ValueError("expected {findings: [...], attacks_tried: [...]}")
    out = []
    for r in value.get("findings", [])[:4]:
        if isinstance(r, dict):
            out.append(WatchFinding(**{k: str(r.get(k, "") or "").strip() for k in (
                "clause_id", "corpus_ref", "quote", "corpus_quote", "facts", "summary")},
                raised_by=[attacker]))
    return out, [str(t) for t in (value.get("attacks_tried") or [])][:6]


def check_document(
    target: BaseDoc,
    base: list[BaseDoc],
    *,
    attackers: int = ATTACKERS,
    neighbours: int = NEIGHBOURS,
    model: str = llm.DEFAULT_MODEL,
    call: Callable[..., Any] = run_role,
    embed: Callable[[list[BaseClause]], None] = embed_missing,
    emit: Callable[[str], None] = print,
    heartbeat: Callable[[], None] = lambda: None,
) -> CheckResult:
    """Attack one document against every other document in the base.

    `call` and `embed` are injectable so what happens to a reply -- validation,
    consensus, incompleteness -- is testable without a model.
    """
    res = CheckResult(key=target.key, doc_id=target.doc_id, title=target.title,
                      fingerprint=target.fingerprint, attackers=attackers, model=model,
                      n_clauses=len(target.clauses))
    others_docs = [complete_from_source(d) for d in base
                   if d.doc_id != target.doc_id and not d.error and d.clauses]
    others = [c for d in others_docs for c in d.clauses]
    res.n_docs_compared, res.n_clauses_compared = len(others_docs), len(others)
    if not others or not target.clauses:
        # nothing compared is not nothing found
        why = ("the base holds no other document to compare against" if not others
               else "the document has no clauses to compare")
        emit(f"  {target.doc_id}: {why}")
        res.complete = False
        res.errors.append(why)
        res.finished = time.time()
        return res

    embed(target.clauses + others)
    near = nearest(target.clauses, others, neighbours)
    by_ref = {c.ref: c for c in others}
    raw: list[WatchFinding] = []

    groups = [list(range(i, min(i + BATCH, len(target.clauses))))
              for i in range(0, len(target.clauses), BATCH)]
    res.batches = len(groups)
    for b, idxs in enumerate(groups):
        clauses = [target.clauses[i] for i in idxs]
        rivals: list[BaseClause] = []
        for rank in range(neighbours):       # interleaved so every clause keeps its best
            for i in idxs:
                if rank < len(near[i]):
                    c = others[near[i][rank]]
                    if c not in rivals and len(rivals) < MAX_CONTEXT:
                        rivals.append(c)
        text = packet(target, clauses, rivals)
        schema = reply_schema([c.clause_id for c in clauses], [c.ref for c in rivals])
        for a in range(attackers):
            ok, last = False, ""
            for attempt in (1, 2):
                heartbeat()
                seed = llm.DEFAULT_SEED + 1009 * (b + 1) + 97 * a + 31 * (attempt - 1)
                emit(f"  batch {b + 1}/{len(groups)} · attacker {a + 1}/{attackers}"
                     f" · attempt {attempt} ({len(clauses)} clauses vs {len(rivals)} rivals)")
                r = call(CROSS_CONTRADICTION, text, model=model, seed=seed, schema=schema)
                if not r.ok:
                    emit(f"    unusable reply: {r.error[:160]}")
                    last = r.error
                    continue
                try:
                    found, tried = _parse(r.value, a)
                except ValueError as e:
                    emit(f"    malformed: {e}")
                    last = str(e)
                    continue
                raw += found
                res.attacks_tried += tried
                emit(f"    {len(found)} claim(s)")
                ok = True
                break
            if not ok:
                res.complete = False
                res.errors.append(f"batch {b + 1}, attacker {a + 1}: {last[:200]}")

    for f in raw:
        verify(f, target, by_ref)
    res.discarded = [f for f in raw if f.status == DISCARDED]
    merged: dict[tuple[str, str], WatchFinding] = {}
    for f in raw:
        if f.status == DISCARDED:
            continue
        k = (f.clause_id, f.corpus_ref)
        if k in merged:
            merged[k].raised_by = sorted(set(merged[k].raised_by) | set(f.raised_by))
        else:
            merged[k] = f
    for f in merged.values():
        f.status = CORROBORATED if len(f.raised_by) >= 2 else SINGLE
    res.findings = sorted(merged.values(), key=lambda f: (f.status != CORROBORATED, f.clause_id))
    res.finished = time.time()
    emit(f"  {target.doc_id}: {res.verdict()}")
    return res


# --- the watcher ----------------------------------------------------------

Launcher = Callable[[str, dict[str, Any], Callable[[Callable[[str], None]], dict]], bool]
"""(label, params, body) -> whether it was started. `body(emit)` runs the check."""


def inline_launcher(label: str, _params: dict[str, Any], body) -> bool:
    print(label)
    body(print)
    return True


class Watcher:
    """Scan, claim one pending document, hand it to the launcher. Repeat.

    Every failure outside a check -- MongoDB gone, the model not loaded, a
    launcher busy with somebody else's job -- is a reason to wait for the next
    tick, never a reason for the thread to end.
    """

    def __init__(self, *, launcher: Launcher = inline_launcher, db_name: str | None = None,
                 interval: int = INTERVAL, model: str = llm.DEFAULT_MODEL,
                 check_model: bool = True):
        from .mongo_store import DB_NAME
        self.launcher = launcher
        self.db_name = db_name or DB_NAME
        self.interval = interval
        self.model = model
        self.check_model = check_model
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._client = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_scan: float = 0.0
        self.last_error: str = ""
        self.waiting_on: str = ""
        self.scans = 0

    # -- plumbing
    def db(self):
        if self._client is None:
            from .mongo_store import connect
            self._client = connect()
        return self._client[self.db_name]

    def _drop_client(self) -> None:
        try:
            if self._client is not None:
                self._client.close()
        finally:
            self._client = None

    def start(self) -> "Watcher":
        self._thread = threading.Thread(target=self.run_forever, name="doc-watch", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = ""
            except Exception as e:                              # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                self._drop_client()
            self._stop.wait(self.interval)

    # -- one pass
    def scan(self) -> dict[str, dict[str, Any]]:
        db = self.db()
        state = {r["_id"]: r for r in db[STATE].find({"_id": {"$ne": META_KEY}})}
        first = db[STATE].find_one({"_id": META_KEY}) is None
        try:
            docs = read_base(db)
        except BaseUnsettled as e:
            self.waiting_on = str(e)
            return {}
        if first and not any(d.source == "index" for d in docs):
            # a baseline taken before the index is loaded would make the whole
            # corpus arrive as new the moment it is
            self.waiting_on = "an index to take the baseline from (scripts/sync_mongo.py)"
            return {}
        updates = plan_scan(docs, state, first)
        for key, u in updates.items():
            db[STATE].update_one({"_id": key}, {"$set": u}, upsert=True)
        if first:
            db[STATE].replace_one({"_id": META_KEY},
                                  {"_id": META_KEY, "baselined_at": _now()}, upsert=True)
        self.last_scan, self.scans = time.time(), self.scans + 1
        return updates

    def tick(self) -> str | None:
        self.waiting_on = ""
        self.scan()
        if self.waiting_on:
            return None
        db = self.db()
        stale = time.time() - STALE_CLAIM_SECONDS
        db[STATE].update_many({"status": CHECKING, "heartbeat": {"$lt": stale}},
                              {"$set": {"status": PENDING, "owner": ""}})
        if db[STATE].count_documents({"status": PENDING}) == 0:
            self.waiting_on = ""
            return None
        if self.check_model:
            try:
                llm.require_model(self.model)
            except llm.ModelUnavailable as e:
                self.waiting_on = f"the local model: {e}"
                return None
        return self.launch_next()

    def claim(self, key: str | None = None) -> dict[str, Any] | None:
        from pymongo import ReturnDocument
        flt: dict[str, Any] = {"status": PENDING, "retry_after": {"$lte": time.time()}}
        if key:
            flt["_id"] = key
        return self.db()[STATE].find_one_and_update(
            flt, {"$set": {"status": CHECKING, "owner": self.owner, "heartbeat": time.time()}},
            sort=[("first_seen", 1)], return_document=ReturnDocument.AFTER)

    def launch_next(self, key: str | None = None) -> str | None:
        row = self.claim(key)
        if row is None:
            return None
        label = f"contradiction check: {row.get('title') or row['doc_id']}"
        started = self.launcher(label, {"key": row["_id"], "doc_id": row["doc_id"]},
                                lambda emit: self.run_check(row["_id"], emit))
        if not started:
            self.db()[STATE].update_one({"_id": row["_id"], "owner": self.owner},
                                        {"$set": {"status": PENDING, "owner": ""}})
            self.waiting_on = "another job on the local model"
            return None
        self.waiting_on = ""
        return row["_id"]

    def run_check(self, key: str, emit: Callable[[str], None] = print) -> dict[str, Any]:
        db = self.db()
        row = db[STATE].find_one({"_id": key}) or {}
        try:
            try:
                base = read_base(db)
            except BaseUnsettled as e:
                db[STATE].update_one({"_id": key, "owner": self.owner},
                                     {"$set": {"status": PENDING, "owner": ""}})
                emit(f"  deferred: {e}")
                return {"key": key, "verdict": f"deferred: {e}"}
            target = next((d for d in base if d.key == key), None)
            if target is None:
                db[STATE].update_one({"_id": key}, {"$set": {"status": REMOVED, "owner": ""}})
                return {"key": key, "verdict": "the document left the base before it was checked"}
            res = check_document(
                target, base, model=self.model, emit=emit,
                heartbeat=lambda: db[STATE].update_one(
                    {"_id": key}, {"$set": {"heartbeat": time.time()}}))
        except Exception as e:                                  # noqa: BLE001
            emit(traceback.format_exc())
            self._requeue(key, row, f"{type(e).__name__}: {e}")
            raise

        out = {"id": uuid.uuid4().hex[:12], **res.to_dict()}
        db[CHECKS].insert_one({"_id": out["id"], **res.to_dict()})
        db[STATE].update_one({"_id": key}, {"$set": {"check_id": out["id"],
                                                     "last_check": _brief(res)}})
        attempts = int(row.get("attempts", 0)) + 1
        if res.complete or attempts >= MAX_ATTEMPTS:
            # a document edited mid-check was re-queued by a scan; leave it queued
            db[STATE].update_one({"_id": key, "fingerprint": res.fingerprint}, {"$set": {
                "status": CHECKED if res.complete else INCOMPLETE, "owner": "",
                "attempts": attempts, "checked_at": _now(),
                "error": "; ".join(res.errors)[:500]}})
        else:
            self._requeue(key, row, "; ".join(res.errors)[:500])
        return out

    def _requeue(self, key: str, row: dict[str, Any], error: str) -> None:
        attempts = int(row.get("attempts", 0)) + 1
        final = attempts >= MAX_ATTEMPTS
        self.db()[STATE].update_one({"_id": key}, {"$set": {
            "status": INCOMPLETE if final else PENDING, "owner": "", "attempts": attempts,
            "error": error, "retry_after": time.time() + 300 * attempts}})

    # -- reading
    def requeue(self, key: str) -> bool:
        r = self.db()[STATE].update_one(
            {"_id": key, "status": {"$nin": [CHECKING, REMOVED, UNREADABLE]}},
            {"$set": {"status": PENDING, "attempts": 0, "retry_after": 0, "error": ""}})
        return r.matched_count == 1

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "running": bool(self._thread and self._thread.is_alive()),
            "interval": self.interval, "scans": self.scans,
            "last_scan": self.last_scan, "last_error": self.last_error,
            "waiting_on": self.waiting_on, "attackers": ATTACKERS, "model": self.model,
        }
        try:
            db = self.db()
            rows = list(db[STATE].find({"_id": {"$ne": META_KEY}}, {"heartbeat": 0}))
            counts: dict[str, int] = {}
            for r in rows:
                counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
            out["counts"] = counts
            out["documents"] = sorted(
                ({"key": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in rows),
                key=lambda r: str(r.get("changed_at", "")), reverse=True)
            out["mongo"] = "connected"
        except Exception as e:                                  # noqa: BLE001
            out["mongo"] = f"unavailable: {type(e).__name__}"
            self._drop_client()
        return out

    def checks(self, key: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        flt = {"key": key} if key else {}
        rows = self.db()[CHECKS].find(flt).sort("started", -1).limit(limit)
        return [{"id": r.pop("_id"), **r} for r in rows]


def _brief(res: CheckResult) -> dict[str, Any]:
    return {"verdict": res.verdict(), "findings": len(res.findings),
            "corroborated": len(res.corroborated), "complete": res.complete}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
