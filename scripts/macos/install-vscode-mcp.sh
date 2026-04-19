#!/usr/bin/env bash
# RAGConnect — install MCP for VS Code (GitHub Copilot Chat) on macOS
#
# Usage:
#   bash install-vscode-mcp.sh                          # user-level (global)
#   bash install-vscode-mcp.sh --scope project          # project-level (.vscode/mcp.json)
#   bash install-vscode-mcp.sh --project-dir /path/to/project
#   bash install-vscode-mcp.sh --repo-root /path/to/RAGConnect

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RAG_HOME="$HOME/.ragconnect"
PYTHON_EXE="$RAG_HOME/.venv/bin/python3"
SCOPE="user"
PROJECT_DIR="$(pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)   REPO_ROOT="$2";   shift 2 ;;
    --scope)       SCOPE="$2";       shift 2 ;;
    --project-dir) PROJECT_DIR="$2"; shift 2 ;;
    *) shift ;;
  esac
done

merge_vscode_config() {
  local config_path="$1"
  local scope="$2"

  mkdir -p "$(dirname "$config_path")"
  [[ -f "$config_path" ]] || echo '{}' > "$config_path"

  "$PYTHON_EXE" - <<PYEOF
import json

config_path = """$config_path"""
repo_root   = """$REPO_ROOT"""
rag_home    = """$RAG_HOME"""
python_exe  = """$PYTHON_EXE"""
scope       = """$scope"""

with open(config_path) as f:
    cfg = json.load(f)

server_block = {
    'type':    'stdio',
    'command': python_exe,
    'args':    ['-m', 'client_gateway.mcp_server'],
    'env': {
        'PYTHONPATH':                      repo_root,
        'RAGCONNECT_CONFIG_PATH':          rag_home + '/client_config.yaml',
        'RAGCONNECT_PROMPTS_DIR':          repo_root + '/config/prompts',
        'RAGCONNECT_HTTP_TIMEOUT_SECONDS': '600',
        'MCP_TOOL_TIMEOUT':                '600000',
        'PYTHONUTF8':                      '1',
        'PYTHONIOENCODING':                'utf-8',
    }
}

if scope == 'project':
    cfg.setdefault('servers', {})
    cfg['servers']['ragconnect'] = server_block
else:
    cfg.setdefault('mcp.servers', {})
    cfg['mcp.servers']['ragconnect'] = server_block

with open(config_path, 'w') as f:
    json.dump(cfg, f, indent=2)

print(f'[RAGConnect] VS Code {scope} MCP → {config_path}')
PYEOF
}

if [[ "$SCOPE" == "project" ]]; then
  merge_vscode_config "$PROJECT_DIR/.vscode/mcp.json" "project"
else
  # User-level settings — try standard VS Code path, then VS Code Insiders
  SETTINGS="$HOME/Library/Application Support/Code/User/settings.json"
  [[ -d "$HOME/Library/Application Support/Code" ]] || \
    SETTINGS="$HOME/Library/Application Support/Code - Insiders/User/settings.json"
  merge_vscode_config "$SETTINGS" "user"
fi

echo "[RAGConnect] Reload VS Code (Cmd+Shift+P → 'Developer: Reload Window') to activate."
