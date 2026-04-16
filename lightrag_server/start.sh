#!/bin/bash
# RAGConnect LightRAG entrypoint
#
# Starts the routing proxy when split embedding / local Jina mode is needed,
# then starts lightrag-server with the correct OPENAI_API_BASE.
#
# The proxy is skipped (zero overhead) when:
#   - LOCAL_EMBEDDING_MODE is not "true"
#   - EMBEDDING_API_BASE is empty or equals OPENAI_API_BASE
set -e

PROXY_PORT="${PROXY_PORT:-9622}"
LIGHTRAG_HOST="${LIGHTRAG_HOST:-0.0.0.0}"
LIGHTRAG_PORT="${LIGHTRAG_PORT:-9621}"
LIGHTRAG_WORKING_DIR="${LIGHTRAG_WORKING_DIR:-/data/lightrag}"

_needs_proxy() {
    [ "${LOCAL_EMBEDDING_MODE:-false}" = "true" ] && return 0
    local embed_base="${EMBEDDING_API_BASE:-}"
    local llm_base="${OPENAI_API_BASE:-https://api.openai.com/v1}"
    [ -n "$embed_base" ] && [ "$embed_base" != "$llm_base" ] && return 0
    return 1
}

if _needs_proxy; then
    echo "[start.sh] Starting embedding proxy on port ${PROXY_PORT}..."
    cd /app
    python -m local_embeddings.proxy &
    PROXY_PID=$!

    # Wait up to 15 s for the proxy to be ready
    for i in $(seq 1 15); do
        if curl -sf "http://localhost:${PROXY_PORT}/health" > /dev/null 2>&1; then
            echo "[start.sh] Proxy ready."
            break
        fi
        sleep 1
    done

    # LightRAG sends all OpenAI calls through the proxy; the proxy routes
    # /v1/embeddings to the embedding backend and everything else to the LLM.
    export OPENAI_API_BASE="http://localhost:${PROXY_PORT}/v1"

    # LightRAG only needs one key; the proxy handles per-backend auth.
    # Keep OPENAI_API_KEY as-is so the proxy can read it from the environment.
fi

echo "[start.sh] Starting lightrag-server on ${LIGHTRAG_HOST}:${LIGHTRAG_PORT}..."
exec lightrag-server \
    --host   "${LIGHTRAG_HOST}" \
    --port   "${LIGHTRAG_PORT}" \
    --working-dir "${LIGHTRAG_WORKING_DIR}"
