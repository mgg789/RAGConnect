from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.ops_log import append_jsonl, read_jsonl_tail, utc_now_iso
from shared.runtime import get_server_log_dir


def _path(name: str) -> Path:
    return get_server_log_dir() / f"{name}.jsonl"


def append_server_log(name: str, event_type: str, payload: dict[str, Any]) -> None:
    append_jsonl(_path(name), {"timestamp": utc_now_iso(), "event_type": event_type, **payload})


def read_server_log(name: str, limit: int = 100) -> list[dict[str, Any]]:
    return read_jsonl_tail(_path(name), limit=limit)
