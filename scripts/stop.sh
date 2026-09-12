#!/usr/bin/env bash
# Stop what start.sh started. Leaves MongoDB's data and the model store alone.
cd "$(dirname "$0")/.." || exit 1
PORT="${LKS_PORT:-8765}"
P=$(ss -lntp 2>/dev/null | grep ":$PORT" | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$P" ] && kill "$P" && echo "  stopped web (pid $P)"
SEARCH_PORT="${LKS_SEARCH_PORT:-8770}"
P=$(ss -lntp 2>/dev/null | grep ":$SEARCH_PORT " | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$P" ] && kill "$P" && echo "  stopped openclaw search (pid $P)"
P=$(pgrep -f 'ollama serv[e]' | head -1)
[ -n "$P" ] && kill "$P" && echo "  stopped ollama (pid $P)"
sg docker -c "docker stop lks-mongo" >/dev/null 2>&1 && echo "  stopped mongodb"
echo "  done"
