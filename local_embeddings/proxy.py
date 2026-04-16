"""
RAGConnect embedding proxy.

Routes `/v1/embeddings` either to a local embedding model or to a dedicated
embedding API. All other `/v1/*` requests are proxied to the LLM API.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LLM_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")

EMBED_API_KEY = os.getenv("EMBEDDING_API_KEY") or LLM_API_KEY
EMBED_API_BASE = (os.getenv("EMBEDDING_API_BASE") or LLM_API_BASE).rstrip("/")

LOCAL_EMBEDDING_MODE = os.getenv("LOCAL_EMBEDDING_MODE", "false").lower() == "true"
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
LOCAL_EMBEDDING_DIM = int(os.getenv("LOCAL_EMBEDDING_DIM", "384"))

PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "9622"))

_local_model = None


def _load_local_model():
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        logger.info("Loading local embedding model: %s", LOCAL_EMBEDDING_MODEL)
        _local_model = SentenceTransformer(LOCAL_EMBEDDING_MODEL, trust_remote_code=True)
        logger.info("Local embedding model ready (dim=%d)", LOCAL_EMBEDDING_DIM)
    return _local_model


app = FastAPI(title="RAGConnect Embedding Proxy", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def _preload() -> None:
    if LOCAL_EMBEDDING_MODE:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _load_local_model)
        logger.info("Local embedding mode enabled")
    else:
        logger.info("Embedding API mode enabled: %s", EMBED_API_BASE)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    texts = body.get("input", [])
    if isinstance(texts, str):
        texts = [texts]

    if LOCAL_EMBEDDING_MODE:
        model = _load_local_model()
        loop = asyncio.get_event_loop()
        vectors = await loop.run_in_executor(
            None,
            lambda: model.encode(texts, normalize_embeddings=True),
        )
        return {
            "object": "list",
            "data": [
                {"object": "embedding", "index": idx, "embedding": vector.tolist()}
                for idx, vector in enumerate(vectors)
            ],
            "model": LOCAL_EMBEDDING_MODEL,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{EMBED_API_BASE}/embeddings",
            json=body,
            headers={"Authorization": f"Bearer {EMBED_API_KEY}"},
        )
    return Response(
        content=response.content,
        media_type="application/json",
        status_code=response.status_code,
    )


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_llm(path: str, request: Request):
    target_url = f"{LLM_API_BASE}/{path}"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": request.headers.get("content-type", "application/json"),
    }
    body = await request.body()

    client = httpx.AsyncClient(timeout=300.0)
    upstream_request = client.build_request(
        method=request.method,
        url=target_url,
        content=body,
        headers=headers,
    )
    upstream_response = await client.send(upstream_request, stream=True)
    content_type = upstream_response.headers.get("content-type", "application/json")

    async def _generate():
        try:
            async for chunk in upstream_response.aiter_bytes(chunk_size=1024):
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        _generate(),
        status_code=upstream_response.status_code,
        media_type=content_type,
        headers={
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() not in ("content-length", "transfer-encoding", "content-encoding")
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT, log_level="info")
