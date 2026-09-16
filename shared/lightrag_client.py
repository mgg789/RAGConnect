from __future__ import annotations

import hashlib
from typing import List

import httpx

from shared.models import ResultSource, SearchResult
from shared.timeouts import get_request_timeout_seconds


def _content_source(text: str) -> str:
    """Content-addressed file_source for LightRAG >=1.5.

    LightRAG treats file_source as a unique document identity and rejects
    same-name inserts with HTTP 409. A content hash makes repeated writes of
    the same text idempotent instead of colliding on a fixed name.
    """
    return f"mem-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}.txt"


class LightRAGClient:
    """Thin HTTP client that speaks to a running LightRAG server."""

    def __init__(self, base_url: str, timeout: float | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if timeout is not None else get_request_timeout_seconds()

    async def search(self, query: str) -> List[SearchResult]:
        response = await self._post("/query", {"query": query, "mode": "hybrid"})
        return self._normalize(response, ResultSource.local)

    async def write(self, text: str) -> None:
        await self._post_compatible(
            [
                # LightRAG >=1.5 requires file_source; older versions ignore the extra field.
                ("/documents/text", {"text": text, "file_source": _content_source(text)}),
                ("/insert", {"text": text}),
            ],
            duplicate_ok=True,
        )

    async def ingest(self, texts: list[str]) -> dict:
        return await self._post_compatible(
            [
                (
                    "/documents/texts",
                    {"texts": texts, "file_sources": [_content_source(t) for t in texts]},
                ),
                ("/insert", {"texts": texts}),
            ],
            duplicate_ok=True,
        )

    async def documents(self) -> dict:
        return await self._get("/documents")

    async def entities(self) -> dict:
        return await self._get("/entities")

    async def relations(self) -> dict:
        return await self._get("/relations")

    async def graph(self) -> dict:
        # LightRAG ≥1.x: /graphs?label=*  returns the full graph across all labels.
        # max_nodes must match the server's MAX_GRAPH_NODES ceiling (default 1000);
        # LCT deployments run 5000. Fallback: legacy /graph endpoint for older installs.
        try:
            data = await self._get("/graphs?label=*&max_depth=3&max_nodes=5000")
            nodes = data.get("nodes", [])
            edges = data.get("edges", [])
            if nodes:
                return {"nodes": nodes, "edges": edges}
        except Exception:
            pass
        try:
            return await self._get("/graph")
        except Exception:
            pass
        return {"nodes": [], "edges": []}

    async def rebuild(self) -> dict:
        return await self._post("/rebuild", {})

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False

    async def _get(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    async def _post(self, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}{path}", json=payload)
            response.raise_for_status()
            if not response.content:
                return {"status": "ok"}
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    async def _post_compatible(
        self, attempts: list[tuple[str, dict]], duplicate_ok: bool = False
    ) -> dict:
        last_exc: httpx.HTTPStatusError | None = None
        for index, (path, payload) in enumerate(attempts):
            try:
                return await self._post(path, payload)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code if exc.response is not None else None
                if duplicate_ok and status_code == 409:
                    # Content-addressed insert raced with an identical document
                    # that LightRAG already stores: treat as success.
                    return {"status": "ok", "message": "duplicate content already stored"}
                is_not_found = status_code == 404
                has_fallback = index < len(attempts) - 1
                if is_not_found and has_fallback:
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No LightRAG write endpoints configured.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalize(self, data: object, source: ResultSource) -> List[SearchResult]:
        """Convert whatever LightRAG returns into a list of SearchResult."""
        if isinstance(data, str):
            return [SearchResult(text=data, source=source)]
        if isinstance(data, list):
            return [SearchResult(text=str(item), source=source) for item in data]
        if isinstance(data, dict):
            if "result" in data:
                raw = data["result"]
                if isinstance(raw, str):
                    return [SearchResult(text=raw, source=source)]
                if isinstance(raw, list):
                    return [SearchResult(text=str(r), source=source) for r in raw]
            if "results" in data:
                return [
                    SearchResult(
                        text=r.get("text", str(r)),
                        score=r.get("score"),
                        metadata=r.get("metadata"),
                        source=source,
                    )
                    for r in data["results"]
                ]
        return [SearchResult(text=str(data), source=source)]
