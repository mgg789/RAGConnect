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
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import JSONResponse
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


# ---------------------------------------------------------------------------
# Exception handler — return our own JSON shape instead of FastAPI default
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    return _err("internal_error", "Internal server error.", 500)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
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


async def _require_admin(credentials: HTTPBasicCredentials = Depends(_admin_basic)) -> None:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin password is not configured.")
    valid_user = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    valid_pass = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (valid_user and valid_pass):
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")


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
