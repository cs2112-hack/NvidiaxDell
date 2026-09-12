#!/usr/bin/env python3
"""The only outbound inference call in the system. Local model, OpenAI-compatible.

Hard constraint: all runtime inference goes to $LOCAL_LLM_URL (Nemotron via vLLM).
There is no remote fallback on purpose -- if the local endpoint is down, the pipeline
fails loudly rather than quietly phoning somewhere else.
"""
import json, os, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHEATSHEET = ROOT / "CATALA_CHEATSHEET.md"
MODEL = os.environ.get("LOCAL_LLM_MODEL", "nemotron")
TIMEOUT = int(os.environ.get("LOCAL_LLM_TIMEOUT", "300"))


class LlmUnavailable(RuntimeError):
    pass


def base_url() -> str:
    url = os.environ.get("LOCAL_LLM_URL", "").rstrip("/")
    if not url:
        raise LlmUnavailable(
            "LOCAL_LLM_URL is not set. Export it to point at the local vLLM endpoint; "
            "no remote model is permitted in the runtime path.")
    return url


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base_url() + path, method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=TIMEOUT).read())
    except (urllib.error.URLError, TimeoutError) as e:
        raise LlmUnavailable(f"local model at {base_url()} unreachable: {e}") from e


def complete(prompt: str, system: str = "", with_cheatsheet: bool = False,
             temperature: float = 0.0, max_tokens: int = 4096) -> str:
    """One chat completion. `with_cheatsheet` prepends the Catala reference."""
    if with_cheatsheet:
        system = (f"{system}\n\nCatala reference you must follow exactly:\n\n"
                  f"{CHEATSHEET.read_text()}").strip()
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    r = _post("/v1/chat/completions",
              {"model": MODEL, "messages": msgs, "temperature": temperature,
               "max_tokens": max_tokens})
    return r["choices"][0]["message"]["content"]


def embed(texts: list[str]) -> list[list[float]]:
    r = _post("/v1/embeddings", {"model": MODEL, "input": texts})
    return [d["embedding"] for d in sorted(r["data"], key=lambda d: d["index"])]


def available() -> bool:
    try:
        base_url()
    except LlmUnavailable:
        return False
    try:
        _post("/v1/chat/completions",
              {"model": MODEL, "messages": [{"role": "user", "content": "ok"}],
               "max_tokens": 1})
        return True
    except LlmUnavailable:
        return False
