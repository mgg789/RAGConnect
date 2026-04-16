#!/usr/bin/env bash
# Adds the RAGConnect MCP server block to ~/.codex/config.toml on macOS.
#
# Usage:
#   bash install-codex-mcp.sh [--repo-root /path/to/RAGConnect]

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
CONFIG_FILE="$HOME/.codex/config.toml"

mkdir -p "$(dirname "$CONFIG_FILE")"
[[ -f "$CONFIG_FILE" ]] || touch "$CONFIG_FILE"

# Remove existing ragconnect blocks then append fresh ones
content=$(cat "$CONFIG_FILE")
# Strip existing ragconnect blocks (mcp_servers.ragconnect and mcp_servers.ragconnect.env)
content=$(echo "$content" | perl -0pe 's/\[mcp_servers\.ragconnect(?:\.env)?\][^\[]*//gs')

cat > "$CONFIG_FILE" <<TOML
${content%$'\n'}

[mcp_servers.ragconnect]
command = "$PYTHON_EXE"
args = ["-m", "client_gateway.mcp_server"]
cwd = "$REPO_ROOT"
enabled = true

[mcp_servers.ragconnect.env]
PYTHONPATH = "$REPO_ROOT"
RAGCONNECT_CONFIG_PATH = "$RAG_HOME/client_config.yaml"
RAGCONNECT_PROMPTS_DIR = "$REPO_ROOT/config/prompts"
PYTHONUTF8 = "1"
PYTHONIOENCODING = "utf-8"
TOML

echo "[RAGConnect] Codex config updated: $CONFIG_FILE"
