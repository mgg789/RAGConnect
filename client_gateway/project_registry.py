from __future__ import annotations

from pathlib import Path

from client_gateway.config import ClientConfig, ProjectContextConfig, save_config
from client_gateway.context import normalize_repo_root


SNIPPET_TEMPLATE = """memory-label = "{label}"

## RAGConnect Memory

- Before answering project questions, run `memory_search` with `project_label="{label}"`.
- After decisions, important findings, root causes, and completed milestones, run `memory_write` with `project_label="{label}"`.
- Use unlabeled local memory only for personal or cross-project notes.
"""


def upsert_project_context(config: ClientConfig, repo_root: str, project_label: str) -> ProjectContextConfig:
    normalized = normalize_repo_root(repo_root)
    for context in config.project_contexts:
        if normalize_repo_root(context.repo_root) == normalized:
            context.project_label = project_label
            context.enabled = True
            context.repo_root = str(Path(repo_root))
            return context
    created = ProjectContextConfig(repo_root=str(Path(repo_root)), project_label=project_label, enabled=True)
    config.project_contexts.append(created)
    return created


def remove_project_context(config: ClientConfig, repo_root: str) -> bool:
    normalized = normalize_repo_root(repo_root)
    before = len(config.project_contexts)
    config.project_contexts = [c for c in config.project_contexts if normalize_repo_root(c.repo_root) != normalized]
    return len(config.project_contexts) != before


def register_project(
    config: ClientConfig,
    repo_root: str,
    project_label: str,
    config_path: Path | None = None,
    write_agents: bool = False,
    write_claude: bool = False,
) -> dict:
    context = upsert_project_context(config, repo_root, project_label)
    save_config(config, config_path=config_path)
    snippet_targets: list[str] = []
    if write_agents:
        write_memory_snippet(Path(repo_root) / "AGENTS.md", project_label)
        snippet_targets.append("AGENTS.md")
    if write_claude:
        write_memory_snippet(Path(repo_root) / "CLAUDE.md", project_label)
        snippet_targets.append("CLAUDE.md")
    return {
        "repo_root": context.repo_root,
        "project_label": context.project_label,
        "snippet_targets": snippet_targets,
    }


def write_memory_snippet(path: Path, project_label: str) -> None:
    snippet = SNIPPET_TEMPLATE.format(label=project_label).strip() + "\n"
    if path.exists():
        content = path.read_text(encoding="utf-8", errors="replace")
        if f'memory-label = "{project_label}"' in content:
            return
        updated = content.rstrip() + "\n\n" + snippet
    else:
        updated = snippet
    path.write_text(updated, encoding="utf-8")
