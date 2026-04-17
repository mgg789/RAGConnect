"""Server Gateway — FastAPI application.

Configuration via environment variables:

    LIGHTRAG_URL       URL of the local LightRAG instance  (default: http://127.0.0.1:9621)
    TOKEN_STORE_PATH   Path to the token YAML file          (default: server_tokens.yaml)

Start via CLI:

    ragconnect-server start --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import json
import os
import secrets
import time
import base64
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from shared.errors import ERROR_DESTINATION_UNAVAILABLE
from shared.lightrag_client import LightRAGClient
from server_gateway.auth import AuthError, require_write_role, validate_token
from server_gateway.token_store import TokenStore

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

LIGHTRAG_URL: str = os.environ.get("LIGHTRAG_URL", "http://127.0.0.1:9621")
TOKEN_STORE_PATH: Path = Path(os.environ.get("TOKEN_STORE_PATH", "server_tokens.yaml"))

app = FastAPI(title="RAGConnect Server Gateway")
_admin_basic = HTTPBasic(auto_error=False)

_token_store = TokenStore(TOKEN_STORE_PATH)
_lightrag = LightRAGClient(LIGHTRAG_URL)

# Short-lived page nonces for admin JS calls (avoids Basic-auth-in-fetch issues)
_admin_nonces: dict[str, float] = {}
_NONCE_TTL = 1800  # 30 min
_admin_sessions: dict[str, float] = {}
_ADMIN_SESSION_TTL = 1800  # 30 min
_ADMIN_SESSION_COOKIE = "ragconnect_admin_session"


def _new_nonce() -> str:
    _admin_nonces.update({k: v for k, v in _admin_nonces.items() if v > time.time()})  # GC
    nonce = secrets.token_urlsafe(32)
    _admin_nonces[nonce] = time.time() + _NONCE_TTL
    return nonce


def _check_nonce(nonce: str) -> bool:
    exp = _admin_nonces.get(nonce, 0)
    return exp > time.time()


def _new_admin_session() -> str:
    _admin_sessions.update({k: v for k, v in _admin_sessions.items() if v > time.time()})
    token = secrets.token_urlsafe(32)
    _admin_sessions[token] = time.time() + _ADMIN_SESSION_TTL
    return token


def _check_admin_session(token: Optional[str]) -> bool:
    if not token:
        return False
    exp = _admin_sessions.get(token, 0)
    return exp > time.time()
ADMIN_USERNAME = os.environ.get("RAGCONNECT_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("RAGCONNECT_ADMIN_PASSWORD", "")
RATE_LIMIT_REQUESTS = int(os.environ.get("RAGCONNECT_RATE_LIMIT_REQUESTS", "120"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RAGCONNECT_RATE_LIMIT_WINDOW_SECONDS", "60"))
BRUTE_FORCE_MAX_ATTEMPTS = int(os.environ.get("RAGCONNECT_ADMIN_MAX_ATTEMPTS", "5"))
BRUTE_FORCE_WINDOW_SECONDS = int(os.environ.get("RAGCONNECT_ADMIN_WINDOW_SECONDS", "300"))
BRUTE_FORCE_BLOCK_SECONDS = int(os.environ.get("RAGCONNECT_ADMIN_BLOCK_SECONDS", "900"))
_request_history: dict[str, deque[float]] = defaultdict(deque)
_admin_failures: dict[str, deque[float]] = defaultdict(deque)
_admin_blocks_until: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Exception handler — return our own JSON shape instead of FastAPI default
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    return _err("internal_error", "Internal server error.", 500)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    # Generic API rate limiting by client IP.
    request_bucket = _request_history[client_ip]
    while request_bucket and request_bucket[0] <= now - RATE_LIMIT_WINDOW_SECONDS:
        request_bucket.popleft()
    if len(request_bucket) >= RATE_LIMIT_REQUESTS:
        retry_after = int(request_bucket[0] + RATE_LIMIT_WINDOW_SECONDS - now) + 1
        return JSONResponse(
            status_code=429,
            content={
                "status": "error",
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests. Please retry later.",
                },
            },
            headers={"Retry-After": str(max(retry_after, 1))},
        )
    request_bucket.append(now)

    if request.url.path == "/ui/graph":
        credentials = _basic_credentials(request.headers.get("authorization"))
        if credentials is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Admin authentication required."},
                headers={"WWW-Authenticate": 'Basic realm="RAGConnect Admin"'},
            )
        try:
            await _require_admin(request, credentials)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers or {},
            )
        response = HTMLResponse(_LIGHTRAG_GRAPH_BOOTSTRAP_HTML)
        response.set_cookie(
            key=_ADMIN_SESSION_COOKIE,
            value=_new_admin_session(),
            max_age=_ADMIN_SESSION_TTL,
            httponly=True,
            samesite="Lax",
            path="/",
        )
    else:
        response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    allow_same_origin_frame = (
        request.url.path.startswith("/webui")
        or request.url.path in {
            "/ui/graph",
            "/auth-status",
            "/documents/paginated",
            "/graph/label/popular",
            "/graph/label/list",
            "/graphs",
        }
    )
    response.headers["X-Frame-Options"] = "SAMEORIGIN" if allow_same_origin_frame else "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"status": "error", "error": {"code": code, "message": message}},
    )


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


_PROXY_RESPONSE_HEADERS = {
    "cache-control",
    "content-disposition",
    "content-type",
    "etag",
    "expires",
    "last-modified",
    "location",
    "pragma",
}


async def _proxy_lightrag(request: Request, path: str) -> Response:
    query = request.url.query
    target = f"{LIGHTRAG_URL}{path}"
    if query:
        target = f"{target}?{query}"

    # Strip hop-by-hop headers and let httpx recalculate payload metadata.
    forward_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "connection"}
    }
    body = await request.body()

    async with httpx.AsyncClient(follow_redirects=False, timeout=120.0) as client:
        upstream = await client.request(
            request.method,
            target,
            headers=forward_headers,
            content=body if body else None,
        )

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() in _PROXY_RESPONSE_HEADERS
    }
    location = response_headers.get("location")
    if location and location.startswith(LIGHTRAG_URL):
        rewritten = location[len(LIGHTRAG_URL):]
        response_headers["location"] = rewritten or "/"
    content = upstream.content

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=response_headers,
    )


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str


class WriteRequest(BaseModel):
    text: str


class IngestRequest(BaseModel):
    texts: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/search")
async def search(
    request: SearchRequest,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """Search project memory.  Requires at least readonly role."""
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)

    try:
        results = await _lightrag.search(request.query)
        return JSONResponse(content={
            "status": "ok",
            "source": "project",
            "results": [r.model_dump(exclude_none=True) for r in results],
        })
    except Exception as exc:
        return _err(ERROR_DESTINATION_UNAVAILABLE, f"LightRAG unavailable: {exc}", 503)


@app.post("/write")
async def write(
    request: WriteRequest,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """Write to project memory.  Requires write role."""
    try:
        token_info = validate_token(_bearer(authorization), _token_store)
        require_write_role(token_info)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)

    try:
        await _lightrag.write(request.text)
        return JSONResponse(content={
            "status": "ok",
            "source": "project",
            "message": "Memory entry written successfully.",
        })
    except Exception as exc:
        return _err(ERROR_DESTINATION_UNAVAILABLE, f"LightRAG unavailable: {exc}", 503)


@app.get("/health")
async def health(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False)),
) -> Response:
    """Liveness / readiness probe."""
    if _check_admin_session(request.cookies.get(_ADMIN_SESSION_COOKIE)) or credentials is not None:
        try:
            await _require_admin(request, credentials)
            return await _proxy_lightrag(request, "/health")
        except HTTPException:
            pass
    lightrag_ok = await _lightrag.health()
    return JSONResponse(content={
        "status": "ok" if lightrag_ok else "error",
        "lightrag": "ok" if lightrag_ok else "error",
    })


@app.get("/", include_in_schema=False)
@app.get("/RAGConnect", include_in_schema=False)
@app.get("/RAGConnect/", include_in_schema=False)
async def root_redirect() -> JSONResponse:
    return JSONResponse(
        status_code=307,
        content={"status": "redirect", "location": "/ui/graph"},
        headers={"Location": "/ui/graph"},
    )


@app.post("/ingest")
async def ingest(
    request: IngestRequest,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    try:
        token_info = validate_token(_bearer(authorization), _token_store)
        require_write_role(token_info)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        payload = await _lightrag.ingest(request.texts)
        return JSONResponse(content={"status": "ok", "source": "project", "data": payload})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


async def _require_admin(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(_admin_basic),
) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    if _check_admin_session(request.cookies.get(_ADMIN_SESSION_COOKIE)):
        return

    # Brute-force protection with temporary lockout.
    blocked_until = _admin_blocks_until.get(client_ip)
    if blocked_until and blocked_until > now:
        retry_after = int(blocked_until - now) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed admin login attempts. Retry in {retry_after} seconds.",
            headers={"Retry-After": str(max(retry_after, 1))},
        )
    if blocked_until and blocked_until <= now:
        _admin_blocks_until.pop(client_ip, None)

    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin password is not configured.")
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required.",
            headers={"WWW-Authenticate": 'Basic realm="RAGConnect Admin"'},
        )
    valid_user = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    valid_pass = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (valid_user and valid_pass):
        failures = _admin_failures[client_ip]
        while failures and failures[0] <= now - BRUTE_FORCE_WINDOW_SECONDS:
            failures.popleft()
        failures.append(now)
        if len(failures) >= BRUTE_FORCE_MAX_ATTEMPTS:
            _admin_blocks_until[client_ip] = now + BRUTE_FORCE_BLOCK_SECONDS
            failures.clear()
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")

    # Successful login resets failure counter for this IP.
    _admin_failures.pop(client_ip, None)


@app.api_route("/auth-status", methods=["GET"], include_in_schema=False)
@app.api_route("/documents/paginated", methods=["POST"], include_in_schema=False)
@app.api_route("/graph/label/popular", methods=["GET"], include_in_schema=False)
@app.api_route("/graph/label/list", methods=["GET"], include_in_schema=False)
@app.api_route("/graphs", methods=["GET"], include_in_schema=False)
@app.api_route("/webui", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/webui/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/webui/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def admin_lightrag_proxy(
    request: Request,
    path: str = "",
    _: None = Depends(_require_admin),
) -> Response:
    del path
    return await _proxy_lightrag(request, request.url.path)


@app.get("/documents")
async def documents(
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        return JSONResponse(content={"status": "ok", "source": "project", "data": await _lightrag.documents()})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.get("/entities")
async def entities(
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        return JSONResponse(content={"status": "ok", "source": "project", "data": await _lightrag.entities()})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.get("/relations")
async def relations(
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        return JSONResponse(content={"status": "ok", "source": "project", "data": await _lightrag.relations()})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.get("/graph")
async def graph(
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    try:
        validate_token(_bearer(authorization), _token_store)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        return JSONResponse(content={"status": "ok", "source": "project", "data": await _lightrag.graph()})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.post("/rebuild")
async def rebuild(
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    try:
        token_info = validate_token(_bearer(authorization), _token_store)
        require_write_role(token_info)
    except AuthError as exc:
        return _err(exc.code, exc.message, exc.http_status)
    try:
        payload = await _lightrag.rebuild()
        return JSONResponse(content={"status": "ok", "source": "project", "data": payload})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


@app.get("/admin/graph")
async def admin_graph(_: None = Depends(_require_admin)) -> JSONResponse:
    try:
        return JSONResponse(content={"status": "ok", "source": "project", "data": await _lightrag.graph()})
    except Exception:
        return _err(ERROR_DESTINATION_UNAVAILABLE, "LightRAG unavailable.", 503)


# ---------------------------------------------------------------------------
# Admin token management API
# ---------------------------------------------------------------------------

class TokenCreateRequest(BaseModel):
    role: str          # "readonly" | "write"
    description: str = ""
    expires_days: int = 90


def _read_token_store() -> dict:
    if not TOKEN_STORE_PATH.exists():
        return {"tokens": []}
    with open(TOKEN_STORE_PATH) as fh:
        return yaml.safe_load(fh) or {"tokens": []}


def _write_token_store(data: dict) -> None:
    TOKEN_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_STORE_PATH, "w") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)


def _require_admin_or_nonce(request: Request, nonce: Optional[str] = Query(None)) -> None:
    """Allow access via page nonce (JS calls) OR standard Basic auth."""
    if nonce and _check_nonce(nonce):
        return
    # No valid nonce → fall back to Basic auth challenge
    raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="RAGConnect Admin"'},
                        detail="Admin authentication required.")


@app.get("/admin/tokens")
async def admin_tokens_list(
    request: Request,
    nonce: Optional[str] = Query(None),
    credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False)),
) -> JSONResponse:
    if not (nonce and _check_nonce(nonce)):
        if not credentials:
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="RAGConnect Admin"'})
        await _require_admin(request, credentials)
    data = _read_token_store()
    safe = []
    for t in data.get("tokens", []):
        safe.append({
            "token_id":    t.get("token_id", ""),
            "role":        t.get("role", ""),
            "enabled":     t.get("enabled", True),
            "description": t.get("description", ""),
            "expires_at":  t.get("expires_at", ""),
        })
    return JSONResponse(content={"status": "ok", "tokens": safe})


@app.post("/admin/tokens")
async def admin_tokens_create(
    req: TokenCreateRequest,
    request: Request,
    nonce: Optional[str] = Query(None),
    credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False)),
) -> JSONResponse:
    if not (nonce and _check_nonce(nonce)):
        if not credentials:
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="RAGConnect Admin"'})
        await _require_admin(request, credentials)
    if req.role not in ("readonly", "write"):
        return _err("invalid_role", "Role must be 'readonly' or 'write'.", 400)

    raw = secrets.token_hex(24)
    new_token = f"tok_{raw}"
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=req.expires_days)
    ).isoformat().replace("+00:00", "Z")
    token_id = f"tid_{secrets.token_hex(8)}"

    entry: dict = {
        "token_id":   token_id,
        "token_hash": _token_store.hash_token(new_token),
        "role":       req.role,
        "enabled":    True,
        "expires_at": expires_at,
    }
    if req.description:
        entry["description"] = req.description

    data = _read_token_store()
    data.setdefault("tokens", []).append(entry)
    _write_token_store(data)

    return JSONResponse(content={
        "status":     "ok",
        "token":      new_token,   # shown ONCE — not stored
        "token_id":   token_id,
        "role":       req.role,
        "expires_at": expires_at,
    })


@app.delete("/admin/tokens/{token_id}")
async def admin_tokens_revoke(
    token_id: str,
    request: Request,
    nonce: Optional[str] = Query(None),
    credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False)),
) -> JSONResponse:
    if not (nonce and _check_nonce(nonce)):
        if not credentials:
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="RAGConnect Admin"'})
        await _require_admin(request, credentials)
    data = _read_token_store()
    matched = False
    for t in data.get("tokens", []):
        if t.get("token_id") == token_id:
            t["enabled"] = False
            matched = True
    if not matched:
        return _err("not_found", f"Token '{token_id}' not found.", 404)
    _write_token_store(data)
    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Admin Web UI — /ui/graph and /ui/configs
# ---------------------------------------------------------------------------

_GRAPH_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAGConnect — Memory Graph</title>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js"
  onerror="this.onerror=null;this.src='https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/standalone/umd/vis-network.min.js'"></script>
<style>
  :root{--bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;--accent:#3b6ff5;--text:#e8eaf0;--muted:#6b7280;--node1:#3b6ff5;--node2:#7c3aed;--node3:#059669;--node4:#d97706;}
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;overflow:hidden}
  header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 1.5rem;height:52px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;gap:1rem}
  header .logo{font-weight:700;font-size:1rem;letter-spacing:-.3px}
  header .sub{color:var(--muted);font-size:.8125rem}
  .toolbar{display:flex;align-items:center;gap:.75rem}
  .search{padding:.35rem .75rem;border:1px solid var(--border);border-radius:7px;background:var(--bg);color:var(--text);font-size:.875rem;width:220px}
  .search:focus{outline:none;border-color:var(--accent)}
  .btn{padding:.35rem .875rem;border:none;border-radius:7px;font-size:.8125rem;font-weight:500;cursor:pointer;transition:.12s}
  .btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text)}
  .btn-ghost:hover{background:var(--surface)}
  #graph-container{flex:1;position:relative}
  #graph{width:100%;height:100%}
  #stats{position:absolute;bottom:1rem;left:1rem;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:.5rem .875rem;font-size:.78rem;color:var(--muted)}
  #detail{position:absolute;top:1rem;right:1rem;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:1rem 1.25rem;width:280px;display:none;max-height:60vh;overflow-y:auto}
  #detail h3{font-size:.9rem;margin-bottom:.625rem;word-break:break-word}
  #detail .prop{font-size:.78rem;margin-bottom:.3rem;display:flex;gap:.4rem}
  #detail .prop .k{color:var(--muted);flex-shrink:0}
  #detail .prop .v{word-break:break-word}
  #detail .close{position:absolute;top:.6rem;right:.75rem;background:none;border:none;color:var(--muted);cursor:pointer;font-size:1rem}
  #overlay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(15,17,23,.85);font-size:.9rem;color:#e8eaf0;z-index:10}
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center;gap:.75rem">
    <span class="logo">RAGConnect</span>
    <span class="sub">Memory Graph</span>
  </div>
  <div class="toolbar">
    <input id="search" class="search" placeholder="Filter nodes…" type="search">
    <button class="btn btn-ghost" onclick="loadGraph()">Refresh</button>
    <a href="/ui/configs" class="btn btn-ghost" style="text-decoration:none">Tokens</a>
  </div>
</header>

<div id="graph-container">
  <div id="graph"></div>
  <div id="stats"></div>
  <div id="detail">
    <button class="close" onclick="document.getElementById('detail').style.display='none'">✕</button>
    <h3 id="detail-title"></h3>
    <div id="detail-props"></div>
  </div>
  <div id="overlay">Loading graph…</div>
</div>

<script>
let network = null;
let allNodes = [], allEdges = [];

function showOverlay(msg) {
  const el = document.getElementById('overlay');
  el.textContent = msg; el.style.display = 'flex';
}
function hideOverlay() {
  document.getElementById('overlay').style.display = 'none';
}

function extractGraph(raw) {
  // Handle multiple possible LightRAG graph response formats
  const data = raw.data || raw;
  let nodes = [], edges = [];

  if (data.nodes && data.edges) {
    nodes = data.nodes;
    edges = data.edges;
  } else if (data.graph) {
    nodes = data.graph.nodes || [];
    edges = data.graph.edges || data.graph.links || [];
  } else if (Array.isArray(data)) {
    // Some versions return array of entities
    nodes = data.map((e, i) => ({ id: i, label: e.name || e.id || String(i), ...e }));
  }
  return { nodes, edges };
}

function colorByType(type) {
  if (!type) return '#3b6ff5';
  const h = [...type].reduce((a, c) => a + c.charCodeAt(0), 0);
  const colors = ['#3b6ff5','#7c3aed','#059669','#d97706','#e11d48','#0891b2','#84cc16'];
  return colors[h % colors.length];
}

function loadGraph() {
  showOverlay('Loading…');
  if (typeof vis === 'undefined') {
    showOverlay('❌ vis-network failed to load. Check your internet connection and reload.');
    return;
  }
  try {
    const raw = __GRAPH_DATA__;
    const { nodes, edges } = extractGraph(raw);

    if (!nodes.length) { showOverlay('Graph is empty — write something to memory first.'); return; }

    allNodes = nodes.map((n, i) => ({
      id:    n.id ?? n.name ?? i,
      label: (n.name || n.label || n.id || String(i)).slice(0, 40),
      title: buildTooltip(n),
      color: { background: colorByType(n.type || n.entity_type || (n.labels||[])[0]),
               border: 'transparent',
               highlight: { background: '#fff', border: '#3b6ff5' } },
      font:  { color: '#e8eaf0', size: 13 },
      _raw:  n,
    }));

    allEdges = edges.map((e, i) => ({
      id:     i,
      from:   e.source ?? e.from ?? e.src,
      to:     e.target ?? e.to ?? e.dst,
      label:  (e.relation || e.label || e.type || '').slice(0, 30),
      color:  { color: '#3a3d50', highlight: '#3b6ff5' },
      font:   { color: '#6b7280', size: 11, align: 'middle' },
      arrows: { to: { enabled: true, scaleFactor: .6 } },
      _raw:   e,
    }));

    renderGraph(allNodes, allEdges);
    document.getElementById('stats').textContent =
      `${allNodes.length} nodes · ${allEdges.length} edges`;
    hideOverlay();
  } catch(e) {
    showOverlay('❌ Error: ' + e.message);
    console.error('[RAGConnect graph]', e);
  }
}

function buildTooltip(n) {
  const skip = new Set(['id','name','label','color','font','_raw']);
  return Object.entries(n)
    .filter(([k]) => !skip.has(k))
    .map(([k,v]) => `<b>${k}</b>: ${v}`)
    .join('<br>') || n.name || '';
}

function renderGraph(nodes, edges) {
  const container = document.getElementById('graph');
  const dsNodes = new vis.DataSet(nodes);
  const dsEdges = new vis.DataSet(edges);

  const options = {
    physics: { stabilization: { iterations: 200 },
               barnesHut: { gravitationalConstant: -4000, centralGravity: 0.2, springLength: 140 } },
    interaction: { hover: true, tooltipDelay: 200, navigationButtons: true },
    nodes: { shape: 'dot', size: 14, borderWidth: 2 },
    edges: { smooth: { type: 'continuous', roundness: .3 }, width: 1.5 },
  };

  if (network) network.destroy();
  network = new vis.Network(container, { nodes: dsNodes, edges: dsEdges }, options);

  network.on('click', params => {
    if (!params.nodes.length) { document.getElementById('detail').style.display='none'; return; }
    const node = allNodes.find(n => n.id === params.nodes[0]);
    if (!node) return;
    const raw = node._raw;
    document.getElementById('detail-title').textContent = node.label;
    const props = document.getElementById('detail-props');
    props.innerHTML = Object.entries(raw)
      .filter(([k]) => k !== 'id')
      .map(([k,v]) => `<div class="prop"><span class="k">${k}</span><span class="v">${v ?? '—'}</span></div>`)
      .join('');
    document.getElementById('detail').style.display = 'block';
  });
}

// Filter nodes by search
document.getElementById('search').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  if (!q) { renderGraph(allNodes, allEdges); return; }
  const filtered = allNodes.filter(n => n.label.toLowerCase().includes(q));
  const ids = new Set(filtered.map(n => n.id));
  const filteredEdges = allEdges.filter(e => ids.has(e.from) && ids.has(e.to));
  renderGraph(filtered, filteredEdges);
});

// Wait for vis-network to fully load before initializing the graph
window.addEventListener('load', loadGraph);
</script>
</body>
</html>"""


_CONFIGS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAGConnect — Tokens</title>
<style>
  :root{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2a2d3a;--accent:#3b6ff5;--accent2:#2d58d6;--danger:#e0423a;--success:#1a8a55;--muted:#6b7280;--text:#e8eaf0;--code-bg:#13151f;}
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);font-size:15px;line-height:1.5;min-height:100vh}
  header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 2rem;height:52px;display:flex;align-items:center;justify-content:space-between}
  header .logo{font-weight:700;font-size:1rem;letter-spacing:-.3px}
  header .sub{color:var(--muted);font-size:.8125rem}
  main{max-width:960px;margin:2rem auto;padding:0 1.25rem}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;margin-bottom:1.5rem;overflow:hidden}
  .card-head{padding:.875rem 1.5rem;border-bottom:1px solid var(--border);font-weight:600;font-size:.9375rem;display:flex;justify-content:space-between;align-items:center}
  .card-body{padding:1.5rem}
  table{width:100%;border-collapse:collapse}
  th,td{padding:.5625rem .75rem;text-align:left}
  th{font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);border-bottom:1px solid var(--border)}
  tr:not(:last-child) td{border-bottom:1px solid var(--border)}
  code{font-family:"SF Mono",ui-monospace,monospace;font-size:.78rem;background:var(--code-bg);padding:.1em .45em;border-radius:4px;color:#a5b4fc}
  .badge{display:inline-block;padding:.175em .55em;border-radius:5px;font-size:.72rem;font-weight:600}
  .badge-write{background:rgba(26,138,85,.2);color:#34d399;border:1px solid rgba(52,211,153,.25)}
  .badge-readonly{background:rgba(99,102,241,.2);color:#a5b4fc;border:1px solid rgba(165,180,252,.25)}
  .badge-on{background:rgba(26,138,85,.2);color:#34d399;border:1px solid rgba(52,211,153,.25)}
  .badge-off{background:rgba(107,114,128,.15);color:var(--muted);border:1px solid var(--border)}
  .badge-expired{background:rgba(224,66,58,.2);color:#f87171;border:1px solid rgba(248,113,113,.25)}
  .btn{padding:.4rem .875rem;border:none;border-radius:7px;font-size:.875rem;font-weight:500;cursor:pointer;transition:.12s;white-space:nowrap}
  .btn-primary{background:var(--accent);color:#fff}
  .btn-primary:hover{background:var(--accent2)}
  .btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text)}
  .btn-ghost:hover{background:var(--surface2)}
  .btn-danger-ghost{background:transparent;border:1px solid rgba(224,66,58,.4);color:#f87171}
  .btn-danger-ghost:hover{background:var(--danger);color:#fff;border-color:var(--danger)}
  .btn-sm{padding:.25rem .6rem;font-size:.8125rem}
  .form-row{display:flex;gap:.75rem;flex-wrap:wrap;align-items:flex-end}
  .fg{display:flex;flex-direction:column;gap:.25rem;flex:1;min-width:120px}
  .fg.lg{min-width:200px}
  .fg label{font-size:.78rem;font-weight:500;color:var(--muted)}
  .fg input,.fg select{padding:.46rem .75rem;border:1px solid var(--border);border-radius:7px;font-size:.9375rem;width:100%;background:var(--surface2);color:var(--text);transition:border-color .15s}
  .fg input:focus,.fg select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(59,111,245,.2)}
  .fg select option{background:var(--surface2)}
  .alert{padding:.7rem 1rem;border-radius:8px;margin-bottom:1.25rem;font-size:.9rem;display:none;align-items:center;gap:.6rem}
  .alert.show{display:flex}
  .alert-ok{background:rgba(26,138,85,.15);color:#34d399;border:1px solid rgba(52,211,153,.3)}
  .alert-err{background:rgba(224,66,58,.15);color:#f87171;border:1px solid rgba(248,113,113,.3)}
  .empty-row td{color:var(--muted);text-align:center;padding:2rem}
  .hint{font-size:.8125rem;color:var(--muted);margin-top:.5rem}
  /* Modal */
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.65);display:none;align-items:center;justify-content:center;z-index:100}
  .modal-bg.show{display:flex}
  .modal{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:2rem;width:520px;max-width:95vw;box-shadow:0 24px 64px rgba(0,0,0,.5)}
  .modal h2{font-size:1.05rem;margin-bottom:.375rem}
  .modal p{font-size:.875rem;color:var(--muted);margin-bottom:1.25rem}
  .token-box{background:var(--code-bg);border:1px solid var(--border);border-radius:8px;padding:.875rem 1rem;font-family:"SF Mono",monospace;font-size:.82rem;word-break:break-all;margin-bottom:1.25rem;cursor:pointer;color:#a5b4fc;transition:border-color .15s}
  .token-box:hover{border-color:var(--accent)}
  .modal-meta{font-size:.8125rem;color:var(--muted);margin-bottom:1.25rem}
  .modal-meta strong{color:var(--text)}
  .modal-actions{display:flex;justify-content:flex-end;gap:.5rem}
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center;gap:.75rem">
    <span class="logo">RAGConnect</span>
    <span class="sub">Token Management</span>
  </div>
  <a href="/ui/graph" class="btn btn-ghost" style="text-decoration:none;font-size:.875rem">Graph</a>
</header>

<main>

<div id="alert" class="alert" role="alert"></div>

<div class="card">
  <div class="card-head">
    <span>Access Tokens</span>
    <span id="tok-count" style="font-size:.8125rem;font-weight:400;color:var(--muted)"></span>
  </div>
  <div style="padding:0">
    <table>
      <thead><tr>
        <th>ID</th><th>Role</th><th>Status</th><th>Expires</th><th>Description</th><th style="width:90px"></th>
      </tr></thead>
      <tbody id="tok-tbody">
        <tr class="empty-row"><td colspan="6">Loading…</td></tr>
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <div class="card-head">Create Token</div>
  <div class="card-body">
    <form id="create-form">
      <div class="form-row">
        <div class="fg" style="max-width:150px">
          <label>Role</label>
          <select name="role">
            <option value="write">write</option>
            <option value="readonly">readonly</option>
          </select>
        </div>
        <div class="fg lg">
          <label>Description</label>
          <input name="description" placeholder="e.g. Claude Code on laptop" autocomplete="off">
        </div>
        <div class="fg" style="max-width:120px">
          <label>Expires (days)</label>
          <input name="expires_days" type="number" value="90" min="1" max="3650">
        </div>
        <button type="submit" class="btn btn-primary">Create</button>
      </div>
    </form>
    <p class="hint">The raw token is shown <strong style="color:var(--text)">once</strong> after creation and is never stored in plain text.</p>
  </div>
</div>

</main>

<div id="modal-bg" class="modal-bg">
  <div class="modal">
    <h2>Token created</h2>
    <p>Copy it now — it will not be shown again.</p>
    <div id="modal-token" class="token-box" onclick="copyToken()" title="Click to copy"></div>
    <div class="modal-meta">
      Role: <strong id="modal-role"></strong> &nbsp;·&nbsp; Expires: <strong id="modal-expires"></strong>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="copyToken()">Copy</button>
      <button class="btn btn-primary" onclick="closeModal()">Done</button>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

function flash(msg, type) {
  const el = $('alert');
  el.textContent = msg;
  el.className = `alert show alert-${type === 'ok' ? 'ok' : 'err'}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 5000);
}

function statusBadge(enabled, expires) {
  if (!enabled) return '<span class="badge badge-off">revoked</span>';
  if (expires && new Date(expires) < new Date()) return '<span class="badge badge-expired">expired</span>';
  return '<span class="badge badge-on">active</span>';
}

function fmtExpires(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, {year:'numeric',month:'short',day:'numeric'});
}

const NONCE = '__NONCE__';

// Tokens are server-side rendered; JS only handles mutations + re-render
let _tokens = __TOKENS_JSON__;

function loadTokens() {
  const tokens = _tokens;
  $('tok-count').textContent = tokens.length ? `${tokens.length} total` : '';
  const tbody = $('tok-tbody');
  if (!tokens.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No tokens yet.</td></tr>';
    return;
  }
  tbody.innerHTML = tokens.map(t => {
    const roleBadge = t.role === 'write'
      ? '<span class="badge badge-write">write</span>'
      : '<span class="badge badge-readonly">readonly</span>';
    const revBtn = t.enabled
      ? `<button class="btn btn-sm btn-danger-ghost" onclick="revoke('${t.token_id}')">Revoke</button>`
      : '<span style="color:var(--muted);font-size:.8125rem">revoked</span>';
    return `<tr>
      <td><code>${t.token_id || '—'}</code></td>
      <td>${roleBadge}</td>
      <td>${statusBadge(t.enabled, t.expires_at)}</td>
      <td style="font-size:.8125rem;color:var(--muted)">${fmtExpires(t.expires_at)}</td>
      <td style="font-size:.8125rem;color:var(--muted)">${t.description || '—'}</td>
      <td>${revBtn}</td>
    </tr>`;
  }).join('');
}

$('create-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await fetch(`/admin/tokens?nonce=${NONCE}`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      role: fd.get('role'),
      description: fd.get('description'),
      expires_days: parseInt(fd.get('expires_days') || '90', 10),
    }),
  });
  const d = await r.json();
  if (d.status === 'ok') {
    e.target.reset();
    e.target.querySelector('[name=expires_days]').value = '90';
    _tokens = [..._tokens, {
      token_id: d.token_id, role: d.role, enabled: true,
      description: fd.get('description'), expires_at: d.expires_at,
    }];
    loadTokens();
    showModal(d.token, d.role, d.expires_at);
  } else {
    flash(d.error?.message || 'Failed to create token.', 'err');
  }
});

async function revoke(tokenId) {
  if (!confirm('Revoke this token? This cannot be undone.')) return;
  const r = await fetch(`/admin/tokens/${encodeURIComponent(tokenId)}?nonce=${NONCE}`, {
    method: 'DELETE',
  });
  const d = await r.json();
  if (d.status === 'ok') {
    _tokens = _tokens.map(t => t.token_id === tokenId ? {...t, enabled: false} : t);
    loadTokens();
    flash('Token revoked.', 'ok');
  } else flash(d.error?.message || 'Failed to revoke.', 'err');
}

function showModal(token, role, expires) {
  $('modal-token').textContent = token;
  $('modal-role').textContent = role;
  $('modal-expires').textContent = fmtExpires(expires);
  $('modal-bg').classList.add('show');
}
function closeModal() { $('modal-bg').classList.remove('show'); }
async function copyToken() {
  try { await navigator.clipboard.writeText($('modal-token').textContent); flash('Copied to clipboard.', 'ok'); } catch {}
}
$('modal-bg').addEventListener('click', e => { if (e.target === $('modal-bg')) closeModal(); });

loadTokens();
</script>
</body>
</html>"""


_LIGHTRAG_GRAPH_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAGConnect — Knowledge Graph</title>
<style>
  :root{--bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;--text:#e8eaf0;--muted:#6b7280;}
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;overflow:hidden}
  header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 1.5rem;height:52px;display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-shrink:0}
  .brand{display:flex;align-items:center;gap:.75rem}
  .logo{font-weight:700;font-size:1rem;letter-spacing:-.3px}
  .sub{color:var(--muted);font-size:.8125rem}
  .actions{display:flex;align-items:center;gap:.75rem}
  .btn{padding:.38rem .875rem;border:1px solid var(--border);border-radius:7px;font-size:.84rem;font-weight:500;background:transparent;color:var(--text);text-decoration:none;cursor:pointer}
  .btn:hover{background:#202433}
  #frame-wrap{position:relative;flex:1;min-height:0}
  #graph-frame{width:100%;height:100%;border:0;background:#0b0d14}
  #overlay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(15,17,23,.92);color:var(--text);font-size:.95rem;z-index:2}
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="logo">RAGConnect</span>
    <span class="sub">LightRAG Graph</span>
  </div>
  <div class="actions">
    <button class="btn" onclick="reloadFrame()">Refresh</button>
    <a class="btn" href="/ui/configs">Tokens</a>
  </div>
</header>
<div id="frame-wrap">
  <div id="overlay">Loading LightRAG UI…</div>
  <iframe id="graph-frame" src="/webui/#/" title="LightRAG"></iframe>
</div>
<script>
const frame = document.getElementById('graph-frame');
const overlay = document.getElementById('overlay');

function hideOverlay() {
  overlay.style.display = 'none';
}

function showOverlay(text) {
  overlay.textContent = text;
  overlay.style.display = 'flex';
}

function openGraphTab() {
  try {
    const doc = frame.contentDocument || frame.contentWindow.document;
    if (!doc) return false;
    const tabs = Array.from(doc.querySelectorAll('[role="tab"]'));
    const graphTab = tabs.find((tab) => /knowledge graph/i.test((tab.textContent || '').trim()));
    if (!graphTab) return false;
    graphTab.click();
    return true;
  } catch (error) {
    return false;
  }
}

function syncGraphView() {
  let attempts = 0;
  const timer = setInterval(() => {
    attempts += 1;
    const ready = openGraphTab();
    if (ready) {
      hideOverlay();
      clearInterval(timer);
      return;
    }
    if (attempts >= 40) {
      showOverlay('LightRAG UI loaded, but the graph tab did not auto-open. Use the native "Knowledge Graph" tab inside the page.');
      clearInterval(timer);
    }
  }, 300);
}

function reloadFrame() {
  showOverlay('Reloading LightRAG UI…');
  frame.contentWindow.location.reload();
}

frame.addEventListener('load', () => {
  showOverlay('Opening graph view…');
  syncGraphView();
});
</script>
</body>
</html>"""


_LIGHTRAG_GRAPH_BOOTSTRAP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAGConnect - Opening Graph</title>
<style>
  :root{--bg:#0f1117;--surface:#181c26;--border:#2a3140;--text:#e8ecf3;--muted:#8c95a6}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at top,#1d2431 0,#0f1117 55%);color:var(--text);font:16px/1.45 "Segoe UI",system-ui,sans-serif}
  .card{width:min(30rem,calc(100vw - 2rem));padding:1.25rem 1.35rem;border:1px solid var(--border);border-radius:16px;background:rgba(24,28,38,.94);box-shadow:0 24px 80px rgba(0,0,0,.35)}
  h1{margin:0 0 .45rem;font-size:1rem}
  p{margin:0;color:var(--muted)}
  .actions{margin-top:1rem;display:flex;gap:.75rem}
  a{color:var(--text)}
</style>
</head>
<body>
  <div class="card">
    <h1>Opening LightRAG Knowledge Graph...</h1>
    <p>We are switching the native LightRAG UI to the graph tab and redirecting you now.</p>
    <div class="actions">
      <a href="/webui/#/">Open LightRAG</a>
      <a href="/ui/configs">Tokens</a>
    </div>
  </div>
<script>
(() => {
  const key = 'settings-storage';
  try {
    const raw = window.localStorage.getItem(key);
    const payload = raw ? JSON.parse(raw) : {};
    const state = payload && typeof payload.state === 'object' && payload.state !== null
      ? payload.state
      : {};
    state.currentTab = 'knowledge-graph';
    payload.state = state;
    window.localStorage.setItem(key, JSON.stringify(payload));
  } catch (error) {
    console.warn('Failed to persist LightRAG graph tab preference.', error);
  }
  window.location.replace('/webui/#/');
})();
</script>
</body>
</html>"""


@app.get("/ui/graph", response_class=HTMLResponse)
async def native_ui_graph(_: None = Depends(_require_admin)) -> HTMLResponse:
    return HTMLResponse(_LIGHTRAG_GRAPH_BOOTSTRAP_HTML)


@app.get("/ui/graph", response_class=HTMLResponse)
async def ui_graph(_: None = Depends(_require_admin)) -> str:
    # Embed graph data server-side — no JS fetch needed (avoids Basic auth in fetch)
    return _LIGHTRAG_GRAPH_BOOTSTRAP_HTML


@app.get("/ui/configs", response_class=HTMLResponse)
async def ui_configs(_: None = Depends(_require_admin)) -> str:
    data = _read_token_store()
    tokens_json = json.dumps(data.get("tokens", []))
    nonce = _new_nonce()
    return _CONFIGS_HTML.replace("__TOKENS_JSON__", tokens_json).replace("__NONCE__", nonce)


@app.get("/ui/graph", include_in_schema=False)
async def native_ui_graph_override(_: None = Depends(_require_admin)) -> HTMLResponse:
    return HTMLResponse(_LIGHTRAG_GRAPH_BOOTSTRAP_HTML)
