"""Generate a legal document from a request, and issue it only if it survives.

    request
      |
      v
    1  retrieve   precedent from the vector store, plus the documents and
      |           Catala encodings it came from
      v
    2  draft      DRAFTER writes the document in the house convention
      |
      v
    3  encode     ENCODER writes a literate Catala module for it     <---+
      |           G1-G4 (lks.gates). Failure: repair and re-encode.   |
      |           A provably dead branch is a defect in the DOCUMENT, |
      |           so that one redrafts instead.                        |
      v                                                                 |
    4  screen     three blind reviewers (lks.screen). Blocked:          |
      |           redraft from the findings, then back to 3 ------------+
      v
    5  roundtrip  G5: a fresh agent re-encodes the prose alone; the two
      |           encodings must agree on structure and behaviour
      v
    6  issue      document.pdf, with its provenance

Nothing is written outside the run's workspace, `generated/<slug>/`. A
document that passes every gate is still not part of the corpus: joining the
knowledge base stays a separate, human act through the existing proposal
machinery (`lks ingest` / `lks merge`).

## What the workspace holds, and why all of it is kept

    document.md          the draft that was issued (or last attempted)
    <Module>.catala_en   its encoding
    context.json         what step 1 retrieved, with scores
    triage.yaml          RULE / HYBRID / PROSE per clause, derived from the encoding
    attempts/            every raw model reply, in order
    packet-*.md          exactly what each reviewer was shown
    roundtrip/           G5's independent re-encoding
    report.md            every gate, every finding, every iteration, human-readable
    run.json             the same, machine-readable
    document.pdf         only if every gate passed (or --emit-failed-pdf)

A generated legal document is only as good as the evidence that it was checked,
and that evidence is worthless if it is summarised away. So nothing here is
deleted, including failed attempts.

## Why the encoder does not quote clauses

G4 requires every `|` quotation in the module to match the document byte for
byte. Asking a model to copy clause text verbatim is asking it to do the one
thing a program does perfectly and a model does unreliably, and every slip
would cost a whole encoding attempt. So the encoder writes `| ENCODES: P-4.1`
and `assemble_module` expands that into the exact quotation from the parsed
document. G4 still runs on the result -- it catches a clause id that does not
exist, a code block that encodes nothing, and any bug in the assembler -- but a
model's transcription error is no longer a way to fail it.
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from . import agents, gates, llm, render, screen
from .agents import DRAFTER, ENCODER, REENCODER, Role, RoleResult, run_role
from .literate import parse_literate
from .model import Document
from .reviewer import discover_scopes
from .segment import FRONT_MATTER_RE, ConventionError, load_corpus, parse_document

REPO = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO / "generated"
"""Absolute, and the same directory the API lists (`lks.api.GENERATED`). Catala
runs with the repository as its working directory (`catala_runner._run`), so
module paths handed to it must not depend on the caller's."""
CATALA_REFERENCE = REPO / "draft" / "roundtrips" / "overtime" / "catala-reference.md"

CONTEXT_WINDOW = inspect.signature(llm.generate).parameters["num_ctx"].default
"""The context `llm.generate` asks Ollama for. A prompt that approaches it is a
problem nothing else would reveal: Ollama drops the *start* of an over-long
prompt without an error, and the encoder's prompt starts with the document it
is supposed to encode."""


# --- limits ---------------------------------------------------------------


@dataclass
class Limits:
    catala_attempts: int = 3
    """Encodings per document version: attempt 1 fresh, 2-3 repairing from the
    compiler's diagnostic and the failing input vector."""
    dead_branch_redrafts: int = 2
    """Redrafts of the document after a provably dead branch, per screen round.
    Each redrafted document gets `catala_attempts` encodings of its own."""
    screen_rounds: int = 2
    """Screen passes. A blocked round redrafts; the last round's verdict stands."""
    convention_attempts: int = 2
    """Drafts that fail to parse in the house convention before giving up."""
    roundtrip: bool = True
    roundtrip_attempts: int = 2
    """G5 runs once, and once more on a different seed if it does not converge."""
    battery_cap: int = gates.BATTERY_CAP
    context_k: int = 8
    precedent_documents: int = 2
    precedent_modules: int = 1
    emit_failed_pdf: bool = False


# --- 1. retrieval --------------------------------------------------------


@dataclass
class ContextItem:
    ref: str
    kind: str            # prose | hybrid | document | encoding
    score: float | None
    doc_id: str
    doc_title: str
    text: str = ""
    path: str = ""

    def to_dict(self, *, with_text: bool = False) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        if not with_text:
            d.pop("text")
        return d


class RetrievalUnavailable(RuntimeError):
    """Step 1 cannot run. Raised rather than drafting without precedent,
    because retrieval is a required step of the pipeline, not an enhancement."""


class WorkspaceInUse(RuntimeError):
    """The requested workspace already holds files, so a run cannot own it."""


def retrieve(
    prompt: str,
    *,
    k: int = 8,
    exclude_docs: tuple[str, ...] = (),
    n_documents: int = 2,
    n_modules: int = 1,
) -> tuple[list[ContextItem], dict[str, Any]]:
    """Precedent for a request: nearest clauses, their documents, their encodings.

    Ranking uses the committed file index, never MongoDB's `$vectorSearch`,
    for the reason `lks.chat.Chat._pick_ranker` documents: Atlas reports
    `(1 + cosine) / 2`, so its scores are on a different scale from every
    constant in this system, and the scores recorded in provenance must mean
    what they say. If MongoDB is live it is opened -- which runs its three-way
    staleness check -- and whether it agreed is recorded.

    The vector store holds PROSE and HYBRID clauses only; RULE clauses are
    deliberately not indexed, so the chat layer cannot quote code as law. But
    a rule is exactly the precedent a drafter of rules needs. So the nearest
    chunks are used to find *documents*, and each document's full text and its
    Catala encoding (via the registry's `encodes`) come along. That reaches
    RULE precedent without adding a row to the index, which would move
    `corpus_aggregate` and disturb the router's calibration.

    `exclude_docs` removes whole documents from every part of the pack. It is
    what makes leave-one-out evaluation honest: a held-out document cannot
    leak back in as a chunk, as a document, or as its encoding.
    """
    from .registry import load_registry
    from .vector import IndexMissingError, IndexStaleError, VectorStore

    try:
        store = VectorStore.open()
    except (IndexStaleError, IndexMissingError) as e:
        raise RetrievalUnavailable(
            f"the vector index cannot be used, so step 1 cannot run: {e}"
        ) from e

    status: dict[str, Any] = {"ranking": "committed file index (cosine)"}
    try:
        from .mongo_store import MongoVectorStore

        ms = MongoVectorStore.open()
        status["mongo"] = f"in agreement ({ms.status()['n_chunks']} chunks, {ms.status()['search_path']})"
        ms.close()
    except Exception as e:                                  # noqa: BLE001
        status["mongo"] = f"not used: {type(e).__name__}"

    excluded = set(exclude_docs)
    hits = [h for h in store.search(prompt, k=k + 12 * len(excluded))
            if h.chunk.doc_id not in excluded][:k]

    items: list[ContextItem] = [
        ContextItem(
            ref=h.chunk.ref, kind=h.chunk.label.lower(), score=round(h.score, 4),
            doc_id=h.chunk.doc_id, doc_title=h.chunk.doc_title, text=h.chunk.text,
            path=h.chunk.source_path,
        )
        for h in hits
    ]

    best: dict[str, float] = {}
    for h in hits:
        best[h.chunk.doc_id] = max(best.get(h.chunk.doc_id, -1.0), h.score)
    docs = {d.doc_id: d for d in load_corpus()}
    ranked_docs = [d for d, _ in sorted(best.items(), key=lambda kv: -kv[1])][:n_documents]
    for doc_id in ranked_docs:
        d = docs.get(doc_id)
        if d is None:
            continue
        items.append(ContextItem(
            ref=doc_id, kind="document", score=round(best[doc_id], 4),
            doc_id=doc_id, doc_title=d.title, text=d.raw, path=d.source_path,
        ))

    reg = load_registry()
    seen_paths: list[str] = []
    for doc_id in ranked_docs:
        for e in sorted(reg.values(), key=lambda e: -len(e.encodes)):
            # A module that also encodes a held-out document quotes its clauses
            # verbatim, so it would leak that document back in.
            if any(r.split()[0] in excluded for r in e.encodes):
                continue
            if any(r.split()[0] == doc_id for r in e.encodes) and e.path not in seen_paths:
                seen_paths.append(e.path)
    for path in seen_paths[:n_modules]:
        doc_id = next((r.split()[0] for e in reg.values() if e.path == path for r in e.encodes), "")
        items.append(ContextItem(
            ref=Path(path).name, kind="encoding", score=round(best.get(doc_id, 0.0), 4),
            doc_id=doc_id, doc_title=docs[doc_id].title if doc_id in docs else "",
            text=Path(path).read_text(encoding="utf-8"), path=path,
        ))
    status["excluded"] = sorted(excluded)
    return items, status


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + "\n\n[... truncated for length ...]\n"


# --- extraction and assembly ---------------------------------------------

_FENCE_RE = re.compile(r"^```[a-zA-Z_-]*\s*\n(.*?)\n```\s*$", re.S)


def extract_markdown(raw: str) -> str:
    """The document from a drafter reply, tolerating a fence or a preamble.

    Instruction-tuned models wrap files in fences and open with a sentence even
    when told not to. If a fenced block contains front matter, its contents are
    the document; otherwise everything before the front matter is dropped and a
    trailing fence line is removed. Nothing inside the document is touched.

    The trailing fence is the case that mattered. A reply of the form "Here is
    the document: ```markdown ... ```" has a preamble, so a fence anchored at
    the start of the reply never matched, cutting to the front matter kept the
    closing ``` -- and the segmenter, correctly, made it part of the last
    clause. It was printed in the PDF as the final words of K-3.1.
    """
    t = raw.strip()
    for m in re.finditer(r"^```[a-zA-Z_-]*[ \t]*\n(.*?)\n```[ \t]*$", t, re.S | re.M):
        if m.group(1).lstrip().startswith("---"):
            t = m.group(1).strip()
            break
    start = t.find("---\n")
    if start > 0:
        t = t[start:]
    lines = t.rstrip().splitlines()
    while lines and re.match(r"^```\s*$", lines[-1]):
        lines.pop()
    return "\n".join(lines).rstrip() + "\n"


EFFECTIVE_DATE_RE = re.compile(r"^(effective_date:[ \t]*)(.*?)[ \t]*$", re.M)
UNCONFIRMED_DATE = "TO BE CONFIRMED"


def settle_effective_date(md: str, request: str, override: str | None = None) -> tuple[str, str]:
    """Never let a model date a rule. Returns (document, note for the log).

    An effective date decides which facts a rule governs, so an invented one
    silently changes the law the document states. This repo already refuses to
    guess one when converting a real document (`lks convert` requires
    `--effective-date`). The first real generation run showed the drafter will
    invent one anyway: asked for a remote working policy with no date, it wrote
    2024-06-01.

    So it is settled by the program, not requested of the model. An explicit
    `override` wins. Otherwise the drafter's date survives only if that exact
    string appears in the request; anything else becomes "TO BE CONFIRMED",
    which the PDF prints as such. A date the request states in words ("1
    October 2026") is also replaced -- conservative on purpose, and
    `--effective-date` is the way to state it.
    """
    fm = FRONT_MATTER_RE.match(md)
    if not fm:
        return md, ""
    block = fm.group(1)
    m = EFFECTIVE_DATE_RE.search(block)
    current = m.group(2).strip().strip("\"'") if m else ""
    if override:
        value = override
        note = f"effective date set to {override}, as requested" if current != override else ""
    elif current and current != UNCONFIRMED_DATE and current in request:
        return md, ""
    else:
        value = UNCONFIRMED_DATE
        note = (f"the drafter dated the document {current!r}, which the request does not state; "
                f"replaced with \"{UNCONFIRMED_DATE}\"") if current and current != UNCONFIRMED_DATE else ""
    line = f'effective_date: "{value}"' if value == UNCONFIRMED_DATE else f"effective_date: {value}"
    new_block = (EFFECTIVE_DATE_RE.sub(lambda _m: line, block, count=1) if m
                 else block + "\n" + line)
    return md[:fm.start(1)] + new_block + md[fm.end(1):], note


def extract_catala(raw: str) -> str:
    """The module from an encoder reply, whatever it was wrapped in.

    The shapes a model actually returns: the bare file; the file inside one
    fence of any language (including ```catala, which is also the language of
    the module's own code blocks); either of those after a sentence of
    preamble; and any of those followed by a sign-off. A regex that looks for
    an outer fence cannot tell the outer ```catala from the first inner one, so
    this works from the structure of the file instead:

    1. Everything before the file's first line is dropped. That line is the
       `# Title` heading nearest above `> Module`, or `> Module` itself.
    2. Fences are balanced. A literate module's fences always pair
       (```catala... opens, a bare ``` closes), so a surplus bare ``` can only
       be the wrapper's closing fence, and trailing surplus is removed along
       with anything after it.
    """
    lines = raw.strip().splitlines()
    module_at = next((i for i, ln in enumerate(lines) if MODULE_RE.match(ln)), None)
    if module_at is not None:
        start = module_at
        for j in range(module_at - 1, max(-1, module_at - 8), -1):
            if re.match(r"^#\s+\S", lines[j]):
                start = j
                break
            if lines[j].startswith("```"):
                break
        lines = lines[start:]

    def balance(ls: list[str]) -> int:
        opens = sum(1 for ln in ls if re.match(r"^```\S", ln))
        closes = sum(1 for ln in ls if re.match(r"^```\s*$", ln))
        return closes - opens

    while lines and balance(lines) > 0:
        last_close = max(i for i, ln in enumerate(lines) if re.match(r"^```\s*$", ln))
        lines = lines[:last_close]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


MODULE_RE = re.compile(r"^>\s*Module\s+([A-Za-z]\w*)\s*$", re.M)
ENCODES_RE = re.compile(r"^\|[ \t]*ENCODES:[ \t]*(.+?)[ \t]*$", re.M)
FORBIDDEN_DIRECTIVE_RE = re.compile(r"^>\s*(Using|Include)\b", re.M)


def module_name(text: str) -> str | None:
    m = MODULE_RE.search(text)
    return m.group(1) if m else None


def assemble_module(
    text: str, doc: Document, *, document_filename: str = "document.md",
    reserved: set[str] | None = None,
) -> tuple[str, str, list[str]]:
    """Expand `| ENCODES:` markers into verbatim quotations. Returns
    (module text, module name, notes).

    The module name is also made safe here. Catala requires a module's name to
    match its filename, and an out-of-tree draft is compiled with
    `catala/modules` on its include path, so a draft that called itself
    `Overtime` would share a name with a committed module. It is renamed with a
    `Gen` prefix rather than rejected: a name is not a defect worth an
    encoding attempt.
    """
    notes: list[str] = []
    clauses = {c.clause_id: c for c in doc.clauses}
    name = module_name(text)
    if not name:
        raise ValueError("the encoding has no `> Module <Name>` line")
    reserved = {r.lower() for r in (reserved or set())}
    if name.lower() in reserved:
        new = "Gen" + name
        text = MODULE_RE.sub(f"> Module {new}", text, count=1)
        notes.append(f"renamed module {name} -> {new} (collides with a committed module)")
        name = new
    if not name[0].isupper():
        # The file is named after the capitalised form, so the directive must
        # say the same, or the module and its filename disagree.
        new = name[0].upper() + name[1:]
        text = MODULE_RE.sub(f"> Module {new}", text, count=1)
        notes.append(f"renamed module {name} -> {new} (a module name must be capitalised)")
        name = new

    def expand(m: re.Match[str]) -> str:
        ids = [i.strip() for i in re.split(r"[,\s]+", m.group(1)) if i.strip()]
        blocks: list[str] = []
        for cid in ids:
            c = clauses.get(cid)
            if c is None:
                # Left in a form G4 will name, rather than silently dropped.
                blocks.append(f"| {doc.doc_id} {cid} ({document_filename}:0)\n|\n| (no such clause)")
                notes.append(f"ENCODES names {cid}, which the document does not contain")
                continue
            body = "\n".join(("| " + ln).rstrip() for ln in c.body.splitlines())
            blocks.append(
                f"| {doc.doc_id} {cid} ({document_filename}:{c.line_start})\n|\n{body}"
            )
        return "\n\n".join(blocks)

    text = ENCODES_RE.sub(expand, text)
    return text, name, notes


def derive_triage(doc: Document, module: Path) -> dict[str, dict[str, Any]]:
    """RULE / HYBRID / PROSE per clause, read off what the encoding claims.

    A clause quoted above code is RULE; if the scope that code defines takes a
    boolean input whose name appears in the clause's judgement vocabulary, it
    is HYBRID; an unquoted clause is PROSE. Marked `source: derived`, because
    it is the encoder's claim and not an adjudication -- the ledger in
    `triage/decisions.yaml` is adjudicated one clause at a time, and nothing
    here pretends to be that.
    """
    from .catala_runner import CatalaError
    from .interface import scope_io

    lf = parse_literate(module)
    judgement_words = ("good reason", "reasonabl", "material", "satisf", "certif",
                       "opinion", "discretion", "appropriate", "in good faith")
    # Matched against snake_case input names. This used to test the first word
    # of each phrase, and for "in good faith" that is "in": every input whose
    # name contains those two letters -- `termination_date`, `minutes_after_
    # midnight` -- turned a pure date or number rule into HYBRID.
    name_stems = tuple(w.replace(" ", "_") for w in judgement_words)
    quoted: dict[str, set[str]] = {}
    for b in lf.blocks:
        defined = set(re.findall(r"^\s*scope\s+([A-Z]\w*)", b.code, re.M))
        for q in b.quotes:
            quoted.setdefault(q.clause_id, set()).update(defined)
    booleans: dict[str, list[str]] = {}

    def boolean_inputs(scope: str) -> list[str]:
        # A judgement is encoded as a boolean input (the ENCODER's instruction),
        # so only a boolean can be one.
        if scope not in booleans:
            try:
                io = scope_io(module, scope)
                booleans[scope] = [n for n, t in io.inputs.items() if t == "boolean"]
            except CatalaError:
                booleans[scope] = []
        return booleans[scope]

    out: dict[str, dict[str, Any]] = {}
    for c in doc.clauses:
        if c.clause_id not in quoted:
            out[c.clause_id] = {"label": "PROSE", "source": "derived"}
            continue
        low = c.body.lower()
        judged = [
            v for s in quoted[c.clause_id] for v in boolean_inputs(s)
            if any(stem in v for stem in name_stems)
        ]
        if judged or any(w in low for w in judgement_words):
            out[c.clause_id] = {"label": "HYBRID", "source": "derived",
                                "judgement_inputs": sorted(set(judged))}
        else:
            out[c.clause_id] = {"label": "RULE", "source": "derived"}
    return out


# --- the run --------------------------------------------------------------


@dataclass
class Attempt:
    stage: str
    n: int
    ok: bool
    seconds: float
    note: str = ""
    file: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class GenerationResult:
    workspace: Path
    prompt: str
    passed: bool = False
    failure_stage: str = ""
    failure_reason: str = ""
    pdf: Path | None = None
    pdf_sha256: str = ""
    doc_id: str = ""
    title: str = ""
    catala_iterations: int = 0
    screen_rounds: int = 0
    gate_report: gates.GateReport | None = None
    g5: gates.GateResult | None = None
    screens: list[screen.ScreenResult] = field(default_factory=list)
    confirmed_history: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    context: list[ContextItem] = field(default_factory=list)
    retrieval_status: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    model: str = ""
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "prompt": self.prompt,
            "passed": self.passed,
            "failure_stage": self.failure_stage,
            "failure_reason": self.failure_reason,
            "pdf": str(self.pdf) if self.pdf else None,
            "pdf_sha256": self.pdf_sha256,
            "doc_id": self.doc_id,
            "title": self.title,
            "catala_iterations": self.catala_iterations,
            "screen_rounds": self.screen_rounds,
            "gates": self.gate_report.to_dict() if self.gate_report else None,
            "g5": self.g5.to_dict() if self.g5 else None,
            "screens": [s.to_dict() for s in self.screens],
            "confirmed_history": self.confirmed_history,
            "attempts": [dataclasses.asdict(a) for a in self.attempts],
            "context": [c.to_dict() for c in self.context],
            "retrieval": self.retrieval_status,
            "seconds": round(self.seconds, 1),
            "model": self.model,
            "seed": self.seed,
        }


def slugify(prompt: str, *, now: float | None = None) -> str:
    words = re.findall(r"[a-z0-9]+", prompt.lower())
    stem = "-".join(words[:6])[:48].strip("-") or "document"
    tag = hashlib.sha256(f"{prompt}{now or time.time()}".encode()).hexdigest()[:6]
    return f"{stem}-{tag}"


class _Workspace:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / "attempts").mkdir(exist_ok=True)
        self._n = 0

    def keep(self, stage: str, text: str, ext: str = "txt") -> Path:
        self._n += 1
        p = self.root / "attempts" / f"{self._n:02d}-{stage}.{ext}"
        p.write_text(text, encoding="utf-8")
        return p


def _reference_text() -> str:
    try:
        return CATALA_REFERENCE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _drafter_prompt(
    request: str, context: list[ContextItem], *, previous: str = "", brief: str = "",
    limits: Limits | None = None,
) -> str:
    """The drafter's prompt, fitted to the context, precedent first.

    The encoder's prompt was fitted after run 3; this one was not, and it had
    the request as its first line -- the part Ollama drops from an over-long
    prompt. A redraft carries two clipped precedent documents, the clause list,
    the whole previous draft and the screen's brief, and a drafter reply can
    run to 8,192 tokens, so a redraft of a long document overflows. Precedent
    now comes first and is what gets shed: the documents are shortened, then
    the clause list goes, then the documents; the brief is clipped last. The
    request, the previous draft and the instruction are never shortened.
    """
    budget = _prompt_budget_chars(DRAFTER)
    docs = [c for c in context if c.kind == "document"]
    clauses = [c for c in context if c.kind in ("prose", "hybrid")]
    doc_limit = 7000
    with_clauses = True
    brief_txt = brief

    def build() -> str:
        rows: list[str] = []
        if docs and doc_limit:
            rows += ["# Precedent: the company's closest existing documents", ""]
            for c in docs:
                rows += [f"## {c.doc_id} — {c.doc_title} (similarity {c.score})", "",
                         _clip(c.text, doc_limit), ""]
        if clauses and with_clauses:
            rows += ["# Precedent: the closest individual clauses", ""]
            for c in clauses:
                rows += [f"- {c.ref} (similarity {c.score}): " + " ".join(c.text.split())[:400]]
            rows.append("")
        rows += ["# The request", "", request.strip(), ""]
        if previous:
            rows += ["# Your previous draft", "", previous, ""]
        if brief_txt:
            rows += ["# What must change", "", brief_txt, "",
                     "Return the complete corrected document. Keep every clause id that "
                     "does not need to change, so references to it stay valid.", ""]
        return "\n".join(rows)

    text = build()
    while len(text) > budget:
        if doc_limit > 2000:
            doc_limit -= 2500
        elif with_clauses and clauses:
            with_clauses = False
        elif doc_limit:
            doc_limit = 0
        elif len(brief_txt) > 3000:
            brief_txt = _clip(brief_txt, 3000)
        else:
            break                       # the request and the previous draft stay whole
        text = build()
    return text


CHARS_PER_TOKEN = 3.38
"""Characters per prompt token, for budgeting prompts before they are sent.

Calibrated, not assumed: the first real run's drafter prompt was 14816 characters
including its system prompt, and Ollama reported 3942 prompt tokens -- 3.76
characters a token. 10% is taken off so an estimate errs towards a shorter prompt.
"""


def _prompt_budget_chars(role: Role) -> int:
    """How long a prompt can be before prompt + budget overflows the context.

    Ollama does not refuse an over-long prompt; it drops the start of it. Run 3's
    first repair prompt was 9,218 tokens against an 8,192-token budget in a
    16,384-token context, and the encoder prompt then began with the document to
    be encoded. A 512-token margin covers the chat template.
    """
    tokens = CONTEXT_WINDOW - role.num_predict - 512
    return int(tokens * CHARS_PER_TOKEN) - len(role.system)


def _encoder_prompt(
    document_md: str, context: list[ContextItem], *, previous: str = "", brief: str = "",
) -> str:
    """The encoder's prompt, fitted to the context, with the document last.

    What can be shed, in the order it is shed: the precedent module (only house
    style, and never sent on a repair, where the model has already seen it),
    then the Catala reference, shortened in steps, then the previous encoding.
    The compiler's diagnostic is clipped to its first errors, which are the ones
    that matter -- later errors in a Catala file are usually the parser losing
    its place after the first.

    The document is never shortened and always comes last. If an estimate is
    ever wrong and Ollama truncates anyway, what it drops from the start is
    reference text, not the clauses being encoded.
    """
    budget = _prompt_budget_chars(ENCODER)
    ref = _reference_text()
    mods = [c for c in context if c.kind == "encoding"]
    repairing = bool(previous or brief)
    brief_txt = _clip(brief, 3000) if brief else ""
    prev_txt = previous
    ref_limit = 14000
    with_precedent = bool(mods) and not repairing

    def build() -> str:
        rows: list[str] = []
        if ref:
            rows += ["# Catala 1.2.1 reference", "", _clip(ref, ref_limit), ""]
        if with_precedent:
            rows += ["# How this company encodes its documents (an existing module, "
                     "for house style only — it encodes a DIFFERENT document)", "",
                     "```", _clip(mods[0].text, 6000), "```", ""]
        if prev_txt:
            rows += ["# Your previous encoding", "", "```", prev_txt, "```", ""]
        if brief_txt:
            rows += ["# Why it was rejected — the compiler's own words", "", brief_txt, "",
                     "Return the complete corrected file for the document below.", ""]
        rows += ["# The document to encode", "", document_md, ""]
        return "\n".join(rows)

    text = build()
    while len(text) > budget:
        if with_precedent:
            with_precedent = False
        elif ref_limit > 3000:
            ref_limit -= 2000
        elif len(prev_txt) > 6000:
            prev_txt = _clip(prev_txt, 6000)
        else:
            break                       # the document and a minimum of guidance stay
        text = build()
    return text


def _reencoder_prompt(document_md: str) -> str:
    """G5's prompt: the reference, the syntax notes, then the specification last.

    Fitted the same way as the encoder's, and for the same reason: what an
    over-long prompt loses is its start, so the document goes at the end.
    """
    budget = _prompt_budget_chars(dataclasses.replace(REENCODER, think=False))
    tail = "\n".join([
        "", agents.CATALA_SYNTAX_NOTES.strip(), "",
        "Encode every clause that states a computation. Scope inputs may only be "
        "boolean, integer, decimal, money, date, or a payload-free enumeration you "
        "declare. Start the file with a `# Title` line and `> Module Roundtrip`.", "",
        "# The specification", "", document_md,
    ])
    ref = _reference_text()
    limit = 14000
    while limit > 3000 and len(_clip(ref, limit)) + len(tail) + 40 > budget:
        limit -= 2000
    return "\n".join(["# Catala 1.2.1 reference", "", _clip(ref, limit), tail])


def _scope_map(a: Path, b: Path) -> dict[str, str]:
    """Pair scopes of two encodings by their outputs, not their names.

    An independent re-encoding picks its own scope names, and `lks.draft`
    refuses to let names decide convergence. So scopes are matched greedily on
    the overlap of their output variables; unmatched scopes are left out and
    `compare_encodings` reports the comparison on what did match.
    """
    sa, sb = discover_scopes(a), discover_scopes(b)
    pairs: list[tuple[float, str, str]] = []
    for na, qa in sa.items():
        for nb, qb in sb.items():
            oa, ob = set(qa["output"]), set(qb["output"])
            if not oa or not ob:
                continue
            j = len(oa & ob) / len(oa | ob)
            if j > 0 or na == nb:
                pairs.append((j + (0.5 if na == nb else 0.0), na, nb))
    out: dict[str, str] = {}
    used: set[str] = set()
    for _score, na, nb in sorted(pairs, reverse=True):
        if na in out or nb in used:
            continue
        out[na] = nb
        used.add(nb)
    return out


def _flushing_print(line: str) -> None:
    """Print and flush, because a run is usually watched through a log file.

    Python block-buffers stdout when it is not a terminal, so `lks generate ...
    > run.log` showed nothing at all for the first half hour of a real run --
    the pipeline was working and looked hung.
    """
    print(line, flush=True)


def generate(
    prompt: str,
    *,
    limits: Limits | None = None,
    model: str = llm.DEFAULT_MODEL,
    seed: int = llm.DEFAULT_SEED,
    workspace: str | Path | None = None,
    exclude_docs: tuple[str, ...] = (),
    emit: Callable[[str], None] | None = None,
    call: Callable[..., RoleResult] = run_role,
    on_stage: Callable[[dict[str, str]], None] | None = None,
    effective_date: str | None = None,
) -> GenerationResult:
    """Run the whole pipeline for one request. See the module docstring.

    Never raises for a legal or model failure: a draft that does not pass is a
    result, recorded in full, not an exception. It raises only when the system
    itself cannot run -- Catala is not installed, or the workspace already
    holds another run (`WorkspaceInUse`).

    `call` is injectable for the same reason as in `lks.screen`: what the
    pipeline does with a reply is the part that has to be right, and it can be
    tested without a model.
    """
    limits = limits or Limits()
    emit = emit or _flushing_print

    def stage(key: str, title: str, status: str, summary: str = "") -> None:
        """Report a pipeline stage to an observer (the web interface).

        Every `running` is followed by `done` or `failed` for the same key. An
        observer that raises is ignored: a display must never break a run.
        """
        if on_stage is None:
            return
        try:
            on_stage({"key": key, "title": title, "status": status, "summary": summary[:400]})
        except Exception:                                   # noqa: BLE001
            pass
    t_start = time.monotonic()
    started = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    root = (Path(workspace) if workspace else WORKSPACE_ROOT / slugify(prompt)).resolve()
    if root.is_dir() and any(root.iterdir()):
        # A reused workspace mixes two runs' evidence: attempt files are
        # overwritten from 01, and a document.pdf from an earlier run that
        # passed survives a later run that failed -- where the API then
        # served it as this run's PDF.
        raise WorkspaceInUse(
            f"{root} already holds files. A run's workspace is its evidence and "
            f"nothing in it is deleted, so each run needs a new or empty directory."
        )
    ws = _Workspace(root)
    res = GenerationResult(workspace=root, prompt=prompt, model=model, seed=seed)
    (root / "request.txt").write_text(prompt.strip() + "\n", encoding="utf-8")

    def log(line: str) -> None:
        emit(f"[{time.monotonic() - t_start:6.0f}s] {line}")

    def fail(where: str, reason: str) -> GenerationResult:
        # Not `stage`: that name is the observer callback above, and shadowing
        # it handed `_finish` a string on every failure path.
        res.passed = False
        res.failure_stage, res.failure_reason = where, reason
        log(f"FAILED at {where}: {reason}")
        return _finish(res, root, limits, started, log, t_start, stage)

    def _ask_once(role: Role, text: str, stage: str, n: int, *, validate=None, s: int) -> RoleResult:
        log(f"{stage}: attempt {n} ({role.name}, seed {s})")
        t0 = time.monotonic()
        # Keep what the model actually said, not what extraction made of it.
        # Writing the validated value here once meant an extractor bug would
        # have been recorded as if the model had written the mangled text, and
        # the evidence to tell the two apart would have been gone.
        captured: dict[str, Any] = {}

        def keep_raw(v: Any) -> Any:
            captured["raw"] = v
            return validate(v) if validate else v

        r = call(role, text, model=model, seed=s, validate=keep_raw)
        raw = captured.get("raw")
        if raw is None:
            raw = r.raw or r.error
        kept = ws.keep(stage, raw if isinstance(raw, str) else json.dumps(raw, indent=2),
                       "md" if role.name == "drafter" else "txt")
        res.attempts.append(Attempt(stage, n, r.ok, round(time.monotonic() - t0, 1),
                                    note=r.error[:300], file=str(kept),
                                    prompt_tokens=r.usage.prompt_tokens,
                                    completion_tokens=r.usage.completion_tokens))
        if r.usage.prompt_tokens:
            log(f"{stage}: {r.usage}")
            if r.usage.prompt_tokens + role.num_predict > CONTEXT_WINDOW:
                log(f"{stage}: WARNING — a {r.usage.prompt_tokens}-token prompt plus a "
                    f"{role.num_predict}-token budget exceeds the {CONTEXT_WINDOW}-token "
                    f"context; Ollama may have dropped the start of the prompt")
        if not r.ok:
            log(f"{stage}: unusable reply — {r.error[:200]}")
        return r

    def ask(role: Role, text: str, stage: str, n: int, *, validate=None, s: int) -> RoleResult:
        """One model call, with a fallback for a deliberation that never ended.

        A reasoning role on this model sometimes spends its entire budget
        deliberating and returns nothing. It is not a property of the role:
        the same drafter, prompt shape and seed fit its budget in the first
        real run and exhausted it in the second, once retrieval had returned
        different precedent. So a reasoning role is tried with reasoning, and
        if -- and only if -- the reply says the budget went on reasoning, the
        same attempt is repeated at once without it. Reasoning is kept where it
        fits, and a run no longer loses a whole attempt, or its draft, to a
        deliberation that did not finish. Both calls are recorded.
        """
        r = _ask_once(role, text, stage, n, validate=validate, s=s)
        if not r.ok and role.think and "consumed by reasoning" in r.error:
            log(f"{stage}: reasoning used the whole budget; repeating attempt {n} without it")
            r = _ask_once(dataclasses.replace(role, think=False), text, f"{stage}-direct", n,
                          validate=validate, s=s)
        return r

    # ---- 1. retrieve
    log("retrieve: searching the vector store for precedent")
    stage("retrieve", "Retrieve precedent", "running")
    try:
        context, status = retrieve(
            prompt, k=limits.context_k, exclude_docs=exclude_docs,
            n_documents=limits.precedent_documents, n_modules=limits.precedent_modules,
        )
    except RetrievalUnavailable as e:
        stage("retrieve", "Retrieve precedent", "failed", str(e))
        return fail("retrieve", str(e))
    res.context, res.retrieval_status = context, status
    (root / "context.json").write_text(json.dumps(
        {"status": status, "items": [c.to_dict(with_text=False) for c in context]},
        indent=2), encoding="utf-8")
    log("retrieve: " + ", ".join(f"{c.ref} ({c.kind} {c.score})" for c in context[:6])
        + (f" (+{len(context) - 6} more)" if len(context) > 6 else ""))
    stage("retrieve", "Retrieve precedent", "done",
          f"{len(context)} items; nearest " + ", ".join(c.ref for c in context[:3]))

    corpus_refs = {c.ref for d in load_corpus() for c in d.clauses}
    from .registry import load_registry
    reserved = {e.module for e in load_registry().values()}
    doc_path = root / "document.md"

    # ---- 2. draft (and redraft)
    def draft(previous: str = "", brief: str = "", round_no: int = 1) -> tuple[Document | None, str]:
        err = ""
        dkey = f"draft-{round_no}"
        dtitle = "Draft the document" if round_no == 1 else "Redraft the document"
        stage(dkey, dtitle, "running")
        for n in range(1, limits.convention_attempts + 1):
            text = _drafter_prompt(prompt, context, previous=previous,
                                   brief=(brief + ("\n\n" + err if err else "")).strip(),
                                   limits=limits)

            def validate(v: Any) -> str:
                md = extract_markdown(str(v))
                tmp = root / ".draft-check.md"
                tmp.write_text(md, encoding="utf-8")
                try:
                    parse_document(tmp)
                finally:
                    tmp.unlink(missing_ok=True)
                return md

            r = ask(DRAFTER, text, f"draft-r{round_no}", n, validate=validate,
                    s=seed + 1000 * round_no + n)
            if r.ok:
                md, date_note = settle_effective_date(r.value, prompt, effective_date)
                if date_note:
                    log(f"draft: {date_note}")
                doc_path.write_text(md, encoding="utf-8")
                d = parse_document(doc_path)
                log(f"draft: {d.doc_id} \"{d.title}\" — {len(d.clauses)} clauses in "
                    f"{len({c.section_id for c in d.clauses})} sections")
                stage(dkey, dtitle, "done", f"{d.doc_id} \u201c{d.title}\u201d, {len(d.clauses)} clauses")
                return d, md
            err = ("Your previous reply was rejected before anyone read it: "
                   + r.error[:600])
        stage(dkey, dtitle, "failed", "no draft parsed in the house convention: " + err[:200])
        return None, ""

    doc, document_md = draft()
    if doc is None:
        return fail("draft", "the drafter did not produce a document in the house convention "
                             f"after {limits.convention_attempts} attempts")
    res.doc_id, res.title = doc.doc_id, doc.title

    module_path: Path | None = None
    run: gates.BatteryRun | None = None
    round_no = 0
    while True:
        round_no += 1

        # ---- 3. encode until the cheap gates pass
        previous_module, brief = "", ""
        passed_gates = False
        dead_redrafts = 0
        n = 0
        # Attempts are counted per version of the document. A redraft after a
        # dead branch starts the count again: it used to carry on from the old
        # draft's attempt number, so the rewritten document got whatever was
        # left -- two attempts, or one -- and a run could fail "after 3
        # attempts" having tried the draft it failed on once.
        while n < limits.catala_attempts:
            n += 1
            res.catala_iterations += 1
            version = f".{dead_redrafts}" if dead_redrafts else ""
            ckey = f"catala-{round_no}{version}-{n}"
            ctitle = (f"Catala gate \u2014 attempt {n}"
                      + (f" (draft {round_no})" if round_no > 1 else "")
                      + (" (rewritten after a dead branch)" if dead_redrafts else ""))
            stage(ckey, ctitle, "running")
            r = ask(ENCODER, _encoder_prompt(document_md, context, previous=previous_module,
                                             brief=brief),
                    f"encode-r{round_no}{version}", n, validate=lambda v: extract_catala(str(v)),
                    s=seed + 2000 * round_no + 100 * dead_redrafts + n)
            if not r.ok:
                brief = "Your previous reply could not be used: " + r.error[:600]
                stage(ckey, ctitle, "failed", "the encoder\u2019s reply was unusable: " + r.error[:200])
                continue
            raw_module = r.value
            if FORBIDDEN_DIRECTIVE_RE.search(raw_module):
                brief = ("The module uses `> Using` or `> Include`. It must be "
                         "self-contained. Declare everything it needs.")
                previous_module = raw_module
                log(f"encode: rejected — {brief}")
                stage(ckey, ctitle, "failed", brief)
                continue
            try:
                assembled, name, notes = assemble_module(raw_module, doc, reserved=reserved)
            except ValueError as e:
                brief = str(e)
                previous_module = raw_module
                log(f"encode: rejected — {brief}")
                stage(ckey, ctitle, "failed", brief)
                continue
            for note in notes:
                log(f"encode: {note}")
            for old in root.glob("*.catala_en"):
                old.unlink()
            module_path = root / f"{name}.catala_en"
            module_path.write_text(assembled, encoding="utf-8")

            log(f"gates: G1-G4 on {module_path.name}")
            report, run = gates.run_cheap_gates(module_path, doc_path, cap=limits.battery_cap)
            res.gate_report = report
            for g in report.results:
                log(f"  {g}")
            if report.ok:
                passed_gates = True
                stage(ckey, ctitle, "done", "; ".join(f"{g.id} {g.detail}".strip() for g in report.results))
                break

            g3 = report.get("G3")
            others_ok = all(g.ok for g in report.results if g.id != "G3")
            # An unconditional base case that exhaustive exceptions always
            # override is how the encoder wrote the rule, not something the
            # document says: ENCODER is told to write an unconditional base, and
            # run 4 wrote one under exceptions for `>= 2` and `< 2`. No redraft
            # removes it, so only a conditional dead branch goes to the drafter;
            # the rest is repaired like any other gate failure.
            in_document = [d for d in (g3.evidence.get("dead_branches", []) if g3 else [])
                           if not d.get("unconditional")]
            if (g3 is not None and not g3.ok and others_ok and in_document
                    and dead_redrafts < limits.dead_branch_redrafts):
                # A provably dead branch in a module that is otherwise sound is
                # a clause of ours that cannot change anything. That is a
                # defect in the document, so the document is what gets fixed.
                why = ("The encoding proves these branches can never fire over every "
                       "boundary of their inputs, so the clauses they encode cannot "
                       "change any outcome. Redraft those clauses so each can take "
                       "effect, or remove them:\n"
                       + "\n".join(f"- {d['scope']}.{d['variable']}: {d['branch']}"
                                   for d in in_document))
                stage(ckey, ctitle, "failed", "a provably dead branch: the document is being redrafted")
                log("gates: dead branch — redrafting the document, not the encoding")
                dead_redrafts += 1
                new_doc, new_md = draft(previous=document_md, brief=why,
                                        round_no=round_no * 10 + dead_redrafts)
                if new_doc is None:
                    return fail("draft", "redraft after a dead branch did not parse")
                doc, document_md = new_doc, new_md
                res.doc_id, res.title = doc.doc_id, doc.title
                previous_module, brief = "", ""
                n = 0
                continue

            stage(ckey, ctitle, "failed", "; ".join(f"{g.id}: {g.detail}" for g in report.failures))
            previous_module = raw_module
            brief = report.repair_brief()

        if not passed_gates:
            failed = ", ".join(f"{g.id} ({g.detail[:80]})" for g in (res.gate_report.failures
                                                                     if res.gate_report else []))
            return fail("catala", f"the encoding did not pass G1-G4 after "
                                  f"{limits.catala_attempts} attempts"
                                  + (" on the draft rewritten after a dead branch"
                                     if dead_redrafts else "")
                                  + f": {failed or 'no usable encoding'}")

        # ---- 4. screen
        exercised = gates.exercised_clauses(module_path, run) if run is not None else set()
        while True:
            res.screen_rounds += 1
            skey = f"screen-{res.screen_rounds}"
            stitle = f"Review screen \u2014 round {res.screen_rounds}"
            stage(skey, stitle, "running")
            log(f"screen: round {res.screen_rounds} of {limits.screen_rounds}")
            sr = screen.run_screen(
                doc=doc, document_md=document_md, module=module_path, workspace=root,
                corpus_context=[c.to_dict(with_text=True) for c in context],
                corpus_refs=corpus_refs, exercised_clauses=exercised,
                iteration=res.screen_rounds, model=model,
                base_seed=seed, emit=log, call=call,
            )
            res.screens.append(sr)
            stage(skey, stitle, "done" if sr.passed else "failed", sr.verdict())
            if sr.passed or sr.blocking or res.screen_rounds >= limits.screen_rounds:
                break
            # Incomplete, with nothing blocking: a reviewer never replied, which
            # says nothing about the draft. It used to be redrafted anyway, from
            # a `repair_brief()` holding a heading and no items, so the drafter
            # rewrote a document nobody had faulted. The same draft is screened
            # again instead, on new seeds.
            log("screen: incomplete and nothing blocked \u2014 screening the same draft again")
        if sr.passed:
            break
        cited = {c.clause_id: c.hash for c in doc.clauses}
        for f in sr.blocking:
            res.confirmed_history.append({
                **f.to_dict(), "round": res.screen_rounds,
                "clause_hashes": {cid: cited[cid] for cid in f.clause_ids if cid in cited},
            })
        if res.screen_rounds >= limits.screen_rounds:
            outcome = "blocked" if sr.blocking else "incomplete"
            return fail("screen", f"{outcome} after {res.screen_rounds} round(s): {sr.verdict()}")
        log("screen: redrafting from the findings")
        new_doc, new_md = draft(previous=document_md, brief=sr.repair_brief(),
                                round_no=100 + res.screen_rounds)
        if new_doc is None:
            return fail("draft", "redraft after the screen did not parse")
        doc, document_md = new_doc, new_md
        res.doc_id, res.title = doc.doc_id, doc.title

    if res.confirmed_history:
        stage("screen-replay", "Replay the findings that blocked", "running")
        exercised = gates.exercised_clauses(module_path, run) if run is not None else set()
        still = replay_blocked(res.confirmed_history, doc=doc, module=module_path,
                               corpus_refs=corpus_refs, exercised_clauses=exercised,
                               final_round=res.screen_rounds)
        for h in res.confirmed_history:
            log(f"screen: round-{h['round']} {h['status']} finding on "
                f"{', '.join(h['clause_ids'])} — {h['outcome'][:200]}")
        if still:
            why = "; ".join(f"{', '.join(h['clause_ids'])} ({h['summary'][:80]})" for h in still)
            stage("screen-replay", "Replay the findings that blocked", "failed",
                  f"{len(still)} confirmed finding(s) still reproduce: {why}")
            return fail("screen", f"{len(still)} confirmed finding(s) from an earlier round still "
                                  f"reproduce on the final draft, whose cited clauses did not "
                                  f"change: {why}")
        stage("screen-replay", "Replay the findings that blocked", "done",
              f"{len(res.confirmed_history)} finding(s) replayed; none still reproduces")

    # ---- 5. roundtrip
    if limits.roundtrip:
        rt_dir = root / "roundtrip"
        rt_dir.mkdir(exist_ok=True)
        # think=False for the reason recorded on agents.ENCODER: with reasoning
        # on, the encoding roles spent their whole budget deliberating and
        # returned nothing. Changed here rather than on REENCODER itself, which
        # the drafting roundtrip experiments also use.
        role = dataclasses.replace(REENCODER, reads=[str(doc_path)], think=False)
        for n in range(1, limits.roundtrip_attempts + 1):
            rkey, rtitle = f"roundtrip-{n}", f"Roundtrip (G5) \u2014 attempt {n}"
            stage(rkey, rtitle, "running")
            r = ask(role, _reencoder_prompt(document_md), "roundtrip", n,
                    validate=lambda v: extract_catala(str(v)), s=seed + 5000 + n)
            if not r.ok:
                res.g5 = gates.GateResult("G5", "roundtrip convergence", False,
                                          detail="the re-encoder produced no usable module",
                                          evidence={"diagnostic": r.error[:600]})
                stage(rkey, rtitle, "failed", "the re-encoder produced no usable module")
                continue
            text = MODULE_RE.sub("> Module Roundtrip", r.value, count=1)
            if not module_name(text):
                text = "# Roundtrip\n\n> Module Roundtrip\n\n" + text
            for old in rt_dir.glob("*.catala_en"):
                old.unlink()
            rt_path = rt_dir / "Roundtrip.catala_en"
            rt_path.write_text(text, encoding="utf-8")
            tc = gates.g1_typecheck(rt_path)
            if not tc.ok:
                res.g5 = gates.GateResult("G5", "roundtrip convergence", False,
                                          detail="the independent re-encoding does not compile",
                                          evidence=tc.evidence)
                log(f"G5: attempt {n} — re-encoding does not compile")
                stage(rkey, rtitle, "failed", "the independent re-encoding does not compile")
                continue
            smap = _scope_map(module_path, rt_path)
            log(f"G5: comparing {module_path.name} with an independent re-encoding "
                f"(scopes {smap or 'unmatched'})")
            res.g5 = gates.g5_roundtrip(module_path, rt_path, scope_map=smap or None,
                                        cap=limits.battery_cap)
            log(f"  {res.g5}")
            stage(rkey, rtitle, "done" if res.g5.ok else "failed", res.g5.detail)
            if res.g5.ok:
                break
        if res.g5 is None or not res.g5.ok:
            return fail("roundtrip", "G5: " + (res.g5.detail if res.g5 else "not run"))

    if not limits.roundtrip:
        stage("roundtrip", "Roundtrip (G5)", "done", "skipped on request; the omission is stamped on the PDF")
    res.passed = True
    log("all gates and the screen passed")
    return _finish(res, root, limits, started, log, t_start, stage)


def replay_blocked(
    history: list[dict[str, Any]],
    *,
    doc: Document,
    module: Path,
    corpus_refs: set[str],
    exercised_clauses: set[str],
    final_round: int,
) -> list[dict[str, Any]]:
    """Re-verify every finding that blocked a round against the final draft.

    A later round passing is not evidence that an earlier finding was fixed.
    The reviewers run blind on new seeds, so a round-2 logic reviewer that did
    not happen to probe the same inputs says nothing about them -- and the
    pipeline used to print "Resolved by redrafting" for a confirmed break whose
    clause and code were both byte-identical to the round that confirmed it.

    So each finding is put back through `screen.verify` on the final document
    and encoding, and gets an `outcome`. Returned are the ones that must still
    block: CONFIRMED again, on clauses whose text has not changed, which is the
    same fact the first round established. A finding whose clauses were
    redrafted is recorded, not blocked -- its expected value was derived from
    words that are gone. Agreed judgement findings cannot be re-verified and
    say so.
    """
    ifaces = screen.scope_interfaces(module)
    now = {c.clause_id: c.hash for c in doc.clauses}
    still: list[dict[str, Any]] = []
    for h in history:
        if h.get("status") != screen.CONFIRMED:
            h["outcome"] = (f"a judgement two reviewers shared in round {h['round']}; not "
                            f"raised again by round {final_round}. Cannot be re-verified by "
                            f"execution.")
            continue
        f = screen.Finding(
            agent=h["agent"], kind=h["kind"], clause_ids=list(h.get("clause_ids") or []),
            summary=h.get("summary", ""), quote=h.get("quote", ""), scope=h.get("scope", ""),
            inputs=dict(h.get("inputs") or {}), expected=h.get("expected"),
            corpus_refs=list(h.get("corpus_refs") or []),
        )
        screen.verify(f, doc=doc, module=module, ifaces=ifaces, corpus_refs=corpus_refs,
                      exercised_clauses=exercised_clauses)
        unchanged = all(now.get(cid) == old for cid, old in (h.get("clause_hashes") or {}).items())
        if f.status == screen.CONFIRMED and unchanged:
            h["outcome"] = ("STILL REPRODUCES on the final draft, and the cited clauses are "
                            "unchanged: " + f.verified_by)
            still.append(h)
        elif f.status == screen.CONFIRMED:
            h["outcome"] = ("the cited clauses were redrafted, so the expectation derived from "
                            "the old words no longer binds; replayed as first stated: "
                            + f.verified_by)
        elif f.status == screen.REFUTED:
            h["outcome"] = "resolved, re-verified on the final draft: " + f.verified_by
        else:
            h["outcome"] = "no longer applies to the final draft: " + f.discard_reason
    return still


# --- finishing: triage, report, PDF --------------------------------------


def _provenance(res: GenerationResult, limits: Limits, started: str) -> render.Provenance:
    gate_rows = [g.to_dict() for g in (res.gate_report.results if res.gate_report else [])]
    if res.g5 is not None:
        gate_rows.append(res.g5.to_dict())
    last = res.screens[-1] if res.screens else None
    agents_rows = []
    if last:
        for a in last.agents:
            agents_rows.append({
                "name": a.agent, "enforcement": a.enforcement,
                "raised": len(a.findings),
                "confirmed": sum(1 for f in a.findings if f.status == screen.CONFIRMED),
                "attacks_tried": a.attacks_tried,
            })
    module = next(iter(sorted(res.workspace.glob("*.catala_en"))), None)
    doc_path = res.workspace / "document.md"
    return render.Provenance(
        prompt=res.prompt, model=res.model, seed=res.seed, started=started,
        workspace=str(res.workspace),
        catala_iterations=res.catala_iterations, screen_iterations=res.screen_rounds,
        gates=gate_rows,
        context=[c.to_dict() for c in res.context],
        confirmed=[{
            "agent": h.get("agent"), "clause_ids": h.get("clause_ids"),
            "summary": h.get("summary"), "verified_by": h.get("verified_by"),
            "status": h.get("status"), "round": h.get("round"),
            "outcome": h.get("outcome", ""),
        } for h in res.confirmed_history],
        open_questions=[{
            "agent": f.agent, "clause_ids": f.clause_ids, "summary": f.summary,
        } for f in (last.open_questions if last else [])],
        agents=agents_rows,
        module_sha256=hashlib.sha256(module.read_bytes()).hexdigest() if module else "",
        document_sha256=(hashlib.sha256(doc_path.read_bytes()).hexdigest()
                         if doc_path.exists() else ""),
        failed=not res.passed,
        failure_reason=(f"{res.failure_stage}: {res.failure_reason}" if not res.passed else ""),
        roundtrip_skipped=not limits.roundtrip,
    )


def _finish(
    res: GenerationResult, root: Path, limits: Limits, started: str, log: Callable[[str], None],
    t_start: float,
    stage: Callable[..., None] | None = None,
) -> GenerationResult:
    doc_path = root / "document.md"
    module = next(iter(sorted(root.glob("*.catala_en"))), None)
    doc: Document | None = None
    if doc_path.exists():
        try:
            doc = parse_document(doc_path)
        except ConventionError:
            doc = None

    if doc is not None and module is not None:
        try:
            (root / "triage.yaml").write_text(yaml.safe_dump(
                {"_comment": "DERIVED from the encoding, not adjudicated. See "
                             "lks.generate.derive_triage.",
                 "doc_id": doc.doc_id,
                 "decisions": derive_triage(doc, module)},
                sort_keys=False, allow_unicode=True), encoding="utf-8")
        except Exception as e:                              # noqa: BLE001
            log(f"triage: could not derive ({type(e).__name__}: {e})")

    res.seconds = time.monotonic() - t_start
    st = stage or (lambda *_a, **_k: None)
    if doc is not None and (res.passed or limits.emit_failed_pdf):
        st("pdf", "Issue the PDF", "running")
        prov = _provenance(res, limits, started)
        rr = render.render_document(doc, root / "document.pdf", provenance=prov)
        res.pdf, res.pdf_sha256 = rr.path, rr.sha256
        if rr.unmapped:
            log(f"pdf: WARNING — characters with no WinAnsi glyph were replaced: {rr.unmapped}")
        log(f"pdf: {rr.path} ({rr.pages} pages, sha256 {rr.sha256[:16]})"
            + ("  [stamped FAILED GATE]" if not res.passed else ""))
        st("pdf", "Issue the PDF", "done" if res.passed else "failed",
           f"{rr.pages} pages, sha256 {rr.sha256[:16]}"
           + ("" if res.passed else " \u2014 stamped FAILED GATE on every page"))
    elif not res.passed:
        log("pdf: not issued — a gate did not pass (use --emit-failed-pdf to render it anyway)")
        st("pdf", "Issue the PDF", "running")
        st("pdf", "Issue the PDF", "failed",
           f"not issued \u2014 failed at {res.failure_stage}: {res.failure_reason}")

    (root / "run.json").write_text(json.dumps(res.to_dict(), indent=2, default=str),
                                   encoding="utf-8")
    (root / "report.md").write_text(report_markdown(res), encoding="utf-8")
    return res


def report_markdown(res: GenerationResult) -> str:
    L = [f"# Generation report — {res.title or '(no document)'}", ""]
    L.append(f"**Outcome:** {'ISSUED' if res.passed else 'NOT ISSUED'}")
    if not res.passed:
        L.append(f"**Failed at:** {res.failure_stage} — {res.failure_reason}")
    L += ["", "## Request", "", "> " + res.prompt.strip().replace("\n", "\n> "), ""]
    L += ["## Run", "",
          f"- workspace: `{res.workspace}`",
          f"- model: `{res.model}`, seed `{res.seed}`",
          f"- document: `{res.doc_id}`",
          f"- Catala encodings attempted: {res.catala_iterations}",
          f"- screen rounds: {res.screen_rounds}"]
    if res.pdf:
        L.append(f"- PDF: `{res.pdf}` sha256 `{res.pdf_sha256}`")
    L += ["", "## Retrieval", ""]
    for k, v in res.retrieval_status.items():
        L.append(f"- {k}: {v}")
    L += ["", "| ref | kind | score |", "|---|---|---|"]
    L += [f"| {c.ref} | {c.kind} | {c.score} |" for c in res.context]
    L += ["", "## Gates", ""]
    rows = list(res.gate_report.results) if res.gate_report else []
    if res.g5:
        rows.append(res.g5)
    for g in rows:
        state = "skip" if g.skipped else ("pass" if g.ok else "**FAIL**")
        L.append(f"- **{g.id} {g.name}** — {state}. {g.detail}")
    if res.confirmed_history:
        L += ["", "## Findings that blocked a round, replayed on the final draft", ""]
        for h in res.confirmed_history:
            L.append(f"- round {h.get('round')} `{h.get('status')}` **{h.get('kind')}** "
                     f"{', '.join(h.get('clause_ids') or [])} — {h.get('summary')}"
                     + (f"\n  - outcome: {h['outcome']}" if h.get("outcome") else ""))
    for i, s in enumerate(res.screens, 1):
        L += ["", f"## Screen round {i} — {s.verdict()}", ""]
        for a in s.agents:
            L.append(f"### {a.agent} ({a.enforcement}, {a.attempts} attempt(s), {a.seconds:.0f}s)"
                     + ("" if a.ok else f" — UNUSABLE: {a.error}"))
            if a.attacks_tried:
                L.append("Tried: " + "; ".join(a.attacks_tried))
            for f in a.findings:
                L.append(f"- `{f.status}` **{f.kind}** {', '.join(f.clause_ids)} — {f.summary}"
                         + (f" _(agreed with {', '.join(f.agreed_with)})_" if f.agreed_with else "")
                         + (f"\n  - verified: {f.verified_by}" if f.verified_by else "")
                         + (f"\n  - discarded: {f.discard_reason}" if f.discard_reason else ""))
            L.append("")
    L += ["## Attempts", "",
          "| stage | n | ok | seconds | prompt tokens | completion tokens | file |",
          "|---|---|---|---|---:|---:|---|"]
    L += [f"| {a.stage} | {a.n} | {'yes' if a.ok else 'no'} | {a.seconds} | "
          f"{a.prompt_tokens} | {a.completion_tokens} | `{Path(a.file).name}` |"
          for a in res.attempts]
    return "\n".join(L) + "\n"
