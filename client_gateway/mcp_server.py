"""MCP server exposing memory tools to AI clients (Claude Code, Cursor, Codex, …).

Run via stdio (the standard MCP transport):

    python -m client_gateway.mcp_server
    # or, after pip install -e .:
    ragconnect-client

How the AI learns about memory
-------------------------------
1. MCP Prompts  — `memory-context` prompt injects available destinations and
   usage rules into the AI context at session start.

2. CLAUDE.md    — Project-level instructions that tell Claude which project_label
   to use when working in a specific repository (see config/CLAUDE.md.example).
"""

from __future__ import annotations

import asyncio
import json

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from client_gateway.config import find_local, load_config
from client_gateway.router import Router
from client_gateway.server_client import ServerGatewayClient
from shared.lightrag_client import LightRAGClient
from shared.models import HealthResponse, HealthStatus, ListProjectsResponse, ProjectInfo

server = Server("ragconnect-client-gateway")


# ---------------------------------------------------------------------------
# Prompts — teach the AI about the memory system
# ---------------------------------------------------------------------------

@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="memory-context",
            description=(
                "Injects the current memory configuration into the AI's context: "
                "available destinations, default project, and rules for proactive "
                "memory use."
            ),
            arguments=[],
        )
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
    if name != "memory-context":
        raise ValueError(f"Unknown prompt: {name}")

    config = load_config()
    local = find_local(config)
    projects = [d for d in config.destinations if not d.is_local]

    lines: list[str] = [
        "## Memory system",
        "",
        "You have access to a distributed memory system. "
        "Use it to persist and retrieve knowledge across sessions.",
        "",
        "### Tools",
        "| Tool | Purpose |",
        "|------|---------|",
        "| `memory_search` | Retrieve context **before** answering questions about a project. |",
        "| `memory_write`  | Store decisions, agreements, discoveries **immediately** after they occur. |",
        "| `memory_list_projects` | List available project destinations. |",
        "| `memory_health` | Check reachability of all destinations. |",
        "",
    ]

    # --- local destination ---
    if local:
        lines += [
            "### Local LightRAG (default when no label given)",
            f"- URL: `{local.url}`",
            f"- Status: {'enabled' if local.enabled else 'disabled'}",
            "- Access: **native API** (no auth, direct LightRAG calls)",
            "",
        ]
    else:
        lines += [
            "### Local LightRAG",
            "- **Not configured.** Add one in `ragconnect-web` to enable local memory.",
            "",
        ]

    # --- project destinations ---
    if projects:
        lines.append("### Project destinations")
        for p in projects:
            marker = " ← **default**" if p.label == config.default_project else ""
            status = "enabled" if p.enabled else "disabled"
            lines.append(f"- `{p.label}` ({status}){marker}")
        lines.append("")
    else:
        lines += ["### Project destinations", "- None configured.", ""]

    # --- routing hint ---
    if config.default_project:
        lines += [
            f"### Default project: `{config.default_project}`",
            f"When no `project_label` is given, requests route to **`{config.default_project}`** "
            "(via Server Gateway). Omit the label for the default project.",
            "",
        ]
    else:
        lines += [
            "### Routing",
            "No default project set. Omitting `project_label` routes to the **local LightRAG** "
            "(native API, no auth).",
            "",
        ]

    # --- rules ---
    lines += [
        "### Rules",
        "- **Always search** before answering questions about architecture, decisions, or past work.",
        "- **Always write** after: design decisions, agreed constraints, discovered issues, "
        "completed milestones.",
        "- Use the project label that matches your current working context.",
        "- For personal notes or cross-project observations, omit `project_label` "
        "(goes to local LightRAG).",
        "- Never silently skip writing — report the error rather than dropping information.",
    ]

    return types.GetPromptResult(
        description="Memory system context and usage rules",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text="\n".join(lines)),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    config = load_config()
    if config.default_project:
        default_hint = f"If omitted, routes to the default project ('{config.default_project}')."
    else:
        default_hint = "If omitted, routes to the local LightRAG (native API, no auth)."

    return [
        types.Tool(
            name="memory_search",
            description=(
                "Search memory for relevant information. "
                f"Specify project_label for a project destination. {default_hint} "
                "Falls back to local LightRAG with a warning if the project is unreachable."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "project_label": {
                        "type": "string",
                        "description": f"Project label. {default_hint}",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="memory_write",
            description=(
                "Write information to memory. "
                f"Specify project_label for a project destination. {default_hint} "
                "Write failures do NOT silently fall back to local."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to store."},
                    "project_label": {
                        "type": "string",
                        "description": f"Project label. {default_hint}",
                    },
                    "allow_local_fallback_for_write": {
                        "type": "boolean",
                        "description": (
                            "Allow writing to local LightRAG if the project write fails. "
                            "Default: false."
                        ),
                        "default": False,
                    },
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="memory_list_projects",
            description="List all memory destinations (local + projects) from the client config.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="memory_health",
            description="Check reachability of all memory destinations.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.Content]:
    args = arguments or {}

    if name == "memory_search":
        config = load_config()
        response = await Router(config).search(
            query=args.get("query", ""),
            project_label=args.get("project_label"),
        )
        return [types.TextContent(type="text", text=_dump(response.model_dump(exclude_none=True)))]

    if name == "memory_write":
        config = load_config()
        response = await Router(config).write(
            text=args.get("text", ""),
            project_label=args.get("project_label"),
            allow_local_fallback=args.get("allow_local_fallback_for_write", False),
        )
        return [types.TextContent(type="text", text=_dump(response.model_dump(exclude_none=True)))]

    if name == "memory_list_projects":
        config = load_config()
        local = find_local(config)
        payload = {
            "local": {"url": local.url, "enabled": local.enabled} if local else None,
            "projects": [
                ProjectInfo(label=d.label, enabled=d.enabled).model_dump()
                for d in config.destinations
                if not d.is_local
            ],
            "default_project": config.default_project,
        }
        return [types.TextContent(type="text", text=_dump(payload))]

    if name == "memory_health":
        config = load_config()
        router = Router(config)
        components: list[HealthStatus] = [
            HealthStatus(name="client_gateway", status="ok")
        ]

        # Local LightRAG — native health check
        local = find_local(config)
        if local and local.enabled:
            ok = await LightRAGClient(local.url).health()
            components.append(HealthStatus(
                name="local_lightrag",
                status="ok" if ok else "error",
                message=None if ok else f"LightRAG at {local.url} not responding.",
            ))
        else:
            components.append(HealthStatus(name="local_lightrag", status="disabled"))

        # Project destinations — health via Server Gateway
        for dest in config.destinations:
            if dest.is_local:
                continue
            if not dest.enabled:
                components.append(HealthStatus(name=f"project:{dest.label}", status="disabled"))
                continue
            ok = await ServerGatewayClient(dest.url, dest.token or "").health()
            components.append(HealthStatus(
                name=f"project:{dest.label}",
                status="ok" if ok else "error",
            ))

        overall = "ok" if all(c.status in ("ok", "disabled") for c in components) else "error"
        response = HealthResponse(status=overall, components=components)
        return [types.TextContent(type="text", text=_dump(response.model_dump(exclude_none=True)))]

    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _dump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main_sync() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
