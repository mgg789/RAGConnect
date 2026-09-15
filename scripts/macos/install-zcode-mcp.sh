#!/usr/bin/env bash
# RAGConnect — install MCP for ZCode (macOS)
# Usage:
#   bash install-zcode-mcp.sh
#   bash install-zcode-mcp.sh --repo-root /path/to/RAGConnect

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
CONFIG_FILE="$HOME/.zcode/cli/config.json"

mkdir -p "$(dirname "$CONFIG_FILE")"
[[ -f "$CONFIG_FILE" ]] || echo '{}' > "$CONFIG_FILE"

"$PYTHON_EXE" - <<PYEOF
import json

config_path = """$CONFIG_FILE"""
repo_root   = """$REPO_ROOT"""
rag_home    = """$RAG_HOME"""
python_exe  = """$PYTHON_EXE"""

with open(config_path) as f:
    cfg = json.load(f)

# ZCode nests servers under top-level mcp.servers (unlike mcpServers in other clients).
cfg.setdefault('mcp', {})
cfg['mcp'].setdefault('servers', {})
cfg['mcp']['servers']['ragconnect'] = {
    'command': python_exe,
    'args': ['-m', 'client_gateway.mcp_server'],
    'env': {
        'PYTHONPATH': repo_root,
        'RAGCONNECT_CONFIG_PATH': rag_home + '/client_config.yaml',
        'RAGCONNECT_PROMPTS_DIR': repo_root + '/config/prompts',
        'RAGCONNECT_HTTP_TIMEOUT_SECONDS': '600',
        'MCP_TOOL_TIMEOUT': '600000',
        'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8',
    }
}

with open(config_path, 'w') as f:
    json.dump(cfg, f, indent=2)

print(f'[RAGConnect] ZCode MCP → {config_path}')
PYEOF
