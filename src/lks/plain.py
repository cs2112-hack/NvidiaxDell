"""What the checkers found, in words a person without Catala or JSON can act on.

The verification layer was written for the people who built it: `rejected
re-execution agrees with expected {'total_rate': 2.0}`, `malformed
ModelRefused: reply is not valid JSON`, `died`. Every one of those is accurate
and none of them tells a manager or a lawyer what is wrong, whether it matters,
or what to do next.

Every sentence built here comes from values that were executed or validated.
Where one carries the model's own words -- a headline, why it matters -- it is
labelled as the reviewer's, because the model is not the authority on either.
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

ERROR_WORDS = {
    "ScopeConflict": "no answer: two provisions both apply and the documents do not say which one wins",
    "NoApplicableRule": "no answer: no provision covers this situation",
    "AssertionFailed": "a refusal: the rule treats these facts as impossible",
    "CatalaError": "an error: the rule could not be run",
}

FAILED_ATTEMPT = {
    "timeout": "The model took too long to reply (it was probably busy with another job), "
               "so this attempt was skipped.",
    "unavailable": "The model could not be reached, so this attempt was skipped.",
    "truncated": "The reviewer ran out of room before it finished writing its answer, so "
                 "there was nothing to check.",
    "invalid": "The reviewer's answer was not in the form the checker needs, so there was "
               "nothing to check.",
    "refused": "The model sent back an empty answer, so there was nothing to check.",
}

_BOX = re.compile(r"[┌└│─┐┘├┤]+")


def words(name: str) -> str:
    """`total_rate` -> `total rate`; `HourPremium` -> `hour premium`."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name))
    return s.replace("_", " ").strip().lower()


def rule_name(module: str | None, scope: str | None) -> str:
    m = words(module or "")
    s = words(scope or "")
    m = m[:1].upper() + m[1:]
    if not s or s == m.lower():
        return m or "the rule"
    return f"{m}: {s}" if m else s


def names(items: list[str]) -> str:
    items = [str(i) for i in items]
    if not items:
        return "nothing"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def first_line(diagnostic: str) -> str:
    """The useful part of a Catala diagnostic, without its box drawing."""
    lines = [_BOX.sub("", ln).strip() for ln in str(diagnostic).splitlines()]
    lines = [ln for ln in lines if ln and ln != "[ERROR]" and "[ERROR]" not in ln]
    return " ".join(lines[:2])[:200] or "no detail was given"


def value(v: Any, ty: str | None = None) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if v is None:
        return "nothing"
    if isinstance(v, dict):
        if "__error__" in v:
            return ERROR_WORDS.get(v["__error__"], ERROR_WORDS["CatalaError"])
        if v and set(v) <= {"years", "months", "days"}:
            parts = [f"{n} {u[:-1] if n == 1 else u}" for u, n in
                     (("years", v.get("years", 0)), ("months", v.get("months", 0)),
                      ("days", v.get("days", 0))) if n]
            return names(parts) if parts else "no time"
        if set(v) == {"year", "month", "day"}:
            try:
                return value(date(v["year"], v["month"], v["day"]).isoformat())
            except (TypeError, ValueError):
                pass
        return "; ".join(f"{words(k)} {value(x)}" for k, x in v.items())
    if isinstance(v, list):
        return ", ".join(value(x) for x in v) if v else "none"
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        try:
            d = date.fromisoformat(v)
            return f"{d.day} {d:%B %Y}"
        except ValueError:
            return v
    if isinstance(v, (int, float)) or ty in ("money", "decimal", "integer"):
        try:
            d = Decimal(str(v))
        except InvalidOperation:
            return str(v)
        if ty == "money":
            return f"{d:,.2f}"
        n = d.normalize()
        return f"{n:,f}" if abs(n) >= 10000 else f"{n:f}"
    return str(v)


def outcome(fields: Any, types: dict[str, str] | None = None) -> str:
    """A set of results as one phrase: `total rate 2.5 and night premium 0`."""
    types = types or {}
    if not isinstance(fields, dict) or "__error__" in fields:
        return value(fields)
    return names([f"{words(k)} {value(v, types.get(k))}" for k, v in fields.items()])


def comparison(expected: Any, observed: Any,
               types: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    """(result, what the document requires, what the system gives), one row per
    result the expected answer names."""
    types = types or {}
    if not isinstance(expected, dict):
        return [("the answer", value(expected), value(observed))]
    rows = []
    for k, want in expected.items():
        if k == "__error__":
            rows.append(("the answer", value({"__error__": want}), value(observed)))
            continue
        if isinstance(observed, dict) and "__error__" in observed:
            got = value(observed)
        elif isinstance(observed, dict):
            got = value(observed[k], types.get(k)) if k in observed else "(not produced)"
        else:
            got = value(observed)
        rows.append((words(k), value(want, types.get(k)), got))
    return rows


def break_summary(ce: Any, types: dict[str, str] | None = None) -> str:
    """One sentence from the recorded values alone; no model prose in it."""
    rows = comparison(ce.expected, ce.observed, types)
    if isinstance(ce.observed, dict) and "__error__" in ce.observed:
        want = names([f"{r} {w}" if r != "the answer" else w for r, w, _ in rows])
        return (f"In this situation the document requires {want}, but the system gives "
                f"{value(ce.observed)}.")
    want = names([f"{r} {w}" if r != "the answer" else w for r, w, _ in rows])
    got = names([f"{r} {g}" if r != "the answer" else g for r, _, g in rows])
    return f"In this situation the document requires {want}, but the system gives {got}."


def break_report(ce: Any, types: dict[str, str] | None = None) -> str:
    """A counterexample as a page someone outside engineering can read."""
    target = ce.target or {}
    rule = rule_name(target.get("module"), target.get("scope"))
    cites = names(ce.citations)
    status = ("Still failing: the system gives this wrong answer today."
              if ce.status == "open" else
              "Fixed: the system now gives the answer the document requires.")
    rows = comparison(ce.expected, ce.observed, types)
    lines = [
        f"# {ce.id}: {ce.headline or break_summary(ce, types)}",
        "",
        f"**Rule:** {rule}  ",
        f"**Status:** {status}  ",
        f"**Found:** {ce.discovered}",
        "",
        "## In short",
        "",
        break_summary(ce, types),
    ]
    if ce.headline:
        lines += ["", f"> In the reviewer's words: {ce.headline}"]
    lines += [
        "",
        "## The situation",
        "",
        ce.fact_pattern,
        "",
        "## What should happen, and what actually happens",
        "",
        "| | The document requires | The system gives |",
        "|---|---|---|",
        *[f"| {r} | {w} | {g} |" for r, w, g in rows],
        "",
        f"What the document requires is the reviewer's reading of {cites}. What the "
        f"system gives was checked by running the rule on this situation, not taken "
        f"from the reviewer.",
        "",
        "## Why the document requires that",
        "",
        ce.source_reasoning or "(the reviewer gave no reasoning)",
        "",
        "## Why it matters",
        "",
        (f"In the reviewer's words: {ce.why_it_matters}" if ce.why_it_matters else
         "Anyone in this situation is given the system's answer instead of the "
         "document's."),
        "",
        "## What happens next",
        "",
        "- This situation is now a permanent test. Every run of `lks check` tries it "
        "again, and it keeps failing until the rule is corrected.",
        f"- Before anything is changed, someone who knows {cites} should confirm the "
        f"reviewer's reading. If the document itself is unclear, the fix belongs in "
        f"the document rather than in the rule.",
        "",
        "## Technical detail",
        "",
        f"Scope `{target.get('scope', '')}` in `{target.get('path', '')}`",
        "",
        "```json",
        json.dumps({"inputs": ce.inputs, "expected": ce.expected,
                    "observed": ce.observed}, indent=2, default=str),
        "```",
        "",
    ]
    return "\n".join(lines)


def failed_attempt(kind: str, error: str = "") -> str:
    return FAILED_ATTEMPT.get(kind, "The attempt failed, so there was nothing to check.")


def duration(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def attack_result(why: str) -> str:
    """The exposure adjudicator's reason an attack found nothing, for a person.

    `died` and "no predicate fired" read, to anyone who did not build the
    engine, as the agents failing. They mean the opposite: the rule held."""
    w = str(why or "")
    if w.startswith("the scope computed an answer and no predicate fired"):
        return ("the rule gave an answer, and it is not one that any known claim "
                "against the company relies on")
    for prefix, lead in (
        ("the facts describe nobody: ", "the facts describe someone who could not exist: "),
        ("the proposed inputs are not the inputs the scope takes: ",
         "the agent's facts do not fit the rule: "),
        ("the scope could not be executed: ", "the rule could not be run on the agent's facts: "),
    ):
        if w.startswith(prefix):
            rest = w[len(prefix):]
            return lead + (first_line(rest) if "executed" in prefix else rest)
    return w
