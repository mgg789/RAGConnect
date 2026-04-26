#!/usr/bin/env bash
set -euo pipefail

RAG_HOME="${HOME}/.ragconnect"
CLI_EXE="$RAG_HOME/.venv/bin/ragconnect-local-service"
PYTHON_EXE="$RAG_HOME/.venv/bin/python3"
REPO_ROOT="${PWD}"

if [[ -x "$CLI_EXE" ]]; then
  "$CLI_EXE" stop --repo-root "$REPO_ROOT" --rag-home "$RAG_HOME"
elif [[ -x "$PYTHON_EXE" ]]; then
  "$PYTHON_EXE" -m client_gateway.local_service stop --repo-root "$REPO_ROOT" --rag-home "$RAG_HOME"
else
  echo "[RAGConnect] local service executable not found under $RAG_HOME/.venv" >&2
  exit 1
fi
