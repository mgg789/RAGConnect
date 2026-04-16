"""Server Gateway — FastAPI application.

Configuration via environment variables:

    LIGHTRAG_URL       URL of the local LightRAG instance  (default: http://127.0.0.1:9621)
    TOKEN_STORE_PATH   Path to the token YAML file          (default: server_tokens.yaml)

Start via CLI:

    ragconnect-server start --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse
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
_admin_basic = HTTPBasic()

_token_store = TokenStore(TOKEN_STORE_PATH)
_lightrag = LightRAGClient(LIGHTRAG_URL)
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

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
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
async def health() -> JSONResponse:
    """Liveness / readiness probe."""
    lightrag_ok = await _lightrag.health()
    return JSONResponse(content={
        "status": "ok" if lightrag_ok else "error",
        "lightrag": "ok" if lightrag_ok else "error",
    })


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
    credentials: HTTPBasicCredentials = Depends(_admin_basic),
) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

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


@app.get("/admin/tokens")
async def admin_tokens_list(_: None = Depends(_require_admin)) -> JSONResponse:
    data = _read_token_store()
    safe = []
    for t in data.get("tokens", []):
        safe.append({
            "token_id":   t.get("token_id", ""),
            "role":       t.get("role", ""),
            "enabled":    t.get("enabled", True),
            "description": t.get("description", ""),
            "expires_at": t.get("expires_at", ""),
        })
    return JSONResponse(content={"status": "ok", "tokens": safe})


@app.post("/admin/tokens")
async def admin_tokens_create(
    req: TokenCreateRequest,
    _: None = Depends(_require_admin),
) -> JSONResponse:
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
    _: None = Depends(_require_admin),
) -> JSONResponse:
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
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/standalone/umd/vis-network.min.js"></script>
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
  #overlay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(15,17,23,.85);font-size:.9rem;color:var(--muted)}
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

async function loadGraph() {
  showOverlay('Loading graph…');
  try {
    const r = await fetch('/admin/graph', { credentials: 'include' });
    if (!r.ok) { showOverlay('Failed to load: ' + r.status); return; }
    const raw = await r.json();
    const { nodes, edges } = extractGraph(raw);

    if (!nodes.length) { showOverlay('Graph is empty — no entities in memory yet.'); return; }

    allNodes = nodes.map((n, i) => ({
      id:    n.id ?? n.name ?? i,
      label: n.name || n.label || n.id || String(i),
      title: buildTooltip(n),
      color: { background: colorByType(n.type || n.entity_type), border: 'transparent',
               highlight: { background: '#fff', border: '#3b6ff5' } },
      font:  { color: '#e8eaf0', size: 13 },
      _raw:  n,
    }));

    allEdges = edges.map((e, i) => ({
      id:     i,
      from:   e.source ?? e.from ?? e.src,
      to:     e.target ?? e.to ?? e.dst,
      label:  e.relation || e.label || e.type || '',
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
    showOverlay('Error: ' + e.message);
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

loadGraph();
</script>
</body>
</html>"""


_CONFIGS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAGConnect — Token Management</title>
<style>
  :root{--bg:#f5f6f8;--surface:#fff;--border:#e0e3e8;--accent:#3b6ff5;--accent2:#2d58d6;--danger:#e0423a;--success:#1a8a55;--muted:#6b7280;--text:#1a1d23;--code-bg:#f0f2f5;}
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);font-size:15px;line-height:1.5}
  header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 2rem;height:56px;display:flex;align-items:center;justify-content:space-between}
  header .logo{font-weight:700;font-size:1.1rem;letter-spacing:-.3px}
  header .sub{color:var(--muted);font-size:.85rem}
  main{max-width:960px;margin:2rem auto;padding:0 1.25rem}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;margin-bottom:1.5rem;overflow:hidden}
  .card-head{padding:.875rem 1.5rem;border-bottom:1px solid var(--border);font-weight:600;font-size:.9375rem;display:flex;justify-content:space-between;align-items:center}
  .card-body{padding:1.5rem}
  table{width:100%;border-collapse:collapse}
  th,td{padding:.5625rem .75rem;text-align:left}
  th{font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);border-bottom:1px solid var(--border)}
  tr:not(:last-child) td{border-bottom:1px solid var(--border)}
  code{font-family:"SF Mono",ui-monospace,monospace;font-size:.8rem;background:var(--code-bg);padding:.1em .45em;border-radius:4px}
  .badge{display:inline-block;padding:.175em .55em;border-radius:5px;font-size:.72rem;font-weight:600}
  .badge-write{background:#d1fadf;color:var(--success)}
  .badge-readonly{background:#e0e7ff;color:#3730a3}
  .badge-on{background:#d1fadf;color:var(--success)}
  .badge-off{background:#f0f2f5;color:var(--muted)}
  .badge-expired{background:#fee2e2;color:#7f1d1d}
  .btn{padding:.4rem .875rem;border:none;border-radius:7px;font-size:.875rem;font-weight:500;cursor:pointer;transition:.12s;white-space:nowrap}
  .btn-primary{background:var(--accent);color:#fff}
  .btn-primary:hover{background:var(--accent2)}
  .btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text)}
  .btn-ghost:hover{background:var(--bg)}
  .btn-danger-ghost{background:transparent;border:1px solid #fca5a5;color:var(--danger)}
  .btn-danger-ghost:hover{background:var(--danger);color:#fff;border-color:var(--danger)}
  .btn-sm{padding:.25rem .6rem;font-size:.8125rem}
  .form-row{display:flex;gap:.75rem;flex-wrap:wrap;align-items:flex-end}
  .fg{display:flex;flex-direction:column;gap:.25rem;flex:1;min-width:120px}
  .fg.lg{min-width:200px}
  .fg label{font-size:.78rem;font-weight:500;color:var(--muted)}
  .fg input,.fg select{padding:.46rem .75rem;border:1px solid var(--border);border-radius:7px;font-size:.9375rem;width:100%;background:var(--surface);color:var(--text);transition:border-color .15s}
  .fg input:focus,.fg select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(59,111,245,.15)}
  .alert{padding:.7rem 1rem;border-radius:8px;margin-bottom:1.25rem;font-size:.9rem;display:none;align-items:center;gap:.6rem}
  .alert.show{display:flex}
  .alert-ok{background:#d1fadf;color:#065f35;border:1px solid #a7f3c0}
  .alert-err{background:#fee2e2;color:#7f1d1d;border:1px solid #fca5a5}
  .empty-row td{color:var(--muted);text-align:center;padding:2rem}
  /* Modal */
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:100}
  .modal-bg.show{display:flex}
  .modal{background:var(--surface);border-radius:12px;padding:2rem;width:520px;max-width:95vw;box-shadow:0 20px 60px rgba(0,0,0,.2)}
  .modal h2{font-size:1.1rem;margin-bottom:.375rem}
  .modal p{font-size:.875rem;color:var(--muted);margin-bottom:1.25rem}
  .token-box{background:var(--code-bg);border:1px solid var(--border);border-radius:8px;padding:.875rem 1rem;font-family:"SF Mono",monospace;font-size:.85rem;word-break:break-all;margin-bottom:1.25rem;cursor:pointer;position:relative}
  .token-box:hover::after{content:"Copied!";position:absolute;right:.75rem;top:50%;transform:translateY(-50%);font-size:.75rem;color:var(--success)}
  .modal-actions{display:flex;justify-content:flex-end;gap:.5rem}
  .hint{font-size:.8125rem;color:var(--muted);margin-top:.375rem}
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center;gap:.75rem">
    <span class="logo">RAGConnect</span>
    <span class="sub">Token Management</span>
  </div>
  <a href="/ui/graph" class="btn btn-ghost" style="text-decoration:none;font-size:.875rem">View Graph</a>
</header>

<main>

<div id="alert" class="alert" role="alert"></div>

<!-- ── Token list ── -->
<div class="card">
  <div class="card-head">
    <span>Access Tokens</span>
    <span id="tok-count" style="font-size:.8125rem;font-weight:400;color:var(--muted)"></span>
  </div>
  <div style="padding:0">
    <table>
      <thead><tr>
        <th>ID</th><th>Role</th><th>Status</th><th>Expires</th><th>Description</th><th style="width:100px"></th>
      </tr></thead>
      <tbody id="tok-tbody">
        <tr class="empty-row"><td colspan="6">Loading…</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- ── Create token ── -->
<div class="card">
  <div class="card-head">Create Token</div>
  <div class="card-body">
    <form id="create-form">
      <div class="form-row">
        <div class="fg" style="max-width:160px">
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
    <p class="hint">The raw token is shown <strong>once</strong> after creation and is never stored.</p>
  </div>
</div>

</main>

<!-- ── New-token modal ── -->
<div id="modal-bg" class="modal-bg">
  <div class="modal">
    <h2>Token created</h2>
    <p>Copy it now — it will not be shown again.</p>
    <div id="modal-token" class="token-box" onclick="copyToken()"></div>
    <div style="font-size:.8125rem;color:var(--muted);margin-bottom:1.25rem">
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
  if (expires) {
    const dt = new Date(expires);
    if (dt < new Date()) return '<span class="badge badge-expired">expired</span>';
  }
  return '<span class="badge badge-on">active</span>';
}

function fmtExpires(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, {year:'numeric',month:'short',day:'numeric'});
}

async function loadTokens() {
  try {
    const r = await fetch('/admin/tokens', { credentials: 'include' });
    if (!r.ok) { flash('Failed to load tokens: ' + r.status, 'err'); return; }
    const d = await r.json();
    const tokens = d.tokens || [];
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
        : '<span style="color:var(--muted);font-size:.8125rem">—</span>';
      return `<tr>
        <td><code style="font-size:.75rem">${t.token_id || '—'}</code></td>
        <td>${roleBadge}</td>
        <td>${statusBadge(t.enabled, t.expires_at)}</td>
        <td style="font-size:.8125rem;color:var(--muted)">${fmtExpires(t.expires_at)}</td>
        <td style="font-size:.8125rem">${t.description || '<span style="color:var(--muted)">—</span>'}</td>
        <td>${revBtn}</td>
      </tr>`;
    }).join('');
  } catch(e) { flash('Error: ' + e.message, 'err'); }
}

$('create-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await fetch('/admin/tokens', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
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
    loadTokens();
    showModal(d.token, d.role, d.expires_at);
  } else {
    flash(d.error?.message || 'Failed to create token.', 'err');
  }
});

async function revoke(tokenId) {
  if (!confirm('Revoke this token? This cannot be undone.')) return;
  const r = await fetch(`/admin/tokens/${encodeURIComponent(tokenId)}`, {
    method: 'DELETE', credentials: 'include',
  });
  const d = await r.json();
  if (d.status === 'ok') { loadTokens(); flash('Token revoked.', 'ok'); }
  else flash(d.error?.message || 'Failed to revoke.', 'err');
}

function showModal(token, role, expires) {
  $('modal-token').textContent = token;
  $('modal-role').textContent = role;
  $('modal-expires').textContent = fmtExpires(expires);
  $('modal-bg').classList.add('show');
}

function closeModal() {
  $('modal-bg').classList.remove('show');
}

async function copyToken() {
  const text = $('modal-token').textContent;
  try { await navigator.clipboard.writeText(text); } catch { }
}

$('modal-bg').addEventListener('click', e => {
  if (e.target === $('modal-bg')) closeModal();
});

loadTokens();
</script>
</body>
</html>"""


@app.get("/ui/graph", response_class=HTMLResponse)
async def ui_graph(_: None = Depends(_require_admin)) -> str:
    return _GRAPH_HTML


@app.get("/ui/configs", response_class=HTMLResponse)
async def ui_configs(_: None = Depends(_require_admin)) -> str:
    return _CONFIGS_HTML
