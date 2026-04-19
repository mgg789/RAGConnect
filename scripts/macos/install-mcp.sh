#!/usr/bin/env bash
# RAGConnect — install MCP for one or all supported clients (macOS)
# Usage:
#   bash install-mcp.sh                        # all detected clients
#   bash install-mcp.sh --target claude
#   bash install-mcp.sh --target codex
#   bash install-mcp.sh --target cursor
#   bash install-mcp.sh --target claude,cursor
#   bash install-mcp.sh --repo-root /path/to/RAGConnect

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$2"; shift 2 ;;
    --target)    TARGET="$2";    shift 2 ;;
    *) shift ;;
  esac
done

RAG_HOME="$HOME/.ragconnect"
PYTHON_EXE="$RAG_HOME/.venv/bin/python3"

# ── Python helper: merge JSON MCP config ──────────────────────────────────────
merge_json_config() {
  local config_file="$1"
  local client_name="$2"
  mkdir -p "$(dirname "$config_file")"
  [[ -f "$config_file" ]] || echo '{}' > "$config_file"

  "$PYTHON_EXE" - <<PYEOF
import json

config_path = """$config_file"""
repo_root   = """$REPO_ROOT"""
rag_home    = """$RAG_HOME"""
python_exe  = """$PYTHON_EXE"""

with open(config_path) as f:
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
        'RAGCONNECT_HTTP_TIMEOUT_SECONDS': '600',
        'MCP_TOOL_TIMEOUT': '600000',
        'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8',
    }
}

with open(config_path, 'w') as f:
    json.dump(cfg, f, indent=2)

print(f'[RAGConnect] $client_name MCP → $config_file')
PYEOF
}

# ── Codex: TOML config ─────────────────────────────────────────────────────────
install_codex() {
  local config_file="$HOME/.codex/config.toml"
  mkdir -p "$(dirname "$config_file")"
  [[ -f "$config_file" ]] || touch "$config_file"

  # Remove old ragconnect blocks
  python3 - <<PYEOF
import re, pathlib

path = pathlib.Path("""$config_file""")
content = path.read_text() if path.exists() else ''
content = re.sub(r'(?ms)^\[mcp_servers\.ragconnect\.env\].*?(?=^\[|\Z)', '', content)
content = re.sub(r'(?ms)^\[mcp_servers\.ragconnect\].*?(?=^\[|\Z)', '', content)

block = '''
[mcp_servers.ragconnect]
command = "$PYTHON_EXE"
args = ["-m", "client_gateway.mcp_server"]
cwd = "$REPO_ROOT"
enabled = true

[mcp_servers.ragconnect.env]
PYTHONPATH = "$REPO_ROOT"
RAGCONNECT_CONFIG_PATH = "$RAG_HOME/client_config.yaml"
RAGCONNECT_PROMPTS_DIR = "$REPO_ROOT/config/prompts"
RAGCONNECT_HTTP_TIMEOUT_SECONDS = "600"
MCP_TOOL_TIMEOUT = "600000"
PYTHONUTF8 = "1"
PYTHONIOENCODING = "utf-8"
'''
path.write_text(content.rstrip() + '\n' + block.strip() + '\n')
print('[RAGConnect] Codex MCP →', str(path))
PYEOF
}

# ── Dispatch ───────────────────────────────────────────────────────────────────
IFS=',' read -ra TARGETS <<< "$TARGET"
[[ "${TARGETS[0]}" == "all" ]] && TARGETS=(claude codex cursor)

for t in "${TARGETS[@]}"; do
  t="$(echo "$t" | tr -d ' ')"
  case "$t" in
    claude)
      merge_json_config "$HOME/Library/Application Support/Claude/claude_desktop_config.json" "Claude Desktop"
      ;;
    cursor)
      merge_json_config "$HOME/.cursor/mcp.json" "Cursor"
      ;;
    codex)
      install_codex
      ;;
    *)
      echo "[RAGConnect] WARNING: unknown target '$t'. Supported: claude, codex, cursor" ;;
  esac
done

echo ""
echo "[RAGConnect] Done. Restart the configured clients to activate MCP."
