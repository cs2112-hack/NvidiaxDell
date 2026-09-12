"""Client for the local model served by Ollama.

Standard library only, like the rest of this system.

## Why a local model at all

Until now every judgement this system needed from an agent came from outside
it: a Claude session adjudicated the triage, wrote the Catala, played the
adversarial reviewer, and re-encoded the English. That makes the *verification
loop* -- the part the brief says matters most -- dependent on someone sitting
at a terminal. A local model lets the loop run unattended and indefinitely on
the user's own hardware, which is the only way "do not stop until the reviewer
runs out of attacks" becomes a property of the system rather than of a session.

## What it is emphatically NOT for

It never decides a legal question. Every figure still comes from executing
Catala; every quotation still comes from the corpus verbatim. The model's jobs
are all *proposal* jobs whose output is checked by something that cannot lie:
a triage label checked against the adjudicated ledger, a fact pattern checked
by re-executing the scope, a re-encoding checked by AST and behavioural
equivalence, extracted facts checked by showing them to the user before
anything runs. See `lks.agents` for the roles and what each is allowed to see.

## Determinism

Defaults are `temperature=0` and a fixed `seed`, because a verification suite
whose contents depend on sampling luck is not a suite. Sampling is available
for the one role that wants variety -- an adversarial reviewer generating
attack ideas benefits from not proposing the same fact pattern every round --
and that role's output is validated by re-execution anyway.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

OLLAMA_HOST = os.environ.get("LKS_OLLAMA_HOST", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("LKS_MODEL", "qwen3.6:27b-q4_K_M")
DEFAULT_SEED = 20260912


class ModelUnavailable(RuntimeError):
    """Ollama is not reachable, or the model is not present in its store."""


class ModelRefused(RuntimeError):
    """The model returned something the caller cannot use (empty, or not the
    JSON shape the role requires). Raised rather than papered over: a role
    that silently accepts malformed output produces findings nobody can trust."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.seconds + other.seconds,
        )

    def __str__(self) -> str:
        tps = (self.completion_tokens / self.seconds) if self.seconds else 0.0
        return (f"{self.prompt_tokens} in, {self.completion_tokens} out, "
                f"{self.seconds:.1f}s ({tps:.1f} tok/s)")


@dataclass
class Reply:
    text: str
    usage: Usage
    model: str
    thinking: str = ""
    """The model's chain of thought, when `think=True`.

    Kept separate from `text` and never parsed as an answer. qwen3.6 is a
    reasoning model: with thinking on it emits a long deliberation and then the
    answer, and an early version of this client read an empty `response` as a
    failure because the whole token budget had gone into the deliberation.
    Reasoning roughly halves throughput, so it is a per-role choice.
    """
    truncated: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        """Parse the reply as JSON, tolerating the fencing and preamble that
        instruction-tuned models add even when told not to."""
        t = self.text.strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[-1]
            if t.rstrip().endswith("```"):
                t = t.rstrip()[:-3]
        start = min((i for i in (t.find("{"), t.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise ModelRefused(f"no JSON in reply: {self.text[:300]!r}")
        try:
            value, _end = json.JSONDecoder().raw_decode(t[start:])
            return value
        except json.JSONDecodeError as e:
            raise ModelRefused(f"reply is not valid JSON ({e}): {self.text[:300]!r}") from e


def _post(path: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(
        OLLAMA_HOST + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        raise ModelUnavailable(
            f"cannot reach Ollama at {OLLAMA_HOST}: {e}. Start it with: "
            f"~/.local/bin/ollama serve"
        ) from e


def available() -> dict[str, Any]:
    """Models Ollama currently has, or raise ModelUnavailable."""
    try:
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        raise ModelUnavailable(f"cannot reach Ollama at {OLLAMA_HOST}: {e}") from e


def require_model(model: str = DEFAULT_MODEL) -> None:
    names = {m.get("name") for m in available().get("models", [])}
    if model not in names and f"{model}:latest" not in names:
        raise ModelUnavailable(
            f"model {model!r} is not in Ollama's store. Present: {sorted(names)}. "
            f"Stage it with: cp -r ~/Downloads/Hackathon/bundle/models/ollama/* "
            f"~/.ollama/models/"
        )


def generate(
    prompt: str,
    *,
    system: str | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.0,
    seed: int | None = DEFAULT_SEED,
    num_ctx: int = 16384,
    num_predict: int = 2048,
    as_json: bool = False,
    timeout: int = 900,
    stop: list[str] | None = None,
    think: bool = False,
) -> Reply:
    """One completion. Deterministic by default; see the module docstring."""
    options: dict[str, Any] = {
        "temperature": temperature,
        "num_ctx": num_ctx,
        "num_predict": num_predict,
    }
    if seed is not None:
        options["seed"] = seed
    if stop:
        options["stop"] = stop
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }
    if system:
        payload["system"] = system
    if as_json:
        payload["format"] = "json"
    payload["think"] = bool(think)

    t0 = time.monotonic()
    data = _post("/api/generate", payload, timeout)
    elapsed = time.monotonic() - t0

    text = data.get("response") or ""
    thinking = data.get("thinking") or ""
    reason = data.get("done_reason") or ""
    if not text.strip():
        if thinking.strip() and reason == "length":
            raise ModelRefused(
                f"the token budget ({num_predict}) was consumed by reasoning and no "
                f"answer was produced. Raise num_predict or set think=False."
            )
        raise ModelRefused(f"the model returned an empty completion (reason: {reason})")
    return Reply(
        text=text,
        usage=Usage(
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            seconds=elapsed,
        ),
        model=model,
        thinking=thinking,
        truncated=(reason == "length"),
        raw={k: v for k, v in data.items() if k not in ("response", "thinking", "context")},
    )


def warm(model: str = DEFAULT_MODEL, timeout: int = 900) -> Usage:
    """Load the weights so the first real call is not charged for the load.

    A 17 GB model takes a noticeable while to page in, and a role's timing
    numbers are meaningless if one of them paid for that.
    """
    r = generate("Reply with the single word: ready.", model=model,
                 num_predict=16, timeout=timeout, think=False)
    return r.usage
