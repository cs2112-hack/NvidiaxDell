"""The closing edit, and the exact price of making it.

An exposure with no remedy attached is a complaint. An exposure with a remedy
whose side effects nobody computed is worse, because somebody will apply it.
So a remedy here is two things that travel together:

  * the minimal edit that closes the hole -- proposed by an agent, in the same
    way everything else here is proposed by an agent, and worth nothing on its
    own;
  * the diff of everything else that edit changes, which is not proposed by
    anybody. The edit is applied to the real module, the project is rebuilt,
    every mapped scope is re-executed over its whole grid, every recorded
    counterexample is re-run, and the module is put back. What comes out is the
    set of regions that moved, by how much, and who is in them.

The second half is the part that is hard to fake and the part a lawyer will
actually use, because the question in the room is never "can we fix it", it is
"what else does that break".

## Applying an edit to the real tree

The edit is written to the actual module file, not a copy. That is not
laziness: Catala resolves a module's dependencies through compiled objects
under `_build`, keyed by the module's path in the source tree, so a module
evaluated from a temporary directory either fails to find its dependencies or
-- far worse -- silently loads the objects built from the *unedited* source and
reports that the edit changed nothing.

The file is restored in a `finally`, the restoration is verified by content
hash before the function returns, and the project is rebuilt on the way out.
If the restore ever fails, that is raised loudly rather than reported as a
diff: a half-applied counterfactual left in the tree is the worst possible
outcome of asking a question.
"""
from __future__ import annotations

import hashlib
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from . import exposure, llm
from . import surface as sf
from .agents import Role, RoleResult, run_role
from .catala_runner import (
    REPO_ROOT,
    begin_edit,
    build_lock,
    end_edit,
    restore_interrupted_edits,
    toolchain,
    tree_lock,
    typecheck,
)
from .registry import load_registry
from .reviewer import run_regression


class RestoreFailed(RuntimeError):
    """The module could not be put back after a counterfactual. Raised rather
    than returned: the tree is now in a state nobody asked for."""


def rebuild() -> tuple[bool, str]:
    """Recompile the project's Catala objects.

    `clerk test` is the rebuild hook -- it compiles every module and runs the
    in-source `#[test]` assertions on the way through, which is exactly the
    pair of things a counterfactual needs to know about an edit. It takes
    about a second on this corpus.
    """
    with build_lock(exclusive=True):
        proc = subprocess.run(
            [toolchain()["clerk"], "test"], capture_output=True, text=True,
            cwd=str(REPO_ROOT), timeout=600,
        )
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


@contextmanager
def edited(path: str | Path, new_text: str) -> Iterator[None]:
    """Apply `new_text` to the real module for the length of the block.

    The edit is made in the real tree (D-9), so it holds the exclusive tree
    lock throughout -- other processes' executions wait for the module to be
    put back rather than run against a policy nobody adopted -- and journals
    the original first, so a process killed inside the block is undone by the
    next process to read the tree. See `catala_runner.TREE_LOCK`.
    """
    p = Path(path)
    with tree_lock(exclusive=True):
        restore_interrupted_edits(rebuild=False)     # the rebuild below covers it
        original = p.read_bytes()
        want = hashlib.sha256(original).hexdigest()
        manifest = begin_edit(p, original)
        try:
            p.write_text(new_text, encoding="utf-8")
            rebuild()
            yield
        finally:
            p.write_bytes(original)
            got = hashlib.sha256(p.read_bytes()).hexdigest()
            rebuild()
            if got != want:
                raise RestoreFailed(
                    f"{p} was not restored to its original content after a "
                    f"counterfactual. Restore it from git before doing anything else."
                )
            end_edit(manifest)


def splice(source: str, old: str, new: str) -> str:
    """Replace `old` with `new`, insisting that `old` occurs exactly once.

    A replacement that matches twice is not a minimal edit, it is two edits of
    which one was not intended, and applying it would make the counterfactual
    measure something nobody proposed.
    """
    n = source.count(old)
    if n == 0:
        raise ValueError("the text to replace does not occur in the module")
    if n > 1:
        raise ValueError(f"the text to replace occurs {n} times; it must be unique")
    return source.replace(old, new)


# --- comparing two surfaces ------------------------------------------------

def _key(inputs: dict[str, Any]) -> str:
    return "|".join(f"{k}={inputs[k]!r}" for k in sorted(inputs))


def _dec(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass
class CellChange:
    inputs: dict[str, Any]
    before_outcome: str
    after_outcome: str
    before_value: Any = None
    after_value: Any = None
    before_refs: list[str] = field(default_factory=list)
    after_refs: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        if self.before_outcome != self.after_outcome:
            return f"{self.before_outcome} -> {self.after_outcome}"
        if self.before_value != self.after_value:
            return "revalued"
        return "regoverned"

    @property
    def delta(self) -> Decimal | None:
        a, b = _dec(self.before_value), _dec(self.after_value)
        return None if a is None or b is None else b - a


@dataclass
class SurfaceDiff:
    scope: str
    measure: str = ""
    unit: str = ""
    population: str = ""
    cells_compared: int = 0
    changes: list[CellChange] = field(default_factory=list)
    regions_before: int = 0
    regions_after: int = 0
    unmatched: int = 0
    """Cells present on one side and not the other. Non-zero means the grid
    itself moved -- an edit that changes a threshold changes where the borders
    are, so the two maps are not over the same points. Reported rather than
    silently intersected, because a diff over a shifted grid understates."""

    @property
    def touched(self) -> int:
        return len(self.changes)

    @property
    def worst(self) -> CellChange | None:
        costed = [c for c in self.changes if c.delta is not None]
        return max(costed, key=lambda c: abs(c.delta)) if costed else None

    def summary(self) -> str:
        if not self.changes and not self.unmatched:
            return f"{self.scope}: nothing moved ({self.cells_compared} cells identical)"
        kinds: dict[str, int] = {}
        for c in self.changes:
            kinds[c.kind] = kinds.get(c.kind, 0) + 1
        parts = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
        tail = f"; {self.unmatched} cells only on one side" if self.unmatched else ""
        return (f"{self.scope}: {self.touched}/{self.cells_compared} cells moved "
                f"({parts}); regions {self.regions_before} -> {self.regions_after}{tail}")


def diff_surfaces(before: sf.Surface, after: sf.Surface, sd: sf.ScopeDomain) -> SurfaceDiff:
    out = SurfaceDiff(
        scope=before.key, measure=sd.measure_output, unit=sd.measure_unit,
        population=sd.population,
        regions_before=len(before.regions), regions_after=len(after.regions),
    )
    a = {_key(c.inputs): (r, c) for r in before.regions for c in r.cells}
    b = {_key(c.inputs): (r, c) for r in after.regions for c in r.cells}
    out.cells_compared = len(set(a) & set(b))
    out.unmatched = len(set(a) ^ set(b))
    m = sd.measure_output
    for k in sorted(set(a) & set(b)):
        ra, ca = a[k]
        rb, cb = b[k]
        va = ca.outputs.get(m) if m else None
        vb = cb.outputs.get(m) if m else None
        same_value = _dec(va) == _dec(vb) if (_dec(va) is not None and _dec(vb) is not None) else va == vb
        if ca.outcome == cb.outcome and same_value and ra.clause_refs == rb.clause_refs:
            continue
        out.changes.append(CellChange(
            inputs=ca.inputs, before_outcome=ca.outcome, after_outcome=cb.outcome,
            before_value=va, after_value=vb,
            before_refs=list(ra.clause_refs), after_refs=list(rb.clause_refs),
        ))
    return out


# --- the counterfactual ----------------------------------------------------

@dataclass
class Counterfactual:
    module: str
    ok: bool = False
    typecheck_ok: bool = False
    typecheck_diagnostic: str = ""
    tests_ok: bool = False
    tests_output: str = ""
    regressions_broken: list[str] = field(default_factory=list)
    regressions_fixed: list[str] = field(default_factory=list)
    diffs: list[SurfaceDiff] = field(default_factory=list)
    closes: bool | None = None
    """Whether the exposure the edit was proposed for still lands. None when no
    exposure was supplied to check against."""
    error: str = ""

    def report(self) -> list[str]:
        rows = [f"typecheck: {'ok' if self.typecheck_ok else 'FAILED'}",
                f"in-source assertions: {'ok' if self.tests_ok else 'FAILED'}"]
        if self.closes is not None:
            rows.append(f"closes the exposure: {'yes' if self.closes else 'NO'}")
        if self.regressions_broken:
            rows.append(f"counterexamples broken: {', '.join(self.regressions_broken)}")
        if self.regressions_fixed:
            rows.append(f"counterexamples now passing: {', '.join(self.regressions_fixed)}")
        rows += [d.summary() for d in self.diffs]
        return rows


def counterfactual(
    module_path: str | Path,
    new_text: str,
    *,
    check: exposure.Attack | None = None,
    scopes: list[str] | None = None,
) -> Counterfactual:
    """Apply an edit, measure everything it changes, and put the module back.

    `scopes` defaults to every mapped scope of every module, not only the
    edited one: an edit to a definition another module uses moves that module's
    map too, and the whole point of the exercise is that the blast radius is
    measured rather than assumed.
    """
    module_path = Path(module_path)
    cf = Counterfactual(module=str(module_path))
    domains = sf.load_domains()
    keys = scopes if scopes is not None else sorted(domains)

    before: dict[str, sf.Surface] = {}
    for k in keys:
        try:
            before[k] = sf.partition(k, domains=domains)
        except Exception as e:                       # a scope that cannot map now
            cf.error += f"{k} could not be mapped before the edit: {e}; "

    before_reg = {r.id: r.passed for r in run_regression("catala")}

    try:
        with edited(module_path, new_text):
            tc = typecheck(module_path)
            cf.typecheck_ok = bool(tc)
            cf.typecheck_diagnostic = tc.diagnostic[:1500]
            cf.tests_ok, cf.tests_output = rebuild()
            cf.tests_output = cf.tests_output[-1500:]

            if check is not None:
                v = exposure.adjudicate(check, domains=domains)
                cf.closes = not v.landed

            after_reg = {r.id: r.passed for r in run_regression("catala")}
            for cid, was in before_reg.items():
                now = after_reg.get(cid, was)
                if was and not now:
                    cf.regressions_broken.append(cid)
                elif not was and now:
                    cf.regressions_fixed.append(cid)

            for k in keys:
                if k not in before:
                    continue
                try:
                    after = sf.partition(k, domains=domains)
                except Exception as e:
                    cf.error += f"{k} could not be mapped after the edit: {e}; "
                    continue
                d = diff_surfaces(before[k], after, domains[k])
                if d.touched or d.unmatched:
                    cf.diffs.append(d)
    except RestoreFailed:
        raise
    except Exception as e:
        cf.error += f"{type(e).__name__}: {e}"
        return cf

    cf.ok = cf.typecheck_ok and cf.tests_ok and not cf.regressions_broken
    return cf


# --- proposing the edit ----------------------------------------------------

CLOSER = Role(
    name="remedy",
    purpose="Propose the smallest change that closes one exposure.",
    reads=["corpus", "catala/modules"],
    forbidden=["docs", "src", "tests", "exposure/queue"],
    temperature=0.2,
    num_predict=2048,
    system=(
        "You close a legal exposure in a company's own documents.\n\n"
        "You are given one exposure: the reading an opponent will advance, the "
        "provisions it rests on, and the Catala encoding of those provisions. "
        "Propose TWO things.\n\n"
        "1. The amendment to the DOCUMENT, in the drafting style of the clause "
        "you are amending. This is the real fix: the exposure exists because "
        "the text does not settle something, and code cannot settle it.\n\n"
        "2. The corresponding edit to the Catala encoding, so that the "
        "consequences of the amendment can be computed before anyone signs it. "
        "Give it as an exact replacement: `old` must be a passage that appears "
        "ONCE in the module, copied character for character, and `new` is what "
        "it becomes. Change as little as possible. Do not reformat, do not "
        "rename, do not tidy anything you were not asked to change -- every "
        "extra character you touch is a region of the map that moves for no "
        "reason and a lawyer who has to ask why.\n\n"
        "A Catala exception does not inherit its parent's condition, so any "
        "condition you rely on must be restated in full.\n\n"
        "Reply with JSON only: "
        '{"clause_amendment": "<the amended clause text, as it would be '
        'published>", "rationale": "<one paragraph: what the amendment settles '
        'and which reading it forecloses>", "old": "<exact text from the '
        'module>", "new": "<replacement>"}'
    ),
)


def _validate_edit(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise ValueError("expected a JSON object")
    for k in ("clause_amendment", "rationale", "old", "new"):
        if not str(v.get(k, "")).strip():
            raise ValueError(f"a closing edit must state {k}")
    if v["old"].strip() == v["new"].strip():
        raise ValueError("the replacement is identical to what it replaces")
    return {k: str(v[k]) for k in ("clause_amendment", "rationale", "old", "new")}


def propose_closing_edit(
    e: exposure.Exposure, *, model: str = llm.DEFAULT_MODEL
) -> RoleResult:
    entry = load_registry().get(e.scope)
    if entry is None:
        return RoleResult(CLOSER.name, False, error=f"no scope {e.scope}")
    module = Path(entry.path).read_text(encoding="utf-8")
    prompt = "\n".join([
        f"# Exposure {e.id} — {e.headline}",
        f"\nClass: {e.klass}. Archetype: {e.archetype}.",
        f"\nThe opponent's theory:\n{e.why}",
        f"\nProvisions relied on: {', '.join(e.citations) or '(none)'}",
        f"\nThe facts: {e.facts}",
        f"\nWhat the company's own policy computes: {e.outputs or e.diagnostic[:300]}",
        (f"\nThe amount at stake: {e.amount} ({e.amount_basis})" if e.amount else ""),
        f"\n## The module encoding these provisions ({entry.path})\n",
        "```", module, "```",
    ])
    return run_role(CLOSER, prompt, model=model, validate=_validate_edit)


def close(
    e: exposure.Exposure,
    edit: dict[str, str],
    *,
    scopes: list[str] | None = None,
) -> Counterfactual:
    """Apply a proposed closing edit and measure it."""
    entry = load_registry().get(e.scope)
    if entry is None:
        cf = Counterfactual(module="?", error=f"no scope {e.scope}")
        return cf
    source = Path(entry.path).read_text(encoding="utf-8")
    try:
        new_text = splice(source, edit["old"], edit["new"])
    except ValueError as ex:
        return Counterfactual(module=entry.path, error=str(ex))
    attack = exposure.Attack(e.archetype, e.scope, e.facts, origin="remedy-check")
    return counterfactual(entry.path, new_text, check=attack, scopes=scopes)
