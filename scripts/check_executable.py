#!/usr/bin/env python3
"""Statically detect scopes that cannot be executed through the JSON interface.

Catala 1.2.1 cannot serialise an enum-typed value in a scope's output, directly
or nested inside a struct (see docs/CATALA-REFERENCE.md). Such a scope
typechecks, and its `#[test]` assertions pass because those run under the
interpreter rather than through JSON -- so nothing in the ordinary build tells
you it is dead. It fails only when someone asks it a real question.

That is the worst possible shape for a defect in this system: the whole
architecture answers rule questions by executing a scope and reading its JSON,
so an unserialisable output means a clause is silently unanswerable. MSA-SCH4
L-5 shipped in exactly that state.

This check resolves enum types through struct fields transitively, because an
enum one level down inside an output struct fails identically.
"""
import re
import sys
from pathlib import Path

ENUM_RE = re.compile(r"^\s*declaration enumeration ([A-Z]\w*)\s*:", re.M)
STRUCT_RE = re.compile(r"^\s*declaration structure ([A-Z]\w*)\s*:", re.M)
FIELD_RE = re.compile(r"^\s*data\s+\w+\s+content\s+(.+?)\s*$", re.M)
SCOPE_RE = re.compile(r"^\s*(?:#\[\w+\]\s*)?declaration scope ([A-Z]\w*)\s*:", re.M)
OUTPUT_RE = re.compile(r"^\s*(?:input\s+)?output\s+(\w+)\s+content\s+(.+?)\s*$", re.M)


def type_names(t: str) -> list[str]:
    """Capitalised type names mentioned in a type expression, so that
    `optional of Choice` and `list of Wrap` are both resolved."""
    return re.findall(r"\b([A-Z]\w*)\b", t)


def main() -> int:
    modules = sorted(Path("catala/modules").glob("*.catala_en"))
    enums: set[str] = set()
    struct_fields: dict[str, list[str]] = {}
    for f in modules:
        src = f.read_text(encoding="utf-8")
        enums.update(ENUM_RE.findall(src))
        for m in STRUCT_RE.finditer(src):
            name = m.group(1)
            body = src[m.end(): m.end() + 1500].split("declaration")[0]
            struct_fields[name] = [
                n for fld in FIELD_RE.findall(body) for n in type_names(fld)
            ]

    def reaches_enum(t: str, seen: frozenset[str] = frozenset()) -> str | None:
        for n in type_names(t):
            if n in enums:
                return n
            if n in struct_fields and n not in seen:
                inner = reaches_enum(" ".join(struct_fields[n]), seen | {n})
                if inner:
                    return f"{n} -> {inner}"
        return None

    problems: list[str] = []
    n_scopes = 0
    for f in modules:
        src = f.read_text(encoding="utf-8")
        positions = [(m.start(), m.group(1)) for m in SCOPE_RE.finditer(src)]
        for i, (pos, scope) in enumerate(positions):
            n_scopes += 1
            end = positions[i + 1][0] if i + 1 < len(positions) else len(src)
            body = src[pos:end]
            fence = body.find("```")
            if fence > 0:
                body = body[:fence]
            for var, ty in OUTPUT_RE.findall(body):
                hit = reaches_enum(ty)
                if hit:
                    problems.append(
                        f"{f.name}: {scope}.{var} has output type '{ty.strip()}' which "
                        f"reaches enumeration {hit}. Catala 1.2.1 cannot JSON-encode "
                        f"it, so this scope cannot be executed and cannot answer "
                        f"anything. Use a boolean, several booleans, or a documented "
                        f"integer code."
                    )
    if problems:
        for p in problems:
            print(f"   FAIL  {p}")
        return 1
    print(f"   ok    {n_scopes} scopes, no enum-typed outputs (all JSON-executable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
