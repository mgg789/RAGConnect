#!/usr/bin/env bash
# RAGConnect local stack stopper for macOS
# Kills processes listening on LightRAG (9621) and proxy (9622) ports.

set -euo pipefail

PORTS=("${@:-9621 9622 8090}")

for port in "${PORTS[@]}"; do
  pids=$(lsof -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "[RAGConnect] Stopping process(es) on port $port: $pids"
    kill -TERM $pids 2>/dev/null || true
  else
    echo "[RAGConnect] Nothing listening on port $port"
  fi
done
