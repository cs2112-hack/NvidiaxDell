"""The registry: which Catala scope answers which question, and what it needs.

This is the join between the two engines. It is *derived* from the literate
sources rather than maintained by hand, because a hand-maintained mapping from
clauses to scopes is exactly the kind of index that silently goes stale and
then misroutes a question to a module that no longer encodes the clause.

Attribution works by reading each code block's `scope X:` header and crediting
the quotations standing above that block to scope X. So the same mechanism that
makes the literate source honest (quotation directly above the code) is what
makes the registry derivable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

from .literate import parse_literate
from .reviewer import discover_scopes
from .triage import Label, load_ledger

REGISTRY_PATH = Path("catala/registry.yaml")
SCOPE_USE_RE = re.compile(r"^\s*scope\s+([A-Z]\w*)\s*(?:under condition|:)", re.M)
MODULE_RE = re.compile(r"^>\s*Module\s+([A-Z]\w*)", re.M)


@dataclass
class ScopeEntry:
    module: str
    scope: str
    path: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    internals: list[str] = field(default_factory=list)
    encodes: list[str] = field(default_factory=list)        # clause refs
    judgement_inputs: list[str] = field(default_factory=list)
    law_headings: list[str] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.module}.{self.scope}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_registry(
    catala_dir: str | Path = "catala/modules",
) -> dict[str, ScopeEntry]:
    ledger = load_ledger()
    out: dict[str, ScopeEntry] = {}

    for f in sorted(Path(catala_dir).glob("*.catala_en")):
        src = f.read_text(encoding="utf-8")
        m = MODULE_RE.search(src)
        module = m.group(1) if m else f.stem
        scopes = discover_scopes(f)
        lf = parse_literate(f)

        for scope, qual in scopes.items():
            out[f"{module}.{scope}"] = ScopeEntry(
                module=module, scope=scope, path=str(f),
                inputs=list(qual["input"]) + list(qual["context"]),
                outputs=list(qual["output"]),
                internals=list(qual["internal"]),
            )

        # credit each block's quotations to the scope the block defines
        for b in lf.blocks:
            if b.kind not in ("catala", "catala-metadata"):
                continue
            defined = SCOPE_USE_RE.findall(b.code)
            for scope in set(defined):
                key = f"{module}.{scope}"
                e = out.get(key)
                if e is None:
                    continue
                for q in b.quotes:
                    if q.ref not in e.encodes:
                        e.encodes.append(q.ref)
                    for h in q.__dict__.get("law_headings", []) or []:
                        if h not in e.law_headings:
                            e.law_headings.append(h)

    # judgement inputs come from the triage ledger, not from the code, so that
    # a HYBRID clause whose predicate was quietly inferred in code is visible
    # as a mismatch rather than being blessed by the registry.
    for e in out.values():
        for ref in e.encodes:
            d = ledger.get(ref)
            if d and d.label is Label.HYBRID:
                for ji in d.judgement_inputs:
                    if ji not in e.judgement_inputs:
                        e.judgement_inputs.append(ji)
    return out


def write_registry(
    entries: dict[str, ScopeEntry], path: str | Path = REGISTRY_PATH
) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "_comment": (
            "DERIVED FILE -- regenerate with scripts/build_registry.py. Maps each "
            "Catala scope to the clauses it encodes, its inputs/outputs, and the "
            "judgement inputs its HYBRID clauses require. The chat router uses "
            "this to decide which scope can answer a question; a PROSE chunk's "
            "`qualifies` field points back at the module names here."
        ),
        "scopes": {k: v.to_dict() for k, v in sorted(entries.items())},
    }
    p.write_text(yaml.safe_dump(body, sort_keys=False, width=100, allow_unicode=True))


def load_registry(path: str | Path = REGISTRY_PATH) -> dict[str, ScopeEntry]:
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    out: dict[str, ScopeEntry] = {}
    for k, d in (data.get("scopes") or {}).items():
        out[k] = ScopeEntry(**d)
    return out


def coverage(
    entries: dict[str, ScopeEntry] | None = None,
) -> dict[str, Any]:
    """Which RULE/HYBRID clauses are encoded, and which are encoded twice.

    Duplication is measured **across modules**, not across scopes. A clause
    encoded in two modules is a conflict by construction: two units now claim
    authority over the same rule and nothing makes them agree. A clause
    encoded across two scopes of the SAME module is ordinary decomposition --
    C-9.2's rounding proviso lives in its own scope so the tie case is
    reachable, and is then applied by the accrual scope. Flagging that as a
    conflict would push authors to inline such provisos, which is the opposite
    of what we want.

    Clauses quoted above a declaration prologue are credited to the module
    too: a clause whose entire content is a type (E-2.1's City Tier is an
    enumeration and nothing else) is genuinely encoded, and reporting it as
    missing would understate coverage and hide the real gaps.
    """
    entries = entries if entries is not None else build_registry()
    ledger = load_ledger()
    need = {r for r, d in ledger.items() if d.label in (Label.RULE, Label.HYBRID)}
    by_module: dict[str, set[str]] = {}
    for e in entries.values():
        for ref in e.encodes:
            by_module.setdefault(ref, set()).add(e.module)
    # a clause quoted anywhere in a module's literate source counts as encoded.
    # encoded_refs() keys on FILENAME, so resolve each to its declared module
    # name -- otherwise overtime.catala_en and module Overtime look like two
    # different modules and every clause reads as duplicated.
    from .literate import encoded_refs

    stem_to_module = {Path(e.path).stem: e.module for e in entries.values()}
    for ref, files in encoded_refs().items():
        for f in files:
            stem = Path(f).stem
            by_module.setdefault(ref, set()).add(stem_to_module.get(stem, stem))
    return {
        "required": len(need),
        "encoded": len(need & set(by_module)),
        "missing": sorted(need - set(by_module)),
        "duplicated": {r: sorted(v) for r, v in by_module.items() if len(v) > 1},
        "encoded_but_prose": sorted(
            r for r in by_module
            if r in ledger and ledger[r].label is Label.PROSE
        ),
    }
