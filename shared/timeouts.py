from __future__ import annotations

import os


DEFAULT_HTTP_TIMEOUT_SECONDS = 600.0


def get_request_timeout_seconds(default: float = DEFAULT_HTTP_TIMEOUT_SECONDS) -> float:
    """Resolve the request timeout for long-running MCP-backed memory calls.

    Priority:
    1. `RAGCONNECT_HTTP_TIMEOUT_SECONDS` — explicit RAGConnect setting in seconds.
    2. `MCP_TOOL_TIMEOUT` — MCP-style timeout in milliseconds.
    3. built-in default (`600` seconds).
    """
    raw_seconds = os.environ.get("RAGCONNECT_HTTP_TIMEOUT_SECONDS")
    if raw_seconds:
        try:
            return max(float(raw_seconds), 1.0)
        except ValueError:
            pass

    raw_mcp_timeout = os.environ.get("MCP_TOOL_TIMEOUT")
    if raw_mcp_timeout:
        try:
            return max(float(raw_mcp_timeout) / 1000.0, 1.0)
        except ValueError:
            pass

    return default
