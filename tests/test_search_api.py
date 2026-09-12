#!/usr/bin/env python3
"""Tests for lks.search_api, the read-only search server for sandboxed agents.

    . scripts/env.sh && $PY tests/test_search_api.py

Plain asserts and a `__main__` that runs them all, matching tests/test_extract.py.

The load-bearing tests are the refusals. This server is reachable from outside
the loopback guard that protects the web API's writes, so what matters most is
what it will not do:

  * `test_only_three_routes` -- no /api/* endpoint is reachable through it
  * `test_search_takes_no_facts` -- an agent cannot choose inputs or a scope
  * `test_foreign_host_refused` / `test_foreign_origin_refused`
  * `test_wildcard_bind_refused`
"""
from __future__ import annotations

import http.client
import sys
import threading
import traceback
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lks import api                                             # noqa: E402
from lks import search_api as S                                 # noqa: E402

_SERVER = None


def server():
    global _SERVER
    if _SERVER is None:
        _SERVER = S.make_server("127.0.0.1", 0)
        threading.Thread(target=_SERVER.serve_forever, daemon=True).start()
    return _SERVER


def get(path: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    port = server().server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    conn.request("GET", path, headers=headers or {})
    r = conn.getresponse()
    body = r.read().decode("utf-8")
    conn.close()
    return r.status, body


# --- refusals ---------------------------------------------------------------


def test_only_three_routes():
    for path in ("/", "/api/state", "/api/ask", "/api/generate", "/api/resolve",
                 "/api/exposure/whatif", "/search/../api/state"):
        status, body = get(path)
        assert status == 404, (path, status, body)
    port = server().server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("POST", "/search", body=b"{}", headers={"Content-Type": "application/json"})
    status = conn.getresponse().status
    conn.close()
    assert status == 501, f"POST must not be handled at all, got {status}"


def test_search_takes_no_facts():
    for qs in ("q=overtime&inputs=%7B%7D", "q=overtime&scope=Overtime.HourPremium",
               "q=a&q=b", "q=", "question=overtime"):
        status, body = get(f"/search?{qs}")
        assert status == 400, (qs, status, body)


def test_foreign_host_refused():
    status, body = get("/health", {"Host": "evil.example:80"})
    assert status == 403, (status, body)
    port = server().server_address[1]
    status, body = get("/health", {"Host": f"{S.SANDBOX_HOST}:{port}"})
    assert status == 200 and body == "ok\n", (status, body)


def test_foreign_origin_refused():
    status, body = get("/health", {"Origin": "http://evil.example"})
    assert status == 403, (status, body)


def test_wildcard_bind_refused():
    for host in ("0.0.0.0", "::", ""):
        try:
            S.make_server(host, 0)
        except ValueError:
            continue
        raise AssertionError(f"bound {host!r}")


def test_overlong_question_refused():
    status, body = get("/search?q=" + "a" * (S.MAX_QUESTION + 1))
    assert status == 400, (status, body)


# --- what it relays ---------------------------------------------------------


def test_clause_is_verbatim():
    d = api._corpus()[0]
    c = d.clauses[0]
    status, body = get(f"/clause?ref={quote(c.ref)}")
    assert status == 200, (status, body)
    assert c.cite() in body.splitlines()[0], body
    assert c.body in body, "the clause body must appear exactly as in the corpus"


def test_unknown_clause_is_404():
    status, body = get("/clause?ref=NO-SUCH-DOC%20X-0")
    assert status == 404, (status, body)


def test_search_is_the_rendered_answer():
    try:
        chat = api._chat()
    except Exception as e:
        return f"vector store unavailable: {type(e).__name__}: {e}"
    question = "what do our documents say about data retention?"
    status, body = get(f"/search?q={quote(question)}")
    assert status == 200, (status, body)
    assert body == chat.answer(question).render(), "search must relay Answer.render() unchanged"
    assert body.startswith(f"Q: {question}"), body[:200]
    assert "[VECTOR" in body or "[CATALA" in body, body[:400]


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    failed: list[tuple[str, str]] = []
    skipped: list[str] = []
    for fn in TESTS:
        try:
            note = fn()
            if note:
                skipped.append(f"{fn.__name__}: {note}")
                print(f"skip {fn.__name__}  ({note})")
            else:
                print(f"ok   {fn.__name__}")
        except Exception:
            failed.append((fn.__name__, traceback.format_exc()))
            print(f"FAIL {fn.__name__}")
    if _SERVER is not None:
        _SERVER.shutdown()
    print()
    for name, tb in failed:
        print(f"--- {name} ---\n{tb}")
    print(f"{len(TESTS) - len(failed) - len(skipped)} passed, {len(skipped)} skipped, "
          f"{len(failed)} failed, of {len(TESTS)} tests")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
