"""Local HTTP API for the web interface.

Standard library only, deliberately. This runs on a lawyer's laptop next to a
Catala compiler and a MongoDB container; adding a web framework would add a
dependency and an upgrade treadmill for a JSON router that fits in a page.

Threaded, because executing a Catala scope shells out to `clerk run` and takes
of the order of a second — a single-threaded server would block the interface
on every answer.

Nothing here computes a legal result. Every endpoint delegates to the same
modules the CLI uses, so the interface cannot drift from the command line or
acquire a second, subtly different notion of what a clause says.
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- cached, rebuilt on demand --------------------------------------------

_cache: dict[str, Any] = {}


def _registry():
    if "registry" not in _cache:
        from .registry import build_registry, load_registry
        _cache["registry"] = load_registry() or build_registry()
    return _cache["registry"]


def _corpus():
    if "corpus" not in _cache:
        from .segment import load_corpus
        _cache["corpus"] = load_corpus("corpus")
    return _cache["corpus"]


def _ledger():
    if "ledger" not in _cache:
        from .triage import load_ledger
        _cache["ledger"] = load_ledger()
    return _cache["ledger"]


def _chat():
    if "chat" not in _cache:
        from .chat import Chat
        _cache["chat"] = Chat.open(backend="auto")
    return _cache["chat"]


def invalidate() -> None:
    _cache.clear()


# --- endpoints -------------------------------------------------------------

def ep_state(_body: Any, _q: dict) -> dict:
    """Everything the interface needs to render its shell."""
    from .counterexample import summary
    ledger = _ledger()
    docs = []
    for d in _corpus():
        labels = {"RULE": 0, "PROSE": 0, "HYBRID": 0}
        for c in d.clauses:
            dec = ledger.get(c.ref)
            labels[dec.label.value if dec else "PROSE"] += 1
        docs.append({
            "doc_id": d.doc_id, "title": d.title, "version": d.version,
            "effective_date": d.effective_date, "owner": d.owner,
            "jurisdiction": d.jurisdiction, "n_clauses": len(d.clauses),
            "labels": labels,
            "sections": _sections(d),
        })
    reg = _registry()
    store_kind = "unavailable"
    n_chunks = 0
    try:
        st = _chat().store
        n_chunks = len(getattr(st, "chunks", [])) or st.status()["n_chunks"]
        store_kind = ("mongo:" + st.status()["search_path"]) if hasattr(st, "status") else "files"
    except Exception:
        pass
    return {
        "documents": docs,
        "n_scopes": len(reg),
        "n_modules": len({e.module for e in reg.values()}),
        "counterexamples": summary(),
        "vector_store": {"kind": store_kind, "n_chunks": n_chunks},
    }


def _sections(doc) -> list[dict]:
    out: list[dict] = []
    for c in doc.clauses:
        if not out or out[-1]["section_id"] != c.section_id:
            out.append({"section_id": c.section_id, "title": c.section_title, "clauses": []})
        dec = _ledger().get(c.ref)
        out[-1]["clauses"].append({
            "clause_id": c.clause_id, "ref": c.ref,
            "label": dec.label.value if dec else "PROSE",
            "module": (dec.module if dec else None),
            "line": c.line_start,
        })
    return out


def ep_ask(body: Any, _q: dict) -> dict:
    q = (body or {}).get("question", "").strip()
    if not q:
        raise ApiError("A question is required.")
    inputs = (body or {}).get("inputs") or None
    scope = (body or {}).get("scope") or None
    ans = _chat().answer(q, inputs=inputs, scope=scope)
    parts = [
        {
            "engine": p.engine, "kind": p.kind, "text": p.text,
            "citations": p.citations, "scope": p.scope,
            "outputs": p.outputs, "inputs": p.inputs,
            "score": round(p.score, 3) if p.score is not None else None,
            "model": None,
        }
        for p in ans.parts
    ]
    if parts and all(p["kind"] == "no-coverage" for p in parts):
        parts = _general_fallback(q, parts)
    engines: list[str] = []
    for p in parts:
        if p["engine"] not in engines:
            engines.append(p["engine"])
    return {"question": ans.question, "engines": engines, "parts": parts}


GENERAL_CLAUSES = 6
"""How many of the closest clauses the fallback model reads."""


def _closest_clauses(question: str) -> list[tuple[str, str, str]]:
    """The corpus clauses nearest the question, whether prose or rule.

    Prose comes from the quotable store and rule clauses from the route index,
    both with no floor: these are the clauses that fell *below* the answering
    thresholds, which is exactly why the model is being asked.
    """
    chat = _chat()
    by_ref = {c.ref: c for d in _corpus() for c in d.clauses}
    refs: list[str] = []
    if chat._ranker is not None:
        for h in chat._ranker.search(question, k=GENERAL_CLAUSES, min_score=0.0)[:GENERAL_CLAUSES // 2]:
            refs.append(h.chunk.ref)
    refs += chat.route.best_refs(question, set(by_ref), GENERAL_CLAUSES)
    out: list[tuple[str, str, str]] = []
    for ref in dict.fromkeys(refs):
        c = by_ref.get(ref)
        if c is not None and len(out) < GENERAL_CLAUSES:
            out.append((c.ref, c.section_title, c.body))
    return out


def _general_fallback(question: str, parts: list[dict]) -> list[dict]:
    """When no rule or clause answers, let the local model read the closest.

    Done here rather than in `Chat.answer`, which stays exactly as measured:
    the routing evaluation and the search service read it, and neither should
    start calling a model. The no-coverage part is kept and reworded to say
    where the question went, and the model's reply is its own MODEL part, so
    its reading is never presented as a computed or quoted answer.
    """
    from . import agents, llm
    from .chat import Engine
    model = llm.DEFAULT_MODEL
    clauses = _closest_clauses(question)
    res = agents.answer_general(question, clauses, model=model)
    note = ("No rule and no clause in your documents matched this closely enough "
            "to compute or quote an answer.")
    if res.ok:
        note += (f" The local model ({model}) read the {len(clauses)} closest clauses "
                 f"and answered below. That is its reading, not a computed or quoted answer.")
        extra = {"engine": Engine.MODEL, "kind": "general", "text": res.value["text"],
                 "citations": res.value["cited"]}
    else:
        why = ("it is probably busy with another job" if res.kind == "timeout"
               else res.error)
        note += f" It was passed to the local model ({model}), which did not answer."
        extra = {"engine": Engine.MODEL, "kind": "general-failed",
                 "text": f"No answer from {model}: {why}"}
    parts[0] = {**parts[0], "text": note}
    return parts + [{"citations": [], "scope": None, "outputs": None, "inputs": None,
                     "score": None, "model": model, "read": [r for r, _h, _b in clauses], **extra}]


def ep_scopes(_body: Any, _q: dict) -> dict:
    out = []
    for k, e in sorted(_registry().items()):
        out.append({
            "key": k, "module": e.module, "scope": e.scope,
            "inputs": e.inputs, "outputs": e.outputs, "internals": e.internals,
            "encodes": e.encodes, "judgement_inputs": e.judgement_inputs,
        })
    return {"scopes": out}


def ep_scope(_body: Any, q: dict) -> dict:
    key = q.get("key", "")
    e = _registry().get(key)
    if e is None:
        raise ApiError(f"No scope {key!r}.", 404)
    from .catala_runner import json_schema
    try:
        in_schema, out_schema = json_schema(e.path, e.scope)
    except Exception:
        in_schema, out_schema = {}, {}
    clauses = []
    by_ref = {c.ref: c for d in _corpus() for c in d.clauses}
    for ref in e.encodes:
        c = by_ref.get(ref)
        if c:
            dec = _ledger().get(ref)
            clauses.append({
                "ref": ref, "body": c.body, "section": c.section_title,
                "label": dec.label.value if dec else None,
                "file": Path(c.source_path).name, "line": c.line_start,
            })
    return {
        "key": key, "module": e.module, "scope": e.scope, "path": e.path,
        "inputs": e.inputs, "outputs": e.outputs, "internals": e.internals,
        "judgement_inputs": e.judgement_inputs,
        "input_schema": _flatten_schema(in_schema),
        "clauses": clauses,
    }


def _flatten_schema(schema: dict) -> dict[str, str]:
    """input name -> catala type, read off the compiler's own JSON Schema so the
    interface never guesses a type."""
    defs = schema.get("definitions", {})
    ref = schema.get("$ref", "")
    root = defs.get(ref.split("/")[-1], {})
    out: dict[str, str] = {}
    for name, spec in (root.get("properties") or {}).items():
        r = spec.get("$ref", "")
        out[name] = r.split("/")[-1] if r else spec.get("type", "unknown")
    return out


def ep_run(body: Any, _q: dict) -> dict:
    from .catala_runner import CatalaError, run_scope
    key = (body or {}).get("target", "")
    e = _registry().get(key)
    if e is None:
        raise ApiError(f"No scope {key!r}.", 404)
    inputs = (body or {}).get("inputs") or {}
    try:
        return {"outputs": run_scope(e.path, e.scope, inputs), "error": None}
    except CatalaError as ex:
        return {"outputs": None, "error": {
            "kind": type(ex).__name__, "diagnostic": ex.diagnostic}}


def ep_hierarchy(_body: Any, q: dict) -> dict:
    """The exception hierarchy as the compiler built it, plus — when facts are
    supplied — which rung actually governed."""
    from .catala_runner import CatalaError, exception_tree
    key, var = q.get("key", ""), q.get("variable", "")
    e = _registry().get(key)
    if e is None:
        raise ApiError(f"No scope {key!r}.", 404)
    try:
        trees = exception_tree(e.path, e.scope, var, normalise_labels=False)
    except CatalaError as ex:
        raise ApiError(f"No hierarchy for {key}.{var}: {ex.diagnostic[:200]}", 404)

    def node(n) -> dict:
        return {
            "label": n.label, "conditions": n.conditions,
            "law_headings": n.law_headings,
            "exceptions": [node(c) for c in n.exceptions],
        }

    return {"key": key, "variable": var, "trees": [node(t) for t in trees]}


def ep_explain(body: Any, _q: dict) -> dict:
    """Execute a scope and say which rung of each hierarchy governed.

    The governing rung is not inferred by re-evaluating conditions here -- that
    would be a second implementation of the law, free to disagree with the
    first. It comes from the interpreter's own trace: Catala reports the source
    line of the definition it applied, and that line identifies a node of the
    exception tree.
    """
    from .catala_runner import CatalaError, exception_tree, run_scope_traced
    key = (body or {}).get("target", "")
    e = _registry().get(key)
    if e is None:
        raise ApiError(f"No scope {key!r}.", 404)
    inputs = (body or {}).get("inputs") or {}
    try:
        outputs, decisions = run_scope_traced(e.path, e.scope, inputs)
    except CatalaError as ex:
        return {"key": key, "outputs": None, "hierarchies": [],
                "error": {"kind": type(ex).__name__, "diagnostic": ex.diagnostic}}

    governing = {d["variable"]: d for d in decisions}

    def node(n, gov_line: int | None) -> dict:
        governs = gov_line is not None and gov_line in n.lines
        return {
            "label": n.label, "conditions": n.conditions,
            "law_headings": n.law_headings, "lines": n.lines,
            "governs": governs,
            "exceptions": [node(c, gov_line) for c in n.exceptions],
        }

    hierarchies = []
    for var in e.outputs:
        try:
            trees = exception_tree(e.path, e.scope, var, normalise_labels=False)
        except CatalaError:
            continue
        g = governing.get(var)
        gline = g.get("line") if g else None
        hierarchies.append({
            "variable": var,
            "value": outputs.get(var),
            "governing_line": gline,
            "governing_headings": (g.get("law_headings") if g else []) or [],
            "trees": [node(t, gline) for t in trees],
            "n_nodes": sum(_count(t) for t in trees),
        })
    return {"key": key, "outputs": outputs, "hierarchies": hierarchies, "error": None}


def _count(n) -> int:
    return 1 + sum(_count(c) for c in n.exceptions)


def ep_agent_status(_body: Any, _q: dict) -> dict:
    """Whether a local agent is available, and what each role may see."""
    from . import agents, llm
    out: dict[str, Any] = {
        "model": llm.DEFAULT_MODEL,
        "host": llm.OLLAMA_HOST,
        "available": False,
        "models": [],
        "isolation": agents.REVIEWER.enforcement(),
        "openshell": agents.openshell_available(),
        "roles": [],
    }
    try:
        tags = llm.available()
        out["available"] = True
        out["models"] = sorted(m.get("name", "") for m in tags.get("models", []))
    except llm.ModelUnavailable as e:
        out["error"] = str(e)
    for name, r in agents.ROLES.items():
        out["roles"].append({
            "name": name, "purpose": r.purpose,
            "reads": r.reads, "forbidden": r.forbidden,
            "enforcement": r.enforcement(),
            "isolation_problems": agents.audit_isolation(r),
        })
    from . import fleet, remedy
    out["fleet"] = [
        {"key": k, "name": r.name, "purpose": r.purpose, "forbidden": r.forbidden,
         "enforcement": r.enforcement()}
        for k, r in sorted(fleet.ARCHETYPES.items())
    ]
    out["writers"] = [
        {"name": r.name, "purpose": r.purpose, "forbidden": r.forbidden,
         "enforcement": r.enforcement()}
        for r in (fleet.DEMAND, remedy.CLOSER)
    ]
    return out


def ep_slotfill(body: Any, _q: dict) -> dict:
    """Extract the facts a question states, for a person to check.

    Nothing is executed here. The extracted facts are returned so the
    interface can prefill the form and the person can see, and correct, every
    value before a rule runs on it. A fact the question did not state is
    reported as omitted rather than guessed.
    """
    from . import agents, llm
    key = (body or {}).get("target", "")
    question = ((body or {}).get("question") or "").strip()
    if not key or not question:
        raise ApiError("target and question are both required.")
    e = _registry().get(key)
    if e is None:
        raise ApiError(f"No scope {key!r}.", 404)
    from .catala_runner import json_schema
    try:
        in_schema, _ = json_schema(e.path, e.scope)
    except Exception as ex:
        raise ApiError(f"Cannot read the input schema for {key}: {ex}", 500)
    types = _flatten_schema(in_schema)
    try:
        res = agents.extract_facts(question, types, model=llm.DEFAULT_MODEL)
    except llm.ModelUnavailable as ex:
        raise ApiError(str(ex), 503)
    if not res.ok:
        return {"key": key, "facts": {}, "omitted": sorted(types),
                "error": res.error, "usage": str(res.usage)}
    return {
        "key": key, "facts": res.value["facts"], "omitted": res.value["omitted"],
        "error": None, "usage": str(res.usage), "model": res.role,
    }


def ep_clause(_body: Any, q: dict) -> dict:
    ref = unquote(q.get("ref", "")).strip()
    for d in _corpus():
        for c in d.clauses:
            if c.ref == ref:
                dec = _ledger().get(ref)
                enc = []
                for k, e in _registry().items():
                    if ref in e.encodes:
                        enc.append(k)
                return {
                    "ref": ref, "doc_id": d.doc_id, "doc_title": d.title,
                    "version": d.version, "effective_date": d.effective_date,
                    "section_id": c.section_id, "section_title": c.section_title,
                    "body": c.body, "file": Path(c.source_path).name,
                    "line": c.line_start, "hash": c.hash,
                    "label": dec.label.value if dec else None,
                    "module": dec.module if dec else None,
                    "qualifies": dec.qualifies if dec else [],
                    "judgement_inputs": dec.judgement_inputs if dec else [],
                    "encoded_by": sorted(enc),
                }
    raise ApiError(f"No clause {ref!r}.", 404)


def ep_verification(_body: Any, _q: dict) -> dict:
    from . import plain
    from .counterexample import load_all, summary
    from .registry import coverage
    ces = [
        {
            "id": ce.id, "status": ce.status, "round": ce.round,
            "component": ce.component,
            "module": ce.target.get("module"), "scope": ce.target.get("scope"),
            "rule": plain.rule_name(ce.target.get("module"), ce.target.get("scope")),
            "fact_pattern": ce.fact_pattern, "citations": ce.citations,
            "expected": ce.expected, "observed": ce.observed,
            "source_reasoning": ce.source_reasoning, "discovered": ce.discovered,
            "headline": ce.headline, "why_it_matters": ce.why_it_matters,
            "plain": plain.break_summary(ce),
        }
        for ce in load_all()
    ]
    cov = coverage(_registry())
    defects = []
    p = ROOT / "docs" / "DOCUMENT-DEFECTS.md"
    if p.exists():
        blocks = re.split(r"^## ", p.read_text(), flags=re.M)[1:]
        for b in blocks:
            head, _, rest = b.partition("\n")
            defects.append({"title": head.strip(), "body": rest.strip()})
    return {
        "counterexamples": ces, "summary": summary(),
        "coverage": {k: v for k, v in cov.items() if k != "duplicated"},
        "document_defects": defects,
    }


def ep_proposals(_body: Any, _q: dict) -> dict:
    from .ingest import PROPOSALS, Proposal
    out = []
    if PROPOSALS.exists():
        for d in sorted(PROPOSALS.iterdir()):
            if not (d / "proposal.yaml").exists():
                continue
            pr = Proposal.load(d.name)
            out.append({
                "id": pr.id, "doc_id": pr.doc_id, "title": pr.title,
                "created": pr.created, "mergeable": pr.mergeable,
                "triage": pr.triage, "notes": pr.notes,
                "conflicts": [
                    {
                        "kind": c.kind, "severity": c.severity,
                        "incoming_ref": c.incoming_ref, "detail": c.detail,
                        "existing_refs": c.existing_refs,
                        "resolved": c.resolved, "resolution": c.resolution,
                        "resolved_by": c.resolved_by,
                    }
                    for c in pr.conflicts
                ],
            })
    return {"proposals": out}


def ep_resolve(body: Any, _q: dict) -> dict:
    """Record a human's resolution against one conflict. The merge gate reads
    this; nothing else can satisfy it."""
    import yaml
    from .ingest import PROPOSALS, Proposal
    pid = (body or {}).get("id")
    idx = (body or {}).get("index")
    resolution = ((body or {}).get("resolution") or "").strip()
    who = ((body or {}).get("resolved_by") or "").strip()
    if not pid or idx is None or not resolution or not who:
        raise ApiError("id, index, resolution and resolved_by are all required.")
    path = PROPOSALS / pid / "proposal.yaml"
    if not path.exists():
        raise ApiError(f"No proposal {pid!r}.", 404)
    data = yaml.safe_load(path.read_text())
    try:
        data["conflicts"][int(idx)]["resolution"] = resolution
        data["conflicts"][int(idx)]["resolved_by"] = who
    except (IndexError, KeyError, ValueError):
        raise ApiError("No such conflict on that proposal.", 404)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    pr = Proposal.load(pid)
    return {"id": pid, "mergeable": pr.mergeable, "unresolved": len(pr.unresolved)}


# --- the adversarial legal exposure engine --------------------------------
#
# Every one of these delegates to the same modules the CLI uses. The map in
# particular is not recomputed here from a second notion of what a region is:
# `lks.surface.partition` is the only thing that decides that, and the browser
# receives what it decided.

EXEC_LOCK = threading.RLock()
"""Held by every endpoint that executes Catala.

Not for throughput -- it costs some, and this is a single-user tool on a
laptop. It is because `ep_exposure_whatif` and the closing-edit job **write to
a module in the source tree**, rebuild, measure and put it back (see
`lks.remedy`, and DECISIONS.md D-9 for why it has to be the real tree). A map
request arriving during that window would execute against the edited module and
return borders that belong to a version of the policy nobody has adopted, with
nothing to indicate it.

Re-entrant, because a request thread nests these calls -- `ep_exposure_map`
takes it and `_surface` takes it again.
"""


@contextmanager
def executing() -> Any:
    """Serialise Catala execution against edits to the tree."""
    with EXEC_LOCK:
        yield


def _surface(scope: str):
    """A partitioned scope, cached against the module's content.

    Keyed on the module's bytes rather than on the scope name alone. The map is
    advertised as redrawing when a clause changes, so a cache that survived an
    edit made outside this process -- in an editor, or by the counterfactual --
    would show the previous wording's borders and say nothing about it. That is
    the one failure this screen cannot have.
    """
    from . import surface as sf
    from .watchers import populate

    entry = _registry().get(scope)
    if entry is None:
        raise ApiError(f"no scope {scope!r}", 404)
    # the module and everything it `> Using`s: an edit to a dependency moves
    # this map as surely as an edit to the module itself
    stamp = sf.source_hash(entry.path)
    key = f"surfaces:{scope}:{stamp}"
    if key not in _cache:
        for stale in [k for k in _cache if k.startswith(f"surfaces:{scope}:")]:
            del _cache[stale]
        with executing():
            _cache[key] = populate(sf.partition(scope))
    return _cache[key]


def _region_payload(r, sd) -> dict:
    rng = r.value_range(sd.measure_output) if sd.measure_output else None
    return {
        "id": r.id,
        "outcome": r.outcome,
        "cases": r.size,
        "population": r.population,
        "clause_refs": list(r.clause_refs),
        "exemplar": r.exemplar(),
        "outputs": r.cells[0].outputs if r.cells else {},
        "diagnostic": (r.cells[0].diagnostic[:400] if r.cells else ""),
        "measure": sd.measure_output,
        "value_low": (str(rng[0]) if rng else None),
        "value_high": (str(rng[1]) if rng else None),
        "governing": [
            {"variable": g["variable"], "line": g["line"],
             "clause_refs": g["clause_refs"], "law_headings": g["law_headings"]}
            for g in r.governing
        ],
    }


def ep_exposure_scopes(_body: Any, _q: dict) -> dict:
    from . import exposure
    from . import surface as sf
    doms = sf.load_domains()
    reg = _registry()
    preds = exposure.load_predicates()
    return {
        "scopes": [
            {
                "key": k,
                "population": d.population,
                "measure": d.measure_output,
                "unit": d.measure_unit,
                "predicates": sum(1 for p in preds if p.scope == k),
                "encodes": (reg[k].encodes if k in reg else []),
            }
            for k, d in sorted(doms.items())
        ],
        "unmapped": sorted(set(reg) - set(doms)),
        "problems": sf.validate_domains(doms, reg) + exposure.validate_predicates(preds, reg),
        "queue": exposure.summary(),
    }


def ep_exposure_map(_body: Any, q: dict) -> dict:
    from . import surface as sf
    scope = q.get("scope")
    if not scope:
        raise ApiError("scope is required")
    doms = sf.load_domains()
    if scope not in doms:
        raise ApiError(f"{scope} has no declared realisable domain", 404)
    from . import exposure

    surf = _surface(scope)
    sd = doms[scope]
    by_region: dict[str, list[str]] = {}
    for e in exposure.load_queue():
        if e.scope != scope:
            continue
        r = sf.locate(surf, e.facts)
        if r is not None:
            by_region.setdefault(r.id, []).append(e.id)
    return {
        "scope": scope,
        "population": sd.population,
        "measure": sd.measure_output,
        "unit": sd.measure_unit,
        "summary": surf.summary(),
        "axes": surf.axes,
        "axis_reasons": surf.axis_reasons,
        "elided": surf.elided,
        "coherence": surf.coherence,
        "incoherent": surf.incoherent,
        "borders": [
            {"variable": b.variable, "op": b.op, "literal": b.literal,
             "text": b.text, "label": b.label, "output": b.output,
             "clause_refs": b.clause_refs}
            for b in surf.borders
        ],
        "regions": [
            dict(_region_payload(r, sd), exposures=by_region.get(r.id, []))
            for r in surf.regions
        ],
    }


def ep_exposure_queue(_body: Any, q: dict) -> dict:
    from . import exposure
    items = exposure.load_queue()
    if q.get("class"):
        items = [e for e in items if e.klass == q["class"].upper()]
    if q.get("scope"):
        items = [e for e in items if e.scope == q["scope"]]

    def key(e):
        from decimal import Decimal, InvalidOperation
        try:
            amt = Decimal(e.amount) if e.amount is not None else Decimal(-1)
        except InvalidOperation:
            amt = Decimal(-1)
        return (0 if e.klass == exposure.Klass.DIVERGENCE else 1, -amt, e.id)

    return {
        "summary": exposure.summary(),
        "items": [e.to_dict() for e in sorted(items, key=key)],
    }


def ep_exposure_item(_body: Any, q: dict) -> dict:
    from . import exposure
    for e in exposure.load_queue():
        if e.id == q.get("id"):
            return e.to_dict()
    raise ApiError(f"no exposure {q.get('id')!r}", 404)


def ep_exposure_operations(body: Any, _q: dict) -> dict:
    """GET reads; POST {"record": true} also writes the divergences to the
    queue. Recording is never reachable by GET, which a page on any site can
    trigger with an <img> tag."""
    from .watchers import check_operations, load_decisions, record_divergences
    ds = load_decisions()
    with executing():
        divs = check_operations(ds)
        record = isinstance(body, dict) and body.get("record") is True
        made = record_divergences(divs) if record else []
    return {
        "decisions": len(ds),
        "agreeing": len(ds) - len({d.decision.id for d in divs}),
        "recorded": [e.id for e in made],
        "divergences": [
            {
                "id": d.decision.id, "subject": d.decision.subject,
                "decided_on": d.decision.decided_on, "source": d.decision.source,
                "scope": d.decision.scope, "field": d.field,
                "recorded": d.recorded, "computed": d.computed,
                "clause_refs": d.clause_refs, "error": d.error,
                "headline": d.headline, "facts": d.decision.facts,
                "direction": d.direction, "generosity": d.generosity,
                "favours": d.favours,
            }
            for d in divs
        ],
    }


def ep_exposure_holdings(_body: Any, _q: dict) -> dict:
    from .watchers import holding_impact, load_proposed_holdings
    out = []
    for h in load_proposed_holdings():
        with executing():
            imp = holding_impact(h)
        out.append({
            "id": h.id, "scope": h.scope, "title": h.title, "holding": h.holding,
            "when": h.when, "citations": h.citations, "status": h.status,
            "contends": {"output": h.contends_output, "value": h.contends_value},
            "impact": {
                "summary": imp.summary(), "regions": imp.regions,
                "cells_hit": imp.cells_hit, "cells_total": imp.cells_total,
                "population": imp.population, "clause_refs": imp.clause_refs,
                "degenerate": imp.degenerate, "inverted": imp.inverted,
                "cells_caught": imp.cells_caught,
                "contends_no_change": imp.contends_no_change,
                "contends_against": imp.contends_against,
            },
        })
    return {"holdings": out}


def ep_exposure_adjudicate(body: Any, _q: dict) -> dict:
    from . import exposure
    if not isinstance(body, dict):
        raise ApiError("expected a JSON object")
    scope = body.get("scope")
    facts = body.get("facts")
    if not scope or not isinstance(facts, dict):
        raise ApiError("scope and facts are required")
    att = exposure.Attack(archetype="(from the interface)", scope=scope,
                          facts=facts, narrative=str(body.get("narrative", "")),
                          origin="web")
    with executing():
        v = exposure.adjudicate(att)
        recorded, already = "", False
        if v.landed and body.get("record"):
            e = exposure.record(att, v)
            # None means the hole is already queued. That is not a failure and
            # must not read as one: it is the interface confirming a known hole
            # by another route, which is what most hand-probing does.
            recorded, already = (e.id if e else ""), e is None
    return {
        "landed": v.landed, "class": v.klass, "why": v.why,
        "predicate": v.predicate, "outputs": v.outputs,
        "governing": v.governing, "diagnostic": v.diagnostic,
        "amount": (str(v.amount) if v.amount is not None else None),
        "amount_basis": v.amount_basis,
        "recorded": recorded, "already_known": already,
    }


def ep_exposure_whatif(body: Any, _q: dict) -> dict:
    """Redraw the map for an edited clause, and say what else moved.

    This is the counterfactual, run for real: the edit is applied to the
    module, the project is rebuilt, every mapped scope is re-executed and the
    module is put back. It takes tens of seconds and it is not a simulation --
    which is the point, because a map that redrew itself by guessing would be
    the one thing on this screen nobody could rely on.
    """
    from . import surface as sf
    from .remedy import counterfactual, splice
    if not isinstance(body, dict):
        raise ApiError("expected a JSON object")
    scope = body.get("scope")
    old, new = body.get("old"), body.get("new")
    if not scope or not old or new is None:
        raise ApiError("scope, old and new are required")
    entry = _registry().get(scope)
    if entry is None:
        raise ApiError(f"no scope {scope!r}", 404)
    source = Path(entry.path).read_text(encoding="utf-8")
    try:
        edited_source = splice(source, old, new)
    except ValueError as e:
        raise ApiError(str(e)) from e
    only = [k for k in sf.load_domains() if k.startswith(entry.module + ".")] or [scope]
    with executing():
        cf = counterfactual(entry.path, edited_source, scopes=only)
    return {
        "scope": scope,
        "module": entry.path,
        "ok": cf.ok,
        "typecheck_ok": cf.typecheck_ok,
        "typecheck_diagnostic": cf.typecheck_diagnostic,
        "tests_ok": cf.tests_ok,
        "regressions_broken": cf.regressions_broken,
        "regressions_fixed": cf.regressions_fixed,
        "error": cf.error,
        "report": cf.report(),
        "diffs": [
            {
                "scope": d.scope, "measure": d.measure, "unit": d.unit,
                "cells_compared": d.cells_compared, "touched": d.touched,
                "unmatched": d.unmatched,
                "regions_before": d.regions_before, "regions_after": d.regions_after,
                "summary": d.summary(),
                "changes": [
                    {"inputs": c.inputs, "kind": c.kind,
                     "before": c.before_value, "after": c.after_value,
                     "before_outcome": c.before_outcome, "after_outcome": c.after_outcome,
                     "before_refs": c.before_refs, "after_refs": c.after_refs,
                     "delta": (str(c.delta) if c.delta is not None else None)}
                    for c in d.changes[:400]
                ],
            }
            for d in cf.diffs
        ],
    }


def ep_exposure_schema(_body: Any, q: dict) -> dict:
    """Everything the interface needs to build a fact pattern by hand.

    The types come from the compiler's own JSON Schema and the ranges from the
    declared domain, so a form built from this cannot offer a field the scope
    does not take, and shows the note explaining every narrowing next to the
    field it narrows. Probing by hand is how somebody satisfies themselves that
    the adjudicator is deciding rather than agreeing, so it has to be as easy
    to put an impossible person in as a real one -- the form does not enforce
    the domain, it displays it, and the executor does the refusing.
    """
    from . import exposure
    from . import surface as sf
    from .fleet import scope_interface

    scope = q.get("scope")
    if not scope:
        raise ApiError("scope is required")
    entry = _registry().get(scope)
    if entry is None:
        raise ApiError(f"no scope {scope!r}", 404)
    sd = sf.load_domains().get(scope)
    if sd is None:
        raise ApiError(f"{scope} has no declared realisable domain", 404)
    iface = scope_interface(entry)

    fields = []
    for name in entry.inputs:
        d = sd.inputs.get(name)
        fields.append({
            "name": name,
            "type": iface.get(name, "unknown"),
            "kind": (d.type if d else ""),
            "realisable": (d.realisable if d else None),
            "probe": (d.probe if d else None),
            "note": (d.note.strip() if d else ""),
            "enum": (sf.enum_values(entry, name) if d and d.type == "enum" else []),
            "judgement": name in entry.judgement_inputs,
        })
    return {
        "scope": scope,
        "population": sd.population,
        "measure": sd.measure_output,
        "unit": sd.measure_unit,
        "outputs": entry.outputs,
        "encodes": entry.encodes,
        "coherence": [{"holds": c.holds, "note": c.note} for c in sd.coherence],
        "fields": fields,
        "predicates": [
            {"id": pr.id, "title": pr.title, "archetype": pr.archetype,
             "citations": pr.citations}
            for pr in exposure.load_predicates() if pr.scope == scope
        ],
    }


def ep_exposure_contradictions(_body: Any, _q: dict) -> dict:
    from . import exposure
    with executing():
        cs = exposure.check_rivalries()
    rivs = exposure.load_rivalries()
    return {
        "rivalries": [
            {"left": r.left, "right": r.right, "question": r.question,
             "citations": r.citations, "fixed_inputs": r.fixed_inputs,
             "agree_on": [{"left": a, "right": b} for a, b in r.agree_on]}
            for r in rivs
        ],
        "contradictions": [
            {"left": c.rivalry.left, "right": c.rivalry.right,
             "headline": c.headline, "facts": c.facts, "field": c.field,
             "error": c.error}
            for c in cs
        ],
    }


# --- long-running actions --------------------------------------------------
#
# Three things here need the local model and take between one and ten minutes:
# running the fleet, drafting a demand letter, and proposing a closing edit and
# measuring it. A blocking POST would leave the browser on a spinner with no
# idea whether anything was happening, and for a fleet run of any length the
# request would simply time out.
#
# So they are jobs: POST starts one and returns its id, GET polls it, and the
# fleet emits a line per round as it goes, which is the thing worth watching --
# most rounds die, and watching them die is how somebody comes to believe that
# the ones that do not are worth reading.

@dataclass
class Job:
    id: str
    kind: str
    label: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "running"          # running | done | failed
    lines: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    """Structured progress, so the interface draws each stage and round rather
    than parsing a transcript. `lines` stays as the plain transcript."""
    progress: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def emit(self, line: str) -> None:
        self.lines.append(line)

    def event(self, kind: str, **data: Any) -> None:
        self.events.append({"type": kind, "t": round(time.time() - self.started, 1), **data})

    def payload(self, since: int = 0, since_events: int = 0, *, brief: bool = False) -> dict:
        out = {
            "id": self.id, "kind": self.kind, "label": self.label,
            "params": self.params, "status": self.status, "error": self.error,
            "progress": self.progress, "result": self.result,
            "n_lines": len(self.lines), "n_events": len(self.events),
            "started": self.started,
            "seconds": round((self.finished or time.time()) - self.started, 1),
        }
        if not brief:
            out["lines"] = self.lines[since:]
            out["events"] = self.events[since_events:]
        return out


_JOBS: dict[str, Job] = {}
_JOB_LOCK = threading.Lock()


def _running_job() -> Job | None:
    return next((j for j in _JOBS.values() if j.status == "running"), None)


def _start_job(kind: str, label: str, body: Callable[[Job], Any],
               params: dict[str, Any] | None = None) -> Job:
    """Run one model-backed action in the background.

    One at a time, refused rather than queued. Ollama serves this hardware one
    request at a time anyway, so a second job would not run sooner for being
    accepted -- it would only make the interface claim two things were
    happening when one was.
    """
    with _JOB_LOCK:
        live = _running_job()
        if live is not None:
            raise ApiError(
                f"{live.label} is already running ({round(time.time() - live.started)}s "
                f"so far). The local model serves one request at a time, so a second "
                f"job would wait rather than run.", 409)
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label, params=params or {})
        _JOBS[job.id] = job

    def run() -> None:
        try:
            job.result = body(job)
            job.status = "done"
        except Exception as e:                                  # noqa: BLE001
            job.status = "failed"
            job.error = str(e) if isinstance(e, ApiError) else f"{type(e).__name__}: {e}"
            job.emit(job.error)
            job.event("failed", error=job.error)
        finally:
            job.finished = time.time()

    threading.Thread(target=run, daemon=True).start()
    return job


FLEET_STALL_LIMIT = 2
"""Consecutive attacks the model may fail to answer before the fleet stops.

Each unanswered attack has already waited out the model client's own limit
(fifteen minutes), so a third would only confirm that the model is serving
somebody else, at the cost of another quarter of an hour."""


def _fleet_rounds(job: Job, plan: list[tuple[str, str]]) -> tuple[list[str], str]:
    """Run attack rounds, reporting each one as it moves through its phases.

    A round is `proposing` while the model drafts, `adjudicating` while the
    executor runs the facts, then `died`, `known`, `landed`, `malformed` or
    `unanswered`. Only the executor's part holds EXEC_LOCK: the model's minute
    does not touch the tree, and holding the lock through it froze the map and
    the probe for the length of a run.

    Returns the findings recorded and, if the fleet stopped early because the
    model stopped answering, the reason; otherwise an empty string.
    """
    from . import exposure, fleet, plain

    reg = _registry()
    packets: dict[str, str] = {}
    ifaces: dict[str, dict[str, str]] = {}
    tried: dict[str, list[str]] = {}
    found: list[str] = []
    silent = 0
    job.progress = {"stage": "fleet", "done": 0, "total": len(plan)}
    for n, (scope, arch) in enumerate(plan, 1):
        base = {"n": n, "of": len(plan), "scope": scope, "archetype": arch,
                "role": fleet.ARCHETYPES[arch].purpose.rstrip(".")}
        if scope not in packets:
            with executing():
                packets[scope] = fleet.build_packet(scope)
                ifaces[scope] = fleet.scope_interface(reg[scope])
            job.emit(f"packet for {scope}: {len(packets[scope])} characters of clauses, "
                     f"interface and realisable domain. The exposure predicates are not in it.")
        job.event("round", phase="proposing", **base)
        try:
            res = fleet.propose(scope, arch, packets[scope], iface=ifaces[scope],
                                already_tried=tried.get(scope))
        except Exception as e:                                  # noqa: BLE001
            # The model client lets a bare socket timeout through when the
            # local model is busy with somebody else's request for longer than
            # the role's limit. That is one attack that produced nothing, not
            # a failed sweep: record it and go on, unless it keeps happening.
            silent += 1
            why = f"the model did not answer in time ({type(e).__name__}: {e})"
            job.event("round", phase="unanswered", why=why[:300], **base)
            job.emit(f"{n:>3}  {arch:<12} unanswered     {why[:64]}")
            job.progress = {"stage": "fleet", "done": n, "total": len(plan)}
            if silent >= FLEET_STALL_LIMIT:
                return found, (f"the local model did not answer {silent} attacks in a row, "
                               f"so the fleet stopped rather than keep waiting")
            continue
        silent = 0
        if not res.ok:
            job.event("round", phase="malformed", why=res.error[:300],
                      plain_why=plain.failed_attempt(res.kind),
                      usage=str(res.usage), **base)
            job.emit(f"{n:>3}  {arch:<12} no answer      {plain.failed_attempt(res.kind)}")
            job.progress = {"stage": "fleet", "done": n, "total": len(plan)}
            continue
        attack = fleet.to_attack(res.value, arch)
        tried.setdefault(scope, []).append(attack.narrative[:150])
        job.event("round", phase="adjudicating", narrative=attack.narrative,
                  contends=res.value.get("contends", ""), citations=attack.citations,
                  facts=attack.facts, usage=str(res.usage), **base)
        with executing():
            v = exposure.adjudicate(attack, registry=reg)
            rec = exposure.record(attack, v) if v.landed else None
        phase = "landed" if rec else ("known" if v.landed else "died")
        if rec:
            found.append(rec.id)
        job.event("round", phase=phase, klass=v.klass, why=v.why,
                  plain_why=plain.attack_result(v.why),
                  recorded=(rec.id if rec else ""), predicate=v.predicate,
                  amount=(str(v.amount) if v.amount is not None else None),
                  amount_basis=v.amount_basis, **base)
        word = {"landed": f"EXPOSURE FOUND {rec.id if rec else ''}",
                "known": "already known ", "died": "no exposure   "}[phase]
        job.emit(f"{n:>3}  {arch:<12} {word} "
                 f"{plain.attack_result(v.why)[:90] if phase == 'died' else attack.narrative[:40]}")
        job.progress = {"stage": "fleet", "done": n, "total": len(plan)}
    return found, ""


def _job_fleet(job: Job, scope: str, archetype: str | None, rounds: int) -> dict:
    from . import fleet, llm

    llm.require_model(llm.DEFAULT_MODEL)
    archetypes = [archetype] if archetype else sorted(fleet.ARCHETYPES)
    job.emit(f"model {llm.DEFAULT_MODEL}, isolation "
             f"{fleet.ARCHETYPES[archetypes[0]].enforcement()}")
    job.event("stage", key="fleet", title=SWEEP_TITLES["fleet"], status="running",
              note="loading the model's weights")
    llm.warm(llm.DEFAULT_MODEL)
    plan = [(scope, archetypes[i % len(archetypes)]) for i in range(rounds)]
    found, stalled = _fleet_rounds(job, plan)
    summary = f"{rounds} attack{'s' if rounds != 1 else ''}; {len(found)} new on the queue"
    job.event("stage", key="fleet", title=SWEEP_TITLES["fleet"],
              status="failed" if stalled else "done",
              summary=(f"{stalled}; {len(found)} new on the queue" if stalled else summary))
    job.emit(f"{len(found)} new exposure(s) on the queue")
    invalidate()
    return {"found": found, "rounds": rounds}


SWEEP_STAGES = [
    ("operations", "Reconcile what we did against what we wrote"),
    ("rivalries", "Execute both sides of every rivalry"),
    ("reverify", "Re-execute every finding on the queue"),
    ("fleet", "Send the fleet against the rules"),
]
SWEEP_TITLES = dict(SWEEP_STAGES)


def _fleet_targets() -> list[str]:
    """Mapped scopes, the ones an opponent already has a theory about first."""
    from . import exposure
    from . import surface as sf

    preds = exposure.load_predicates()
    reg = _registry()
    keys = [k for k in sf.load_domains() if k in reg]
    return sorted(keys, key=lambda k: (-sum(1 for p in preds if p.scope == k), k))


def _job_sweep(job: Job, rounds: int, scopes: list[str]) -> dict:
    """One unattended pass of the whole engine.

    The deterministic watchers run first because they take seconds and need no
    model: what the company actually did, whether two rules disagree, and
    whether everything already on the queue still reproduces. Then the fleet
    attacks. Each stage reports as it starts and finishes, so the interface is
    watching the engine work rather than waiting on it.
    """
    from . import exposure, fleet, llm
    from . import surface as sf
    from .watchers import check_operations, load_decisions, record_divergences

    reg = _registry()
    job.event("plan", stages=[{"key": k, "title": t} for k, t in SWEEP_STAGES],
              rounds=rounds, scopes=scopes)
    out: dict[str, Any] = {"recorded": [], "found": [], "stale": [], "contradictions": 0}

    def stage(key: str, status: str, **kw: Any) -> None:
        job.event("stage", key=key, title=SWEEP_TITLES[key], status=status, **kw)
        if kw.get("summary"):
            job.emit(f"{SWEEP_TITLES[key]}: {kw['summary']}")

    # 1. operations. Divergences are not deduplicated by the recorder (ten
    # people underpaid by one clause are ten facts), so a repeat sweep filters
    # out the decisions it has already put on the queue.
    stage("operations", "running")
    ds = load_decisions()
    with executing():
        divs = check_operations(ds, registry=reg)
    queued = {(e.origin, e.headline) for e in exposure.load_queue()}
    fresh = [d for d in divs if (f"operations:{d.decision.id}", d.headline) not in queued]
    made = record_divergences(fresh) if fresh else []
    agreeing = len(ds) - len({d.decision.id for d in divs})
    out["recorded"] = [e.id for e in made]
    stage("operations", "done",
          summary=(f"{len(ds)} decisions executed: {agreeing} agree with the policy, "
                   f"{len(divs)} diverge, {len(made)} new on the queue"),
          counts={"decisions": len(ds), "agreeing": agreeing,
                  "divergences": len(divs), "new": len(made)},
          recorded=out["recorded"])

    # 2. rivalries
    stage("rivalries", "running")
    with executing():
        cs = exposure.check_rivalries(registry=reg)
    n_riv = len(exposure.load_rivalries())
    out["contradictions"] = len(cs)
    stage("rivalries", "done",
          summary=(f"{n_riv} pair{'s' if n_riv != 1 else ''} of rules executed on both "
                   f"sides, {len(cs)} contradiction{'s' if len(cs) != 1 else ''}"),
          counts={"rivalries": n_riv, "contradictions": len(cs)},
          contradictions=[c.headline for c in cs][:20])

    # 3. re-verify, the same test as gate 9, reported one finding at a time
    queue = [e for e in exposure.load_queue() if e.status == "open"]
    stage("reverify", "running", total=len(queue))
    live = {(f"operations:{d.decision.id}", d.headline) for d in divs}
    doms, preds = sf.load_domains(), exposure.load_predicates()
    job.progress = {"stage": "reverify", "done": 0, "total": len(queue)}
    for i, e in enumerate(queue, 1):
        if e.klass == exposure.Klass.DIVERGENCE:
            ok = (e.origin, e.headline) in live
            why = ("the record still diverges from the policy" if ok
                   else "the record no longer diverges from the policy")
        else:
            with executing():
                v = exposure.adjudicate(
                    exposure.Attack(e.archetype, e.scope, e.facts, origin="sweep"),
                    registry=reg, domains=doms, predicates=preds)
            ok = v.landed and v.klass == e.klass
            why = "executed again and reached the same outcome" if ok else v.why
        if not ok:
            out["stale"].append(e.id)
        job.event("verify", id=e.id, klass=e.klass, scope=e.scope, ok=ok, why=why,
                  n=i, of=len(queue))
        job.progress = {"stage": "reverify", "done": i, "total": len(queue)}
    stale = out["stale"]
    stage("reverify", "done",
          summary=(f"{len(queue) - len(stale)} of {len(queue)} still reproduce"
                   + (f"; {', '.join(stale)} no longer do" if stale else "")),
          counts={"checked": len(queue), "stale": len(stale)})

    # 4. the fleet
    if rounds <= 0:
        stage("fleet", "skipped", summary="no attack rounds were asked for")
        invalidate()
        return out
    try:
        llm.require_model(llm.DEFAULT_MODEL)
    except llm.ModelUnavailable as ex:
        stage("fleet", "skipped",
              summary=f"the local model is not available, so nothing was proposed: {ex}")
        invalidate()
        return out
    targets = scopes or _fleet_targets()
    archs = sorted(fleet.ARCHETYPES)
    plan = [(targets[i % len(targets)], archs[i % len(archs)]) for i in range(rounds)]
    stage("fleet", "running", note="loading the model's weights", total=rounds,
          model=llm.DEFAULT_MODEL,
          isolation=fleet.ARCHETYPES[archs[0]].enforcement())
    llm.warm(llm.DEFAULT_MODEL)
    out["found"], stalled = _fleet_rounds(job, plan)
    if stalled:
        stage("fleet", "failed",
              summary=f"{stalled}; {len(out['found'])} new on the queue before it stopped")
        invalidate()
        return out
    stage("fleet", "done",
          summary=(f"{rounds} attack{'s' if rounds != 1 else ''} across "
                   f"{len({s for s, _ in plan})} rule{'s' if len({s for s, _ in plan}) != 1 else ''}; "
                   f"{len(out['found'])} new on the queue"),
          counts={"rounds": rounds, "found": len(out["found"])})
    invalidate()
    return out


def _find_exposure(exposure_id: str):
    from . import exposure as ex

    e = next((x for x in ex.load_queue() if x.id == exposure_id), None)
    if e is None:
        raise ApiError(f"no exposure {exposure_id!r}", 404)
    return e


def _check_letter(exposure_id: str) -> None:
    """Refuse a letter that has nobody to be addressed to, before a job starts.

    Checked here rather than inside the job so the answer is immediate and no
    slot is consumed: a job that fails on a precondition looks like a failure
    of the model, and it locks out the next request for as long as it takes to
    find that out.
    """
    e = _find_exposure(exposure_id)
    if e.headline_direction == "more":
        raise ApiError(
            f"{e.id} runs the other way: the decision was MORE favourable to the "
            f"other side than the policy provides, so there is nobody to write to "
            f"and nothing to demand. It is still evidence — that this company does "
            f"not apply the construction it would need to refuse somebody else — "
            f"but a letter is the wrong instrument for it.")


def _job_letter(job: Job, exposure_id: str) -> dict:
    from . import exposure as ex
    from .fleet import demand_letter

    e = _find_exposure(exposure_id)
    job.emit(f"drafting from {e.id}; every figure is passed in computed and the "
             f"role is forbidden to produce one of its own")
    job.event("stage", key="draft", title="Draft the letter from the other side",
              status="running", note="every figure is passed in computed")
    r = demand_letter(e)
    if not r.ok:
        raise ApiError(f"the role produced nothing usable: {r.error}")
    e.demand_letter = r.value
    ex.save_exposure(e)
    job.emit(f"drafted and saved to exposure/queue/{e.id}.yaml ({r.usage})")
    job.event("stage", key="draft", title="Draft the letter from the other side",
              status="done", summary=f"saved to exposure/queue/{e.id}.yaml ({r.usage})")
    return {"id": e.id, "letter": r.value}


def _job_close(job: Job, exposure_id: str) -> dict:
    from .remedy import close, propose_closing_edit

    e = _find_exposure(exposure_id)
    job.emit(f"asking for the smallest edit that closes {e.id}…")
    job.event("stage", key="propose", title="Propose the smallest closing edit",
              status="running")
    r = propose_closing_edit(e)
    if not r.ok:
        raise ApiError(f"the role produced nothing usable: {r.error}")
    edit = r.value
    job.event("stage", key="propose", title="Propose the smallest closing edit",
              status="done", summary=str(r.usage))
    job.emit("proposed. Applying it to the module, rebuilding, re-executing every "
             "mapped scope and every recorded counterexample, then putting it back…")
    job.event("stage", key="measure",
              title="Apply it, rebuild, re-execute everything, put it back",
              status="running")
    with executing():
        cf = close(e, edit)
    for row in cf.report():
        job.emit(row)
    if cf.error:
        job.emit(f"note: {cf.error}")
    job.event("stage", key="measure",
              title="Apply it, rebuild, re-execute everything, put it back",
              status="done" if cf.ok else "failed", summary="; ".join(cf.report()[:3]))
    invalidate()
    return {
        "id": e.id, "edit": edit, "ok": cf.ok, "report": cf.report(),
        "closes": cf.closes, "error": cf.error,
        "diffs": [
            {"scope": d.scope, "summary": d.summary(), "touched": d.touched,
             "worst": (None if d.worst is None else {
                 "inputs": d.worst.inputs, "kind": d.worst.kind,
                 "before": d.worst.before_value, "after": d.worst.after_value})}
            for d in cf.diffs
        ],
    }


JOB_KINDS = {
    "sweep": ("autonomous sweep", _job_sweep, None),
    "fleet": ("run the attack fleet", _job_fleet, None),
    "letter": ("draft the demand letter", _job_letter, _check_letter),
    "close": ("propose a fix and measure it", _job_close, _find_exposure),
}


def sf_domains() -> dict:
    from . import surface as sf
    return sf.load_domains()


def ep_exposure_run(body: Any, _q: dict) -> dict:
    if not isinstance(body, dict):
        raise ApiError("expected a JSON object")
    kind = str(body.get("kind", ""))
    if kind not in JOB_KINDS:
        raise ApiError(f"unknown action {kind!r}; expected one of {sorted(JOB_KINDS)}")
    label, fn, precheck = JOB_KINDS[kind]
    if kind == "sweep":
        rounds = max(0, min(int(body.get("rounds", 6)), 50))
        scopes = [s for s in (body.get("scopes") or []) if s]
        unknown = [s for s in scopes if s not in sf_domains()]
        if unknown:
            raise ApiError(f"no declared realisable domain for {unknown}", 404)
        job = _start_job(kind, label, lambda j: fn(j, rounds, scopes),
                         params={"rounds": rounds, "scopes": scopes})
    elif kind == "fleet":
        from . import fleet as _fleet

        scope = body.get("scope")
        if not scope:
            raise ApiError("scope is required")
        if scope not in sf_domains():
            raise ApiError(f"{scope} has no declared realisable domain, so the fleet "
                           f"has nothing to tell an archetype about who exists", 404)
        rounds = max(1, min(int(body.get("rounds", 5)), 50))
        arch = body.get("archetype") or None
        if arch and arch not in _fleet.ARCHETYPES:
            raise ApiError(f"unknown archetype {arch!r}; expected one of "
                           f"{sorted(_fleet.ARCHETYPES)}")
        job = _start_job(kind, f"{label} against {scope}",
                         lambda j: fn(j, scope, arch, rounds),
                         params={"scope": scope, "archetype": arch, "rounds": rounds})
    else:
        eid = body.get("id")
        if not eid:
            raise ApiError("id is required")
        if precheck is not None:
            precheck(eid)          # raises before a slot is taken
        job = _start_job(kind, f"{label} for {eid}", lambda j: fn(j, eid),
                         params={"id": eid})
    return job.payload()


def ep_exposure_job(_body: Any, q: dict) -> dict:
    jid = q.get("id")
    if jid:
        job = _JOBS.get(jid)
        if job is None:
            raise ApiError(f"no job {jid!r}", 404)
        return job.payload(int(q.get("since", 0) or 0), int(q.get("since_events", 0) or 0))
    live = _running_job()
    return {"running": (live.payload(brief=True) if live else None)}


def ep_exposure_jobs(_body: Any, _q: dict) -> dict:
    """Jobs this server has run, newest first. Held in memory only: a restart
    forgets the runs, never the findings, which are on disk."""
    jobs = sorted(_JOBS.values(), key=lambda j: j.started, reverse=True)[:12]
    return {"jobs": [j.payload(brief=True) for j in jobs]}


# --- document generation --------------------------------------------------

GENERATED = ROOT / "generated"
_RUN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")


@dataclass
class RawResponse:
    """A non-JSON body. Every other endpoint returns JSON; the generated PDF
    cannot be, and base64 inside JSON would make the browser unable to open it."""
    data: bytes
    ctype: str


def _run_dir(name: str) -> Path:
    if not name or not _RUN_NAME_RE.fullmatch(name):
        raise ApiError("name must be the name of a run under generated/")
    d = (GENERATED / name).resolve()
    if d.parent != GENERATED.resolve() or not d.is_dir():
        raise ApiError(f"no generation run {name!r}", 404)
    return d


def _run_summary(d: Path) -> dict:
    try:
        r = json.loads((d / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"name": d.name, "passed": False, "finished": False,
                "modified": (d.stat().st_mtime if d.exists() else 0)}
    return {
        "name": d.name, "finished": True, "passed": bool(r.get("passed")),
        "doc_id": r.get("doc_id", ""), "title": r.get("title", ""),
        "request": (r.get("prompt") or "")[:300],
        "failure_stage": r.get("failure_stage", ""),
        "failure_reason": (r.get("failure_reason") or "")[:300],
        "catala_iterations": r.get("catala_iterations", 0),
        "screen_rounds": r.get("screen_rounds", 0),
        "seconds": r.get("seconds", 0),
        "has_pdf": (d / "document.pdf").exists(),
        "modified": (d / "run.json").stat().st_mtime,
    }


def _job_generate(job: Job, request: str, opts: dict) -> dict:
    """The whole pipeline, as one background job.

    It does not hold EXEC_LOCK. That lock serialises execution against edits to
    the committed modules, and a run can take an hour: holding it would freeze
    every other view for that hour. Nothing a run executes is in the tree --
    drafts are compiled out of tree and may not `> Using` a committed module --
    so the counterfactual's edits cannot change what a run computes. What a run
    does read from the tree is the compiled standard library under `_build`,
    which clerk rewrites; `catala_runner.BUILD_LOCK` keeps each execution from
    loading it mid-build.
    """
    from . import generate as gen

    name = gen.slugify(request)
    limits = gen.Limits(
        catala_attempts=opts["catala_attempts"], screen_rounds=opts["screen_rounds"],
        roundtrip=not opts["no_roundtrip"], emit_failed_pdf=opts["emit_failed_pdf"],
    )
    job.progress = {"stage": "retrieve", "name": name}

    def on_stage(ev: dict) -> None:
        # key, title, status (running | done | failed), summary
        job.event("stage", **ev)
        job.progress = {"stage": ev["key"], "title": ev["title"],
                        "status": ev["status"], "name": name}

    r = gen.generate(request, limits=limits, workspace=GENERATED / name,
                     emit=job.emit, on_stage=on_stage,
                     effective_date=opts["effective_date"])
    job.event("done", passed=r.passed)
    return {
        "name": name, "passed": r.passed, "doc_id": r.doc_id, "title": r.title,
        "failure_stage": r.failure_stage, "failure_reason": r.failure_reason,
        "seconds": round(r.seconds),
        "pdf_url": f"/api/generate/pdf?name={name}" if r.pdf else None,
        "run_url": f"/api/generate/run?name={name}",
    }


def ep_generate(body: Any, _q: dict) -> dict:
    if not isinstance(body, dict):
        raise ApiError("expected a JSON object")
    request = str(body.get("request", "")).strip()
    if len(request) < 12:
        raise ApiError("describe the document to draft in a sentence or more")
    if len(request) > 8000:
        raise ApiError("the request is longer than 8,000 characters")

    def clamp(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(int(body.get(key, default)), hi))
        except (TypeError, ValueError):
            raise ApiError(f"{key} must be a number") from None

    eff = body.get("effective_date")
    if eff not in (None, "") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(eff)):
        raise ApiError("effective_date must be YYYY-MM-DD")

    opts = {
        "effective_date": str(eff) if eff else None,
        "catala_attempts": clamp("catala_attempts", 3, 1, 6),
        "screen_rounds": clamp("screen_rounds", 2, 1, 4),
        "no_roundtrip": bool(body.get("no_roundtrip", False)),
        "emit_failed_pdf": bool(body.get("emit_failed_pdf", False)),
    }
    job = _start_job("generate", "generate a document",
                     lambda j: _job_generate(j, request, opts),
                     params={"request": request[:200], **opts})
    return job.payload()


def ep_generate_job(_body: Any, q: dict) -> dict:
    jid = q.get("id")
    if not jid:
        live = _running_job()
        return {"running": live.payload(brief=True) if live and live.kind == "generate" else None}
    job = _JOBS.get(jid)
    if job is None or job.kind != "generate":
        raise ApiError(f"no generation job {jid!r}", 404)
    return job.payload(int(q.get("since", 0) or 0), int(q.get("since_events", 0) or 0))


def ep_generate_runs(_body: Any, _q: dict) -> dict:
    if not GENERATED.is_dir():
        return {"runs": []}
    dirs = [d for d in GENERATED.iterdir() if d.is_dir() and _RUN_NAME_RE.fullmatch(d.name)]
    runs = sorted((_run_summary(d) for d in dirs), key=lambda r: -r["modified"])
    return {"runs": runs[:100]}


def ep_generate_run(_body: Any, q: dict) -> dict:
    d = _run_dir(q.get("name", ""))
    try:
        run = json.loads((d / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ApiError("this run has not finished, or its record is unreadable", 409) from None
    report = (d / "report.md").read_text(encoding="utf-8") if (d / "report.md").exists() else ""
    return {"run": run, "report": report, "summary": _run_summary(d)}


def ep_generate_pdf(_body: Any, q: dict) -> RawResponse:
    d = _run_dir(q.get("name", ""))
    pdf = d / "document.pdf"
    if not pdf.is_file():
        raise ApiError("this run issued no PDF", 404)
    return RawResponse(pdf.read_bytes(), "application/pdf")


# --- the contradiction watch ----------------------------------------------
#
# Started by `serve`, so it runs for as long as the server start.sh brings up
# does. Each check is an ordinary job: it waits its turn on the local model and
# a person's job waits for it, the same one-at-a-time rule as everything else.

_WATCHER: Any = None


def _launch_watch_job(label: str, params: dict[str, Any], body: Callable) -> bool:
    try:
        _start_job("doc-watch", label, lambda job: body(job.emit), params)
    except ApiError as e:
        if e.status == 409:
            return False
        raise
    return True


def start_watcher() -> Any:
    global _WATCHER
    if _WATCHER is None:
        from .doc_watch import Watcher
        _WATCHER = Watcher(launcher=_launch_watch_job).start()
    return _WATCHER


def _watcher() -> Any:
    if _WATCHER is None:
        raise ApiError("the contradiction watch is not running in this server "
                       "(LKS_DOC_WATCH=0)", 503)
    return _WATCHER


def ep_watch(_body: Any, q: dict) -> dict:
    w = _watcher()
    out = w.status()
    try:
        out["checks"] = w.checks(q.get("key") or None, limit=int(q.get("limit", 10) or 10))
    except Exception as e:                                      # noqa: BLE001
        out["checks"], out["checks_error"] = [], f"{type(e).__name__}: {e}"
    return out


def ep_watch_recheck(body: Any, _q: dict) -> dict:
    key = str((body or {}).get("key") or "")
    if not key:
        raise ApiError("give the document's `key`, e.g. index:EMP-ANNEX-C")
    if not _watcher().requeue(key):
        raise ApiError(f"{key!r} is unknown, already being checked, removed or unreadable", 404)
    return {"key": key, "status": "pending"}


ROUTES: dict[tuple[str, str], Callable[[Any, dict], Any]] = {
    ("GET", "/api/watch"): ep_watch,
    ("POST", "/api/watch/recheck"): ep_watch_recheck,
    ("GET", "/api/state"): ep_state,
    ("POST", "/api/ask"): ep_ask,
    ("GET", "/api/scopes"): ep_scopes,
    ("GET", "/api/scope"): ep_scope,
    ("POST", "/api/run"): ep_run,
    ("GET", "/api/hierarchy"): ep_hierarchy,
    ("POST", "/api/explain"): ep_explain,
    ("GET", "/api/agent"): ep_agent_status,
    ("POST", "/api/slotfill"): ep_slotfill,
    ("GET", "/api/clause"): ep_clause,
    ("GET", "/api/verification"): ep_verification,
    ("GET", "/api/proposals"): ep_proposals,
    ("POST", "/api/resolve"): ep_resolve,
    ("GET", "/api/exposure/scopes"): ep_exposure_scopes,
    ("GET", "/api/exposure/map"): ep_exposure_map,
    ("GET", "/api/exposure/queue"): ep_exposure_queue,
    ("GET", "/api/exposure/item"): ep_exposure_item,
    ("GET", "/api/exposure/operations"): ep_exposure_operations,
    ("POST", "/api/exposure/operations"): ep_exposure_operations,
    ("GET", "/api/exposure/holdings"): ep_exposure_holdings,
    ("POST", "/api/exposure/adjudicate"): ep_exposure_adjudicate,
    ("POST", "/api/exposure/whatif"): ep_exposure_whatif,
    ("GET", "/api/exposure/schema"): ep_exposure_schema,
    ("GET", "/api/exposure/contradictions"): ep_exposure_contradictions,
    ("POST", "/api/exposure/run"): ep_exposure_run,
    ("GET", "/api/exposure/job"): ep_exposure_job,
    ("GET", "/api/exposure/jobs"): ep_exposure_jobs,
    ("POST", "/api/generate"): ep_generate,
    ("GET", "/api/generate/job"): ep_generate_job,
    ("GET", "/api/generate/runs"): ep_generate_runs,
    ("GET", "/api/generate/run"): ep_generate_run,
    ("GET", "/api/generate/pdf"): ep_generate_pdf,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "lks"

    def log_message(self, fmt: str, *args: Any) -> None:
        if "/api/" in (args[0] if args else ""):
            print(f"  {args[0]}")

    def _send(self, status: int, payload: Any, ctype: str = "application/json") -> None:
        if ctype == "application/json":
            data = json.dumps(payload).encode("utf-8")
        else:
            data = payload
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def _cross_site(self, method: str) -> str:
        """Why this request cannot have come from this interface, or "".

        Bound to loopback is not the same as reachable only from this
        interface: any page open in the browser can send a request to
        127.0.0.1. A text/plain POST needs no preflight, so without this a
        hostile page could record a "human" conflict resolution -- the one act
        the merge gate exists to require of a person -- or write to a real
        module through the counterfactual. A foreign Host is DNS rebinding;
        a foreign Origin is another site; a POST that is not JSON is the only
        shape a cross-site form or beacon can send without a preflight this
        server never answers.
        """
        bound, port = self.server.server_address[:2]
        local = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"{bound}:{port}"}
        host = (self.headers.get("Host") or "").lower()
        if host not in local:
            return f"Host {host!r} is not this interface."
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {f"http://{h}" for h in local}:
            return f"Origin {origin!r} is not this interface."
        if method == "POST":
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return "POST bodies must be sent as application/json."
        return ""

    def _handle(self, method: str) -> None:
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._static(u.path)
        why = self._cross_site(method)
        if why:
            return self._send(403, {"error": why})
        q = {}
        for pair in (u.query or "").split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                q[k] = unquote(v.replace("+", " "))
        body = None
        if method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400, {"error": "Content-Length is not a number."})
            raw = self.rfile.read(n) if n > 0 else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return self._send(400, {"error": "Request body is not valid JSON."})
        fn = ROUTES.get((method, u.path))
        if fn is None:
            return self._send(404, {"error": f"No endpoint {method} {u.path}."})
        try:
            out = fn(body, q)
            if isinstance(out, RawResponse):
                self._send(200, out.data, out.ctype)
            else:
                self._send(200, out)
        except ApiError as e:
            self._send(e.status, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB / rel).resolve()
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            target = WEB / "index.html"
            if not target.is_file():
                return self._send(404, {"error": "Web interface not built."})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Legal Knowledge System — http://{host}:{port}")
    print("Warming the rule registry and the vector store…")
    try:
        ep_state(None, {})
        print("Ready.")
    except Exception as e:
        print(f"Warm-up incomplete: {type(e).__name__}: {e}")
    import os
    if os.environ.get("LKS_DOC_WATCH", "1") != "0":
        w = start_watcher()
        print(f"Contradiction watch: scanning MongoDB every {w.interval}s.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
