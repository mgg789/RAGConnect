#!/usr/bin/env bash
# RAGConnect local stack installer for macOS
#
# Usage:
#   bash install-local-stack.sh \
#     --repo-root /path/to/RAGConnect \
#     --python    /usr/bin/python3 \
#     --api-key   sk-... \
#     [--api-base https://api.openai.com/v1] \
#     [--llm-model gpt-4o-mini] \
#     [--embedding-model intfloat/multilingual-e5-small] \
#     [--install-claude-mcp] \
#     [--install-codex-mcp] \
#     [--enable-autostart]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="python3"
OPENAI_API_KEY=""
OPENAI_API_BASE=""
LLM_MODEL="gpt-4o-mini"
LOCAL_EMBEDDING_MODEL="intfloat/multilingual-e5-small"
LOCAL_EMBEDDING_DIM="384"
INSTALL_CLAUDE_MCP=0
INSTALL_CODEX_MCP=0
ENABLE_AUTOSTART=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)         REPO_ROOT="$2"; shift 2 ;;
    --python)            PYTHON="$2"; shift 2 ;;
    --api-key)           OPENAI_API_KEY="$2"; shift 2 ;;
    --api-base)          OPENAI_API_BASE="$2"; shift 2 ;;
    --llm-model)         LLM_MODEL="$2"; shift 2 ;;
    --embedding-model)   LOCAL_EMBEDDING_MODEL="$2"; shift 2 ;;
    --install-claude-mcp) INSTALL_CLAUDE_MCP=1; shift ;;
    --install-codex-mcp)  INSTALL_CODEX_MCP=1; shift ;;
    --enable-autostart)   ENABLE_AUTOSTART=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

RAG_HOME="$HOME/.ragconnect"
VENV="$RAG_HOME/.venv"
PYTHON_EXE="$VENV/bin/python3"
ENV_FILE="$RAG_HOME/.env"
CLIENT_CONFIG="$RAG_HOME/client_config.yaml"

mkdir -p "$RAG_HOME"

# ── venv ───────────────────────────────────────────────────────────────────────
if [[ ! -x "$PYTHON_EXE" ]]; then
  echo "[RAGConnect] Creating venv at $VENV ..."
  "$PYTHON" -m venv "$VENV"
fi

echo "[RAGConnect] Installing dependencies ..."
"$PYTHON_EXE" -m pip install --upgrade pip "setuptools<82" wheel
"$PYTHON_EXE" -m pip install -e "$REPO_ROOT"
"$PYTHON_EXE" -m pip install "lightrag-hku[api]>=1.4.14" "sentence-transformers>=3.0.0"

# ── client_config.yaml ─────────────────────────────────────────────────────────
if [[ ! -f "$CLIENT_CONFIG" ]]; then
  cp "$REPO_ROOT/config/client_config.example.yaml" "$CLIENT_CONFIG"
fi

# ── .env ───────────────────────────────────────────────────────────────────────
# Preserve existing key if not passed
if [[ -z "$OPENAI_API_KEY" && -f "$ENV_FILE" ]]; then
  OPENAI_API_KEY=$(grep -m1 '^OPENAI_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
fi
if [[ -z "$OPENAI_API_KEY" ]]; then
  echo "ERROR: --api-key is required" >&2; exit 1
fi
if [[ -z "$OPENAI_API_BASE" && -f "$ENV_FILE" ]]; then
  OPENAI_API_BASE=$(grep -m1 '^OPENAI_API_BASE=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
fi

cat > "$ENV_FILE" <<EOF
OPENAI_API_KEY=$OPENAI_API_KEY
$([ -n "$OPENAI_API_BASE" ] && echo "OPENAI_API_BASE=$OPENAI_API_BASE")
LLM_MODEL=$LLM_MODEL
LOCAL_EMBEDDING_MODE=true
LOCAL_EMBEDDING_MODEL=$LOCAL_EMBEDDING_MODEL
LOCAL_EMBEDDING_DIM=$LOCAL_EMBEDDING_DIM
EMBEDDING_MODEL=$LOCAL_EMBEDDING_MODEL
EMBEDDING_DIM=$LOCAL_EMBEDDING_DIM
LIGHTRAG_WORKING_DIR=$RAG_HOME/data/lightrag
PROXY_PORT=9622
EOF

chmod 600 "$ENV_FILE"

# ── optional steps ─────────────────────────────────────────────────────────────
[[ $INSTALL_CLAUDE_MCP -eq 1 ]] && bash "$SCRIPT_DIR/install-claude-mcp.sh" --repo-root "$REPO_ROOT"
[[ $INSTALL_CODEX_MCP  -eq 1 ]] && bash "$SCRIPT_DIR/install-codex-mcp.sh"  --repo-root "$REPO_ROOT"
[[ $ENABLE_AUTOSTART   -eq 1 ]] && bash "$SCRIPT_DIR/install-autostart.sh"  --repo-root "$REPO_ROOT"

echo "[RAGConnect] Local stack prepared in $RAG_HOME"
