from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel


class ProjectConfig(BaseModel):
    label: str
    url: str
    token: str
    enabled: bool = True


class LocalMemoryConfig(BaseModel):
    url: str = "http://127.0.0.1:9621"
    enabled: bool = True


class ClientConfig(BaseModel):
    projects: List[ProjectConfig] = []
    local_memory: LocalMemoryConfig = LocalMemoryConfig()


# Default config path: $RAGCONNECT_CONFIG_PATH or ~/.ragconnect/client_config.yaml
def _default_path() -> Path:
    env = os.environ.get("RAGCONNECT_CONFIG_PATH")
    if env:
        return Path(env)
    return Path.home() / ".ragconnect" / "client_config.yaml"


def load_config(config_path: Optional[Path] = None) -> ClientConfig:
    path = config_path or _default_path()
    if not path.exists():
        return ClientConfig()
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    return ClientConfig(**data)


def find_project(config: ClientConfig, label: str) -> Optional[ProjectConfig]:
    for project in config.projects:
        if project.label == label and project.enabled:
            return project
    return None
