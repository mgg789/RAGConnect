"""
RAGConnect Embedding Proxy
==========================
A lightweight routing proxy that sits between LightRAG and the upstream APIs.

Routing logic:
  POST /v1/embeddings  →  local Jina (if LOCAL_EMBEDDING_MODE=true)
                       OR  EMBEDDING_API_BASE  (if set and differs from LLM)
  all other /v1/*      →  OPENAI_API_BASE  (LLM API, passed through as-is)

When to use:
  Set OPENAI_API_BASE=http://localhost:9622/v1 in LightRAG's environment
  so that all its API calls pass through this proxy.

Environment variables:
  OPENAI_API_KEY        LLM API key
  OPENAI_API_BASE       LLM API base URL (default: https://api.openai.com/v1)
  EMBEDDING_API_KEY     Embedding API key (falls back to OPENAI_API_KEY)
  EMBEDDING_API_BASE    Embedding API base URL (falls back to OPENAI_API_BASE)
  LOCAL_EMBEDDING_MODE  "true" to serve embeddings locally with Jina
  LOCAL_EMBEDDING_MODEL HuggingFace model id (default: jinaai/jina-embeddings-v2-small-en)
  LOCAL_EMBEDDING_DIM   Embedding dimension for local model (default: 512)
  PROXY_HOST            Bind host (default: 0.0.0.0)
  PROXY_PORT            Bind port (default: 9622)

Usage (standalone):
  python -m local_embeddings.proxy
"""

import asyncio
import logging
import os

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

LLM_API_KEY   = os.getenv("OPENAI_API_KEY",   "")
LLM_API_BASE  = os.getenv("OPENAI_API_BASE",  "https://api.openai.com/v1").rstrip("/")

EMBED_API_KEY  = os.getenv("EMBEDDING_API_KEY")  or LLM_API_KEY
EMBED_API_BASE = (os.getenv("EMBEDDING_API_BASE") or LLM_API_BASE).rstrip("/")

LOCAL_EMBEDDING_MODE  = os.getenv("LOCAL_EMBEDDING_MODE",  "false").lower() == "true"
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "jinaai/jina-embeddings-v2-small-en")
LOCAL_EMBEDDING_DIM   = int(os.getenv("LOCAL_EMBEDDING_DIM", "512"))

PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "9622"))

# ── Jina model (loaded lazily, once) ─────────────────────────────────────────

_jina_model = None


def _load_jina_model():
    global _jina_model
    if _jina_model is None:
        from sentence_transformers import SentenceTransformer  # type: ignore
        logger.info("Loading Jina model: %s", LOCAL_EMBEDDING_MODEL)
        _jina_model = SentenceTransformer(LOCAL_EMBEDDING_MODEL, trust_remote_code=True)
        logger.info("Jina model ready (dim=%d).", LOCAL_EMBEDDING_DIM)
    return _jina_model


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="RAGConnect Embedding Proxy", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def _preload():
    if LOCAL_EMBEDDING_MODE:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _load_jina_model)
        logger.info("Local embedding mode active.")
    else:
        logger.info(
            "API embedding mode — LLM: %s | Embed: %s",
            LLM_API_BASE,
            EMBED_API_BASE,
        )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    texts = body.get("input", [])
    if isinstance(texts, str):
        texts = [texts]

    if LOCAL_EMBEDDING_MODE:
        model = _load_jina_model()
        loop = asyncio.get_event_loop()
        vecs = await loop.run_in_executor(
            None,
            lambda: model.encode(texts, normalize_embeddings=True),
        )
        return {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": v.tolist()}
                for i, v in enumerate(vecs)
            ],
            "model": LOCAL_EMBEDDING_MODEL,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    # Forward to embedding API
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{EMBED_API_BASE}/embeddings",
            json=body,
            headers={"Authorization": f"Bearer {EMBED_API_KEY}"},
        )
    return Response(
        content=resp.content,
        media_type="application/json",
        status_code=resp.status_code,
    )


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_llm(path: str, request: Request):
    """Pass all non-embedding requests straight through to the LLM API."""
    target_url = f"{LLM_API_BASE}/{path}"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": request.headers.get("content-type", "application/json"),
    }
    body = await request.body()

    client = httpx.AsyncClient(timeout=300.0)
    req = client.build_request(
        method=request.method,
        url=target_url,
        content=body,
        headers=headers,
    )
    resp = await client.send(req, stream=True)
    content_type = resp.headers.get("content-type", "application/json")

    async def _generate():
        try:
            async for chunk in resp.aiter_bytes(chunk_size=1024):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        _generate(),
        status_code=resp.status_code,
        media_type=content_type,
        headers={
            k: v
            for k, v in resp.headers.items()
            if k.lower() not in ("content-length", "transfer-encoding", "content-encoding")
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT, log_level="info")
