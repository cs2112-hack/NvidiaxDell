"""Read-only document search for agents outside this process.

    lks serve-search --host 172.18.0.1 --port 8770

The web API (`lks.api`) is bound to loopback and refuses any Host that is not
this interface, because some of its endpoints change things: recording a
conflict resolution, writing a counterfactual edit to a real module. An
OpenClaw agent in an OpenShell sandbox reaches the host as
`host.openshell.internal`, so it could only use that API if the guard
protecting those writes were loosened. This server exists so it never is.

Three routes, all GET, all plain text, and nothing else:

    GET /search?q=<question>     the answer, exactly as `lks ask` prints it
    GET /clause?ref=<DOC CLAUSE> one clause verbatim, exactly as `lks cite` prints it
    GET /health

Nothing here answers anything. /search is `Chat.answer` on the question alone.
It takes no `inputs` and no `scope`, so an agent cannot choose the facts a rule
is executed on: a rule question comes back naming the scope and the inputs it
needs. The body is `Answer.render()` with its engine labels and citations, so
an agent has exact text to relay rather than a structure to paraphrase.
"""
from __future__ import annotations

import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import api

SANDBOX_HOST = "host.openshell.internal"
"""The name an OpenShell sandbox uses for this machine."""

MAX_QUESTION = 2000

WILDCARD_BINDS = {"", "0.0.0.0", "::", "[::]"}


class Refused(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _one(q: dict[str, list[str]], name: str, allowed: set[str]) -> str:
    extra = sorted(set(q) - allowed)
    if extra:
        raise Refused(f"Unknown parameter(s): {', '.join(extra)}. "
                      f"Accepted: {', '.join(sorted(allowed))}.")
    vals = q.get(name) or []
    if len(vals) != 1 or not vals[0].strip():
        raise Refused(f"Exactly one non-empty `{name}` is required.")
    return vals[0].strip()


def search(q: dict[str, list[str]]) -> str:
    question = _one(q, "q", {"q"})
    if len(question) > MAX_QUESTION:
        raise Refused(f"The question is longer than {MAX_QUESTION} characters.")
    return api._chat().answer(question).render()


def clause(q: dict[str, list[str]]) -> str:
    ref = _one(q, "ref", {"ref"})
    for d in api._corpus():
        for c in d.clauses:
            if c.ref == ref:
                return (f"{c.cite()}  [{d.title} v{d.version}, effective {d.effective_date}]\n"
                        f"Section {c.section_id} {c.section_title}\n\n{c.body}\n")
    raise Refused(f"No clause {ref!r} in the corpus.", 404)


def health(_q: dict[str, list[str]]) -> str:
    return "ok\n"


ROUTES: dict[str, Callable[[dict[str, list[str]]], str]] = {
    "/search": search,
    "/clause": clause,
    "/health": health,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "lks-search"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"  {self.address_string()} {fmt % args}")

    def _send(self, status: int, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def _refusal(self) -> str:
        """Why this request is not from this machine or a sandbox on it, or "".

        The same Host and Origin checks as `lks.api`, with one name added: the
        one a sandbox uses. A foreign Host is DNS rebinding; a foreign Origin
        is a page in a browser.
        """
        bound, port = self.server.server_address[:2]
        allowed = {f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]", bound, SANDBOX_HOST)}
        host = (self.headers.get("Host") or "").lower()
        if host not in allowed:
            return f"Host {host!r} is not this interface."
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {f"http://{h}" for h in allowed}:
            return f"Origin {origin!r} is not this interface."
        return ""

    def do_GET(self) -> None:
        why = self._refusal()
        if why:
            return self._send(403, why + "\n")
        u = urlparse(self.path)
        fn = ROUTES.get(u.path)
        if fn is None:
            return self._send(404, f"No endpoint {u.path}. This server answers "
                                   f"GET /search, /clause and /health only.\n")
        try:
            self._send(200, fn(parse_qs(u.query, keep_blank_values=True)))
        except Refused as e:
            self._send(e.status, f"{e}\n")
        except Exception as e:
            traceback.print_exc()
            self._send(500, f"{type(e).__name__}: {e}\n")


def make_server(host: str = "127.0.0.1", port: int = 8770) -> ThreadingHTTPServer:
    """Bind the search server. A wildcard bind is refused: the point is to be
    reachable from this machine's sandboxes, not from its network."""
    if host.strip() in WILDCARD_BINDS:
        raise ValueError(
            f"Refusing to bind {host!r}: that exposes search to the network. Bind "
            f"127.0.0.1, or the OpenShell bridge address that {SANDBOX_HOST} "
            f"resolves to inside a sandbox."
        )
    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8770) -> None:
    httpd = make_server(host, port)
    print(f"Legal Knowledge System search (read-only) — http://{host}:{port}")
    print("  GET /search?q=…   GET /clause?ref=…   GET /health")
    print("Warming the vector store…")
    try:
        api._chat()
        print("Ready.")
    except Exception as e:
        print(f"Warm-up incomplete: {type(e).__name__}: {e}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
