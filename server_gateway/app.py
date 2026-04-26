from __future__ import annotations

import base64
import json
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from server_gateway.audit import append_server_log, read_server_log
from server_gateway.auth import AuthError, require_write_role, validate_token
from server_gateway.token_store import TokenStore
from shared.control_plane import heartbeat_path, queue_request, read_result
from shared.dotenv import read_dotenv, update_dotenv
from shared.errors import ERROR_DESTINATION_UNAVAILABLE
from shared.lightrag_client import LightRAGClient
from shared.ops_log import mask_secret, read_json, tail_text, utc_now_iso, write_json
from shared.runtime import get_server_backup_dir, get_server_control_dir, get_server_log_dir
from shared.timeouts import get_request_timeout_seconds

LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://127.0.0.1:9621")
TOKEN_STORE_PATH = Path(os.environ.get("TOKEN_STORE_PATH", "server_tokens.yaml"))
ENV_FILE_PATH = Path(os.environ.get("RAGCONNECT_ENV_FILE", Path.cwd() / ".env"))
REQUEST_TIMEOUT_SECONDS = get_request_timeout_seconds()
CONTROL_DIR = get_server_control_dir()
BACKUP_DIR = get_server_backup_dir()
SERVER_LOG_DIR = get_server_log_dir()
HELPER_CONFIG_PATH = CONTROL_DIR / "state" / "helper_config.json"
LAST_MODEL_STATUS_PATH = CONTROL_DIR / "state" / "last_model_status.json"
LAST_APPLY_STATUS_PATH = CONTROL_DIR / "state" / "last_apply_status.json"

ADMIN_USERNAME = os.environ.get("RAGCONNECT_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("RAGCONNECT_ADMIN_PASSWORD", "")
RATE_LIMIT_REQUESTS = int(os.environ.get("RAGCONNECT_RATE_LIMIT_REQUESTS", "120"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RAGCONNECT_RATE_LIMIT_WINDOW_SECONDS", "60"))
BRUTE_FORCE_MAX_ATTEMPTS = int(os.environ.get("RAGCONNECT_ADMIN_MAX_ATTEMPTS", "5"))
BRUTE_FORCE_WINDOW_SECONDS = int(os.environ.get("RAGCONNECT_ADMIN_WINDOW_SECONDS", "300"))
BRUTE_FORCE_BLOCK_SECONDS = int(os.environ.get("RAGCONNECT_ADMIN_BLOCK_SECONDS", "900"))
ADMIN_SESSION_COOKIE = "ragconnect_admin_session"
ADMIN_SESSION_TTL = 1800

app = FastAPI(title="RAGConnect Server Gateway")
_token_store = TokenStore(TOKEN_STORE_PATH)
_lightrag = LightRAGClient(LIGHTRAG_URL, timeout=REQUEST_TIMEOUT_SECONDS)
_request_history: dict[str, deque[float]] = defaultdict(deque)
_admin_failures: dict[str, deque[float]] = defaultdict(deque)
_admin_blocks_until: dict[str, float] = {}
_admin_sessions: dict[str, float] = {}
_admin_basic = HTTPBasic(auto_error=False)


class SearchRequest(BaseModel):
    query: str


class WriteRequest(BaseModel):
    text: str


class IngestRequest(BaseModel):
    texts: list[str]


class TokenCreateRequest(BaseModel):
    role: str
    description: str = ""
    expires_days: int = 90


class RuntimeConfigUpdateRequest(BaseModel):
    openai_api_base: Optional[str] = None
    openai_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    local_embedding_mode: Optional[str] = None
    local_embedding_model: Optional[str] = None
    local_embedding_dim: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dim: Optional[str] = None


class HelperConfigRequest(BaseModel):
    backup_schedule_minutes: int = 0
    backup_retention_count: int = 5
    backup_retention_days: int = 14


class BackupRestoreRequest(BaseModel):
    artifact: str


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    append_server_log("runtime", "unhandled_exception", {"path": str(request.url.path), "error": str(exc)})
    return _err("internal_error", "Internal server error.", 500)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    request_bucket = _request_history[client_ip]
    while request_bucket and request_bucket[0] <= now - RATE_LIMIT_WINDOW_SECONDS:
        request_bucket.popleft()
    if len(request_bucket) >= RATE_LIMIT_REQUESTS:
        append_server_log("security", "rate_limited", {"client_ip": client_ip, "path": str(request.url.path)})
        retry_after = int(request_bucket[0] + RATE_LIMIT_WINDOW_SECONDS - now) + 1
        return JSONResponse(
            status_code=429,
            content={"status": "error", "error": {"code": "rate_limited", "message": "Too many requests."}},
            headers={"Retry-After": str(max(retry_after, 1))},
        )
    request_bucket.append(now)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN" if request.url.path.startswith("/webui") else "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"status": "error", "error": {"code": code, "message": message}})


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None


def _basic_credentials(authorization: Optional[str]) -> Optional[HTTPBasicCredentials]:
    if not authorization or not authorization.startswith("Basic "):
        return None
    try:
        raw = base64.b64decode(authorization[6:]).decode("utf-8")
        username, password = raw.split(":", 1)
    except Exception:
        return None
    return HTTPBasicCredentials(username=username, password=password)


def _new_admin_session() -> str:
    _admin_sessions.update({token: expires for token, expires in _admin_sessions.items() if expires > time.time()})
    token = secrets.token_urlsafe(32)
    _admin_sessions[token] = time.time() + ADMIN_SESSION_TTL
    return token


def _check_admin_session(token: Optional[str]) -> bool:
    if not token:
        return False
    return _admin_sessions.get(token, 0) > time.time()


async def _require_admin(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(_admin_basic),
) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    if _check_admin_session(request.cookies.get(ADMIN_SESSION_COOKIE)):
        return

    blocked_until = _admin_blocks_until.get(client_ip)
    if blocked_until and blocked_until > now:
        retry_after = int(blocked_until - now) + 1
        raise HTTPException(status_code=429, detail="Too many failed admin attempts.", headers={"Retry-After": str(retry_after)})

    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin password is not configured.")

    if credentials is None:
        raise HTTPException(status_code=401, detail="Admin authentication required.", headers={"WWW-Authenticate": 'Basic realm="RAGConnect Admin"'})

    valid_user = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    valid_pass = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (valid_user and valid_pass):
        failures = _admin_failures[client_ip]
        while failures and failures[0] <= now - BRUTE_FORCE_WINDOW_SECONDS:
            failures.popleft()
        failures.append(now)
        append_server_log("security", "invalid_admin_login", {"client_ip": client_ip})
        if len(failures) >= BRUTE_FORCE_MAX_ATTEMPTS:
            _admin_blocks_until[client_ip] = now + BRUTE_FORCE_BLOCK_SECONDS
            failures.clear()
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")

    _admin_failures.pop(client_ip, None)


def _read_token_store() -> dict:
    if not TOKEN_STORE_PATH.exists():
        return {"tokens": []}
    with TOKEN_STORE_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"tokens": []}


def _write_token_store(data: dict) -> None:
    TOKEN_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TOKEN_STORE_PATH.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)


def _runtime_payload() -> dict:
    runtime = read_dotenv(ENV_FILE_PATH)
    api_key = runtime.get("OPENAI_API_KEY", "")
    return {
        "status": "ok",
        "env_path": str(ENV_FILE_PATH),
        "openai_api_base": runtime.get("OPENAI_API_BASE", ""),
        "has_openai_api_key": bool(api_key),
        "masked_openai_api_key": mask_secret(api_key),
        "llm_model": runtime.get("LLM_MODEL", ""),
        "local_embedding_mode": runtime.get("LOCAL_EMBEDDING_MODE", ""),
        "local_embedding_model": runtime.get("LOCAL_EMBEDDING_MODEL", ""),
        "local_embedding_dim": runtime.get("LOCAL_EMBEDDING_DIM", ""),
        "embedding_model": runtime.get("EMBEDDING_MODEL", ""),
        "embedding_dim": runtime.get("EMBEDDING_DIM", ""),
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
    }


def _helper_config() -> dict:
    return read_json(HELPER_CONFIG_PATH, {"backup_schedule_minutes": 0, "backup_retention_count": 5, "backup_retention_days": 14})


def _helper_online() -> bool:
    heartbeat = read_json(heartbeat_path(), None)
    if not isinstance(heartbeat, dict):
        return False
    raw = heartbeat.get("timestamp")
    if not raw:
        return False
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - ts).total_seconds() <= 120


async def _proxy_lightrag(request: Request, path: str) -> Response:
    target = f"{LIGHTRAG_URL}{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length", "connection"}}
    body = await request.body()
    async with httpx.AsyncClient(follow_redirects=False, timeout=REQUEST_TIMEOUT_SECONDS) as client:
        upstream = await client.request(request.method, target, headers=forward_headers, content=body if body else None)
    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() in {"cache-control", "content-disposition", "content-type", "etag", "expires", "last-modified", "location", "pragma"}}
    if response_headers.get("location", "").startswith(LIGHTRAG_URL):
        response_headers["location"] = response_headers["location"][len(LIGHTRAG_URL):] or "/"
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


def _queue_helper_action(action: str, payload: dict, wait_seconds: int = 20) -> dict:
    request_id = queue_request(action, payload)
    if not _helper_online():
        return {
            "status": "saved_not_applied",
            "request_id": request_id,
            "helper_online": False,
            "message": "Host helper is offline. Start `ragconnect-host-helper daemon` on the host machine.",
        }
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        result = read_result(request_id)
        if result:
            return {**result, "helper_online": True}
        time.sleep(1)
    return {
        "status": "apply_in_progress",
        "request_id": request_id,
        "helper_online": True,
        "message": "Request queued; host helper is still processing.",
    }


async def _health_payload() -> dict:
    try:
        lightrag_ok = await _lightrag.health()
    except Exception:
        lightrag_ok = False
    return {
        "status": "ok" if lightrag_ok else "error",
        "server_gateway": "ok",
        "lightrag": "ok" if lightrag_ok else "error",
        "token_store_exists": TOKEN_STORE_PATH.exists(),
        "env_exists": ENV_FILE_PATH.exists(),
        "helper_online": _helper_online(),
    }


def _issue_ui_response(html: str) -> HTMLResponse:
    response = HTMLResponse(html)
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=_new_admin_session(),
        max_age=ADMIN_SESSION_TTL,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


@app.post("/search")
async def search(request: SearchRequest, authorization: Optional[str] = Header(None)) -> JSONResponse:
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        append_server_log("security", "invalid_token", {"path": "/search", "code": exc.code})
        return _err(exc.code, exc.message, exc.http_status)
    try:
        results = await _lightrag.search(request.query)
        append_server_log("audit", "memory_search", {"query": request.query, "results": len(results)})
        return JSONResponse(content={"status": "ok", "source": "project", "results": [item.model_dump(exclude_none=True) for item in results]})
    except Exception as exc:
        append_server_log("runtime", "memory_search_error", {"error": str(exc)})
        return _err(ERROR_DESTINATION_UNAVAILABLE, f"LightRAG unavailable: {exc}", 503)


@app.post("/write")
async def write(request: WriteRequest, authorization: Optional[str] = Header(None)) -> JSONResponse:
    try:
        token_info = validate_token(_bearer(authorization), _token_store)
        require_write_role(token_info)
    except AuthError as exc:
        append_server_log("security", "write_denied", {"code": exc.code})
        return _err(exc.code, exc.message, exc.http_status)
    try:
        await _lightrag.write(request.text)
        append_server_log("audit", "memory_write", {"text_preview": request.text[:120]})
        return JSONResponse(content={"status": "ok", "source": "project", "message": "Memory entry written successfully."})
    except Exception as exc:
        append_server_log("runtime", "memory_write_error", {"error": str(exc)})
        return _err(ERROR_DESTINATION_UNAVAILABLE, f"LightRAG unavailable: {exc}", 503)


@app.post("/ingest")
async def ingest(request: IngestRequest, authorization: Optional[str] = Header(None)) -> JSONResponse:
    try:
        token_info = validate_token(_bearer(authorization), _token_store)
        require_write_role(token_info)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        payload = await _lightrag.ingest(request.texts)
        append_server_log("audit", "memory_ingest_bulk", {"count": len(request.texts)})
        return JSONResponse(content={"status": "ok", "source": "project", "data": payload})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.get("/documents")
async def documents(authorization: Optional[str] = Header(None)) -> JSONResponse:
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        return JSONResponse(content={"status": "ok", "source": "project", "data": await _lightrag.documents()})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.get("/entities")
async def entities(authorization: Optional[str] = Header(None)) -> JSONResponse:
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        return JSONResponse(content={"status": "ok", "source": "project", "data": await _lightrag.entities()})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.get("/relations")
async def relations(authorization: Optional[str] = Header(None)) -> JSONResponse:
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        return JSONResponse(content={"status": "ok", "source": "project", "data": await _lightrag.relations()})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.get("/graph")
async def graph(authorization: Optional[str] = Header(None)) -> JSONResponse:
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        return JSONResponse(content={"status": "ok", "source": "project", "data": await _lightrag.graph()})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.post("/rebuild")
async def rebuild(authorization: Optional[str] = Header(None)) -> JSONResponse:
    try:
        token_info = validate_token(_bearer(authorization), _token_store)
        require_write_role(token_info)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        payload = await _lightrag.rebuild()
        append_server_log("audit", "memory_rebuild", {})
        return JSONResponse(content={"status": "ok", "source": "project", "data": payload})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.get("/health")
async def health(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False))) -> Response:
    if _check_admin_session(request.cookies.get(ADMIN_SESSION_COOKIE)) or credentials is not None:
        try:
            await _require_admin(request, credentials)
            return JSONResponse(content=await _health_payload())
        except HTTPException:
            pass
    lightrag_ok = await _lightrag.health()
    return JSONResponse(content={"status": "ok" if lightrag_ok else "error", "lightrag": "ok" if lightrag_ok else "error"})


@app.get("/", include_in_schema=False)
@app.get("/RAGConnect", include_in_schema=False)
@app.get("/RAGConnect/", include_in_schema=False)
async def root_redirect() -> JSONResponse:
    return JSONResponse(status_code=307, content={"status": "redirect", "location": "/ui/configs"}, headers={"Location": "/ui/configs"})


@app.api_route("/auth-status", methods=["GET"], include_in_schema=False)
@app.api_route("/documents/paginated", methods=["POST"], include_in_schema=False)
@app.api_route("/graph/label/popular", methods=["GET"], include_in_schema=False)
@app.api_route("/graph/label/list", methods=["GET"], include_in_schema=False)
@app.api_route("/graphs", methods=["GET"], include_in_schema=False)
@app.api_route("/webui", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/webui/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/webui/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def admin_lightrag_proxy(request: Request, path: str = "", _: None = Depends(_require_admin)) -> Response:
    del path
    return await _proxy_lightrag(request, request.url.path)


@app.get("/admin/tokens")
async def admin_tokens_list(_: None = Depends(_require_admin)) -> JSONResponse:
    data = _read_token_store()
    safe = [{"token_id": token.get("token_id", ""), "role": token.get("role", ""), "enabled": token.get("enabled", True), "description": token.get("description", ""), "expires_at": token.get("expires_at", "")} for token in data.get("tokens", [])]
    return JSONResponse(content={"status": "ok", "tokens": safe})


@app.post("/admin/tokens")
async def admin_tokens_create(req: TokenCreateRequest, _: None = Depends(_require_admin)) -> JSONResponse:
    if req.role not in ("readonly", "write"):
        return _err("invalid_role", "Role must be 'readonly' or 'write'.", 400)
    raw = secrets.token_hex(24)
    new_token = f"tok_{raw}"
    expires_at = (datetime.now(timezone.utc) + timedelta(days=req.expires_days)).isoformat().replace("+00:00", "Z")
    token_id = f"tid_{secrets.token_hex(8)}"
    entry: dict = {"token_id": token_id, "token_hash": _token_store.hash_token(new_token), "role": req.role, "enabled": True, "expires_at": expires_at}
    if req.description:
        entry["description"] = req.description
    data = _read_token_store()
    data.setdefault("tokens", []).append(entry)
    _write_token_store(data)
    append_server_log("audit", "token_created", {"token_id": token_id, "role": req.role})
    return JSONResponse(content={"status": "ok", "token": new_token, "token_id": token_id, "role": req.role, "expires_at": expires_at})


@app.delete("/admin/tokens/{token_id}")
async def admin_tokens_revoke(token_id: str, _: None = Depends(_require_admin)) -> JSONResponse:
    data = _read_token_store()
    matched = False
    for token in data.get("tokens", []):
        if token.get("token_id") == token_id:
            token["enabled"] = False
            matched = True
    if not matched:
        return _err("not_found", f"Token '{token_id}' not found.", 404)
    _write_token_store(data)
    append_server_log("audit", "token_revoked", {"token_id": token_id})
    return JSONResponse(content={"status": "ok"})


@app.get("/admin/runtime-config")
async def admin_runtime_config(_: None = Depends(_require_admin)) -> JSONResponse:
    return JSONResponse(content=_runtime_payload())


@app.put("/admin/runtime-config")
async def admin_runtime_config_update(req: RuntimeConfigUpdateRequest, _: None = Depends(_require_admin)) -> JSONResponse:
    updates: dict[str, str | None] = {
        "OPENAI_API_BASE": (req.openai_api_base or "").strip() or None,
        "LLM_MODEL": (req.llm_model or "").strip() or None,
        "LOCAL_EMBEDDING_MODE": (req.local_embedding_mode or "").strip() or None,
        "LOCAL_EMBEDDING_MODEL": (req.local_embedding_model or "").strip() or None,
        "LOCAL_EMBEDDING_DIM": (req.local_embedding_dim or "").strip() or None,
        "EMBEDDING_MODEL": (req.embedding_model or "").strip() or None,
        "EMBEDDING_DIM": (req.embedding_dim or "").strip() or None,
    }
    incoming_key = (req.openai_api_key or "").strip()
    if incoming_key:
        updates["OPENAI_API_KEY"] = incoming_key
    update_dotenv(ENV_FILE_PATH, updates)
    append_server_log("runtime", "runtime_config_updated", {"openai_api_base": updates.get("OPENAI_API_BASE"), "llm_model": updates.get("LLM_MODEL")})
    return JSONResponse(content={**_runtime_payload(), "restart_required": True})


@app.get("/admin/health-summary")
async def admin_health_summary(_: None = Depends(_require_admin)) -> JSONResponse:
    return JSONResponse(content={"status": "ok", "health": await _health_payload()})


@app.get("/admin/helper-config")
async def admin_helper_config(_: None = Depends(_require_admin)) -> JSONResponse:
    return JSONResponse(content={"status": "ok", "helper_online": _helper_online(), "config": _helper_config()})


@app.put("/admin/helper-config")
async def admin_helper_config_update(req: HelperConfigRequest, _: None = Depends(_require_admin)) -> JSONResponse:
    write_json(HELPER_CONFIG_PATH, req.model_dump())
    append_server_log("runtime", "helper_config_updated", req.model_dump())
    return JSONResponse(content={"status": "ok", "config": req.model_dump()})


@app.get("/admin/helper-status")
async def admin_helper_status(_: None = Depends(_require_admin)) -> JSONResponse:
    heartbeat = read_json(heartbeat_path(), None)
    return JSONResponse(content={"status": "ok", "helper_online": _helper_online(), "heartbeat": heartbeat, "control_dir": str(CONTROL_DIR), "backup_dir": str(BACKUP_DIR)})


@app.post("/admin/model/validate")
async def admin_model_validate(req: RuntimeConfigUpdateRequest, _: None = Depends(_require_admin)) -> JSONResponse:
    payload = {**_runtime_payload(), **req.model_dump(exclude_none=True)}
    result = _queue_helper_action("validate-runtime", payload, wait_seconds=15)
    write_json(LAST_MODEL_STATUS_PATH, result)
    append_server_log("apply", "model_validate", {"result": result.get("status"), "llm_model": payload.get("llm_model")})
    return JSONResponse(content=result)


@app.get("/admin/model/status")
async def admin_model_status(_: None = Depends(_require_admin)) -> JSONResponse:
    payload = read_json(LAST_MODEL_STATUS_PATH, {"status": "unknown"})
    apply_status = read_json(LAST_APPLY_STATUS_PATH, {"status": "unknown"})
    return JSONResponse(content={"status": "ok", "current_runtime": _runtime_payload(), "last_validation": payload, "last_apply": apply_status})


@app.post("/admin/model/apply")
async def admin_model_apply(req: RuntimeConfigUpdateRequest, _: None = Depends(_require_admin)) -> JSONResponse:
    payload = req.model_dump(exclude_none=True)
    result = _queue_helper_action("apply-runtime", payload, wait_seconds=30)
    write_json(LAST_APPLY_STATUS_PATH, result)
    append_server_log("apply", "model_apply", {"result": result.get("status"), "llm_model": payload.get("llm_model")})
    return JSONResponse(content=result)


@app.get("/admin/backups")
async def admin_backups(_: None = Depends(_require_admin)) -> JSONResponse:
    items = []
    for artifact in sorted(BACKUP_DIR.glob("*.zip"), reverse=True):
        items.append({"name": artifact.name, "size_bytes": artifact.stat().st_size, "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(artifact.stat().st_mtime))})
    return JSONResponse(content={"status": "ok", "items": items, "helper_config": _helper_config(), "helper_online": _helper_online()})


@app.post("/admin/backups")
async def admin_backup_create(_: None = Depends(_require_admin)) -> JSONResponse:
    result = _queue_helper_action("backup", {}, wait_seconds=30)
    append_server_log("backup", "backup_requested", {"result": result.get("status")})
    return JSONResponse(content=result)


@app.post("/admin/backups/restore")
async def admin_backup_restore(req: BackupRestoreRequest, _: None = Depends(_require_admin)) -> JSONResponse:
    result = _queue_helper_action("restore", req.model_dump(), wait_seconds=60)
    append_server_log("backup", "restore_requested", {"artifact": req.artifact, "result": result.get("status")})
    return JSONResponse(content=result)


@app.post("/admin/backups/prune")
async def admin_backup_prune(_: None = Depends(_require_admin)) -> JSONResponse:
    result = _queue_helper_action("prune-backups", _helper_config(), wait_seconds=20)
    return JSONResponse(content=result)


@app.get("/admin/logs")
async def admin_logs(name: str = Query(default="audit"), limit: int = Query(default=100, ge=10, le=500), _: None = Depends(_require_admin)) -> JSONResponse:
    if name in {"audit", "runtime", "security", "health", "backup", "apply"}:
        return JSONResponse(content={"status": "ok", "items": read_server_log(name, limit=limit)})
    text_logs = {
        "server_stdout": SERVER_LOG_DIR / "server.stdout.log",
        "server_stderr": SERVER_LOG_DIR / "server.stderr.log",
    }
    path = text_logs.get(name)
    if not path:
        return _err("invalid_log", f"Unknown log stream '{name}'.", 400)
    return JSONResponse(content={"status": "ok", "lines": tail_text(path, limit=limit)})


@app.get("/ui/configs", response_class=HTMLResponse)
async def ui_configs(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False))) -> HTMLResponse:
    await _require_admin(request, credentials)
    return _issue_ui_response(_ADMIN_HTML)


@app.get("/ui/graph", response_class=HTMLResponse)
async def ui_graph(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False))) -> HTMLResponse:
    await _require_admin(request, credentials)
    return _issue_ui_response(_GRAPH_HTML)


_ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RAGConnect Admin</title>
<style>
  :root { --bg:#0f1117; --surface:#181c26; --muted:#93a0b3; --text:#edf2f7; --border:#2a3344; --accent:#3b82f6; --ok:#10b981; --warn:#f59e0b; --err:#ef4444; }
  * { box-sizing:border-box; } body { margin:0; font:14px/1.45 "Segoe UI",system-ui,sans-serif; background:var(--bg); color:var(--text); }
  header { padding:16px 24px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; }
  main { max-width:1280px; margin:0 auto; padding:24px; display:grid; gap:16px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:16px; }
  h1,h2,p { margin:0; } h1 { font-size:20px; } h2 { font-size:15px; margin-bottom:12px; }
  .muted { color:var(--muted); } .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; }
  .ok { background:rgba(16,185,129,.15); color:#86efac; } .warn { background:rgba(245,158,11,.15); color:#fcd34d; } .err { background:rgba(239,68,68,.15); color:#fca5a5; }
  table { width:100%; border-collapse:collapse; } th,td { padding:8px 6px; border-bottom:1px solid rgba(255,255,255,.05); text-align:left; vertical-align:top; }
  input,select,textarea,button { width:100%; background:#0f172a; color:var(--text); border:1px solid var(--border); border-radius:10px; padding:10px 12px; }
  button { cursor:pointer; background:var(--accent); border-color:var(--accent); font-weight:600; }
  .row { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px; }
  .row-3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-bottom:10px; }
  code, pre { background:#0b1220; border:1px solid rgba(255,255,255,.06); border-radius:10px; padding:8px; overflow:auto; white-space:pre-wrap; }
</style>
</head>
<body>
<header>
  <div><h1>RAGConnect Server Admin</h1><p class="muted">Tokens, runtime, model apply, helper, backups and logs.</p></div>
  <div style="display:flex;gap:12px"><a href="/ui/graph" style="color:white">Graph</a><button style="width:auto" onclick="loadAll()">Refresh</button></div>
</header>
<main>
  <div class="grid">
    <section class="card"><h2>Health</h2><div id="health" class="muted">Loading…</div></section>
    <section class="card"><h2>Runtime</h2><div id="runtime" class="muted">Loading…</div><div class="row"><input id="runtime-base" placeholder="OPENAI_API_BASE"><input id="runtime-model" placeholder="LLM_MODEL"></div><div class="row"><input id="runtime-key" placeholder="OPENAI_API_KEY (optional)"><button onclick="saveRuntime()">Save runtime</button></div></section>
    <section class="card"><h2>Model Status</h2><div id="model-status" class="muted">Loading…</div><div class="row"><button onclick="validateModel()">Validate</button><button onclick="applyModel()">Apply and restart</button></div></section>
  </div>
  <div class="grid">
    <section class="card"><h2>Helper and Backups</h2><div id="helper" class="muted">Loading…</div><div class="row-3"><input id="backup-schedule" placeholder="schedule minutes"><input id="backup-retention" placeholder="retention count"><button onclick="saveHelperConfig()">Save helper config</button></div><div class="row"><button onclick="createBackup()">Create backup</button><input id="restore-artifact" placeholder="artifact.zip for restore"></div><div class="row"><button onclick="restoreBackup()">Restore backup</button><button onclick="pruneBackups()">Prune backups</button></div></section>
    <section class="card"><h2>Tokens</h2><div class="row-3"><select id="token-role"><option value="write">write</option><option value="readonly">readonly</option></select><input id="token-desc" placeholder="description"><button onclick="createToken()">Create token</button></div><div id="tokens" class="muted">Loading…</div></section>
  </div>
  <section class="card"><h2>Logs</h2><div class="row"><select id="log-name"><option value="audit">audit</option><option value="runtime">runtime</option><option value="security">security</option><option value="backup">backup</option><option value="apply">apply</option></select><button onclick="loadLogs()">Load logs</button></div><pre id="logs">No logs loaded.</pre></section>
</main>
<script>
async function j(url, options) { const r = await fetch(url, options); const d = await r.json(); if (!r.ok) throw new Error(d.detail || d.error?.message || JSON.stringify(d)); return d; }
function esc(v) { return String(v ?? '').replace(/[&<>"]/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[s])); }
function badge(ok, label) { const cls = ok === 'ok' || ok === 'applied_ok' ? 'ok' : ok === 'warning' || ok === 'saved_not_applied' || ok === 'apply_in_progress' ? 'warn' : 'err'; return `<span class="badge ${cls}">${esc(label || ok)}</span>`; }
async function loadAll() { await Promise.all([loadHealth(), loadRuntime(), loadModelStatus(), loadHelper(), loadTokens()]); await loadLogs(); }
async function loadHealth() {
  const data = await j('/admin/health-summary');
  const h = data.health;
  document.getElementById('health').innerHTML = `<div>gateway: ${badge(h.server_gateway, h.server_gateway)}</div><div>lightrag: ${badge(h.lightrag, h.lightrag)}</div><div>helper: ${badge(h.helper_online ? 'ok':'warning', h.helper_online ? 'online':'offline')}</div>`;
}
async function loadRuntime() {
  const data = await j('/admin/runtime-config');
  document.getElementById('runtime-base').value = data.openai_api_base || '';
  document.getElementById('runtime-model').value = data.llm_model || '';
  document.getElementById('runtime-key').value = '';
  document.getElementById('runtime').innerHTML = `<div>OPENAI_API_BASE: <code>${esc(data.openai_api_base || 'not set')}</code></div><div>LLM_MODEL: <code>${esc(data.llm_model || 'not set')}</code></div><div>Embedding: <code>${esc(data.local_embedding_model || data.embedding_model || 'n/a')}</code> / dim <code>${esc(data.local_embedding_dim || data.embedding_dim || 'n/a')}</code></div><div>API key: <code>${esc(data.masked_openai_api_key || 'not set')}</code></div>`;
}
async function loadModelStatus() {
  const data = await j('/admin/model/status');
  const current = data.current_runtime;
  const validation = data.last_validation || {};
  const apply = data.last_apply || {};
  const warning = validation.message ? `<div class="muted">${esc(validation.message)}</div>` : '';
  document.getElementById('model-status').innerHTML = `<div>Current model: <code>${esc(current.llm_model || 'not set')}</code></div><div>Validation: ${badge(validation.status || 'warning', validation.status || 'unknown')}</div><div>Last apply: ${badge(apply.status || 'warning', apply.status || 'unknown')}</div>${warning}`;
}
async function loadHelper() {
  const status = await j('/admin/helper-status');
  const backups = await j('/admin/backups');
  document.getElementById('backup-schedule').value = backups.helper_config.backup_schedule_minutes || 0;
  document.getElementById('backup-retention').value = backups.helper_config.backup_retention_count || 5;
  const items = (backups.items || []).slice(0, 5).map(item => `<li>${esc(item.name)} (${Math.round((item.size_bytes || 0)/1024)} KiB)</li>`).join('');
  document.getElementById('helper').innerHTML = `<div>Helper: ${badge(status.helper_online ? 'ok':'warning', status.helper_online ? 'online':'offline')}</div><div>Backups: <ul>${items || '<li class="muted">No backups yet</li>'}</ul></div><div>Schedule: every <code>${esc(backups.helper_config.backup_schedule_minutes || 0)}</code> min</div>`;
}
async function loadTokens() {
  const data = await j('/admin/tokens');
  const rows = (data.tokens || []).map(t => `<tr><td>${esc(t.token_id)}</td><td>${esc(t.role)}</td><td>${badge(t.enabled ? 'ok':'error', t.enabled ? 'enabled':'revoked')}</td><td>${esc(t.expires_at || '—')}</td></tr>`).join('');
  document.getElementById('tokens').innerHTML = `<table><thead><tr><th>ID</th><th>Role</th><th>Status</th><th>Expires</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">No tokens</td></tr>'}</tbody></table>`;
}
async function loadLogs() {
  const name = document.getElementById('log-name').value;
  const data = await j('/admin/logs?name=' + encodeURIComponent(name) + '&limit=100');
  document.getElementById('logs').textContent = JSON.stringify(data.items || data.lines || [], null, 2);
}
async function saveRuntime() {
  await j('/admin/runtime-config', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ openai_api_base: document.getElementById('runtime-base').value, llm_model: document.getElementById('runtime-model').value, openai_api_key: document.getElementById('runtime-key').value }) });
  await loadRuntime();
}
async function validateModel() {
  const data = await j('/admin/model/validate', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ openai_api_base: document.getElementById('runtime-base').value, llm_model: document.getElementById('runtime-model').value, openai_api_key: document.getElementById('runtime-key').value }) });
  alert(JSON.stringify(data, null, 2));
  await loadModelStatus();
}
async function applyModel() {
  const data = await j('/admin/model/apply', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ openai_api_base: document.getElementById('runtime-base').value, llm_model: document.getElementById('runtime-model').value, openai_api_key: document.getElementById('runtime-key').value }) });
  alert(JSON.stringify(data, null, 2));
  await loadModelStatus(); await loadHealth();
}
async function saveHelperConfig() {
  await j('/admin/helper-config', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ backup_schedule_minutes: parseInt(document.getElementById('backup-schedule').value || '0', 10), backup_retention_count: parseInt(document.getElementById('backup-retention').value || '5', 10), backup_retention_days: 14 }) });
  await loadHelper();
}
async function createBackup() {
  const data = await j('/admin/backups', { method:'POST' });
  alert(JSON.stringify(data, null, 2));
  await loadHelper();
}
async function restoreBackup() {
  const data = await j('/admin/backups/restore', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ artifact: document.getElementById('restore-artifact').value }) });
  alert(JSON.stringify(data, null, 2));
}
async function pruneBackups() {
  const data = await j('/admin/backups/prune', { method:'POST' });
  alert(JSON.stringify(data, null, 2));
  await loadHelper();
}
async function createToken() {
  const data = await j('/admin/tokens', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ role: document.getElementById('token-role').value, description: document.getElementById('token-desc').value, expires_days: 90 }) });
  alert('New token (shown once):\\n' + (data.token || ''));
  document.getElementById('token-desc').value = '';
  await loadTokens();
}
loadAll();
</script>
</body>
</html>"""


_GRAPH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RAGConnect Graph</title>
<style>html,body,iframe{margin:0;width:100%;height:100%;background:#0f1117;color:white;font-family:sans-serif}header{height:48px;padding:0 16px;display:flex;align-items:center;justify-content:space-between;background:#181c26;border-bottom:1px solid #2a3344}iframe{height:calc(100% - 49px);border:0}</style>
</head>
<body>
<header><div>RAGConnect Graph</div><a href="/ui/configs" style="color:white">Admin</a></header>
<iframe src="/webui/#/" title="LightRAG"></iframe>
</body>
</html>"""
