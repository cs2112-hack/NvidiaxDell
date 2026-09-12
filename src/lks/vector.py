"""Local embedded vector store, versioned alongside the Catala source.

Requirement: "a git checkout of any commit gives you the matching index."

That requirement has a sharp edge most vector stores fail on. It is not enough
to commit an index; the index must be *provably* the index for the corpus at
that commit, and the store must refuse to answer when it is not. Otherwise a
checkout of an old commit with a newer index silently answers questions about
text that commit does not contain -- which, in a legal system, is the worst
possible failure.

So:
  * Embeddings come from a *static* model (model2vec / potion-base-8M). Static
    embeddings are a token->vector lookup plus a pooled average: no attention,
    no dropout, no nondeterministic kernels. The same text yields bit-identical
    vectors on any machine, forever. A transformer encoder would not give us
    that, and reproducibility matters more here than a few points of recall.
  * The model weights are committed to the repo (30MB, once). A checkout is
    then self-contained: no network, no model-registry drift.
  * `manifest.json` pins the corpus hash, the per-clause hashes, the model
    identity and the embedding dimension. `VectorStore.open()` verifies the
    manifest against the live corpus and raises `IndexStaleError` on mismatch.

Layout (all committed):
    vectorstore/index/chunks.jsonl      text + metadata, one JSON object/line
    vectorstore/index/embeddings.npy    float32 [n_chunks, dim], row i <-> line i
    vectorstore/index/manifest.json     provenance and integrity pins
    vectorstore/model/potion-base-8M/   the embedding model itself
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .model import Clause, content_hash
from .segment import load_corpus
from .triage import Label, load_ledger

INDEX_DIR = Path("vectorstore/index")
MODEL_DIR = Path("vectorstore/model/potion-base-8M")
CHUNKS = "chunks.jsonl"
EMBEDDINGS = "embeddings.npy"
MANIFEST = "manifest.json"


class IndexStaleError(RuntimeError):
    """The committed index does not match the corpus in this checkout."""


class IndexMissingError(RuntimeError):
    pass


def _load_model(model_dir: Path = MODEL_DIR):
    from model2vec import StaticModel

    if not model_dir.exists():
        raise IndexMissingError(
            f"embedding model not found at {model_dir}; run scripts/build_index.py"
        )
    return StaticModel.from_pretrained(str(model_dir))


def model_fingerprint(model_dir: Path = MODEL_DIR) -> str:
    """Hash the model weights so an index can prove which model built it."""
    h = hashlib.sha256()
    for p in sorted(model_dir.rglob("*")):
        if p.is_file() and p.suffix in (".safetensors", ".json"):
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


@dataclass
class Chunk:
    """A retrievable unit of prose, carrying its pointer back into Catala.

    `qualifies` is the whole point of the store: a PROSE clause is not free-
    floating context, it *qualifies* a rule. C-1.2 ("where this Annex would
    produce a result less favourable than the statutory minimum, the statutory
    minimum prevails") qualifies every overtime computation in the module. A
    retrieval that returns C-1.2 without telling you which module it overrides
    is close to useless.
    """

    ref: str
    doc_id: str
    clause_id: str
    section_id: str
    section_title: str
    doc_title: str
    version: str
    effective_date: str
    label: str
    text: str
    clause_hash: str
    source_path: str
    line_start: int
    qualifies: list[str]
    judgement_inputs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chunk":
        return cls(**d)

    def embed_text(self) -> str:
        """What actually gets embedded.

        We prepend the document title and section heading. A bare clause body
        like "This Agreement is governed by the law of England and Wales" is
        near-identical across every contract in the corpus; without the
        document context the store cannot tell you *whose* governing law you
        just retrieved.
        """
        return f"{self.doc_title} :: {self.section_id} {self.section_title} :: {self.text}"

    def cite(self) -> str:
        return f"{self.ref} ({Path(self.source_path).name}:{self.line_start})"


def build_chunks(corpus_dir: str | Path = "corpus") -> list[Chunk]:
    """Chunk = clause. Deliberately not a sliding window.

    Sliding-window chunking would split "Tier 1 city: GBP 75 per Travel Day"
    from the sentence that gives it force, and would let a citation point at
    half a sentence. The clause is the unit the document itself makes
    addressable, and it is the unit a lawyer cites. PROSE and HYBRID clauses
    are indexed; RULE clauses are not -- they are answered by executing Catala,
    and indexing them would invite the chat layer to quote code as if it were
    law.
    """
    ledger = load_ledger()
    chunks: list[Chunk] = []
    for doc in load_corpus(corpus_dir):
        for c in doc.clauses:
            d = ledger.get(c.ref)
            label = d.label if d else Label.PROSE
            if label is Label.RULE:
                continue
            chunks.append(
                Chunk(
                    ref=c.ref,
                    doc_id=c.doc_id,
                    clause_id=c.clause_id,
                    section_id=c.section_id,
                    section_title=c.section_title,
                    doc_title=doc.title,
                    version=doc.version,
                    effective_date=doc.effective_date,
                    label=label.value,
                    text=c.body,
                    clause_hash=c.hash,
                    source_path=c.source_path,
                    line_start=c.line_start,
                    qualifies=list(d.qualifies) if d else [],
                    judgement_inputs=list(d.judgement_inputs) if d else [],
                )
            )
    return chunks


def corpus_fingerprint(corpus_dir: str | Path = "corpus") -> dict[str, str]:
    """Per-clause hashes plus an aggregate. Per-clause so that a stale index
    can say *which* clause moved, not merely that something did."""
    per: dict[str, str] = {}
    for doc in load_corpus(corpus_dir):
        for c in doc.clauses:
            per[c.ref] = c.hash
    agg = content_hash("|".join(f"{k}={v}" for k, v in sorted(per.items())))
    return {"aggregate": agg, "clauses": per}


def triage_fingerprint() -> dict[str, Any]:
    """Fingerprint of the triage decisions, which govern what belongs in the
    index at all.

    Pinning the corpus text alone is not enough, and the gap is a silent one.
    Re-adjudicating triage changes no clause text, so the corpus hash still
    matches -- yet a clause relabelled PROSE -> RULE must LEAVE the index and a
    clause relabelled RULE -> HYBRID must ENTER it. Without this pin the store
    happily serves an index containing rule clauses, and the chat layer can
    then quote a rule as prose. That is exactly the silent blending of the two
    engines the architecture forbids, arrived at through a stale index rather
    than a bad answer.
    """
    led = load_ledger()
    per = {ref: d.label.value for ref, d in sorted(led.items())}
    agg = content_hash("|".join(f"{k}={v}" for k, v in per.items()))
    return {"aggregate": agg, "labels": per}


def build_index(
    corpus_dir: str | Path = "corpus", index_dir: str | Path = INDEX_DIR
) -> dict[str, Any]:
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    chunks = build_chunks(corpus_dir)
    if not chunks:
        raise RuntimeError("no PROSE/HYBRID chunks to index")

    model = _load_model()
    vectors = model.encode([c.embed_text() for c in chunks])
    vectors = np.asarray(vectors, dtype=np.float32)
    # Normalise once at build time so query-time scoring is a plain dot product.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = vectors / norms

    with (index_dir / CHUNKS).open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    np.save(index_dir / EMBEDDINGS, vectors)

    fp = corpus_fingerprint(corpus_dir)
    tfp = triage_fingerprint()
    manifest = {
        "schema": 2,
        "n_chunks": len(chunks),
        "dim": int(vectors.shape[1]),
        "model": {
            "name": "minishlab/potion-base-8M",
            "kind": "static",
            "local_path": str(MODEL_DIR),
            "fingerprint": model_fingerprint(),
        },
        "corpus": {
            "aggregate": fp["aggregate"],
            "n_clauses": len(fp["clauses"]),
            "indexed_refs": sorted(c.ref for c in chunks),
        },
        "triage": {
            "aggregate": tfp["aggregate"],
            "n_decisions": len(tfp["labels"]),
        },
        "labels": {
            lab: sum(1 for c in chunks if c.label == lab) for lab in ("PROSE", "HYBRID")
        },
        # No build timestamp: the manifest must be byte-identical for identical
        # input, or every rebuild shows a spurious diff and reviewers stop
        # reading them.
    }
    (index_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


@dataclass
class Hit:
    chunk: Chunk
    score: float


class VectorStore:
    def __init__(self, chunks: list[Chunk], vectors: np.ndarray, manifest: dict[str, Any]):
        self.chunks = chunks
        self.vectors = vectors
        self.manifest = manifest
        self._model = None

    @classmethod
    def open(
        cls,
        index_dir: str | Path = INDEX_DIR,
        corpus_dir: str | Path = "corpus",
        verify: bool = True,
    ) -> "VectorStore":
        index_dir = Path(index_dir)
        if not (index_dir / MANIFEST).exists():
            raise IndexMissingError(
                f"no index at {index_dir}; run: python scripts/build_index.py"
            )
        manifest = json.loads((index_dir / MANIFEST).read_text())
        chunks = [
            Chunk.from_dict(json.loads(line))
            for line in (index_dir / CHUNKS).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        vectors = np.load(index_dir / EMBEDDINGS)
        if len(chunks) != vectors.shape[0]:
            raise IndexStaleError(
                f"index corrupt: {len(chunks)} chunks vs {vectors.shape[0]} vectors"
            )
        store = cls(chunks, vectors, manifest)
        if verify:
            store.verify(corpus_dir)
        return store

    def verify(self, corpus_dir: str | Path = "corpus") -> None:
        """Refuse to serve an index that does not match this checkout.

        Checks BOTH the corpus text and the triage decisions. Either can change
        without the other, and each independently invalidates the index.
        """
        # -- triage labels -------------------------------------------------
        tfp = triage_fingerprint()
        pinned = (self.manifest.get("triage") or {}).get("aggregate")
        if pinned is None:
            raise IndexStaleError(
                "this index predates triage pinning (manifest schema "
                f"{self.manifest.get('schema')}); it cannot be shown to match the "
                "triage decisions in this checkout. Rebuild with: "
                "python scripts/build_index.py"
            )
        if pinned != tfp["aggregate"]:
            should = {
                r for r, lab in tfp["labels"].items() if lab in ("PROSE", "HYBRID")
            }
            have = {c.ref for c in self.chunks}
            wrong = sorted(have - should)
            missing = sorted(should - have)
            detail = []
            if wrong:
                detail.append(
                    f"{len(wrong)} clause(s) in the index are no longer PROSE or "
                    f"HYBRID and must not be quotable: {wrong[:5]}"
                )
            if missing:
                detail.append(
                    f"{len(missing)} PROSE/HYBRID clause(s) are absent from the "
                    f"index: {missing[:5]}"
                )
            if not detail:
                detail.append(
                    "a triage label changed without changing which clauses are "
                    "indexed (a RULE/HYBRID move)"
                )
            raise IndexStaleError(
                "vector index does not match the triage decisions in this "
                "checkout -- " + "; ".join(detail)
                + ". Rebuild with: python scripts/build_index.py"
            )

        # -- corpus text ---------------------------------------------------
        fp = corpus_fingerprint(corpus_dir)
        if fp["aggregate"] == self.manifest["corpus"]["aggregate"]:
            return
        live = fp["clauses"]
        indexed = {c.ref: c.clause_hash for c in self.chunks}
        changed = [r for r, h in indexed.items() if r in live and live[r] != h]
        gone = [r for r in indexed if r not in live]
        ledger = load_ledger()
        added = [
            r
            for r in live
            if r not in indexed
            and (ledger.get(r).label.value if ledger.get(r) else "PROSE") != "RULE"
        ]
        detail = []
        if changed:
            detail.append(f"{len(changed)} clause(s) changed: {changed[:5]}")
        if gone:
            detail.append(f"{len(gone)} indexed clause(s) no longer in corpus: {gone[:5]}")
        if added:
            detail.append(f"{len(added)} corpus clause(s) missing from index: {added[:5]}")
        if not detail:
            detail.append(
                "aggregate corpus hash differs though no per-clause change was "
                "localised"
            )
        raise IndexStaleError(
            "vector index does not match the corpus in this checkout -- "
            + "; ".join(detail)
            + ". Rebuild with: python scripts/build_index.py"
        )

    @property
    def model(self):
        if self._model is None:
            self._model = _load_model()
        return self._model

    def search(
        self,
        query: str,
        k: int = 5,
        doc_id: str | None = None,
        min_score: float = 0.0,
    ) -> list[Hit]:
        q = np.asarray(self.model.encode([query]), dtype=np.float32)[0]
        n = float(np.linalg.norm(q))
        if n:
            q = q / n
        scores = self.vectors @ q
        order = np.argsort(-scores)
        hits: list[Hit] = []
        for i in order:
            c = self.chunks[int(i)]
            if doc_id and c.doc_id != doc_id:
                continue
            s = float(scores[int(i)])
            if s < min_score:
                break
            hits.append(Hit(chunk=c, score=s))
            if len(hits) >= k:
                break
        return hits

    def by_ref(self, ref: str) -> Chunk | None:
        for c in self.chunks:
            if c.ref == ref:
                return c
        return None

    def qualifying(self, module: str) -> list[Chunk]:
        """Prose clauses that qualify a given Catala module -- the join that
        lets a rule-engine answer carry its prose caveats."""
        return [c for c in self.chunks if module in c.qualifies]
