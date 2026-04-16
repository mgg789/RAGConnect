#!/usr/bin/env bash
# Adds the RAGConnect MCP server to Claude Desktop config on macOS.
#
# Usage:
#   bash install-claude-mcp.sh [--repo-root /path/to/RAGConnect]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

RAG_HOME="$HOME/.ragconnect"
PYTHON_EXE="$RAG_HOME/.venv/bin/python3"
CONFIG_FILE="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
CONFIG_DIR="$(dirname "$CONFIG_FILE")"

mkdir -p "$CONFIG_DIR"

# Read or initialise config
if [[ -f "$CONFIG_FILE" ]]; then
  config=$(cat "$CONFIG_FILE")
else
  config='{}'
fi

# Use Python (already in venv) to merge the config safely
"$PYTHON_EXE" - <<PYEOF
import json, sys, os

config_path = """$CONFIG_FILE"""
repo_root   = """$REPO_ROOT"""
rag_home    = """$RAG_HOME"""
python_exe  = """$PYTHON_EXE"""

with open(config_path, 'r') as f:
    cfg = json.load(f)

cfg.setdefault('mcpServers', {})
cfg['mcpServers']['ragconnect'] = {
    'command': python_exe,
    'args': ['-m', 'client_gateway.mcp_server'],
    'cwd': repo_root,
    'env': {
        'PYTHONPATH': repo_root,
        'RAGCONNECT_CONFIG_PATH': rag_home + '/client_config.yaml',
        'RAGCONNECT_PROMPTS_DIR': repo_root + '/config/prompts',
        'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8',
    }
}

with open(config_path, 'w') as f:
    json.dump(cfg, f, indent=2)

print('[RAGConnect] Claude Desktop config updated:', config_path)
PYEOF
