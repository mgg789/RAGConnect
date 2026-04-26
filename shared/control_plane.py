from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from shared.ops_log import read_json, utc_now_iso, write_json
from shared.runtime import ensure_dir, get_server_control_dir


def control_dir() -> Path:
    return get_server_control_dir()


def requests_dir() -> Path:
    return ensure_dir(control_dir() / "requests")


def results_dir() -> Path:
    return ensure_dir(control_dir() / "results")


def state_dir() -> Path:
    return ensure_dir(control_dir() / "state")


def heartbeat_path() -> Path:
    return state_dir() / "helper_heartbeat.json"


def queue_request(action: str, payload: dict[str, Any]) -> str:
    request_id = f"req_{secrets.token_hex(8)}"
    request = {
        "id": request_id,
        "action": action,
        "payload": payload,
        "status": "queued",
        "requested_at": utc_now_iso(),
    }
    write_json(requests_dir() / f"{request_id}.json", request)
    return request_id


def list_pending_requests() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for path in sorted(requests_dir().glob("*.json")):
        payload = read_json(path, {})
        if isinstance(payload, dict):
            payload["_path"] = str(path)
            output.append(payload)
    return output


def write_result(request_id: str, payload: dict[str, Any]) -> Path:
    path = results_dir() / f"{request_id}.json"
    write_json(path, payload)
    return path


def read_result(request_id: str) -> dict[str, Any] | None:
    path = results_dir() / f"{request_id}.json"
    payload = read_json(path, None)
    return payload if isinstance(payload, dict) else None


def mark_heartbeat(extra: dict[str, Any] | None = None) -> None:
    payload = {
        "timestamp": utc_now_iso(),
        "pid": os.getpid(),
    }
    if extra:
        payload.update(extra)
    write_json(heartbeat_path(), payload)
