"""MCP server exposing memory tools to AI clients (Claude Code, Cursor, Codex, …).

Run via stdio (the standard MCP transport):

    python -m client_gateway.mcp_server
    # or, after pip install -e .:
    ragconnect-client
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
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="memory_search",
            description=(
                "Search memory for relevant information. "
                "Specify project_label to search project memory; "
                "omit it to search local memory. "
                "If the project destination is unreachable, falls back to local memory "
                "and includes a warning in the response."
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
                        "description": (
                            "Optional project label. "
                            "If omitted, local memory is searched."
                        ),
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="memory_write",
            description=(
                "Write information to memory. "
                "Specify project_label to write to project memory; "
                "omit it to write to local memory. "
                "By default, write failures do NOT silently fall back to local memory. "
                "Set allow_local_fallback_for_write=true to enable that behaviour."
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
                        "description": (
                            "Optional project label. "
                            "If omitted, writes to local memory."
                        ),
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
            description="List all memory projects configured in the local client config.",
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
        return [
            types.TextContent(
                type="text",
                text=_dump(ListProjectsResponse(projects=projects).model_dump()),
            )
        ]

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
