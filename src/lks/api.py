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
import traceback
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
    return {
        "question": ans.question,
        "engines": ans.engines_used,
        "parts": [
            {
                "engine": p.engine, "kind": p.kind, "text": p.text,
                "citations": p.citations, "scope": p.scope,
                "outputs": p.outputs, "inputs": p.inputs,
                "score": round(p.score, 3) if p.score is not None else None,
            }
            for p in ans.parts
        ],
    }


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
    from .counterexample import load_all, summary
    from .registry import coverage
    ces = [
        {
            "id": ce.id, "status": ce.status, "round": ce.round,
            "component": ce.component,
            "module": ce.target.get("module"), "scope": ce.target.get("scope"),
            "fact_pattern": ce.fact_pattern, "citations": ce.citations,
            "expected": ce.expected, "observed": ce.observed,
            "source_reasoning": ce.source_reasoning, "discovered": ce.discovered,
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


ROUTES: dict[tuple[str, str], Callable[[Any, dict], Any]] = {
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

    def _handle(self, method: str) -> None:
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._static(u.path)
        q = {}
        for pair in (u.query or "").split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                q[k] = unquote(v.replace("+", " "))
        body = None
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return self._send(400, {"error": "Request body is not valid JSON."})
        fn = ROUTES.get((method, u.path))
        if fn is None:
            return self._send(404, {"error": f"No endpoint {method} {u.path}."})
        try:
            self._send(200, fn(body, q))
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
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
