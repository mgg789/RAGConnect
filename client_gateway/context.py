from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from pydantic import BaseModel

from client_gateway.config import ClientConfig, ProjectContextConfig


class ResolvedContext(BaseModel):
    requested_project_label: Optional[str] = None
    resolved_project_label: Optional[str] = None
    source: str = "local"
    roots: list[str] = []
    matched_repo_root: Optional[str] = None
    warnings: list[str] = []


def normalize_repo_root(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve()).lower()
    except OSError:
        return str(Path(value).expanduser()).lower()


def roots_from_uris(roots: list[str]) -> list[str]:
    output: list[str] = []
    for item in roots:
        if item.startswith("file://"):
            parsed = urlparse(item)
            output.append(normalize_repo_root(unquote(parsed.path.lstrip("/")) if parsed.netloc else unquote(parsed.path)))
        else:
            output.append(normalize_repo_root(item))
    return output


def match_project_context(
    project_contexts: list[ProjectContextConfig],
    roots: list[str],
) -> Optional[ProjectContextConfig]:
    best_match: Optional[ProjectContextConfig] = None
    best_len = -1
    normalized_roots = [normalize_repo_root(root) for root in roots]
    for context in project_contexts:
        if not context.enabled:
            continue
        repo_root = normalize_repo_root(context.repo_root)
        for root in normalized_roots:
            if root == repo_root or root.startswith(repo_root + "\\") or root.startswith(repo_root + "/"):
                current_len = len(repo_root)
                if current_len > best_len:
                    best_len = current_len
                    best_match = context
            elif repo_root.startswith(root + "\\") or repo_root.startswith(root + "/"):
                current_len = len(repo_root)
                if current_len > best_len:
                    best_len = current_len
                    best_match = context
    return best_match


def resolve_project_context(
    config: ClientConfig,
    requested_project_label: Optional[str],
    roots: list[str] | None = None,
) -> ResolvedContext:
    context = ResolvedContext(
        requested_project_label=requested_project_label,
        roots=[normalize_repo_root(root) for root in (roots or [])],
    )

    if requested_project_label:
        context.resolved_project_label = requested_project_label
        context.source = "explicit"
        return context

    matched = match_project_context(config.project_contexts, context.roots)
    if matched:
        context.resolved_project_label = matched.project_label
        context.source = "roots"
        context.matched_repo_root = normalize_repo_root(matched.repo_root)
        return context

    if config.default_project:
        context.resolved_project_label = config.default_project
        context.source = "default"
        if context.roots:
            context.warnings.append("Project context was not resolved from roots; default_project was used.")
        return context

    context.source = "local"
    if context.roots:
        context.warnings.append("Project context was not resolved from roots; local memory will be used.")
    return context
