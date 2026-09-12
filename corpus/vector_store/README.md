# vector_store/

`chunks.jsonl` — 1,291 non-rule clauses from 43 documents, written by
`pipeline/ingest.py batch` and committed to git. File-backed, no server, no remote calls.

Fields per chunk:

| Field | Meaning |
|---|---|
| `doc_id` | manifest doc_id the chunk came from |
| `locator` | clause number, or the numbered paragraph for a statute/pleading |
| `heading` | the section heading the clause sits under |
| `text` | the clause text, verbatim, for quotation |
| `qualifies_module` | the Catala module whose computed answer this clause should be quoted beside, or `null` |
| `embedding` | `null` when built offline; filled from `llm.embed` (local Nemotron at `$LOCAL_LLM_URL`) when the model is up |

With embeddings absent, `pipeline/chat.py` retrieves by keyword with IDF weighting. One
gold Q/A pair (QA-015, force majeure asked as "a labor strike … are we excused") needs
semantic retrieval and fails under keyword scoring; it is left failing rather than tuned
around, and it is the clearest single measure of what the local model adds.

Rebuild with `python3 pipeline/ingest.py batch` (add `--offline` to force the deterministic
path even when the model is reachable).
