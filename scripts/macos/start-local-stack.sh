#!/usr/bin/env bash
# RAGConnect local stack starter for macOS
#
# Starts the embedding proxy (if LOCAL_EMBEDDING_MODE=true) and LightRAG server.
# All configuration is read from ~/.ragconnect/.env
#
# Usage:
#   bash start-local-stack.sh [--repo-root /path/to/RAGConnect]

set -euo pipefail

REPO_ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

RAG_HOME="$HOME/.ragconnect"
ENV_FILE="$RAG_HOME/.env"
PYTHON="$RAG_HOME/.venv/bin/python3"
LIGHTRAG_EXE="$RAG_HOME/.venv/bin/lightrag-server"
DATA_DIR="$RAG_HOME/data/lightrag"

# ── Load .env ──────────────────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"          # ltrim
    [[ -z "$line" || "$line" == \#* ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    val="${val%\"}"
    val="${val#\"}"
    val="${val%\'}"
    val="${val#\'}"
    export "$key=$val"
  done < "$ENV_FILE"
  echo "[RAGConnect] Loaded .env"
else
  echo "[RAGConnect] WARNING: .env not found at $ENV_FILE — using system environment"
fi

PROXY_PORT="${PROXY_PORT:-9622}"
mkdir -p "$DATA_DIR"

# ── Helper: check if port is listening ────────────────────────────────────────
port_in_use() { lsof -iTCP:"$1" -sTCP:LISTEN -t &>/dev/null; }

wait_http() {
  local url="$1" tries="${2:-30}" delay="${3:-2}"
  for ((i=0; i<tries; i++)); do
    if curl -sf --max-time 3 "$url" &>/dev/null; then return 0; fi
    sleep "$delay"
  done
  return 1
}

# ── Embedding proxy ────────────────────────────────────────────────────────────
if [[ "${LOCAL_EMBEDDING_MODE:-}" == "true" ]]; then
  if port_in_use "$PROXY_PORT"; then
    echo "[RAGConnect] Proxy already running on port $PROXY_PORT"
  else
    echo "[RAGConnect] Starting embedding proxy on port $PROXY_PORT ..."

    # Determine working directory (local_embeddings must be resolvable)
    PROXY_CWD="$RAG_HOME"
    if [[ -n "$REPO_ROOT" && -d "$REPO_ROOT/local_embeddings" ]]; then
      PROXY_CWD="$REPO_ROOT"
    fi

    PYTHONPATH="$PROXY_CWD${PYTHONPATH:+:$PYTHONPATH}" \
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 \
      nohup "$PYTHON" -m local_embeddings.proxy \
        >"$RAG_HOME/proxy.stdout.log" 2>"$RAG_HOME/proxy.stderr.log" &

    echo "[RAGConnect] Waiting for proxy to become healthy ..."
    if ! wait_http "http://127.0.0.1:$PROXY_PORT/health"; then
      echo "[RAGConnect] ERROR: Proxy did not start. Check $RAG_HOME/proxy.stderr.log" >&2
      exit 1
    fi
    export OPENAI_API_BASE="http://127.0.0.1:$PROXY_PORT/v1"
  fi
fi

# ── LightRAG server ────────────────────────────────────────────────────────────
if port_in_use 9621; then
  echo "[RAGConnect] LightRAG already running on port 9621"
else
  echo "[RAGConnect] Starting LightRAG on port 9621 ..."

  PYTHONUTF8=1 PYTHONIOENCODING=utf-8 \
    nohup "$LIGHTRAG_EXE" \
      --host 127.0.0.1 --port 9621 \
      --working-dir "$DATA_DIR" \
      --llm-binding openai \
      --embedding-binding openai \
      >"$RAG_HOME/lightrag.stdout.log" 2>"$RAG_HOME/lightrag.stderr.log" &

  echo "[RAGConnect] Waiting for LightRAG to become healthy ..."
  if ! wait_http "http://127.0.0.1:9621/health" 45 2; then
    echo "[RAGConnect] ERROR: LightRAG did not start. Check $RAG_HOME/lightrag.stderr.log" >&2
    exit 1
  fi
fi

# ── ragconnect-web (destination config UI on port 8090) ──────────────────────
WEB_EXE="$RAG_HOME/.venv/bin/ragconnect-web"
if [[ -x "$WEB_EXE" ]]; then
  if port_in_use 8090; then
    echo "[RAGConnect] ragconnect-web already running on port 8090"
  else
    echo "[RAGConnect] Starting ragconnect-web on port 8090 ..."
    nohup "$WEB_EXE" \
      >"$RAG_HOME/web.stdout.log" 2>"$RAG_HOME/web.stderr.log" &
  fi
fi

echo ""
echo "[RAGConnect] Local stack is running."
echo "  LightRAG         : http://127.0.0.1:9621"
echo "  Destination UI   : http://127.0.0.1:8090"
[[ "${LOCAL_EMBEDDING_MODE:-}" == "true" ]] && echo "  Embedding proxy  : http://127.0.0.1:$PROXY_PORT"
