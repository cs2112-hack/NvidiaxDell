"""Ingestion: a new document becomes a *proposal*, never a merge.

The brief: take a new document, run the same triage, generate candidate
Catala, flag every conflict with existing modules, and refuse to merge until a
human resolves the conflict.

"Refuse" has to be a gate rather than a warning, so `merge()` raises unless
every detected conflict carries an explicit human resolution in the
proposal's manifest. Nothing here can be bypassed by re-running with a flag.

## Conflict detection: heuristics plus the compiler

Five of the six detectors below are static analysis over the text, and they
are honest heuristics -- they can miss things. The sixth is different:
`COMPILER_CONFLICT` stages the candidate alongside the existing modules and
asks Catala itself. Catala's default logic reports two applicable definitions
with no priority between them as a hard error, so an overlap that a text
heuristic would never see becomes a machine-proven fact. That is the detector
worth trusting, and it is why candidate generation targets compilable Catala
rather than a summary.

The regression arm matters as much: staging the candidate and re-running every
recorded counterexample catches a new clause that silently changes an answer
the corpus previously settled. A new document that alters an existing
entitlement without saying so is exactly the case a human must see.

## Real documents: convert first, and record the conversion

A real document -- a PDF from a law firm, a .docx from HR -- carries none of
the house convention. Rather than teaching the segmenter to guess, a document
that does not parse is converted by `lks.extract` + `lks.structure` into a
house-convention file under `ingest/converted/`, and *that* file is what the
proposal triages, conflict-checks and would merge. The conversion is recorded
in the proposal, and three further conflicts come out of it:

* `conversion-metadata` (blocking) -- a `doc_id`, `title` or `effective_date`
  the document does not state. Nothing invents one; an invented effective
  date would silently date a rule.
* `conversion-unassigned-text` (blocking) -- a paragraph the converter could
  not place. Text that reached no clause is never merged silently.
* `conversion-synthesised-ids` (advisory) -- clauses whose ids are positional
  because the document numbers nothing. They are not citable, and a human
  should say so or renumber before anyone cites them.

None of this loosens the gate. `merge()` still refuses while any blocking
conflict lacks a human `resolution` and `resolved_by`, and it additionally
refuses to copy a document whose front matter still carries the
`NEEDS-HUMAN-INPUT` sentinel, whatever resolutions have been recorded.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .catala_runner import _env, REPO_ROOT, toolchain
from .literate import encoded_refs
from .model import Clause, Document, referenced_clauses
from .registry import build_registry
from .segment import ConventionError, load_corpus, parse_document
from .structure import NEEDS_HUMAN, Conversion, convert_file
from .triage import Decision, Label, load_ledger, propose as triage_propose

PROPOSALS = Path("ingest/proposals")

MONEY_RE = re.compile(r"(?:£|\$|€)\s?([\d,]+(?:\.\d+)?)")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
MULT_RE = re.compile(r"(\d+(?:\.\d+)?)\s+times")
DURATION_RE = re.compile(r"(\d+)\s+(day|days|month|months|year|years|hour|hours)")
DEFINES_RE = re.compile(r'"([^"]{2,80})"\s+means\b')


class MergeRefused(RuntimeError):
    """Raised when a merge is attempted with unresolved conflicts."""


class ConflictKind:
    TERM_REDEFINITION = "term-redefinition"
    DANGLING_REFERENCE = "dangling-reference"
    NUMERIC_DIVERGENCE = "numeric-divergence"
    DUPLICATE_ENCODING = "duplicate-encoding"
    DOC_COLLISION = "doc-collision"
    COMPILER_CONFLICT = "compiler-conflict"
    REGRESSION = "counterexample-regression"
    CONVERSION_METADATA = "conversion-metadata"
    CONVERSION_UNASSIGNED = "conversion-unassigned-text"
    CONVERSION_SYNTHESISED = "conversion-synthesised-ids"


@dataclass
class Conflict:
    kind: str
    incoming_ref: str
    detail: str
    existing_refs: list[str] = field(default_factory=list)
    severity: str = "blocking"          # blocking | advisory
    resolution: str | None = None       # set by a human; required to merge
    resolved_by: str | None = None

    @property
    def resolved(self) -> bool:
        return bool(self.resolution and self.resolved_by)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Proposal:
    id: str
    source_path: str
    doc_id: str
    title: str
    created: str
    triage: dict[str, str] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    candidate_modules: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    original_path: str = ""                   # before conversion, if converted
    conversion: dict[str, Any] | None = None  # lks.structure.Conversion.to_dict()

    @property
    def dir(self) -> Path:
        return PROPOSALS / self.id

    @property
    def blocking(self) -> list[Conflict]:
        return [c for c in self.conflicts if c.severity == "blocking"]

    @property
    def unresolved(self) -> list[Conflict]:
        return [c for c in self.blocking if not c.resolved]

    @property
    def mergeable(self) -> bool:
        return not self.unresolved

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_path": self.source_path,
            "original_path": self.original_path or self.source_path,
            "converted": self.conversion is not None,
            "conversion": self.conversion,
            "doc_id": self.doc_id,
            "title": self.title,
            "created": self.created,
            "status": "mergeable" if self.mergeable else "blocked",
            "triage": self.triage,
            "candidate_modules": self.candidate_modules,
            "notes": self.notes,
            "conflicts": [c.to_dict() for c in self.conflicts],
        }

    def save(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / "proposal.yaml"
        p.write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False, width=100, allow_unicode=True)
        )
        return p

    @classmethod
    def load(cls, pid: str) -> "Proposal":
        d = yaml.safe_load((PROPOSALS / pid / "proposal.yaml").read_text())
        pr = cls(
            id=d["id"], source_path=d["source_path"], doc_id=d["doc_id"],
            title=d["title"], created=d["created"], triage=d.get("triage") or {},
            candidate_modules=d.get("candidate_modules") or [],
            notes=d.get("notes") or [],
            original_path=d.get("original_path") or "",
            conversion=d.get("conversion"),
        )
        pr.conflicts = [Conflict(**c) for c in (d.get("conflicts") or [])]
        return pr


# --- detectors -------------------------------------------------------------


def _numbers(text: str) -> set[str]:
    out: set[str] = set()
    for rx in (MONEY_RE, PERCENT_RE, MULT_RE):
        out |= {m.replace(",", "") for m in rx.findall(text)}
    out |= {f"{n} {u}" for n, u in DURATION_RE.findall(text)}
    return out


def detect_static_conflicts(incoming: Document, corpus_dir: str | Path = "corpus") -> list[Conflict]:
    existing = load_corpus(corpus_dir)
    conflicts: list[Conflict] = []

    # doc-level collision
    for d in existing:
        if d.doc_id == incoming.doc_id and d.hash != incoming.hash:
            conflicts.append(
                Conflict(
                    kind=ConflictKind.DOC_COLLISION, incoming_ref=incoming.doc_id,
                    detail=(
                        f"doc_id {incoming.doc_id} already exists as "
                        f"{Path(d.source_path).name} v{d.version} with different content "
                        f"(incoming v{incoming.version}). Is this a new version that "
                        f"supersedes it, or a distinct document?"
                    ),
                    existing_refs=[d.doc_id],
                )
            )

    known_ids = {c.clause_id for d in existing for c in d.clauses} | {
        d_.section_id for d_ in existing for d_ in d_.clauses
    } if existing else set()
    known_ids |= {c.clause_id for c in incoming.clauses}
    known_ids |= {c.section_id for c in incoming.clauses}

    # defined terms across the corpus
    defs: dict[str, list[tuple[str, str]]] = {}
    for d in existing:
        for c in d.clauses:
            for term in DEFINES_RE.findall(c.body):
                defs.setdefault(term.lower(), []).append((c.ref, c.body))

    for c in incoming.clauses:
        # dangling references
        for ref in referenced_clauses(c.body, exclude=c.clause_id):
            base = re.sub(r"\([a-z]\)$", "", ref)
            if base not in known_ids:
                conflicts.append(
                    Conflict(
                        kind=ConflictKind.DANGLING_REFERENCE, incoming_ref=c.ref,
                        detail=(
                            f"refers to {ref}, which exists in neither the incoming "
                            f"document nor the corpus. Either the reference is wrong "
                            f"or a document it depends on has not been ingested."
                        ),
                    )
                )

        # term redefinition
        for term in DEFINES_RE.findall(c.body):
            prior = defs.get(term.lower())
            if not prior:
                continue
            for ref, body in prior:
                if " ".join(body.split()) != " ".join(c.body.split()):
                    conflicts.append(
                        Conflict(
                            kind=ConflictKind.TERM_REDEFINITION, incoming_ref=c.ref,
                            detail=(
                                f'redefines "{term}", already defined at {ref} with '
                                f"different wording. A defined term with two meanings "
                                f"silently changes every rule that uses it."
                            ),
                            existing_refs=[ref],
                        )
                    )

        # numeric divergence against a clause that shares a defined term
        nums = _numbers(c.body)
        if nums:
            for d in existing:
                for ec in d.clauses:
                    shared = _shared_terms(c.body, ec.body)
                    # One shared capitalised phrase is far too weak -- nearly every
                    # clause in a document shares one with nearly every other, and
                    # the resulting noise buries the real findings. Require a
                    # genuine subject-matter overlap.
                    if len(shared) < 2:
                        continue
                    enums = _numbers(ec.body)
                    if enums and nums != enums and (nums & enums) != nums:
                        differing = sorted(nums - enums)
                        if differing:
                            conflicts.append(
                                Conflict(
                                    kind=ConflictKind.NUMERIC_DIVERGENCE,
                                    incoming_ref=c.ref,
                                    detail=(
                                        f"states {differing} where {ec.ref} states "
                                        f"{sorted(enums)} for overlapping subject matter "
                                        f"({', '.join(sorted(shared)[:3])}). If the "
                                        f"incoming figure supersedes, say so explicitly."
                                    ),
                                    existing_refs=[ec.ref],
                                    severity="advisory",
                                )
                            )

    # a clause already encoded in a module
    enc = encoded_refs()
    for c in incoming.clauses:
        if c.ref in enc:
            conflicts.append(
                Conflict(
                    kind=ConflictKind.DUPLICATE_ENCODING, incoming_ref=c.ref,
                    detail=f"already encoded in {sorted(set(enc[c.ref]))}",
                    existing_refs=[c.ref],
                )
            )
    return _dedupe(conflicts)


TERM_WORDS_RE = re.compile(r'"([^"]{2,80})"')
CAP_TERM_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b")


def _shared_terms(a: str, b: str) -> set[str]:
    """Defined-looking terms appearing in both clauses.

    Capitalised multi-word phrases are how these documents mark defined terms,
    so they are a reasonable proxy for "these two clauses are about the same
    thing". Common sentence-initial words are excluded to keep the signal from
    drowning.
    """
    stop = {
        "The", "This", "By", "Where", "An", "A", "No", "Each", "Any", "Notwithstanding",
        "Employee", "Company", "Policy", "Plan", "Agreement", "Annex", "Schedule",
        "Standard", "Service", "Customer", "Supplier", "Tier", "Recipient", "Discloser",
        "An Employee", "The Employee", "The Company", "Such an Employee",
        "Base Hourly Rate", "Payroll Week", "Where an Employee", "This Policy",
        "Travel Day", "Plan Year", "Measurement Period", "Data Class",
    }
    def terms(t: str) -> set[str]:
        out = set(TERM_WORDS_RE.findall(t))
        out |= {m for m in CAP_TERM_RE.findall(t) if m not in stop and " " in m}
        return {x.lower() for x in out if x.lower() not in {s.lower() for s in stop}}
    return terms(a) & terms(b)


def _dedupe(cs: list[Conflict]) -> list[Conflict]:
    seen: set[tuple] = set()
    out: list[Conflict] = []
    for c in cs:
        key = (c.kind, c.incoming_ref, c.detail[:120])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# --- compiler-proven conflicts --------------------------------------------


def _typecheck_each(modules: list[Path], cwd: Path) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for m in modules:
        proc = subprocess.run(
            [toolchain()["catala"], "typecheck", str(m)],
            capture_output=True, text=True, env=_env(), cwd=str(cwd), timeout=300,
        )
        out[m.name] = proc.returncode == 0
    return out


def detect_compiler_conflicts(candidate_paths: list[str | Path]) -> list[Conflict]:
    """Stage the candidate beside the existing modules and ask Catala itself.

    Two detectors, and the second is the one that earns its keep.

    **Typecheck, baselined.** Modules are typechecked individually both before
    and after staging, and only a module that *newly* fails is attributed to
    the candidate. An earlier version typechecked everything together and
    blamed the candidate for any failure, which meant an unrelated broken
    module elsewhere in the tree produced a false conflict. A false blocking
    conflict is not a harmless conservatism here -- it trains whoever reviews
    proposals to click through the gate.

    **Exception-tree roots.** Catala's default logic errors on two applicable
    definitions with no priority between them, but only *at runtime, on inputs
    that reach both*. A statically-added definition that merely sits outside
    the existing exception chain is therefore invisible to a typecheck and to
    any single execution. It shows up unmistakably in the exception tree: the
    variable acquires a second ROOT, because a root is by construction a
    definition nothing takes priority over. Comparing the root count before
    and after staging catches an amendment bolted on without an `exception`
    relation -- which is precisely what careless ingestion produces, and what
    would otherwise leave the rule ambiguous until some unlucky input hit it.
    """
    from .reviewer import discover_scopes

    conflicts: list[Conflict] = []
    if not candidate_paths:
        return conflicts

    with tempfile.TemporaryDirectory(prefix="lks-ingest-") as td:
        stage = Path(td)
        shutil.copytree(REPO_ROOT / "catala", stage / "catala")
        if (REPO_ROOT / "_build").exists():
            shutil.copytree(REPO_ROOT / "_build", stage / "_build", dirs_exist_ok=True)
        shutil.copy(REPO_ROOT / "clerk.toml", stage / "clerk.toml")

        mods_dir = stage / "catala" / "modules"
        before_mods = sorted(mods_dir.glob("*.catala_en"))
        baseline = _typecheck_each(before_mods, stage)

        # root counts per (module, scope, variable) before staging
        def roots(base: Path, mod: Path) -> dict[tuple[str, str], int]:
            out: dict[tuple[str, str], int] = {}
            try:
                scopes = discover_scopes(mod)
            except Exception:
                return out
            for scope, qual in scopes.items():
                for var in qual["output"] + qual["internal"]:
                    try:
                        from .catala_runner import _run as _r

                        proc = _r(
                            [toolchain()["catala"], "exceptions", str(mod),
                             "-s", scope, "-v", var, "-F", "json"],
                            cwd=base,
                        )
                        if proc.returncode != 0:
                            continue
                        data = json.loads(proc.stdout[proc.stdout.find("{"):])
                        out[(scope, var)] = len(data.get("trees") or [])
                    except Exception:
                        continue
            return out

        before_roots = {
            p.name: roots(stage, p)
            for p in before_mods
            if p.name in {Path(c).name for c in candidate_paths}
        }

        for p in candidate_paths:
            shutil.copy(p, mods_dir / Path(p).name)

        after_mods = sorted(mods_dir.glob("*.catala_en"))
        after = _typecheck_each(after_mods, stage)

        newly_broken = [
            name for name, ok in after.items() if not ok and baseline.get(name, True)
        ]
        if newly_broken:
            proc = subprocess.run(
                [toolchain()["catala"], "typecheck", str(mods_dir / newly_broken[0])],
                capture_output=True, text=True, env=_env(), cwd=str(stage), timeout=300,
            )
            blob = (proc.stderr or "") + (proc.stdout or "")
            kind = (
                ConflictKind.COMPILER_CONFLICT
                if re.search(r"conflicting definitions|ambiguous exception|"
                             r"same conditions and will always trigger", blob, re.I)
                else ConflictKind.COMPILER_CONFLICT
            )
            conflicts.append(
                Conflict(
                    kind=kind,
                    incoming_ref=", ".join(newly_broken),
                    detail=(
                        f"staging the candidate makes {newly_broken} stop "
                        f"typechecking, where it typechecked before:\n" + blob[:1200]
                    ),
                )
            )

        for p in candidate_paths:
            name = Path(p).name
            if name not in before_roots:
                continue
            after_r = roots(stage, mods_dir / name)
            for key, n_after in sorted(after_r.items()):
                n_before = before_roots[name].get(key)
                if n_before is None or n_after <= n_before:
                    continue
                scope, var = key
                conflicts.append(
                    Conflict(
                        kind=ConflictKind.COMPILER_CONFLICT,
                        incoming_ref=f"{name}:{scope}.{var}",
                        detail=(
                            f"the candidate adds a definition of {scope}.{var} that "
                            f"stands outside the existing exception hierarchy: root "
                            f"definitions went from {n_before} to {n_after}. Two "
                            f"definitions can now apply to the same facts with no "
                            f"priority between them, so Catala will raise a conflict "
                            f"on whichever inputs reach both. If the incoming clause "
                            f"is meant to supersede the existing one it must be "
                            f"written as an `exception` to it; if it is meant to "
                            f"apply in a different situation, its condition must be "
                            f"narrowed so the two cannot overlap."
                        ),
                    )
                )
    return conflicts


def detect_regressions(candidate_paths: list[str | Path]) -> list[Conflict]:
    """Counterexamples that stop holding once the candidate is staged."""
    from .reviewer import run_regression

    if not candidate_paths:
        return []
    before = {r.id: r.passed for r in run_regression("catala")}
    if not before:
        return []
    staged: list[Path] = []
    out: list[Conflict] = []
    try:
        for p in candidate_paths:
            dest = REPO_ROOT / "catala" / "modules" / Path(p).name
            if dest.exists():
                continue
            shutil.copy(p, dest)
            staged.append(dest)
        after = {r.id: r.passed for r in run_regression("catala")}
        broke = [i for i, ok in after.items() if before.get(i) and not ok]
        if broke:
            out.append(
                Conflict(
                    kind=ConflictKind.REGRESSION,
                    incoming_ref=", ".join(Path(p).stem for p in candidate_paths),
                    detail=(
                        f"staging the candidate breaks {len(broke)} counterexample(s) "
                        f"that previously held: {broke}. The incoming document changes "
                        f"an answer the corpus had already settled."
                    ),
                )
            )
    finally:
        for d in staged:
            d.unlink(missing_ok=True)
    return out


# --- conversion-derived conflicts -----------------------------------------


def detect_conversion_conflicts(conv: Conversion) -> list[Conflict]:
    """What a conversion could not settle on its own.

    These are not text heuristics about the rules; they are facts about the
    conversion itself, and each names the thing a human has to do. They are
    raised as conflicts rather than printed as warnings for one reason: a
    warning is a thing you scroll past, and `merge()` refuses on a conflict.
    """
    s = conv.structured
    out: list[Conflict] = []
    ref = s.meta["doc_id"]

    for field_, why in s.needs_human.items():
        out.append(
            Conflict(
                kind=ConflictKind.CONVERSION_METADATA, incoming_ref=f"{ref} {field_}",
                detail=(
                    f"the converted document has no {field_}: {why} Until it is "
                    f"supplied, the front matter carries the literal "
                    f"{NEEDS_HUMAN!r}, and merge refuses on that sentinel whatever "
                    f"resolution is recorded here. Re-run with the value, e.g. "
                    f"`lks convert {Path(s.extracted.source_path).name} "
                    f"--{field_.replace('_', '-')} ...`, then re-ingest."
                ),
            )
        )

    for d in s.unassigned:
        out.append(
            Conflict(
                kind=ConflictKind.CONVERSION_UNASSIGNED,
                incoming_ref=f"{ref} paragraph {d.index}",
                detail=(
                    f"the converter could not assign this paragraph to any clause "
                    f"({d.reason}), so it is NOT in the converted document. Its full "
                    f"text is in {conv.report_path}. Place it by hand in "
                    f"{conv.converted_path} and re-ingest -- text that reached no "
                    f"clause is text nothing in this system can cite."
                ),
            )
        )

    if s.synthesised:
        ids = [c.clause_id for c in s.synthesised]
        out.append(
            Conflict(
                kind=ConflictKind.CONVERSION_SYNTHESISED, incoming_ref=ref,
                detail=(
                    f"{len(ids)} clause id(s) are positional because the document "
                    f"numbers nothing at that level: {ids[:10]}"
                    f"{' ...' if len(ids) > 10 else ''}. A synthesised id is not "
                    f"something a lawyer can cite, so a citation to one is a "
                    f"citation to our guess. Confirm or renumber in "
                    f"{conv.converted_path}; the per-clause provenance is in "
                    f"{conv.report_path}."
                ),
                severity="advisory",
            )
        )

    if s.furniture:
        out.append(
            Conflict(
                kind=ConflictKind.CONVERSION_UNASSIGNED,
                incoming_ref=f"{ref} page furniture",
                detail=(
                    f"{len(s.furniture)} paragraph(s) were treated as running "
                    f"headers, footers or page numbers and left out of the converted "
                    f"document: paragraphs "
                    f"{[d.index for d in s.furniture][:10]}. Their full text is in "
                    f"{conv.report_path}; check that none of them is document text."
                ),
                severity="advisory",
            )
        )
    return out


# --- the pipeline ----------------------------------------------------------


def propose_ingestion(
    doc_path: str | Path,
    candidate_paths: list[str | Path] | None = None,
    corpus_dir: str | Path = "corpus",
    converted_dir: str | Path = "ingest/converted",
    overrides: dict[str, str] | None = None,
) -> Proposal:
    """Run triage and conflict detection on a new document. Merges nothing.

    Accepts any format `lks.extract` supports. A file already in the house
    convention is handled exactly as before; anything else is converted first
    (see the module docstring) and the conversion is recorded in the proposal.
    `overrides` supplies metadata the document does not state, e.g.
    `{"effective_date": "2026-01-01"}`.
    """
    doc_path = Path(doc_path)
    conv: Conversion | None = None
    try:
        doc = parse_document(doc_path)
    except ConventionError as house_error:
        # Not in the house convention: convert, and say so. The converted file
        # is what gets triaged and what would be merged, so the rest of this
        # function -- and everything downstream of it -- is unchanged.
        conv = convert_file(doc_path, out_dir=converted_dir, overrides=overrides)
        try:
            doc = parse_document(conv.converted_path)
        except ConventionError as e:       # pragma: no cover - defensive
            raise ConventionError(
                f"{doc_path} was converted to {conv.converted_path} but the result "
                f"still does not parse under the house convention: {e}. The original "
                f"did not parse either ({house_error}). Inspect "
                f"{conv.report_path} -- nothing has been merged."
            ) from e

    pid = f"{date.today().isoformat()}-{doc.doc_id.lower()}"
    pr = Proposal(
        id=pid, source_path=str(doc_path), doc_id=doc.doc_id, title=doc.title,
        created=date.today().isoformat(),
    )
    if conv is not None:
        s = conv.structured
        # the CONVERTED file is the document of record from here on: it is what
        # segments, what the vector store would index, and what merge copies.
        pr.source_path = str(conv.converted_path)
        pr.original_path = str(doc_path)
        pr.conversion = conv.to_dict()
        pr.notes.append(
            f"{doc_path} is not in the house convention ({s.extracted.fmt}, read "
            f"with {s.extracted.tool}), so it was converted to "
            f"{conv.converted_path} using the {s.scheme.name} scheme. Review "
            f"{conv.report_path}: it accounts for all "
            f"{s.extracted.n_paras} source paragraph(s) and gives the provenance "
            f"of every clause id. Everything below was computed from the "
            f"CONVERTED file."
        )
        for w in s.warnings:
            pr.notes.append(f"conversion: {w}")

    for c in doc.clauses:
        label, reason, _ = triage_propose(c)
        pr.triage[c.ref] = label.value
    counts: dict[str, int] = {}
    for v in pr.triage.values():
        counts[v] = counts.get(v, 0) + 1
    pr.notes.append(
        f"triage proposed {counts} over {len(doc.clauses)} clauses; these are "
        f"heuristic and must be adjudicated before the modules are trusted"
    )

    if conv is not None:
        pr.conflicts.extend(detect_conversion_conflicts(conv))
    pr.conflicts.extend(detect_static_conflicts(doc, corpus_dir))
    cps = [Path(p) for p in (candidate_paths or [])]
    pr.candidate_modules = [str(p) for p in cps]
    if cps:
        pr.conflicts.extend(detect_compiler_conflicts(cps))
        pr.conflicts.extend(detect_regressions(cps))
    else:
        pr.notes.append(
            "no candidate Catala supplied, so the compiler-proven conflict check "
            "and the counterexample regression check did not run. Static text "
            "detectors alone cannot prove the absence of a rule conflict."
        )

    pr.conflicts = _dedupe(pr.conflicts)
    pr.save()
    return pr


def merge(pid: str, corpus_dir: str | Path = "corpus") -> dict[str, Any]:
    """Merge a proposal. Refuses while any blocking conflict is unresolved.

    A converted document is refused on one further ground, which no
    resolution can clear: front matter still carrying the `NEEDS-HUMAN-INPUT`
    sentinel. A `doc_id`, `title` or `effective_date` the document never
    stated must be supplied as a value, not resolved as a comment -- a corpus
    document whose effective date reads `NEEDS-HUMAN-INPUT` would date every
    rule in it to nothing at all.
    """
    pr = Proposal.load(pid)
    if pr.unresolved:
        lines = [
            f"  [{c.kind}] {c.incoming_ref}: {c.detail.splitlines()[0][:140]}"
            for c in pr.unresolved
        ]
        raise MergeRefused(
            f"refusing to merge {pid}: {len(pr.unresolved)} unresolved conflict(s).\n"
            + "\n".join(lines)
            + f"\n\nA human must record a `resolution` and `resolved_by` against each "
            f"in {pr.dir / 'proposal.yaml'}. Nothing in this pipeline can override "
            f"that gate."
        )

    src = Path(pr.source_path)
    if not src.exists():
        raise MergeRefused(
            f"refusing to merge {pid}: its document {src} no longer exists. Re-run "
            f"`lks ingest` on the source."
        )
    head = src.read_text(encoding="utf-8")[:2000]
    if NEEDS_HUMAN in head:
        missing = [ln.split(":", 1)[0].strip() for ln in head.splitlines()
                   if NEEDS_HUMAN in ln]
        raise MergeRefused(
            f"refusing to merge {pid}: {src} still carries the {NEEDS_HUMAN} "
            f"sentinel for {missing or ['a required field']}. The document does not "
            f"state it and nothing here will invent it -- supply the value (e.g. "
            f"`lks convert <file> --effective-date YYYY-MM-DD`, or edit {src}) and "
            f"re-ingest. This refusal is not clearable by a resolution."
        )
    dest = Path(corpus_dir) / src.name
    shutil.copy(src, dest)
    moved = []
    for m in pr.candidate_modules:
        mp = Path(m)
        if mp.exists():
            tgt = Path("catala/modules") / mp.name
            shutil.copy(mp, tgt)
            moved.append(str(tgt))
    return {
        "merged": pid, "corpus": str(dest), "modules": moved,
        "next": [
            "adjudicate the proposed triage labels in triage/decisions.yaml",
            "python scripts/build_index.py && python scripts/sync_mongo.py",
            "python scripts/build_registry.py",
            "./scripts/check.sh",
        ],
    }
