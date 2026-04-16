#!/usr/bin/env bash
# Removes the RAGConnect LaunchAgent.

set -euo pipefail

PLIST_FILE="$HOME/Library/LaunchAgents/com.ragconnect.local-stack.plist"

if [[ -f "$PLIST_FILE" ]]; then
  launchctl unload "$PLIST_FILE" 2>/dev/null || true
  rm -f "$PLIST_FILE"
  echo "[RAGConnect] LaunchAgent removed."
else
  echo "[RAGConnect] LaunchAgent not found — nothing to remove."
fi
