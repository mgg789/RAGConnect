from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel


class ProjectContextConfig(BaseModel):
    repo_root: str
    project_label: str
    enabled: bool = True


class DestinationConfig(BaseModel):
    """A single memory destination."""

    url: str
    label: Optional[str] = None
    token: Optional[str] = None
    enabled: bool = True
    display_name: Optional[str] = None

    @property
    def is_local(self) -> bool:
        return not self.label

    @property
    def identifier(self) -> str:
        return self.label or "local"


class ClientConfig(BaseModel):
    destinations: List[DestinationConfig] = []
    project_contexts: List[ProjectContextConfig] = []
    default_project: Optional[str] = None
    remote_only_mode: bool = False
    strict_project_routing: bool = True


def _default_path() -> Path:
    env = os.environ.get("RAGCONNECT_CONFIG_PATH")
    if env:
        return Path(env)
    return Path.home() / ".ragconnect" / "client_config.yaml"


def load_config(config_path: Optional[Path] = None) -> ClientConfig:
    path = config_path or _default_path()
    if not path.exists():
        return ClientConfig()

    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    if "projects" in data or "local_memory" in data:
        destinations: list[dict] = []
        local_memory = data.pop("local_memory", None) or {}
        if local_memory.get("url"):
            destinations.append(
                {
                    "url": local_memory["url"],
                    "enabled": local_memory.get("enabled", True),
                }
            )
        for project in data.pop("projects", []) or []:
            destinations.append(project)
        data["destinations"] = destinations

    data.setdefault("project_contexts", [])
    return ClientConfig(**data)


def save_config(config: ClientConfig, config_path: Optional[Path] = None) -> None:
    path = config_path or _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(
            config.model_dump(exclude_none=True),
            fh,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def find_local(config: ClientConfig) -> Optional[DestinationConfig]:
    for destination in config.destinations:
        if destination.is_local and destination.enabled:
            return destination
    return None


def find_project(config: ClientConfig, label: str) -> Optional[DestinationConfig]:
    for destination in config.destinations:
        if destination.label == label and destination.enabled:
            return destination
    return None
