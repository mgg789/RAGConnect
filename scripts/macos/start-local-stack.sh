#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

RAG_HOME="${HOME}/.ragconnect"
CLI_EXE="$RAG_HOME/.venv/bin/ragconnect-local-service"
PYTHON_EXE="$RAG_HOME/.venv/bin/python3"

if [[ -x "$CLI_EXE" ]]; then
  "$CLI_EXE" start --repo-root "${REPO_ROOT:-$PWD}" --rag-home "$RAG_HOME"
elif [[ -x "$PYTHON_EXE" ]]; then
  "$PYTHON_EXE" -m client_gateway.local_service start --repo-root "${REPO_ROOT:-$PWD}" --rag-home "$RAG_HOME"
else
  echo "[RAGConnect] local service executable not found under $RAG_HOME/.venv" >&2
  exit 1
fi
