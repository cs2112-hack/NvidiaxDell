"""What a scope takes and what it produces, as the compiler declares it.

Read off `catala json-schema`, never off the source, so no type here is a guess.
Two consumers need it before anything executes:

* the harnesses, to turn away an agent's facts that name an input the rule
  does not have, leave out one it needs, or give a value of the wrong type.
  Executing them anyway made Catala refuse to parse the input, the refusal came
  back as an error, and an error was accepted as an observation -- so a
  misspelt input name became a permanent, forever-failing "counterexample";
* the model client, as the JSON Schema a reply must follow, so the model cannot
  produce those facts in the first place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from .catala_runner import json_schema

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Catala's own schema accepts several spellings of these (a number or a
# string, a date string or a date object). The model is offered one, which is
# both easier to follow and a smaller grammar.
_SCALARS: dict[str, dict[str, Any]] = {
    "integer": {"type": "integer"},
    "decimal": {"type": "number"},
    "money": {"type": "number"},
    "date": {"type": "string", "minLength": 10, "maxLength": 10},
}


@dataclass(frozen=True)
class ScopeIO:
    inputs: dict[str, str]
    """input name -> Catala type name (`integer`, `date`, `DataClass`, ...)"""
    required: tuple[str, ...]
    outputs: dict[str, str]
    enums: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """input name -> allowed values, for inputs of a content-free enumeration"""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    """Every output optional: an expected answer names the results it is about."""


def _type_name(spec: dict[str, Any]) -> str:
    ref = spec.get("$ref", "")
    return ref.split("/")[-1] if ref else spec.get("type", "unknown")


def _simplify(spec: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Catala's JSON Schema, inlined and reduced to what a grammar handles well."""
    if depth > 8:
        return {}
    ref = spec.get("$ref")
    if ref:
        name = ref.split("/")[-1]
        if name in _SCALARS:
            return dict(_SCALARS[name])
        return _simplify(defs.get(name, {}), defs, depth + 1)
    if "enum" in spec:
        return {"type": "string", "enum": list(spec["enum"])}
    alts = spec.get("oneOf") or spec.get("anyOf")
    if alts:
        return {"anyOf": [_simplify(a, defs, depth + 1) for a in alts]}
    kind = spec.get("type")
    if kind == "object":
        props = {k: _simplify(v, defs, depth + 1)
                 for k, v in (spec.get("properties") or {}).items()}
        out: dict[str, Any] = {"type": "object", "properties": props,
                               "additionalProperties": False}
        required = [k for k in props if k in (spec.get("required") or [])]
        if required:
            out["required"] = required
        return out
    if kind == "array":
        return {"type": "array", "items": _simplify(spec.get("items") or {}, defs, depth + 1),
                "maxItems": 6}
    if kind in ("boolean", "integer", "number", "string"):
        return {"type": kind}
    return {}


def _root(schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    defs = schema.get("definitions", {})
    return defs.get(schema.get("$ref", "").split("/")[-1], {}), defs


@lru_cache(maxsize=256)
def _scope_io(path: str, scope: str, _mtime: float) -> ScopeIO:
    ins, outs = json_schema(path, scope)
    ri, di = _root(ins)
    ro, do = _root(outs)
    in_props = ri.get("properties") or {}
    enums = {}
    for name, spec in in_props.items():
        target = di.get(_type_name(spec), {}) if spec.get("$ref") else spec
        if "enum" in target:
            enums[name] = tuple(target["enum"])
    output_schema = _simplify(ro, do)
    output_schema.pop("required", None)
    return ScopeIO(
        inputs={n: _type_name(s) for n, s in in_props.items()},
        required=tuple(ri.get("required") or ()),
        outputs={n: _type_name(s) for n, s in (ro.get("properties") or {}).items()},
        enums=enums,
        input_schema=_simplify(ri, di),
        output_schema=output_schema,
    )


def scope_io(path: str | Path, scope: str) -> ScopeIO:
    """The interface of `scope`, cached until the module file changes.

    Raises CatalaError when the compiler cannot produce a schema."""
    p = Path(path)
    return _scope_io(str(p), scope, p.stat().st_mtime if p.exists() else 0.0)


def _names(items: list[str]) -> str:
    quoted = [f"“{i}”" for i in items]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def _type_problem(v: Any, ty: str, allowed: tuple[str, ...] | None) -> str:
    if allowed is not None:
        return "" if isinstance(v, str) and v in allowed else f"it must be one of {_names(list(allowed))}"
    if ty == "boolean":
        return "" if isinstance(v, bool) else "it must be true or false"
    if ty == "integer":
        return "" if isinstance(v, int) and not isinstance(v, bool) else "it must be a whole number"
    if ty in ("decimal", "money"):
        if isinstance(v, bool):
            return "it must be a number"
        if isinstance(v, (int, float)):
            return ""
        try:
            float(v)
            return ""
        except (TypeError, ValueError):
            return "it must be a number"
    if ty == "date":
        if isinstance(v, str) and DATE_RE.match(v):
            try:
                date.fromisoformat(v)
                return ""
            except ValueError:
                return "that date does not exist"
        if isinstance(v, dict) and set(v) == {"year", "month", "day"}:
            return ""
        return "it must be a date written YYYY-MM-DD"
    if ty == "duration":
        ok = (isinstance(v, dict) and v and set(v) <= {"years", "months", "days"}
              and all(isinstance(x, int) and not isinstance(x, bool) for x in v.values()))
        return "" if ok else 'it must be a length of time such as {"years": 6}'
    if ty == "array":
        return "" if isinstance(v, list) else "it must be a list"
    return ""


def check_facts(io: ScopeIO, facts: Any) -> list[str]:
    """What stops `facts` from being run as stated, worded for a person.

    Empty when the facts can be executed. Structures and lists are checked for
    shape only at the top level; the compiler checks the rest."""
    if not isinstance(facts, dict):
        return ["the facts are not a set of named values"]
    problems: list[str] = []
    unknown = sorted(set(facts) - set(io.inputs))
    if unknown:
        problems.append(f"it uses {_names(unknown)}, which the rule does not take "
                        f"(it takes {_names(sorted(io.inputs))})")
    missing = sorted(set(io.required) - set(facts))
    if missing:
        problems.append(f"it leaves out {_names(missing)}, which the rule needs")
    for name, v in facts.items():
        ty = io.inputs.get(name)
        if ty is None:
            continue
        why = _type_problem(v, ty, io.enums.get(name))
        if why:
            problems.append(f"“{name}” is {v!r}, but {why}")
    return problems


REFUSALS = ("ScopeConflict", "NoApplicableRule", "AssertionFailed")
"""The ways a rule can decline to answer that an expected answer may assert:
no priority between provisions, no provision at all, or facts refused as
impossible (see `catala_runner.AssertionFailed`)."""


def check_results(io: ScopeIO, expected: Any) -> list[str]:
    """What stops `expected` from being compared with what the rule produces,
    worded for a person. Empty when it can be compared.

    The checks `check_facts` makes of the inputs, made of the answer. A value
    of the wrong type -- `null`, `"maybe"` for a yes/no result, `"2.0x"` for a
    rate -- never equals anything the rule produces, so it reads as a
    disagreement every time, and a disagreement is recorded as a defect."""
    if not isinstance(expected, dict):
        return ["the expected answer is not a set of named results"]
    problems: list[str] = []
    for name, v in expected.items():
        if name == "__error__":
            if v not in REFUSALS:
                problems.append(f"it expects the rule to fail with {v!r}, which is not a way "
                                f"a rule declines to answer (those are {_names(list(REFUSALS))})")
            continue
        ty = io.outputs.get(name)
        if ty is None:
            continue
        if v is None:
            problems.append(f"it gives no value for “{name}”")
            continue
        if ty == "integer" and isinstance(v, float) and v.is_integer():
            continue        # Catala's JSON writes an integer result as 40.0
        why = _type_problem(v, ty, None)
        if why:
            problems.append(f"it expects “{name}” to be {v!r}, but {why}")
    return problems
