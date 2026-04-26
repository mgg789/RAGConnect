from __future__ import annotations

import os
from pathlib import Path


def get_ragconnect_home() -> Path:
    custom = os.environ.get("RAGCONNECT_HOME")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".ragconnect"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_local_data_dir() -> Path:
    return ensure_dir(get_ragconnect_home() / "data")


def get_local_log_dir() -> Path:
    return ensure_dir(get_ragconnect_home() / "logs")


def get_local_state_dir() -> Path:
    return ensure_dir(get_ragconnect_home() / "state")


def get_server_control_dir() -> Path:
    raw = os.environ.get("RAGCONNECT_CONTROL_DIR")
    if raw:
        return ensure_dir(Path(raw))
    default = Path("/control")
    if default.exists() or str(default).startswith("/"):
        return ensure_dir(default)
    return ensure_dir(Path.cwd() / "control")


def get_server_backup_dir() -> Path:
    raw = os.environ.get("RAGCONNECT_BACKUP_DIR")
    if raw:
        return ensure_dir(Path(raw))
    default = Path("/backups")
    if default.exists() or str(default).startswith("/"):
        return ensure_dir(default)
    return ensure_dir(Path.cwd() / "backups")


def get_server_log_dir() -> Path:
    raw = os.environ.get("RAGCONNECT_SERVER_LOG_DIR")
    if raw:
        return ensure_dir(Path(raw))
    data_dir = os.environ.get("RAGCONNECT_SERVER_DATA_DIR")
    if data_dir:
        return ensure_dir(Path(data_dir) / "logs")
    default = Path("/data/logs")
    if default.exists() or str(default).startswith("/"):
        return ensure_dir(default)
    return ensure_dir(Path.cwd() / ".ragconnect-server-logs")
