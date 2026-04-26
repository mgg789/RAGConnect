from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from client_gateway.audit import append_audit
from client_gateway.config import find_local, find_project, load_config
from client_gateway.context import resolve_project_context, roots_from_uris
from client_gateway.project_registry import register_project
from client_gateway.router import Router
from client_gateway.server_client import ServerGatewayClient
from shared.lightrag_client import LightRAGClient
from shared.models import HealthResponse, HealthStatus, ProjectInfo

server = Server("ragconnect-client-gateway")
PROMPTS_DIR = Path(os.environ.get("RAGCONNECT_PROMPTS_DIR", "config/prompts"))


@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="memory-context",
            description="Inject current memory configuration, resolved context, and usage rules.",
            arguments=[],
        )
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
    if name != "memory-context":
        raise ValueError(f"Unknown prompt: {name}")

    del arguments
    config = load_config()
    local = find_local(config)
    projects = [d for d in config.destinations if not d.is_local]
    roots = await _current_roots()
    resolved = resolve_project_context(config, None, roots=roots)

    lines: list[str] = _load_prompt_parts("global")
    lines += [
        "### Resolved context",
        f"- Source: `{resolved.source}`",
        f"- Resolved project_label: `{resolved.resolved_project_label or 'local'}`",
        f"- Roots seen: {', '.join(f'`{root}`' for root in resolved.roots) if resolved.roots else 'none'}",
        "",
    ]

    if local:
        lines += [
            "### Local memory",
            f"- URL: `{local.url}`",
            f"- Status: `{'enabled' if local.enabled else 'disabled'}`",
            "",
        ]
    else:
        lines += ["### Local memory", "- Not configured.", ""]

    lines.append("### Project destinations")
    if projects:
        for project in projects:
            marker = " <- default" if project.label == config.default_project else ""
            lines.append(f"- `{project.label}` ({'enabled' if project.enabled else 'disabled'}){marker}")
    else:
        lines.append("- None configured.")
    lines.append("")

    lines.append("### Registered project contexts")
    if config.project_contexts:
        for context in config.project_contexts:
            lines.append(
                f"- `{context.project_label}` => `{context.repo_root}` ({'enabled' if context.enabled else 'disabled'})"
            )
    else:
        lines.append("- None registered.")
    lines.append("")

    lines += _load_prompt_parts("rules")
    if resolved.warnings:
        lines.append("### Warnings")
        lines += [f"- {warning}" for warning in resolved.warnings]
        lines.append("")

    return types.GetPromptResult(
        description="Memory system context and usage rules",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text="\n".join(lines)),
            )
        ],
    )


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    config = load_config()
    default_hint = (
        f"If omitted, the current project context resolves to '{config.default_project}'."
        if config.default_project
        else "If omitted, project context is resolved from client roots or falls back to local memory."
    )
    return [
        _tool(
            "memory_search",
            "Search memory using explicit label, registered project roots, or local fallback.",
            {
                "query": {"type": "string", "description": "The search query."},
                "project_label": {"type": "string", "description": f"Optional explicit project label. {default_hint}"},
            },
            required=["query"],
        ),
        _tool(
            "memory_write",
            "Write memory using explicit label, registered project roots, or local fallback.",
            {
                "text": {"type": "string", "description": "The text to store."},
                "project_label": {"type": "string", "description": f"Optional explicit project label. {default_hint}"},
                "allow_local_fallback_for_write": {
                    "type": "boolean",
                    "description": "Allow writing to local memory if project write fails. Default false.",
                    "default": False,
                },
            },
            required=["text"],
        ),
        _tool("memory_list_projects", "List configured destinations and project contexts.", {}),
        _tool("memory_current_context", "Show current roots and resolved project context.", {}),
        _tool(
            "memory_register_project",
            "Register repo_root -> project_label and optionally write AGENTS.md/CLAUDE.md snippet.",
            {
                "repo_root": {"type": "string"},
                "project_label": {"type": "string"},
                "write_agents_md": {"type": "boolean", "default": False},
                "write_claude_md": {"type": "boolean", "default": False},
            },
            required=["repo_root", "project_label"],
        ),
        _tool("memory_health", "Check reachability of all memory destinations.", {}),
        _tool("memory_graph", "Get graph payload for the resolved destination.", {"project_label": {"type": "string"}}),
        _tool("memory_entities", "List entities from memory graph.", {"project_label": {"type": "string"}}),
        _tool("memory_relations", "List relations from memory graph.", {"project_label": {"type": "string"}}),
        _tool("memory_documents", "List source documents in memory.", {"project_label": {"type": "string"}}),
        _tool(
            "memory_ingest_bulk",
            "Ingest multiple records into the resolved destination.",
            {"texts": {"type": "array", "items": {"type": "string"}}, "project_label": {"type": "string"}},
            required=["texts"],
        ),
        _tool("memory_rebuild_index", "Trigger index or graph rebuild.", {"project_label": {"type": "string"}}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.Content]:
    args = arguments or {}
    roots = await _current_roots()
    started = time.perf_counter()
    config = load_config()

    if name == "memory_search":
        response = await Router(config).search(
            query=args.get("query", ""),
            project_label=args.get("project_label"),
            roots=roots,
        )
        _audit_tool_call(name, response.model_dump(exclude_none=True), args, roots, started)
        return [_text(response.model_dump(exclude_none=True))]

    if name == "memory_write":
        response = await Router(config).write(
            text=args.get("text", ""),
            project_label=args.get("project_label"),
            allow_local_fallback=args.get("allow_local_fallback_for_write", False),
            roots=roots,
        )
        _audit_tool_call(name, response.model_dump(exclude_none=True), args, roots, started)
        return [_text(response.model_dump(exclude_none=True))]

    if name == "memory_list_projects":
        local = find_local(config)
        payload = {
            "local": {"url": local.url, "enabled": local.enabled} if local else None,
            "projects": [
                ProjectInfo(label=d.label or "", enabled=d.enabled).model_dump()
                for d in config.destinations
                if not d.is_local and d.label
            ],
            "project_contexts": [context.model_dump() for context in config.project_contexts],
            "default_project": config.default_project,
        }
        return [_text(payload)]

    if name == "memory_current_context":
        payload = {
            "roots": roots,
            "resolved": resolve_project_context(config, args.get("project_label"), roots=roots).model_dump(exclude_none=True),
            "default_project": config.default_project,
            "project_contexts": [context.model_dump() for context in config.project_contexts],
        }
        return [_text(payload)]

    if name == "memory_register_project":
        result = register_project(
            config=config,
            repo_root=args.get("repo_root", ""),
            project_label=args.get("project_label", ""),
            write_agents=args.get("write_agents_md", False),
            write_claude=args.get("write_claude_md", False),
        )
        append_audit("memory_register_project", result)
        return [_text({"status": "ok", **result})]

    if name == "memory_health":
        payload = await _health_payload(config)
        return [_text(payload.model_dump(exclude_none=True))]

    if name in {"memory_graph", "memory_entities", "memory_relations", "memory_documents", "memory_ingest_bulk", "memory_rebuild_index"}:
        response = await _call_extended(name, args, config, roots)
        _audit_tool_call(name, response, args, roots, started)
        return [_text(response)]

    raise ValueError(f"Unknown tool: {name}")


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> types.Tool:
    return types.Tool(
        name=name,
        description=description,
        inputSchema={"type": "object", "properties": properties, "required": required or []},
    )


def _text(payload: object) -> types.TextContent:
    return types.TextContent(type="text", text=_dump(payload))


def _dump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _load_prompt_parts(section: str) -> list[str]:
    file_path = PROMPTS_DIR / f"{section}.md"
    if not file_path.exists():
        return []
    return file_path.read_text(encoding="utf-8").splitlines()


async def _current_roots() -> list[str]:
    try:
        result = await server.request_context.session.list_roots()
    except Exception:
        return []
    uris = [str(root.uri) for root in result.roots]
    return roots_from_uris(uris)


async def _health_payload(config) -> HealthResponse:
    components: list[HealthStatus] = [HealthStatus(name="client_gateway", status="ok")]
    local = find_local(config)
    if local and local.enabled:
        ok = await LightRAGClient(local.url).health()
        components.append(
            HealthStatus(
                name="local_lightrag",
                status="ok" if ok else "error",
                message=None if ok else f"LightRAG at {local.url} not responding.",
                details={"url": local.url},
            )
        )
    else:
        components.append(HealthStatus(name="local_lightrag", status="disabled"))

    for destination in config.destinations:
        if destination.is_local:
            continue
        if not destination.enabled:
            components.append(HealthStatus(name=f"project:{destination.label}", status="disabled"))
            continue
        ok = await ServerGatewayClient(destination.url, destination.token or "").health()
        components.append(
            HealthStatus(
                name=f"project:{destination.label}",
                status="ok" if ok else "error",
                details={"url": destination.url},
            )
        )

    overall = "ok" if all(item.status in {"ok", "disabled"} for item in components) else "error"
    return HealthResponse(status=overall, components=components)


async def _call_extended(name: str, args: dict, config, roots: list[str]) -> dict:
    resolved = resolve_project_context(config, args.get("project_label"), roots=roots)
    effective_label = resolved.resolved_project_label
    if not effective_label:
        local = find_local(config)
        if not local:
            raise ValueError("No local destination configured and no project_label resolved.")
        client = LightRAGClient(local.url)
        data = await _call_extended_local(client, name, args)
        return {"status": "ok", "source": "local", "context": resolved.model_dump(exclude_none=True), "data": data}

    destination = find_project(config, effective_label)
    if not destination:
        raise ValueError(f"Unknown project label: {effective_label}")
    client = ServerGatewayClient(destination.url, destination.token or "")
    data = await _call_extended_remote(client, name, args)
    return {"status": "ok", "source": "project", "context": resolved.model_dump(exclude_none=True), "data": data}


async def _call_extended_local(client: LightRAGClient, tool_name: str, args: dict) -> dict:
    if tool_name == "memory_graph":
        return await client.graph()
    if tool_name == "memory_entities":
        return await client.entities()
    if tool_name == "memory_relations":
        return await client.relations()
    if tool_name == "memory_documents":
        return await client.documents()
    if tool_name == "memory_ingest_bulk":
        return await client.ingest(args.get("texts", []))
    if tool_name == "memory_rebuild_index":
        return await client.rebuild()
    raise ValueError(f"Unsupported extended local tool: {tool_name}")


async def _call_extended_remote(client: ServerGatewayClient, tool_name: str, args: dict) -> dict:
    if tool_name == "memory_graph":
        _, data = await client.graph()
        return data
    if tool_name == "memory_entities":
        _, data = await client.entities()
        return data
    if tool_name == "memory_relations":
        _, data = await client.relations()
        return data
    if tool_name == "memory_documents":
        _, data = await client.documents()
        return data
    if tool_name == "memory_ingest_bulk":
        _, data = await client.ingest(args.get("texts", []))
        return data
    if tool_name == "memory_rebuild_index":
        _, data = await client.rebuild()
        return data
    raise ValueError(f"Unsupported extended remote tool: {tool_name}")


def _audit_tool_call(name: str, payload: dict, args: dict, roots: list[str], started: float) -> None:
    context = payload.get("context") or {}
    append_audit(
        name,
        {
            "tool": name,
            "requested_project_label": args.get("project_label"),
            "resolved_project_label": context.get("resolved_project_label"),
            "source": context.get("source"),
            "roots": roots,
            "status": payload.get("status"),
            "warning": payload.get("warning"),
            "error": payload.get("error"),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main_sync() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
