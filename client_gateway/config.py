from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel


class DestinationConfig(BaseModel):
    """A single memory destination.

    Two kinds:
    - Local LightRAG  — label is None/empty, token is None.
                        Requests bypass Server Gateway and go directly to
                        LightRAG's native HTTP API (/query, /insert).
    - Project server  — label and token are both set.
                        Requests are proxied through the Server Gateway with
                        Bearer-token authentication.
    """
    url: str
    label: Optional[str] = None   # absent → local (no-auth, native API)
    token: Optional[str] = None   # absent → no auth needed
    enabled: bool = True
    display_name: Optional[str] = None
    prefer_for_search: bool = False
    allow_local_search_augmentation: bool = False

    @property
    def is_local(self) -> bool:
        return not self.label

    @property
    def identifier(self) -> str:
        """Short name used in API routes and log messages."""
        return self.label or "local"


class ClientConfig(BaseModel):
    destinations: List[DestinationConfig] = []
    default_project: Optional[str] = None
    remote_only_mode: bool = False
    strict_project_routing: bool = True


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

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

    # ---- migrate legacy format (v0.1: local_memory + projects) ----
    if "projects" in data or "local_memory" in data:
        destinations: list = []
        lm = data.pop("local_memory", None) or {}
        if lm.get("url"):
            destinations.append({
                "url": lm["url"],
                "enabled": lm.get("enabled", True),
                # no label, no token → local LightRAG
            })
        for p in data.pop("projects", []) or []:
            destinations.append(p)
        data["destinations"] = destinations

    return ClientConfig(**data)


def save_config(config: ClientConfig, config_path: Optional[Path] = None) -> None:
    path = config_path or _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        yaml.dump(
            config.model_dump(exclude_none=True),
            fh,
            default_flow_style=False,
            allow_unicode=True,
        )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def find_local(config: ClientConfig) -> Optional[DestinationConfig]:
    """Return the first enabled local (label-less) destination."""
    for d in config.destinations:
        if d.is_local and d.enabled:
            return d
    return None


def find_project(config: ClientConfig, label: str) -> Optional[DestinationConfig]:
    """Return the first enabled destination with the given label."""
    for d in config.destinations:
        if d.label == label and d.enabled:
            return d
    return None
