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
[ -d _build/libcatala ] || { echo "  staging the Catala stdlib…"; mkdir -p _build; flock _build/.lks-build.lock clerk start >/dev/null 2>&1; }

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
# rebuilt when missing OR stale: the router answers from this file, so a
# registry that no longer matches the modules routes to outputs that are gone
"$PY" -c "
import sys
from lks.registry import build_registry, load_registry
b, c = build_registry(), load_registry()
sys.exit(0 if {k: v.to_dict() for k, v in b.items()} == {k: v.to_dict() for k, v in c.items()} else 1)
" >/dev/null 2>&1 || "$PY" scripts/build_registry.py >/dev/null 2>&1
say "rule registry" "$("$PY" -c "
from lks.registry import load_registry, build_registry
r = load_registry() or build_registry()
print(f'{len(r)} scopes in {len({e.module for e in r.values()})} modules')" 2>/dev/null)"

# --- exposure engine ------------------------------------------------------
# Reported rather than run: the fleet needs the model and takes minutes, and the
# operations watcher writes to the queue. Both are explicit acts.
say "exposure" "$("$PY" -c "
from lks import exposure, surface as sf, watchers
from lks.registry import load_registry
reg, doms = load_registry(), sf.load_domains()
probs = sf.validate_domains(doms, reg) + exposure.validate_predicates(None, reg)
q = exposure.summary()
d = len(watchers.check_operations())
print(f'{q[\"total\"]} on the queue, {d} divergence(s) on record, '
      f'{len(doms)}/{len(reg)} scopes mapped'
      + (f' — {len(probs)} problem(s), run: lks exposure doctor' if probs else ''))" 2>/dev/null)"

# --- document search for OpenClaw -----------------------------------------
# Read-only (lks.search_api), bound to the OpenShell bridge so a sandboxed agent
# can reach it as host.openshell.internal. Never bound to the network at large.
SEARCH_PORT="${LKS_SEARCH_PORT:-8770}"
SEARCH_HOST="${LKS_SEARCH_HOST:-$(sg docker -c "docker network inspect -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' openshell-docker" 2>/dev/null)}"
if [ -z "$SEARCH_HOST" ]; then
  say "openclaw search" "not started — no OpenShell bridge (openshell-docker)"
else
  OLD=$(ss -lntp 2>/dev/null | grep ":$SEARCH_PORT " | grep -oP 'pid=\K[0-9]+' | head -1)
  [ -n "$OLD" ] && kill "$OLD" 2>/dev/null && sleep 1
  nohup setsid "$PY" scripts/lks serve-search --host "$SEARCH_HOST" --port "$SEARCH_PORT" \
    > "$LOGS/search.log" 2>&1 &
  for _ in $(seq 1 30); do
    curl -fsS --max-time 2 "http://$SEARCH_HOST:$SEARCH_PORT/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS --max-time 2 "http://$SEARCH_HOST:$SEARCH_PORT/health" >/dev/null 2>&1 \
    && say "openclaw search" "http://$SEARCH_HOST:$SEARCH_PORT (read-only)" \
    || say "openclaw search" "did not come up — see .run/search.log"
fi

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
  say "contradiction watch" "$(curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/watch" 2>/dev/null | "$PY" -c "
import json, sys
w = json.load(sys.stdin)
c = w.get('counts') or {}
print(('running' if w['running'] else 'NOT running') + f', every {w[\"interval\"]}s'
      + (f', mongo {w[\"mongo\"]}' if w.get('mongo') != 'connected' else
         f', {sum(c.values())} document(s) tracked, {c.get(\"pending\", 0)} pending'))" 2>/dev/null \
    || echo "off (LKS_DOC_WATCH=0) or not answering")"
  echo
  echo "  Open http://127.0.0.1:$PORT"
  echo "  Logs in .run/   Stop with ./scripts/stop.sh"
else
  say "web" "did not come up — see .run/web.log"; exit 1
fi
