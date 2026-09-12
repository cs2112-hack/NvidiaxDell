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

    A clause encoded in two scopes is a conflict by construction: two places
    now claim authority over the same rule, and nothing guarantees they agree.
    """
    entries = entries if entries is not None else build_registry()
    ledger = load_ledger()
    need = {r for r, d in ledger.items() if d.label in (Label.RULE, Label.HYBRID)}
    where: dict[str, list[str]] = {}
    for e in entries.values():
        for ref in e.encodes:
            where.setdefault(ref, []).append(e.qualified)
    return {
        "required": len(need),
        "encoded": len(need & set(where)),
        "missing": sorted(need - set(where)),
        "duplicated": {r: v for r, v in where.items() if len(set(v)) > 1},
        "encoded_but_prose": sorted(
            r for r in where
            if r in ledger and ledger[r].label is Label.PROSE
        ),
    }
