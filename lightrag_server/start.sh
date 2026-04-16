#!/bin/bash
set -e

PROXY_PORT="${PROXY_PORT:-9622}"
LIGHTRAG_HOST="${LIGHTRAG_HOST:-0.0.0.0}"
LIGHTRAG_PORT="${LIGHTRAG_PORT:-9621}"
LIGHTRAG_WORKING_DIR="${LIGHTRAG_WORKING_DIR:-/data/lightrag}"

needs_proxy() {
    [ "${LOCAL_EMBEDDING_MODE:-false}" = "true" ] && return 0
    local embed_base="${EMBEDDING_API_BASE:-}"
    local llm_base="${OPENAI_API_BASE:-https://api.openai.com/v1}"
    [ -n "$embed_base" ] && [ "$embed_base" != "$llm_base" ] && return 0
    return 1
}

if needs_proxy; then
    echo "[start.sh] Starting embedding proxy on port ${PROXY_PORT}..."
    python -m local_embeddings.proxy > /tmp/ragconnect-proxy.log 2>&1 &

    for i in $(seq 1 30); do
        if curl -sf "http://127.0.0.1:${PROXY_PORT}/health" > /dev/null 2>&1; then
            echo "[start.sh] Proxy is healthy."
            break
        fi
        sleep 1
    done

    export LLM_BINDING_HOST="http://127.0.0.1:${PROXY_PORT}/v1"
    export EMBEDDING_BINDING_HOST="http://127.0.0.1:${PROXY_PORT}/v1"
else
    if [ -n "${OPENAI_API_BASE:-}" ]; then
        export LLM_BINDING_HOST="${OPENAI_API_BASE}"
        export EMBEDDING_BINDING_HOST="${OPENAI_API_BASE}"
    fi
    if [ -n "${EMBEDDING_API_BASE:-}" ]; then
        export EMBEDDING_BINDING_HOST="${EMBEDDING_API_BASE}"
    fi
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

if [ -n "${LOCAL_EMBEDDING_DIM:-}" ] && [ -z "${EMBEDDING_DIM:-}" ]; then
    export EMBEDDING_DIM="${LOCAL_EMBEDDING_DIM}"
fi
if [ -n "${LOCAL_EMBEDDING_MODEL:-}" ] && [ -z "${EMBEDDING_MODEL:-}" ]; then
    export EMBEDDING_MODEL="${LOCAL_EMBEDDING_MODEL}"
fi

echo "[start.sh] Starting LightRAG on ${LIGHTRAG_HOST}:${LIGHTRAG_PORT}..."
exec lightrag-server \
  --host "${LIGHTRAG_HOST}" \
  --port "${LIGHTRAG_PORT}" \
  --working-dir "${LIGHTRAG_WORKING_DIR}" \
  --llm-binding openai \
  --embedding-binding openai
