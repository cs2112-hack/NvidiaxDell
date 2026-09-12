#!/usr/bin/env bash
# Bring the whole system up and tell you where it is.
#   ./scripts/start.sh            everything
#   ./scripts/start.sh --no-model skip the local model (chat + rules still work)
cd "$(dirname "$0")/.." || exit 1
. scripts/env.sh

WANT_MODEL=1
[ "${1:-}" = "--no-model" ] && WANT_MODEL=0
PORT="${LKS_PORT:-8765}"
LOGS="$(pwd)/.run"; mkdir -p "$LOGS"

say() { printf '  %-34s %s\n' "$1" "$2"; }
echo "Legal Knowledge System — starting"

# --- Catala toolchain ------------------------------------------------------
if command -v catala >/dev/null 2>&1; then
  say "catala" "$(catala --version 2>/dev/null)"
else
  say "catala" "MISSING — run ./scripts/install_catala.sh"; exit 1
fi
[ -d _build/libcatala ] || { echo "  staging the Catala stdlib…"; clerk start >/dev/null 2>&1; }

# --- MongoDB (optional; the file-backed store works without it) -----------
if ss -lnt 2>/dev/null | grep -q ':27017'; then
  say "mongodb" "already running on 27017"
else
  if sg docker -c "docker start lks-mongo" >/dev/null 2>&1; then
    say "mongodb" "started container lks-mongo"
  elif sg docker -c "docker run -d --name lks-mongo -p 27017:27017 mongodb/mongodb-atlas-local:latest" >/dev/null 2>&1; then
    say "mongodb" "created container lks-mongo"
  else
    say "mongodb" "not available — using the committed file index"
  fi
fi

# --- vector index ---------------------------------------------------------
if "$PY" -c "from lks.vector import VectorStore; VectorStore.open()" >/dev/null 2>&1; then
  say "vector index" "matches the corpus"
else
  echo "  rebuilding the vector index…"
  "$PY" scripts/build_index.py >/dev/null 2>&1 && say "vector index" "rebuilt"
fi
if ss -lnt 2>/dev/null | grep -q ':27017'; then
  "$PY" scripts/sync_mongo.py >/dev/null 2>&1 && say "mongo index" "synced" \
    || say "mongo index" "sync failed — the file index will be used"
fi

# --- local model ----------------------------------------------------------
if [ "$WANT_MODEL" = "1" ]; then
  if curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    say "ollama" "already running"
  elif [ -x "$HOME/.local/bin/ollama" ]; then
    nohup setsid "$HOME/.local/bin/ollama" serve > "$LOGS/ollama.log" 2>&1 &
    for _ in $(seq 1 40); do
      curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
      sleep 1
    done
    curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
      && say "ollama" "started" || say "ollama" "did not come up — see .run/ollama.log"
  else
    say "ollama" "not installed — the model-backed features are off"
  fi
  M=$("$PY" -c "from lks import llm; print(llm.DEFAULT_MODEL)" 2>/dev/null)
  if curl -fsS --max-time 5 http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q "$M"; then
    say "model" "$M present"
  else
    say "model" "$M NOT in the store — see docs/AGENTS.md"
  fi
fi

# --- registry -------------------------------------------------------------
[ -f catala/registry.yaml ] || "$PY" scripts/build_registry.py >/dev/null 2>&1
say "rule registry" "$("$PY" -c "
from lks.registry import load_registry, build_registry
r = load_registry() or build_registry()
print(f'{len(r)} scopes in {len({e.module for e in r.values()})} modules')" 2>/dev/null)"

# --- web ------------------------------------------------------------------
OLD=$(ss -lntp 2>/dev/null | grep ":$PORT" | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$OLD" ] && kill "$OLD" 2>/dev/null && sleep 2
nohup setsid "$PY" scripts/lks serve --port "$PORT" > "$LOGS/web.log" 2>&1 &
for _ in $(seq 1 60); do
  curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/state" >/dev/null 2>&1 && break
  sleep 1
done
if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/state" >/dev/null 2>&1; then
  say "web" "http://127.0.0.1:$PORT"
  echo
  echo "  Open http://127.0.0.1:$PORT"
  echo "  Logs in .run/   Stop with ./scripts/stop.sh"
else
  say "web" "did not come up — see .run/web.log"; exit 1
fi
