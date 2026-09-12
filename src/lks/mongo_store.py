"""MongoDB-backed vector store.

## How this stays compatible with the versioning requirement

The brief requires that a git checkout of any commit yields the matching
index. A mongod data directory cannot be git-versioned in any useful way, so
Mongo is *not* the source of truth. The split is:

    vectorstore/index/{chunks.jsonl,embeddings.npy,manifest.json}
        canonical, committed, deterministic, byte-identical per corpus state

    MongoDB collection `lks.chunks`
        a materialised, queryable view loaded from that artefact

Every Mongo document is stamped with the `corpus_aggregate` hash from the
manifest it was loaded from. `verify()` then checks three things agree: the
live corpus, the committed manifest, and what Mongo actually holds. Any
disagreement refuses the query and says which pair diverged. So checking out
an older commit and querying does not silently answer from a newer index --
it fails, and tells you to run `scripts/sync_mongo.py`.

## Vector search

`mongodb/mongodb-atlas-local` bundles `mongot`, so `$vectorSearch` works
locally. Plain MongoDB Community has no `mongot` and no `$vectorSearch`; the
store detects that and falls back to exact brute-force cosine over the loaded
vectors. The fallback is not a degradation in answer quality -- with a corpus
this size it is exhaustive and therefore *more* accurate than an approximate
ANN index -- it is only slower, and `status()` reports which path is live so
the difference is never invisible.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .vector import (
    CHUNKS,
    EMBEDDINGS,
    INDEX_DIR,
    MANIFEST,
    Chunk,
    Hit,
    IndexMissingError,
    IndexStaleError,
    _load_model,
    corpus_fingerprint,
)

DEFAULT_URI = os.environ.get("LKS_MONGO_URI", "mongodb://localhost:27017/?directConnection=true")
DB_NAME = os.environ.get("LKS_MONGO_DB", "lks")
COLL = "chunks"
META_COLL = "index_meta"
SEARCH_INDEX = "chunk_vector_index"


class MongoUnavailable(RuntimeError):
    pass


def connect(uri: str = DEFAULT_URI, timeout_ms: int = 3000):
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        client.admin.command("ping")
        return client
    except PyMongoError as e:
        raise MongoUnavailable(
            f"cannot reach MongoDB at {uri}: {e}. Start it with:\n"
            f"  sudo docker run -d --name lks-mongo -p 27017:27017 "
            f"mongodb/mongodb-atlas-local:latest"
        ) from e


def _read_committed_index(index_dir: Path = INDEX_DIR) -> tuple[list[Chunk], np.ndarray, dict]:
    if not (index_dir / MANIFEST).exists():
        raise IndexMissingError(
            f"no committed index at {index_dir}; run: python scripts/build_index.py"
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
            f"committed index corrupt: {len(chunks)} chunks vs {vectors.shape[0]} vectors"
        )
    return chunks, vectors, manifest


def supports_vector_search(db) -> bool:
    """Whether $vectorSearch is available (i.e. mongot is running)."""
    try:
        list(db[COLL].aggregate([{"$listSearchIndexes": {}}]))
        return True
    except Exception:
        return False


def sync(uri: str = DEFAULT_URI, index_dir: Path = INDEX_DIR) -> dict[str, Any]:
    """Load the committed index into MongoDB, replacing what is there.

    Idempotent: syncing twice from the same commit leaves the same contents.
    """
    chunks, vectors, manifest = _read_committed_index(Path(index_dir))
    aggregate = manifest["corpus"]["aggregate"]
    client = connect(uri)
    db = client[DB_NAME]

    docs = []
    for c, v in zip(chunks, vectors):
        d = c.to_dict()
        d["_id"] = c.ref
        d["embedding"] = [float(x) for x in v]
        d["corpus_aggregate"] = aggregate
        docs.append(d)

    db[COLL].delete_many({})
    if docs:
        db[COLL].insert_many(docs)
    db[COLL].create_index("doc_id")
    db[COLL].create_index("label")
    db[COLL].create_index("qualifies")

    db[META_COLL].replace_one(
        {"_id": "current"},
        {
            "_id": "current",
            "corpus_aggregate": aggregate,
            "model": manifest["model"],
            "dim": manifest["dim"],
            "n_chunks": manifest["n_chunks"],
            "labels": manifest["labels"],
            "triage_aggregate": (manifest.get("triage") or {}).get("aggregate"),
            "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        upsert=True,
    )

    vs = _ensure_search_index(db, manifest["dim"])
    client.close()
    return {
        "inserted": len(docs),
        "corpus_aggregate": aggregate,
        "dim": manifest["dim"],
        "vector_search": vs,
    }


def _ensure_search_index(db, dim: int) -> str:
    """Create the Atlas vector search index if mongot is present."""
    if not supports_vector_search(db):
        return "unavailable (no mongot); brute-force cosine will be used"
    try:
        from pymongo.operations import SearchIndexModel

        existing = {i["name"] for i in db[COLL].list_search_indexes()}
        if SEARCH_INDEX in existing:
            return f"present ({SEARCH_INDEX})"
        db[COLL].create_search_index(
            SearchIndexModel(
                name=SEARCH_INDEX,
                type="vectorSearch",
                definition={
                    "fields": [
                        {
                            "type": "vector",
                            "path": "embedding",
                            "numDimensions": dim,
                            "similarity": "cosine",
                        },
                        {"type": "filter", "path": "doc_id"},
                        {"type": "filter", "path": "label"},
                    ]
                },
            )
        )
        return f"created ({SEARCH_INDEX})"
    except Exception as e:  # index creation is best-effort; fallback covers us
        return f"creation failed ({type(e).__name__}: {e}); brute-force cosine will be used"


@dataclass
class MongoVectorStore:
    """Query interface over the Mongo-materialised index.

    Deliberately mirrors `lks.vector.VectorStore`'s surface (`search`,
    `by_ref`, `qualifying`) so the chat layer does not know or care which
    backend is live.
    """

    client: Any
    db: Any
    manifest: dict[str, Any]
    use_vector_search: bool
    _model: Any = None

    @classmethod
    def open(
        cls,
        uri: str = DEFAULT_URI,
        corpus_dir: str | Path = "corpus",
        index_dir: Path = INDEX_DIR,
        verify: bool = True,
    ) -> "MongoVectorStore":
        client = connect(uri)
        db = client[DB_NAME]
        meta = db[META_COLL].find_one({"_id": "current"})
        if meta is None:
            raise IndexStaleError(
                "MongoDB holds no index. Load it with: python scripts/sync_mongo.py"
            )
        store = cls(
            client=client, db=db, manifest=meta,
            use_vector_search=supports_vector_search(db),
        )
        if verify:
            store.verify(corpus_dir, Path(index_dir))
        return store

    def verify(self, corpus_dir: str | Path = "corpus", index_dir: Path = INDEX_DIR) -> None:
        """Three-way agreement: live corpus, committed manifest, Mongo contents."""
        live = corpus_fingerprint(corpus_dir)["aggregate"]
        mongo_agg = self.manifest.get("corpus_aggregate")

        committed = None
        mf = Path(index_dir) / MANIFEST
        if mf.exists():
            committed = json.loads(mf.read_text())["corpus"]["aggregate"]

        if committed is not None and committed != live:
            raise IndexStaleError(
                f"the committed index does not match the corpus in this checkout "
                f"(manifest {committed}, corpus {live}). Rebuild with "
                f"scripts/build_index.py, then scripts/sync_mongo.py"
            )
        if mongo_agg != live:
            raise IndexStaleError(
                f"MongoDB holds the index for a different corpus state "
                f"(mongo {mongo_agg}, corpus {live}). This is what happens after "
                f"checking out another commit without re-syncing. "
                f"Run: python scripts/sync_mongo.py"
            )
        from .vector import triage_fingerprint

        live_triage = triage_fingerprint()["aggregate"]
        mongo_triage = self.manifest.get("triage_aggregate")
        if mongo_triage != live_triage:
            raise IndexStaleError(
                f"MongoDB holds an index built against different triage decisions "
                f"(mongo {mongo_triage}, live {live_triage}). A relabelled clause "
                f"changes what may be quoted at all. Run: "
                f"python scripts/build_index.py && python scripts/sync_mongo.py"
            )
        n_mongo = self.db[COLL].count_documents({})
        if n_mongo != self.manifest.get("n_chunks"):
            raise IndexStaleError(
                f"MongoDB chunk count {n_mongo} does not match its own metadata "
                f"{self.manifest.get('n_chunks')}; re-sync"
            )

    @property
    def model(self):
        if self._model is None:
            self._model = _load_model()
        return self._model

    def _embed(self, query: str) -> list[float]:
        q = np.asarray(self.model.encode([query]), dtype=np.float32)[0]
        n = float(np.linalg.norm(q))
        if n:
            q = q / n
        return [float(x) for x in q]

    def search(
        self, query: str, k: int = 5, doc_id: str | None = None, min_score: float = 0.0
    ) -> list[Hit]:
        qv = self._embed(query)
        if self.use_vector_search:
            hits = self._vector_search(qv, k, doc_id)
        else:
            hits = self._brute_force(qv, k, doc_id)
        return [h for h in hits if h.score >= min_score]

    def _vector_search(self, qv: list[float], k: int, doc_id: str | None) -> list[Hit]:
        stage: dict[str, Any] = {
            "index": SEARCH_INDEX,
            "path": "embedding",
            "queryVector": qv,
            "numCandidates": max(50, k * 10),
            "limit": k,
        }
        if doc_id:
            stage["filter"] = {"doc_id": {"$eq": doc_id}}
        pipeline = [
            {"$vectorSearch": stage},
            {"$addFields": {"_score": {"$meta": "vectorSearchScore"}}},
        ]
        try:
            rows = list(self.db[COLL].aggregate(pipeline))
        except Exception:
            # A freshly created search index is not queryable immediately.
            # Falling back keeps answers exact rather than returning nothing.
            return self._brute_force(qv, k, doc_id)
        if not rows:
            return self._brute_force(qv, k, doc_id)
        return [Hit(chunk=_to_chunk(r), score=float(r.get("_score", 0.0))) for r in rows]

    def _brute_force(self, qv: list[float], k: int, doc_id: str | None) -> list[Hit]:
        """Exact cosine over every chunk. Stored vectors are pre-normalised, so
        the dot product *is* cosine similarity."""
        q = np.asarray(qv, dtype=np.float32)
        flt = {"doc_id": doc_id} if doc_id else {}
        rows = list(self.db[COLL].find(flt))
        if not rows:
            return []
        mat = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
        scores = mat @ q
        order = np.argsort(-scores)[:k]
        return [Hit(chunk=_to_chunk(rows[int(i)]), score=float(scores[int(i)])) for i in order]

    def by_ref(self, ref: str) -> Chunk | None:
        r = self.db[COLL].find_one({"_id": ref})
        return _to_chunk(r) if r else None

    def qualifying(self, module: str) -> list[Chunk]:
        return [_to_chunk(r) for r in self.db[COLL].find({"qualifies": module})]

    def status(self) -> dict[str, Any]:
        return {
            "uri": DEFAULT_URI,
            "db": DB_NAME,
            "n_chunks": self.db[COLL].count_documents({}),
            "corpus_aggregate": self.manifest.get("corpus_aggregate"),
            "dim": self.manifest.get("dim"),
            "synced_at": self.manifest.get("synced_at"),
            "search_path": "$vectorSearch (mongot)" if self.use_vector_search
                           else "exact brute-force cosine (no mongot)",
        }

    def close(self) -> None:
        self.client.close()


_CHUNK_FIELDS = set(Chunk.__dataclass_fields__)


def _to_chunk(row: dict[str, Any]) -> Chunk:
    return Chunk(**{k: v for k, v in row.items() if k in _CHUNK_FIELDS})
