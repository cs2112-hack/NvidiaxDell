"""Mutants of the committed encodings: known defects, planted on purpose.

The generation pipeline has two halves that claim to catch defects -- the Catala
gate and the review screen -- and neither claim means anything until it is
measured against defects whose existence is not in doubt. The corpus supplies
31 historical ones in `tests/counterexamples`, but every one of them is
`status: fixed`, so none reproduces against today's modules. They are spent as
fixtures. What they still supply is the *shape* of the defects this domain
produces, and those shapes can be planted again, mechanically, into modules
known to be correct:

| Operator            | What it plants                                   | Real precedent                    |
|---------------------|--------------------------------------------------|-----------------------------------|
| `flip_comparison`   | `>` <-> `>=`, `<` <-> `<=`                        | "more than 40" vs "40 or more"    |
| `shift_threshold`   | a threshold in a condition moved by one          | the 48-hour boundary off by one   |
| `change_constant`   | a rate or amount in a consequence altered        | 1.25 where the clause says 1.5    |
| `negate_condition`  | an exception's condition inverted                | an exception that fires backwards |
| `and_to_or`         | a conjunction in a condition made a disjunction  | CE-0001's `critical or holiday`   |
| `drop_exception`    | one exception deleted outright                   | a clause that was never encoded   |

## Equivalent mutants are not counted

Some mutants change no behaviour: flipping `>` to `>=` on a threshold no
integer input can sit exactly on, or altering a constant that a later
exception always overrides. Counting those as "survived" would charge the
gates for failing to detect a difference that does not exist. So every mutant
is compared with its original by `lks.draft.compare_encodings` over the
boundary battery, and a mutant that agrees on every vector is classified
`equivalent` and removed from the denominator -- the standard treatment in
mutation testing, and the only honest one.

That same comparison has a second reading. Comparing a mutant against the
committed module is exactly what G5 does against an independent re-encoding,
with a perfect re-encoder. So the non-equivalent count is the ceiling on what
G5 could ever catch, and no model was needed to establish it.

## Only code is mutated

Mutation sites are found inside ```` ```catala ```` blocks only -- never in
declarations, tests, or the `|`-gutter law text -- and never on an `assertion`
line. An assertion states which fact patterns exist; mutating it changes the
domain rather than the logic, and would mostly produce mutants that are
"killed" for rejecting the battery, which measures nothing about the rule.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

OPERATORS = (
    "flip_comparison",
    "shift_threshold",
    "change_constant",
    "negate_condition",
    "and_to_or",
    "drop_exception",
)

_FENCE_OPEN = re.compile(r"^```catala\s*$")
_FENCE_CLOSE = re.compile(r"^```\s*$")
_CMP = re.compile(r"(?<![<>=!-])(>=|<=|>|<)(?![=>])")
_THRESHOLD = re.compile(r"(>=|<=|>|<|=)\s*(-?\d+)(?![\d.,|])")
_DECIMAL = re.compile(r"(?<![\d|$-])(\d+\.\d+)(?![\d-])")
_MONEY = re.compile(r"\$(\d[\d,]*)(?:\.(\d{2}))?")
_DEF_START = re.compile(r"^(\s{2})(?:label\s+\w+\s+)?(exception(?:\s+\w+)?\s+)?(definition|rule)\b")
_SCOPE_USE = re.compile(r"^scope\s+[A-Z]\w*\s*(?:under condition.*)?:\s*$")


@dataclass(frozen=True)
class Mutant:
    id: str
    module: str
    operator: str
    line: int               # 1-based line of the first changed line
    span: tuple[int, int]   # [start, end) 0-based line indices replaced
    replacement: tuple[str, ...]
    before: str
    after: str

    def apply(self, source: str) -> str:
        lines = source.splitlines()
        a, b = self.span
        out = lines[:a] + list(self.replacement) + lines[b:]
        return "\n".join(out) + ("\n" if source.endswith("\n") else "")

    def describe(self) -> str:
        return f"{self.operator} at line {self.line}: {self.before.strip()!r} -> {self.after.strip()!r}"


def _code_lines(lines: list[str]) -> Iterator[int]:
    """Indices of lines inside ```catala blocks (not metadata, not tests)."""
    inside = False
    for i, ln in enumerate(lines):
        if not inside and _FENCE_OPEN.match(ln):
            inside = True
            continue
        if inside and _FENCE_CLOSE.match(ln):
            inside = False
            continue
        if inside:
            yield i


def _condition_mask(lines: list[str], code: set[int]) -> set[int]:
    """Lines that are part of an `under condition ... consequence` span.

    Conditions are written both on one line and across several, with
    `consequence` on its own line, so the span is tracked rather than matched.
    """
    out: set[int] = set()
    open_ = False
    for i in sorted(code):
        ln = lines[i]
        if "under condition" in ln:
            open_ = True
        if open_:
            out.add(i)
        if open_ and "consequence" in ln:
            open_ = False
    return out


def enumerate_mutants(path: str | Path) -> list[Mutant]:
    """Every single-site mutant of one module, in a stable order."""
    path = Path(path)
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    code = set(_code_lines(lines))
    cond = _condition_mask(lines, code)
    stem = path.stem
    out: list[Mutant] = []

    def add(op: str, i: int, span: tuple[int, int], repl: list[str], before: str, after: str, k: int) -> None:
        out.append(Mutant(
            id=f"{stem}:{i + 1}:{op}:{k}", module=str(path), operator=op, line=i + 1,
            span=span, replacement=tuple(repl), before=before, after=after,
        ))

    for i in sorted(code):
        ln = lines[i]
        stripped = ln.strip()
        if stripped.startswith("assertion") or stripped.startswith("#"):
            continue

        # comparisons, and thresholds, wherever a condition is being stated
        in_cond = i in cond or re.search(r"\bif\b", ln) is not None
        if in_cond:
            for k, m in enumerate(_CMP.finditer(ln)):
                flip = {">": ">=", ">=": ">", "<": "<=", "<=": "<"}[m.group(1)]
                new = ln[:m.start()] + flip + ln[m.end():]
                add("flip_comparison", i, (i, i + 1), [new], ln, new, k)
            for k, m in enumerate(_THRESHOLD.finditer(ln)):
                n = int(m.group(2))
                for delta in (-1, 1):
                    new = ln[:m.start(2)] + str(n + delta) + ln[m.end(2):]
                    add("shift_threshold", i, (i, i + 1), [new], ln, new, k * 2 + (delta > 0))
            if " and " in ln:
                new = ln.replace(" and ", " or ", 1)
                add("and_to_or", i, (i, i + 1), [new], ln, new, 0)

        # constants in what a definition evaluates to
        if not in_cond or "consequence" in ln or "equals" in ln:
            tail_at = max(ln.find("equals"), ln.find("consequence"))
            if tail_at >= 0 or (i - 1 in code and lines[i - 1].rstrip().endswith("equals")):
                for k, m in enumerate(_DECIMAL.finditer(ln)):
                    if tail_at >= 0 and m.start() < tail_at:
                        continue
                    v = float(m.group(1))
                    places = len(m.group(1).split(".")[1])
                    new_v = f"{v + 0.25:.{max(places, 2)}f}"
                    new = ln[:m.start(1)] + new_v + ln[m.end(1):]
                    add("change_constant", i, (i, i + 1), [new], ln, new, k)
                for k, m in enumerate(_MONEY.finditer(ln)):
                    if tail_at >= 0 and m.start() < tail_at:
                        continue
                    whole = int(m.group(1).replace(",", ""))
                    cents = f".{m.group(2)}" if m.group(2) else ""
                    new = ln[:m.start()] + f"${whole + 1}{cents}" + ln[m.end():]
                    add("change_constant", i, (i, i + 1), [new], ln, new, 100 + k)

        # a single-line condition inverted
        m = re.search(r"under condition\s+(.+?)\s+consequence", ln)
        if m:
            new = ln[:m.start(1)] + f"not ({m.group(1)})" + ln[m.end(1):]
            add("negate_condition", i, (i, i + 1), [new], ln, new, 0)
        elif ln.rstrip().endswith("under condition") and i + 1 in code:
            # condition on the following line(s), up to `consequence`
            j = i + 1
            while j in code and "consequence" not in lines[j]:
                j += 1
            if j in code and j > i + 1:
                body = [lines[t] for t in range(i + 1, j)]
                indent = re.match(r"^\s*", body[0]).group(0)
                wrapped = [f"{indent}not ("] + body + [f"{indent})"]
                add("negate_condition", i, (i + 1, j), wrapped, "\n".join(body),
                    "\n".join(wrapped), 1)

        # an exception deleted
        dm = _DEF_START.match(ln)
        if dm and dm.group(2):
            j = i + 1
            while j in code and not _DEF_START.match(lines[j]) and not lines[j].strip().startswith(
                ("label ", "exception ", "assertion", "rule ")
            ) and not _SCOPE_USE.match(lines[j]):
                j += 1
            start = i
            # a `label X` on its own line belongs to the definition below it
            if i - 1 in code and re.match(r"^\s{2}label\s+\w+\s*$", lines[i - 1]):
                start = i - 1
            siblings = [t for t in code if t < start and _SCOPE_USE.match(lines[t])]
            header = max(siblings) if siblings else None
            removed_all = header is not None and all(
                not lines[t].strip() or start <= t < j
                for t in code if header < t and (t < j or not _SCOPE_USE.match(lines[t]))
                and _block_of(lines, t) == _block_of(lines, i)
            )
            span = (header, j) if removed_all else (start, j)
            add("drop_exception", i, span, [], "\n".join(lines[span[0]:span[1]]), "(deleted)", 0)

    return out


def _block_of(lines: list[str], i: int) -> int:
    """The line index of the fence opening the code block containing line i."""
    for t in range(i, -1, -1):
        if _FENCE_OPEN.match(lines[t]):
            return t
    return -1


def sample(mutants: list[Mutant], n: int, *, seed: int = 20260912) -> list[Mutant]:
    """At most `n` mutants, spread across operators, deterministically.

    Round-robin over operators so a module dense in constants does not fill the
    sample with `change_constant` and leave the boundary operators -- the ones
    this domain's real defects cluster on -- under-represented.
    """
    rng = random.Random(seed)
    by_op: dict[str, list[Mutant]] = {}
    for m in mutants:
        by_op.setdefault(m.operator, []).append(m)
    for v in by_op.values():
        rng.shuffle(v)
    out: list[Mutant] = []
    ops = [o for o in OPERATORS if o in by_op]
    while len(out) < n and any(by_op.get(o) for o in ops):
        for o in ops:
            if by_op.get(o) and len(out) < n:
                out.append(by_op[o].pop())
    return out


def write_mutant(m: Mutant, out_dir: str | Path) -> Path:
    """Write a mutant under its original filename in its own directory.

    The filename must be kept: Catala requires a module's name to match its
    file. Each mutant gets its own directory so two mutants of one module never
    shadow each other on the include path.
    """
    src = Path(m.module).read_text(encoding="utf-8")
    d = Path(out_dir) / re.sub(r"[^A-Za-z0-9_.-]", "_", m.id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / Path(m.module).name
    p.write_text(m.apply(src), encoding="utf-8")
    return p
