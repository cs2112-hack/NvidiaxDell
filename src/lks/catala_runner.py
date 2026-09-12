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

import fcntl
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
OPAM_SWITCH = Path.home() / ".opam" / "lks"
PROJECT_MODULES = REPO_ROOT / "catala" / "modules"
"""The one directory `clerk.toml` lists in `include_dirs`.

A module here is part of the project and clerk resolves it by name. A module
anywhere else -- a freshly generated draft under `generated/`, a mutant under
a temporary directory -- is invisible to clerk, and `_include_args` is what
makes the compiler able to see it anyway. See `_include_args`.
"""


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


BUILD_LOCK = REPO_ROOT / "_build" / ".lks-build.lock"
"""A cross-process reader/writer lock over the compiled objects in `_build`.

`clerk` rebuilds those objects when it runs -- and on this host several
processes do, the web server's watchers and test runs among them, 145 clerk
and catala processes in one 25-second sample -- while every `catala interpret`
of an out-of-tree module loads them through `-I _build/libcatala` and `--bin
_build`. The generation job does not hold the API's EXEC_LOCK, because it runs
for an hour. The committed accommodation module, executed out of tree after
interleaved builds, failed every vector with "implementation mismatch on
Stdlib_en": `ExpenseDefs.cmxs` had been compiled against a different build of
the standard library than the one on disk. The gate reported a `CatalaError`
that was the build's, not the module's, and the same test passed later on its
own.

So a clerk invocation holds the lock exclusively and every catala invocation
holds it shared: catala calls never wait for each other, and never see a
build half written. `lks.remedy.rebuild` and the shell scripts take it too. A
process that does not -- one started before the lock existed, or clerk run by
hand -- can still leave `_build` inconsistent, so `_run` also repairs a stale
build it detects (`STALE_BUILD_RE`).
"""

STALE_BUILD_RE = re.compile(
    r"implementation mismatch on \w+|inconsistent assumptions over (?:implementation|interface)",
    re.I,
)
"""What OCaml's dynamic loader says when a compiled module was built against a
different build of one of its dependencies. It is never a fact about the Catala
source being executed, so `_run` rebuilds and retries once rather than handing
it to a caller that would report it as the module's error."""


@contextmanager
def build_lock(*, exclusive: bool) -> Iterator[None]:
    BUILD_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(BUILD_LOCK, "a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _repair_build() -> None:
    """Rebuild every module against the standard library now on disk.

    `clerk build` takes about 3 seconds on this corpus. Its exit status is not
    consulted: it is 123 whenever a module outside every target draws a warning,
    and whether the repair worked is decided by retrying the command that failed.
    """
    with build_lock(exclusive=True):
        subprocess.run(
            [toolchain()["clerk"], "build"], capture_output=True, text=True,
            env=_env(), cwd=str(REPO_ROOT), timeout=600,
        )


TREE_LOCK = REPO_ROOT / ".run" / "tree.lock"
EDIT_JOURNAL = REPO_ROOT / ".run" / "edit-journal"
"""A cross-process reader/writer lock over the committed modules, and the
journal that makes an edit to them survivable.

The counterfactual has to edit the real module (D-9), and the only thing that
kept anyone from seeing that edit was the API's EXEC_LOCK -- a threading lock,
so it covered one server's threads and nothing else. Reproduced in a copy of
the repo: while one process held a what-if edit open, a second process executed
the edited rule, and the reviewer harness in it recorded a permanent
counterexample against a policy nobody adopted. And an editor killed inside
the edit left the module edited, with nothing to say so.

So an edit holds this lock exclusively for its whole length, every execution
and every read of the tree that feeds a record holds it shared, and the
original is journalled before the write. A reader that finds a journal holds
the lock, so no editor is alive: it puts the module back before it reads.

Lock order is tree, then build, everywhere.
"""

_edit_lock = threading.RLock()
_edit_depth = 0
_edit_fh: Any = None
_reading = threading.local()


@contextmanager
def tree_lock(*, exclusive: bool) -> Iterator[None]:
    """Hold the tree lock.

    Exclusive is owned by the process, not the thread: a counterfactual maps
    scopes on a thread pool, and its workers must execute the edit it is
    measuring. The flip side is that other threads of the editing process are
    not kept out -- within one server that is still EXEC_LOCK's job. Shared
    nests within a thread."""
    global _edit_depth, _edit_fh
    if exclusive:
        with _edit_lock:
            if _edit_depth == 0:
                TREE_LOCK.parent.mkdir(parents=True, exist_ok=True)
                fh = open(TREE_LOCK, "a+b")
                fcntl.flock(fh, fcntl.LOCK_EX)
                _edit_fh = fh
            _edit_depth += 1
            try:
                yield
            finally:
                _edit_depth -= 1
                if _edit_depth == 0:
                    fcntl.flock(_edit_fh, fcntl.LOCK_UN)
                    _edit_fh.close()
                    _edit_fh = None
        return
    if _edit_depth > 0 or getattr(_reading, "depth", 0) > 0:
        yield
        return
    TREE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(TREE_LOCK, "a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        _reading.depth = 1
        try:
            if any(EDIT_JOURNAL.glob("*.json")):
                # Holding the lock means no editor is alive, so this journal is
                # a crash. Converting drops the shared lock before taking the
                # exclusive one, so two readers that find it cannot deadlock.
                fcntl.flock(fh, fcntl.LOCK_EX)
                restore_interrupted_edits()
                fcntl.flock(fh, fcntl.LOCK_SH)
            yield
        finally:
            _reading.depth = 0
            fcntl.flock(fh, fcntl.LOCK_UN)


def reads_tree(fn: Any) -> Any:
    """Run `fn` holding the tree lock shared, for work that reads module source
    and executes it and must not see an edit between the two."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with tree_lock(exclusive=False):
            return fn(*args, **kwargs)
    return wrapper


def _journal_name(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()


def begin_edit(path: str | Path, original: bytes) -> Path:
    """Journal `original` as the content to put back at `path`. Call before
    writing the edit, holding the exclusive tree lock."""
    p = Path(path)
    EDIT_JOURNAL.mkdir(parents=True, exist_ok=True)
    manifest = EDIT_JOURNAL / f"{_journal_name(p)}.json"
    manifest.with_suffix(".orig").write_bytes(original)
    tmp = manifest.with_suffix(".tmp")
    tmp.write_text(json.dumps({"path": str(p.resolve()),
                               "sha256": hashlib.sha256(original).hexdigest()}))
    os.replace(tmp, manifest)
    return manifest


def end_edit(manifest: Path) -> None:
    manifest.unlink(missing_ok=True)
    manifest.with_suffix(".orig").unlink(missing_ok=True)


def restore_interrupted_edits(*, rebuild: bool = True) -> list[str]:
    """Put back every journalled module that was not restored, and rebuild.
    Returns the paths restored. Only call holding the tree lock exclusively."""
    restored: list[str] = []
    for manifest in sorted(EDIT_JOURNAL.glob("*.json")):
        d = json.loads(manifest.read_text())
        target, backup = Path(d["path"]), manifest.with_suffix(".orig")
        if backup.exists():
            original = backup.read_bytes()
            current = target.read_bytes() if target.exists() else b""
            if hashlib.sha256(current).hexdigest() != d["sha256"]:
                target.write_bytes(original)
                restored.append(str(target))
        end_edit(manifest)
    if restored and rebuild:
        _repair_build()
    return restored


def _run(args: list[str], *, cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    clerk = Path(args[0]).name == "clerk"

    def once() -> subprocess.CompletedProcess:
        with tree_lock(exclusive=False), build_lock(exclusive=clerk):
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                env=_env(),
                cwd=str(cwd or REPO_ROOT),
                input=stdin,
                timeout=300,
            )

    proc = once()
    if (not clerk and proc.returncode != 0
            and STALE_BUILD_RE.search((proc.stderr or "") + (proc.stdout or ""))):
        _repair_build()
        proc = once()
    return proc


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

def _include_args(path: str | Path) -> list[str]:
    """`-I` flags that let the compiler see a module outside the project tree.

    Everything in this module used to assume its target lived in
    `catala/modules`, because until now every target did. Generating a document
    breaks that: a draft is written to its own workspace and must be
    typechecked and executed there, before anyone has decided it is fit to join
    the corpus. Staging it into `catala/modules` to compile it would be the
    wrong trade -- `build_registry`, `check_fidelity` and `coverage` all glob
    that directory, so an unproven draft sitting in it would be indexed,
    credited with the clauses it quotes, and counted in coverage while it was
    still being repaired.

    So out-of-tree targets get told where to find what they might depend on:
    the project's own modules, the built standard library, and their own
    directory (for a multi-file draft). An in-tree target gets nothing, so its
    behaviour is byte-for-byte what it was before this existed.
    """
    p = Path(path).resolve()
    if p.parent == PROJECT_MODULES:
        return []
    return [
        "-I", str(PROJECT_MODULES),
        "-I", str(BUILD_LIB),
        "-I", str(p.parent),
    ]


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
    seen: list[str] = []
    for p in paths:
        inc = _include_args(p)
        for i in range(0, len(inc), 2):
            if inc[i + 1] not in seen:
                seen.append(inc[i + 1])
                args += [inc[i], inc[i + 1]]
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

    An in-tree module goes through `clerk run`, because `catala interpret`
    cannot find the built standard library objects on its own and clerk builds
    what it needs.

    An out-of-tree module cannot go through clerk at all: clerk resolves a
    target against `clerk.toml`'s `include_dirs`, and a path outside them is
    rejected with "No source file or module matching ... found" -- verified on
    1.2.1 against a module under `generated/`. So those go to `catala
    interpret` with explicit `-I` flags instead.

    The two paths are the same interpreter on the same source, so this is a
    difference in how the module is *located*, not in what it computes. It is
    the one case where the choice of front-end is invisible to the caller,
    which is why it is spelled out here: every existing caller -- the battery
    in `lks.draft`, the reviewer harness, the exposure adjudicator -- keeps
    working unchanged on a draft that is not yet part of the project.
    """
    stdin = None if inputs is None else json.dumps(inputs)
    inc = _include_args(path)
    if inc:
        args = [
            toolchain()["catala"], "interpret", str(path), "-s", scope, "-F", "json", *inc,
        ]
    else:
        args = [
            toolchain()["clerk"], "run", str(path), f"--scope={scope}", "-F", "json", "--quiet",
        ]
    if inputs is not None:
        args += ["--input", "-"]
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
BUILD_ROOT = REPO_ROOT / "_build"
"""Where clerk puts compiled objects, and what `--bin` must point at.

`--bin` is joined with each module's path *as written in the source tree*, so
a module at `catala/modules/x.catala_en` is looked for at
`<bin>/catala/modules/ocaml/X.cmxs`. Pointing `--bin` at the standard
library's own object directory therefore resolves only modules that depend on
nothing: any module with a `> Using` failed with "Compiled OCaml object ... not
found", which meant tracing -- and so the whole "which provision governed this"
answer -- worked for 8 of the 19 modules and quietly errored for the rest.
"""


def run_scope_traced(
    path: str | Path, scope: str, inputs: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute a scope and return (outputs, decisions).

    Each decision records which source line the interpreter took to define one
    variable, with the law headings attached to that line. That is how the
    system can say *which provision governed* an answer without re-deciding it:
    the compiler reports the definition it applied, and we look it up.

    This goes through `catala interpret` rather than `clerk run`, because clerk
    does not pass `--trace` through. It needs the already-built objects to be
    pointed at explicitly, which `clerk start`/`clerk test` will have produced;
    if they are not there, the caller gets a CatalaError telling them so.
    """
    args = [
        toolchain()["catala"], "interpret", str(path), "-s", scope,
        "--trace", "--trace-format=json",
        "-I", str(BUILD_LIB),            # the standard library's interfaces
        "-I", str(Path(path).parent),    # sibling modules this one uses
        "--bin", str(BUILD_ROOT),        # objects, keyed by source-tree path
    ]
    # An out-of-tree target also needs the project's modules on its include
    # path, or a draft that does `> Using ExpenseDefs` fails every execution
    # with "Required module not found". This function was missed when the
    # others gained `_include_args`, and the gate ladder executes through it:
    # every traced vector of such a module errored, so G2 reported planted
    # defects as caught when nothing had been tested. In-tree targets get no
    # extra flags, so their arguments are unchanged.
    inc = _include_args(path)
    for i in range(0, len(inc), 2):
        if inc[i + 1] not in args:
            args += [inc[i], inc[i + 1]]
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
    # The trace is flat: a sub-scope's definitions sit between a BeginCall and
    # its EndCall. Only depth 0 is this scope's own. Taking every definition by
    # its short name, first one kept, gave a scope that calls another the
    # callee's values: `WeeklyOvertime` came back carrying `HourPremium`'s
    # `multiplier` and `night_premium` from its first hour, and a scope sharing
    # an output name with its callee would have reported the callee's value --
    # to the adjudicator's predicates, the map and the operations watcher.
    depth = 0
    for ev in events if isinstance(events, list) else []:
        kind = ev.get("event")
        if kind == "BeginCall":
            depth += 1
        elif kind == "EndCall":
            depth = max(0, depth - 1)
        elif kind == "DecisionTaken":
            pending = ev.get("pos") or {}
        elif kind == "VariableDefinition":
            name = (ev.get("name") or "").split(".")[-1]
            raw = ev.get("value")
            if depth == 0:
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
    proc = _run(
        [toolchain()["catala"], "json-schema", str(path), "-s", scope, *_include_args(path)]
    )
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
        [toolchain()["catala"], "exceptions", str(path), "-s", scope, "-v", variable,
         "-F", "json", *_include_args(path)]
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


_TRUE_WORDS = {"true", "yes", "1"}
_FALSE_WORDS = {"false", "no", "0"}


def _as_bool(v: Any) -> bool | None:
    """Interpret a value as a boolean, or None if it is not one.

    The comparison used to be `bool(expected) == bool(actual)`, which leans on
    Python truthiness and so reported that a boolean `True` AGREED with the
    string "0" -- every non-empty string is truthy. That is a false pass, the
    one direction a verification primitive must never fail in: a counterexample
    asserting an entitlement arises would have been satisfied by an output
    saying it does not.

    So a boolean is only compared against something that genuinely is one: a
    bool, 0 or 1, or a word that spells a boolean. Anything else disagrees.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        w = v.strip().lower()
        if w in _TRUE_WORDS:
            return True
        if w in _FALSE_WORDS:
            return False
        return None
    if isinstance(v, (int, float, Decimal)) and v in (0, 1):
        return bool(v)
    return None


def _numeric(v: Any) -> bool:
    """Whether a value is a number, including one written as a string.

    Counterexample YAML routinely writes money as a quoted string -- '80000.00'
    reads more like a legal figure than 80000.0, and YAML will keep the quotes.
    Catala's JSON emits money as a number. An earlier version of `values_agree`
    required BOTH sides to be int/float/Decimal before comparing numerically,
    so '80000.00' and 80000.0 fell through to string equality and reported a
    failure against a module that was producing exactly the expected value.

    That is the safe direction for the bug to point -- a false failure, not a
    false pass -- but it still wastes the one signal the regression suite
    exists to give, and three counterexamples were sitting red against correct
    code. Booleans are excluded deliberately: `True` is an `int` in Python and
    comparing it numerically to 1 would let a boolean output agree with a
    quantity.
    """
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float, Decimal)):
        return True
    if isinstance(v, str):
        try:
            Decimal(v.strip())
            return True
        except Exception:
            return False
    return False


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
        e, a = _as_bool(expected), _as_bool(actual)
        if e is None or a is None:
            return False        # a boolean does not agree with a non-boolean
        return e == a
    if _numeric(expected) and _numeric(actual):
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
