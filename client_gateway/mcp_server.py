"""MCP server exposing memory tools to AI clients (Claude Code, Cursor, Codex, …).

Run via stdio (the standard MCP transport):

    python -m client_gateway.mcp_server
    # or, after pip install -e .:
    ragconnect-client

How the AI learns about memory
-------------------------------
Two complementary mechanisms:

1. MCP Prompts  — `memory-context` prompt injects a system-level description of
   available projects and usage rules into the AI's context at session start.
   Supported automatically by Claude Code and other MCP-aware clients.

2. CLAUDE.md    — Project-level instructions that tell Claude which project_label
   to use when working in a specific repository (see config/CLAUDE.md.example).
"""

from __future__ import annotations

import asyncio
import json

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from client_gateway.config import load_config
from client_gateway.router import Router
from client_gateway.server_client import ServerGatewayClient
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
                "available projects, default project, and instructions on when to "
                "proactively read from / write to memory."
            ),
            arguments=[],
        )
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
    if name != "memory-context":
        raise ValueError(f"Unknown prompt: {name}")

    config = load_config()
    lines: list[str] = []

    lines += [
        "## Memory system",
        "",
        "You have access to a distributed memory system. "
        "Use it to persist and retrieve knowledge across sessions.",
        "",
        "### Available tools",
        "| Tool | When to use |",
        "|------|-------------|",
        "| `memory_search` | **Before** answering any non-trivial question about a project — retrieve relevant context first. |",
        "| `memory_write` | **After** any important decision, agreement, design choice, or discovery — store it immediately. |",
        "| `memory_list_projects` | To discover which project labels are available. |",
        "| `memory_health` | To check if memory destinations are reachable. |",
        "",
    ]

    # --- project destinations ---
    enabled = [p for p in config.projects if p.enabled]
    disabled = [p for p in config.projects if not p.enabled]

    if enabled:
        lines.append("### Project destinations (enabled)")
        for p in enabled:
            marker = " ← **default**" if p.label == config.default_project else ""
            lines.append(f"- `{p.label}`{marker}")
        lines.append("")

    if disabled:
        lines.append("### Project destinations (disabled)")
        for p in disabled:
            lines.append(f"- `{p.label}` *(disabled)*")
        lines.append("")

    if not config.projects:
        lines += [
            "No project destinations configured yet. "
            "Run `ragconnect-web` to add one, or edit `~/.ragconnect/client_config.yaml`.",
            "",
        ]

    # --- default project / routing hint ---
    if config.default_project:
        lines += [
            f"### Default project: `{config.default_project}`",
            "",
            f"When no `project_label` is specified in the tool call, "
            f"memory operations are automatically routed to **`{config.default_project}`**. "
            "You can omit the label for the default project.",
            "",
        ]
    else:
        lines += [
            "### Routing",
            "",
            "No default project is set. "
            "Omitting `project_label` routes to **local memory**. "
            "Always pass an explicit `project_label` to write to a project destination.",
            "",
        ]

    # --- behavioural rules ---
    lines += [
        "### Rules",
        "",
        "- Always search memory **before** answering questions about architecture, "
        "decisions, or past work — even if you think you know the answer.",
        "- Write to memory **immediately** after: team decisions, architectural choices, "
        "resolved issues, discovered constraints, and any information that should survive "
        "beyond this session.",
        "- Use the project label that matches the current working context. "
        "If unsure, call `memory_list_projects` to see what is available.",
        "- For personal notes or cross-project observations, omit `project_label` "
        "(routes to local memory).",
        "- Never silently skip writing — if a destination fails, report the error "
        "rather than dropping the information.",
    ]

    text = "\n".join(lines)
    return types.GetPromptResult(
        description="Memory system context and usage rules",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=text),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    config = load_config()
    default_hint = (
        f" If omitted, routes to the default project ('{config.default_project}')."
        if config.default_project
        else " If omitted, routes to local memory."
    )
    return [
        types.Tool(
            name="memory_search",
            description=(
                "Search memory for relevant information. "
                f"Specify project_label to search a specific project.{default_hint} "
                "Falls back to local memory with a warning if the destination is unreachable."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "project_label": {
                        "type": "string",
                        "description": "Project label to search. " + default_hint,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="memory_write",
            description=(
                "Write information to memory. "
                f"Specify project_label to write to a specific project.{default_hint} "
                "By default, write failures do NOT silently fall back to local memory."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to store in memory.",
                    },
                    "project_label": {
                        "type": "string",
                        "description": "Project label to write to. " + default_hint,
                    },
                    "allow_local_fallback_for_write": {
                        "type": "boolean",
                        "description": (
                            "When true, allows writing to local memory if the "
                            "project write fails. Default: false."
                        ),
                        "default": False,
                    },
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="memory_list_projects",
            description=(
                "List all memory projects configured in the local client config, "
                "including which one is the default."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="memory_health",
            description=(
                "Check the health of the memory system: "
                "the Client Gateway itself, local memory, and all configured project destinations."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
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
        router = Router(config)
        response = await router.search(
            query=args.get("query", ""),
            project_label=args.get("project_label"),
        )
        return [types.TextContent(type="text", text=_dump(response.model_dump(exclude_none=True)))]

    if name == "memory_write":
        config = load_config()
        router = Router(config)
        response = await router.write(
            text=args.get("text", ""),
            project_label=args.get("project_label"),
            allow_local_fallback=args.get("allow_local_fallback_for_write", False),
        )
        return [types.TextContent(type="text", text=_dump(response.model_dump(exclude_none=True)))]

    if name == "memory_list_projects":
        config = load_config()
        projects = [
            ProjectInfo(label=p.label, enabled=p.enabled)
            for p in config.projects
        ]
        payload = ListProjectsResponse(projects=projects).model_dump()
        payload["default_project"] = config.default_project
        return [types.TextContent(type="text", text=_dump(payload))]

    if name == "memory_health":
        config = load_config()
        router = Router(config)
        components: list[HealthStatus] = [
            HealthStatus(name="client_gateway", status="ok")
        ]

        # Local memory
        if config.local_memory.enabled and router.local_client:
            ok = await router.local_client.health()
            components.append(
                HealthStatus(
                    name="local_memory",
                    status="ok" if ok else "error",
                    message=None if ok else "LightRAG not responding.",
                )
            )
        else:
            components.append(HealthStatus(name="local_memory", status="disabled"))

        # Project destinations
        for project in config.projects:
            if not project.enabled:
                components.append(
                    HealthStatus(name=f"project:{project.label}", status="disabled")
                )
                continue
            client = ServerGatewayClient(project.url, project.token)
            ok = await client.health()
            components.append(
                HealthStatus(
                    name=f"project:{project.label}",
                    status="ok" if ok else "error",
                )
            )

        overall = (
            "ok"
            if all(c.status in ("ok", "disabled") for c in components)
            else "error"
        )
        response = HealthResponse(status=overall, components=components)
        return [
            types.TextContent(
                type="text",
                text=_dump(response.model_dump(exclude_none=True)),
            )
        ]

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
