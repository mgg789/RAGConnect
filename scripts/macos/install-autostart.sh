#!/usr/bin/env bash
# Installs a macOS LaunchAgent that starts the RAGConnect local stack at login.
#
# Usage:
#   bash install-autostart.sh [--repo-root /path/to/RAGConnect]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/com.ragconnect.local-stack.plist"
START_SCRIPT="$REPO_ROOT/scripts/macos/start-local-stack.sh"
RAG_HOME="$HOME/.ragconnect"

mkdir -p "$PLIST_DIR"
chmod +x "$START_SCRIPT"

cat > "$PLIST_FILE" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ragconnect.local-stack</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$START_SCRIPT</string>
        <string>--repo-root</string>
        <string>$REPO_ROOT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$RAG_HOME/launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$RAG_HOME/launchd.stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLIST

# Load the agent (makes it active immediately without reboot)
launchctl unload "$PLIST_FILE" 2>/dev/null || true
launchctl load -w "$PLIST_FILE"

echo "[RAGConnect] LaunchAgent installed: $PLIST_FILE"
echo "[RAGConnect] Local stack will start automatically at next login."
echo "[RAGConnect] To start now: launchctl start com.ragconnect.local-stack"
