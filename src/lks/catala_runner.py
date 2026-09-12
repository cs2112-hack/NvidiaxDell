"""The only way this system is allowed to get an answer out of Catala.

The brief's requirement -- "answer from Catala by executing the scope, not by
reading the code" -- is an architectural constraint, not a style preference, so
it is enforced by making execution the only available affordance. Nothing in
this package parses Catala source to predict what it computes. If the compiler
cannot run it, the answer is an error, not a guess.

Verified facts about Catala 1.2.1 that shape this module:
  * `catala interpret` alone fails with "Compiled OCaml object ... not found":
    the standard library must be staged by `clerk start` and built by clerk.
    So execution goes through `clerk run`, from the project root.
  * `-F json --quiet` yields the scope's output struct as pure JSON.
  * `catala exceptions -s S -v V -F json` is the only structured (non-
    pretty-printed) view of the exception hierarchy. See docs/DECISIONS.md D-1.
  * Exit code 123 means "reported errors"; 124 CLI misuse; 125 internal bug.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OPAM_SWITCH = Path.home() / ".opam" / "lks"


class CatalaNotInstalled(RuntimeError):
    pass


class CatalaError(RuntimeError):
    """A compiler or runtime error from Catala, with its diagnostic text."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "", code: int = 0):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.code = code

    @property
    def diagnostic(self) -> str:
        return (self.stderr or self.stdout).strip()


class ScopeConflict(CatalaError):
    """Two applicable definitions with no priority between them.

    This is Catala telling us the encoding is ambiguous for these inputs --
    almost always because sibling exceptions have overlapping conditions. It
    is a genuine finding about the encoding (or the source document), never
    something to swallow and retry.
    """


class NoApplicableRule(CatalaError):
    """No definition applies for these inputs. Usually a missing base case."""


class AssertionFailed(CatalaError):
    """An input-domain or internal assertion was violated.

    Distinguished from other errors because it means something different: the
    module was asked a question it has established cannot arise -- a negative
    count of meals provided free, an hour outside the 168 of a Payroll Week --
    and declined to answer. For a legal system that is the correct outcome, not
    a malfunction: computing a figure from an impossible premise is how silent
    over-payments happen. Callers and counterexamples can therefore assert
    "this input is refused" as a legitimate expected result.
    """


def _env() -> dict[str, str]:
    env = os.environ.copy()
    bindirs = [str(OPAM_SWITCH / "bin"), str(Path.home() / ".local" / "bin")]
    env["PATH"] = os.pathsep.join(bindirs + [env.get("PATH", "")])
    env["OPAM_SWITCH_PREFIX"] = str(OPAM_SWITCH)
    env["CAML_LD_LIBRARY_PATH"] = str(OPAM_SWITCH / "lib" / "stublibs")
    env["OCAMLPATH"] = str(OPAM_SWITCH / "lib")
    env.setdefault("CATALA_COLOR", "never")
    return env


@lru_cache(maxsize=1)
def toolchain() -> dict[str, str]:
    env = _env()
    found = {}
    for exe in ("catala", "clerk"):
        p = shutil.which(exe, path=env["PATH"])
        if not p:
            raise CatalaNotInstalled(
                f"{exe} not found. Install with: ./scripts/install_catala.sh"
            )
        found[exe] = p
    ver = subprocess.run(
        [found["catala"], "--version"], capture_output=True, text=True, env=env
    ).stdout.strip()
    found["version"] = ver
    return found


def _run(args: list[str], *, cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(cwd or REPO_ROOT),
        input=stdin,
        timeout=300,
    )


# --- error classification --------------------------------------------------

CONFLICT_RE = re.compile(r"conflict between multiple valid consequences", re.I)
NOVALUE_RE = re.compile(r"no applicable rule to define this variable", re.I)
ASSERT_RE = re.compile(r"assertion (?:failed|doesn't hold)", re.I)


def _classify(msg: str, proc: subprocess.CompletedProcess) -> CatalaError:
    blob = (proc.stderr or "") + (proc.stdout or "")
    kw = {"stdout": proc.stdout, "stderr": proc.stderr, "code": proc.returncode}
    if CONFLICT_RE.search(blob):
        return ScopeConflict(msg, **kw)
    if NOVALUE_RE.search(blob):
        return NoApplicableRule(msg, **kw)
    if ASSERT_RE.search(blob):
        return AssertionFailed(msg, **kw)
    return CatalaError(msg, **kw)


# --- operations ------------------------------------------------------------

@dataclass
class TypecheckResult:
    ok: bool
    files: list[str]
    diagnostic: str = ""

    def __bool__(self) -> bool:
        return self.ok


def typecheck(paths: str | Path | list[str | Path], check_invariants: bool = True) -> TypecheckResult:
    if isinstance(paths, (str, Path)):
        paths = [paths]
    rel = [str(Path(p)) for p in paths]
    args = [toolchain()["catala"], "typecheck", *rel]
    if check_invariants:
        args.append("--check-invariants")
    proc = _run(args)
    ok = proc.returncode == 0
    return TypecheckResult(
        ok=ok, files=rel, diagnostic="" if ok else (proc.stderr or proc.stdout).strip()
    )


def run_scope(
    path: str | Path, scope: str, inputs: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Execute `scope` in `path` and return its output struct as a dict.

    Goes through `clerk run` because `catala interpret` cannot find the built
    standard library objects on its own.
    """
    args = [toolchain()["clerk"], "run", str(path), f"--scope={scope}", "-F", "json", "--quiet"]
    stdin = None
    if inputs is not None:
        args += ["--input", "-"]
        stdin = json.dumps(inputs)
    proc = _run(args, stdin=stdin)
    if proc.returncode != 0:
        raise _classify(
            f"executing {scope} in {path} failed (exit {proc.returncode})", proc
        )
    out = proc.stdout.strip()
    # clerk may prefix build chatter even under --quiet; take the JSON tail.
    brace = out.find("{")
    if brace < 0:
        raise _classify(f"no JSON in output of {scope}: {out[:200]!r}", proc)
    try:
        return json.loads(out[brace:])
    except json.JSONDecodeError as e:
        raise _classify(f"unparseable JSON from {scope}: {e}", proc) from e


BUILD_LIB = REPO_ROOT / "_build" / "libcatala"


def run_scope_traced(
    path: str | Path, scope: str, inputs: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute a scope and return (outputs, decisions).

    Each decision records which source line the interpreter took to define one
    variable, with the law headings attached to that line. That is how the
    system can say *which provision governed* an answer without re-deciding it:
    the compiler reports the definition it applied, and we look it up.

    This goes through `catala interpret` rather than `clerk run`, because clerk
    does not pass `--trace` through. It needs the already-built standard library
    to be pointed at explicitly, which `clerk start`/`clerk test` will have
    produced; if it has not, the caller gets a CatalaError telling them so.
    """
    args = [
        toolchain()["catala"], "interpret", str(path), "-s", scope,
        "--trace", "--trace-format=json",
        "-I", str(BUILD_LIB), "--bin", str(BUILD_LIB / "ocaml"),
    ]
    stdin = None
    if inputs is not None:
        args += ["--input", "-"]
        stdin = json.dumps(inputs)
    proc = _run(args, stdin=stdin)
    if proc.returncode != 0:
        raise _classify(f"tracing {scope} in {path} failed", proc)

    blob = proc.stdout
    start = blob.find("[")
    events: list[dict[str, Any]] = []
    if start >= 0:
        # the trace array is followed by the human RESULT block, so parse the
        # first complete JSON value rather than the whole of stdout
        try:
            events, _idx = json.JSONDecoder().raw_decode(blob[start:])
        except json.JSONDecodeError:
            events = []

    outputs: dict[str, Any] = {}
    decisions: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for ev in events if isinstance(events, list) else []:
        kind = ev.get("event")
        if kind == "DecisionTaken":
            pending = ev.get("pos") or {}
        elif kind == "VariableDefinition":
            name = (ev.get("name") or "").split(".")[-1]
            raw = ev.get("value")
            outputs.setdefault(name, raw)
            if pending:
                decisions.append({
                    "variable": name,
                    "line": pending.get("start_line"),
                    "law_headings": pending.get("law_headings") or [],
                    "value": raw,
                })
            pending = None
    return outputs, decisions


def json_schema(path: str | Path, scope: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(input_schema, output_schema) for a scope. Used to generate the input
    battery for behavioural-equivalence checking."""
    proc = _run([toolchain()["catala"], "json-schema", str(path), "-s", scope])
    if proc.returncode != 0:
        raise _classify(f"json-schema for {scope} failed", proc)
    out = proc.stdout[proc.stdout.find("[") :]
    schemas = json.loads(out)
    return schemas[0], schemas[1]


# --- exception trees -------------------------------------------------------

@dataclass
class ExceptionNode:
    label: str
    conditions: list[str] = field(default_factory=list)
    law_headings: list[str] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)
    """Source lines of this node's definitions. Kept so that a decision in the
    interpreter's trace can be matched back to the rung that took it -- which
    is how the interface can say which provision actually governed an answer,
    on the compiler's evidence rather than by re-deciding it."""
    exceptions: list["ExceptionNode"] = field(default_factory=list)

    def depth(self) -> int:
        return 1 + max((c.depth() for c in self.exceptions), default=0)

    def size(self) -> int:
        return 1 + sum(c.size() for c in self.exceptions)

    def labels(self) -> list[str]:
        out = [self.label]
        for c in self.exceptions:
            out += c.labels()
        return out


GENERATED_LABEL_RE = re.compile(r"^exception_to_(.+)$")


def _canonical_condition(text: str) -> str:
    """Normalise a condition so two encodings that mean the same thing compare
    equal. Catala renders `>=` as `≥`, so ASCII/unicode must be unified, and
    incidental whitespace must not register as a difference."""
    t = text.replace("≥", ">=").replace("≤", "<=").replace("≠", "!=")
    t = t.replace("−", "-").replace("×", "*")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _node_from_json(d: dict[str, Any], normalise_labels: bool) -> ExceptionNode:
    label = d.get("label") or ""
    if normalise_labels:
        # Unlabeled exceptions get compiler-generated `exception_to_<parent>`
        # names. Those are an implementation detail and must not drive a diff.
        m = GENERATED_LABEL_RE.match(label)
        if m:
            label = "<unlabeled>"
    conds: list[str] = []
    headings: list[str] = []
    lines: list[int] = []
    for r in d.get("rules", []) or []:
        ct = r.get("condition_text")
        conds.append(_canonical_condition(ct) if ct else "<unconditional>")
        pos = r.get("pos") or {}
        lh = pos.get("law_headings") or []
        for h in lh:
            if h not in headings:
                headings.append(h)
        # the consequence's own line, and the condition's, both identify the rung
        for key in ("pos", "condition_pos"):
            ln = (r.get(key) or {}).get("start_line")
            if isinstance(ln, int) and ln not in lines:
                lines.append(ln)
    node = ExceptionNode(
        label=label,
        conditions=sorted(conds),
        law_headings=headings,
        lines=sorted(lines),
        exceptions=[
            _node_from_json(c, normalise_labels) for c in (d.get("exceptions") or [])
        ],
    )
    # Sibling order is not semantically meaningful; sort so it cannot cause a
    # spurious diff.
    node.exceptions.sort(key=lambda n: (tuple(n.conditions), n.label))
    return node


def exception_tree(
    path: str | Path, scope: str, variable: str, normalise_labels: bool = True
) -> list[ExceptionNode]:
    """The structured exception hierarchy for one scope variable.

    This is the legally load-bearing structure: which definition defeats which,
    under what condition, at what depth. Canonicalised for diffing -- absolute
    paths and line numbers are dropped, conditions are normalised, siblings are
    sorted, generated labels are collapsed.
    """
    proc = _run(
        [toolchain()["catala"], "exceptions", str(path), "-s", scope, "-v", variable, "-F", "json"]
    )
    if proc.returncode != 0:
        raise _classify(f"exceptions for {scope}.{variable} failed", proc)
    out = proc.stdout[proc.stdout.find("{") :]
    data = json.loads(out)
    trees = [_node_from_json(t, normalise_labels) for t in data.get("trees", [])]
    trees.sort(key=lambda n: (tuple(n.conditions), n.label))
    return trees


def shape_signature(trees: list[ExceptionNode]) -> str:
    """The exception hierarchy's shape alone: depth and arity, no labels and no
    condition text. Two encodings with the same shape agree about which
    definition defeats which and at what depth; whether they agree about the
    *conditions* is settled behaviourally, because a condition factored into a
    helper variable reads as different text while meaning the same thing."""

    def emit(n: ExceptionNode, depth: int) -> list[str]:
        rows = [f"{'  ' * depth}node(children={len(n.exceptions)},defs={len(n.conditions)})"]
        for c in n.exceptions:
            rows += emit(c, depth + 1)
        return rows

    rows: list[str] = []
    for t in trees:
        rows += emit(t, 0)
    return "\n".join(rows)


def structural_signature(trees: list[ExceptionNode]) -> str:
    """Shape and conditions of an exception hierarchy, ignoring label names.

    This is what decides whether two encodings agree about the law. Labels are
    identifiers the author chose: `c4_1` and `over_40_rule` denote the same
    node, and a comparison that counts them as different reports a difference
    where there is none. The same argument that excludes scope names from
    convergence (DECISIONS.md D-1) excludes labels, and for the same reason --
    a loop driven by naming never terminates.

    What is NOT ignored: depth, parent/child relationships, and the exact
    condition attached to every node. Those are the law.
    """

    def emit(n: ExceptionNode, depth: int) -> list[str]:
        rows = [f"{'  ' * depth}[{'; '.join(n.conditions)}]"]
        for c in n.exceptions:
            rows += emit(c, depth + 1)
        return rows

    rows: list[str] = []
    for t in trees:
        rows += emit(t, 0)
    return "\n".join(rows)


def tree_signature(trees: list[ExceptionNode]) -> str:
    """A stable string identifying an exception hierarchy's shape and content.
    Two encodings with the same signature agree about the exception structure."""

    def emit(n: ExceptionNode, depth: int) -> list[str]:
        rows = [f"{'  ' * depth}{n.label} [{'; '.join(n.conditions)}]"]
        for c in n.exceptions:
            rows += emit(c, depth + 1)
        return rows

    rows: list[str] = []
    for t in trees:
        rows += emit(t, 0)
    return "\n".join(rows)


# --- value comparison ------------------------------------------------------

def as_decimal(v: Any) -> Decimal:
    """Catala emits decimals and money as JSON numbers, which are floats and
    therefore inexact. Comparisons in tests must not inherit that: convert via
    `str` so 0.15 is Decimal('0.15'), not Decimal('0.1499999...')."""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        raise TypeError("refusing to coerce a boolean to Decimal")
    return Decimal(str(v))


def values_agree(
    expected: Any,
    actual: Any,
    *,
    tolerance: Any = None,
    money: bool = False,
) -> bool:
    """Compare an expected legal value against Catala's output.

    Exact by default. An earlier version of this function quantized everything
    to cents, which meant a *rate* of 0.1499 compared equal to 0.15 -- a
    verification primitive that lenient silently passes real encoding errors,
    so the default is now exactness and any slack must be asked for.

    Catala emits decimals and money as JSON numbers, i.e. floats, so values are
    converted via `str` before comparison: `Decimal(str(0.15))` is
    `Decimal('0.15')`, not `Decimal('0.1499999999999999944...')`. Decimal
    equality is numeric, so `0.2` and `0.20` still agree.

    `money=True` quantizes both sides to cents with ROUND_HALF_UP, which is the
    precision Catala guarantees for the `money` type. Note this is *not*
    Decimal's default ROUND_HALF_EVEN: banker's rounding would make an expected
    450.005 disagree with an actual 450.01, which is not the behaviour a lawyer
    reading a money figure expects.

    `tolerance` is for genuinely non-terminating values (a third of a penny,
    a rate of 1/3). Use it explicitly and sparingly; never to make a failing
    test pass.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) == bool(actual)
    if isinstance(expected, (int, float, Decimal)) and isinstance(
        actual, (int, float, Decimal)
    ):
        e, a = as_decimal(expected), as_decimal(actual)
        if money:
            q = Decimal("0.01")
            return e.quantize(q, rounding=ROUND_HALF_UP) == a.quantize(
                q, rounding=ROUND_HALF_UP
            )
        if tolerance is not None:
            return abs(e - a) <= as_decimal(tolerance)
        return e == a
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            return False
        return all(
            values_agree(expected[k], actual[k], tolerance=tolerance, money=money)
            for k in expected
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            values_agree(e, a, tolerance=tolerance, money=money)
            for e, a in zip(expected, actual)
        )
    return expected == actual


def money_agree(expected: Any, actual: Any) -> bool:
    """Compare two money values at cent precision."""
    return values_agree(expected, actual, money=True)
