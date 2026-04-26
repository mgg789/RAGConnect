from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.ops_log import append_jsonl, read_jsonl_tail, utc_now_iso
from shared.runtime import get_local_log_dir


def audit_log_path() -> Path:
    return get_local_log_dir() / "client_audit.jsonl"


def runtime_log_path() -> Path:
    return get_local_log_dir() / "client_runtime.jsonl"


def health_log_path() -> Path:
    return get_local_log_dir() / "client_health.jsonl"


def append_audit(event_type: str, payload: dict[str, Any]) -> None:
    entry = {"timestamp": utc_now_iso(), "event_type": event_type, **payload}
    append_jsonl(audit_log_path(), entry)


def append_runtime(event_type: str, payload: dict[str, Any]) -> None:
    entry = {"timestamp": utc_now_iso(), "event_type": event_type, **payload}
    append_jsonl(runtime_log_path(), entry)


def append_health(payload: dict[str, Any]) -> None:
    entry = {"timestamp": utc_now_iso(), **payload}
    append_jsonl(health_log_path(), entry)


def read_recent_activity(limit: int = 50) -> list[dict[str, Any]]:
    return read_jsonl_tail(audit_log_path(), limit=limit)
