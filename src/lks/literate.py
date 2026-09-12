"""Parse and machine-check the literate Catala sources.

A literate legal source makes one promise: the prose above the code is the law
the code encodes. That promise decays silently. Someone edits the policy, the
Catala is updated, and the quoted clause above it still says what the policy
used to say -- and now the file actively lies to every reader, including the
next person to modify the rule.

So the quotation is checked, not trusted. `check_fidelity` compares every
quoted clause against the corpus by content hash and fails the build on drift.

Convention (see docs/ARCHITECTURE.md):

    > EMP-ANNEX-C C-4.1 (001-employment-terms-annex-c.md:63)
    >
    > An Employee is entitled to an overtime payment in respect of each hour
    > worked in excess of 40 hours in a Payroll Week, calculated at 1.25 times
    > the Base Hourly Rate.

    ```catala
    <code encoding exactly that clause>
    ```

Catala treats everything outside a ```catala fence as law text, so the
blockquote is inert to the compiler and carries no syntax risk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import content_hash
from .segment import load_corpus

CITATION_RE = re.compile(
    r"^>\s*([A-Z][A-Z0-9\-]+)\s+([A-Z]{1,4}-\d+(?:\.\d+)*)\s*\(([^:]+):(\d+)\)\s*$"
)
FENCE_OPEN_RE = re.compile(r"^```catala(?:-metadata|-test)?\s*$")
FENCE_CLOSE_RE = re.compile(r"^```\s*$")


@dataclass
class Quotation:
    doc_id: str
    clause_id: str
    filename: str
    line: int
    text: str
    at_line: int          # line in the .catala_en file where the quote starts
    source_file: str

    @property
    def ref(self) -> str:
        return f"{self.doc_id} {self.clause_id}"

    @property
    def hash(self) -> str:
        return content_hash(self.text)


@dataclass
class CodeBlock:
    code: str
    start_line: int
    end_line: int
    kind: str                       # "catala" | "catala-metadata" | "catala-test"
    quotes: list[Quotation] = field(default_factory=list)
    source_file: str = ""


@dataclass
class LiterateFile:
    path: str
    quotations: list[Quotation] = field(default_factory=list)
    blocks: list[CodeBlock] = field(default_factory=list)


def parse_literate(path: str | Path) -> LiterateFile:
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lf = LiterateFile(path=str(path))

    i = 0
    pending: list[Quotation] = []
    while i < len(lines):
        line = lines[i]

        if line.startswith(">"):
            m = CITATION_RE.match(line)
            if m:
                doc_id, clause_id, fname, lno = m.groups()
                j = i + 1
                buf: list[str] = []
                while j < len(lines) and lines[j].startswith(">"):
                    if CITATION_RE.match(lines[j]):
                        break
                    buf.append(re.sub(r"^>\s?", "", lines[j]))
                    j += 1
                q = Quotation(
                    doc_id=doc_id,
                    clause_id=clause_id,
                    filename=fname,
                    line=int(lno),
                    text="\n".join(buf).strip(),
                    at_line=i + 1,
                    source_file=str(path),
                )
                lf.quotations.append(q)
                pending.append(q)
                i = j
                continue
            i += 1
            continue

        fo = FENCE_OPEN_RE.match(line)
        if fo:
            kind = line.strip().strip("`") or "catala"
            start = i
            j = i + 1
            body: list[str] = []
            while j < len(lines) and not FENCE_CLOSE_RE.match(lines[j]):
                body.append(lines[j])
                j += 1
            lf.blocks.append(
                CodeBlock(
                    code="\n".join(body),
                    start_line=start + 1,
                    end_line=j + 1,
                    kind=kind,
                    quotes=list(pending),
                    source_file=str(path),
                )
            )
            pending = []
            i = j + 1
            continue

        # A non-blank, non-quote, non-fence line ends the association between
        # a quotation and the code block below it. Without this, a quotation
        # could claim a code block three sections further down the file.
        if line.strip() and not line.startswith("#"):
            pending = []
        i += 1

    return lf


@dataclass
class FidelityProblem:
    kind: str
    where: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where}: {self.detail}"


def check_fidelity(
    catala_dir: str | Path = "catala/modules", corpus_dir: str | Path = "corpus"
) -> list[FidelityProblem]:
    """Every quotation must match the corpus verbatim, and every code block
    that defines legal content must carry a quotation."""
    clauses = {}
    for doc in load_corpus(corpus_dir):
        for c in doc.clauses:
            clauses[c.ref] = c

    problems: list[FidelityProblem] = []
    files = sorted(Path(catala_dir).glob("*.catala_en"))
    if not files:
        return [FidelityProblem("no-sources", str(catala_dir), "no .catala_en files found")]

    for f in files:
        lf = parse_literate(f)
        if not lf.quotations:
            problems.append(
                FidelityProblem("unsourced-file", f.name, "contains no clause quotations")
            )
        for q in lf.quotations:
            c = clauses.get(q.ref)
            if c is None:
                problems.append(
                    FidelityProblem(
                        "unknown-clause", f"{f.name}:{q.at_line}",
                        f"quotes {q.ref}, which is not in the corpus",
                    )
                )
                continue
            if q.hash != c.hash:
                problems.append(
                    FidelityProblem(
                        "quotation-drift", f"{f.name}:{q.at_line}",
                        f"quoted text for {q.ref} does not match the corpus "
                        f"({q.hash} != {c.hash}); the source clause has changed "
                        f"or the quotation was edited",
                    )
                )
            if Path(c.source_path).name != q.filename:
                problems.append(
                    FidelityProblem(
                        "wrong-file", f"{f.name}:{q.at_line}",
                        f"{q.ref} cites {q.filename} but lives in "
                        f"{Path(c.source_path).name}",
                    )
                )
            elif c.line_start != q.line:
                problems.append(
                    FidelityProblem(
                        "stale-line", f"{f.name}:{q.at_line}",
                        f"{q.ref} cites line {q.line}, actually at {c.line_start}",
                    )
                )
        for b in lf.blocks:
            if b.kind == "catala" and not b.quotes:
                problems.append(
                    FidelityProblem(
                        "unsourced-code", f"{f.name}:{b.start_line}",
                        "code block has no clause quotation directly above it",
                    )
                )
    return problems


def encoded_refs(catala_dir: str | Path = "catala/modules") -> dict[str, list[str]]:
    """ref -> list of files encoding it. Used to check coverage of RULE clauses
    and to detect a clause encoded in two places (a conflict by construction)."""
    out: dict[str, list[str]] = {}
    for f in sorted(Path(catala_dir).glob("*.catala_en")):
        for q in parse_literate(f).quotations:
            out.setdefault(q.ref, []).append(f.name)
    return out
