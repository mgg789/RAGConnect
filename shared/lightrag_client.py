from __future__ import annotations

from typing import List

import httpx

from shared.models import ResultSource, SearchResult
from shared.timeouts import get_request_timeout_seconds


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
                ("/documents/text", {"text": text}),
                ("/insert", {"text": text}),
            ]
        )

    async def ingest(self, texts: list[str]) -> dict:
        return await self._post_compatible(
            [
                ("/documents/texts", {"texts": texts}),
                ("/insert", {"texts": texts}),
            ]
        )

    async def documents(self) -> dict:
        return await self._get("/documents")

    async def entities(self) -> dict:
        return await self._get("/entities")

    async def relations(self) -> dict:
        return await self._get("/relations")

    async def graph(self) -> dict:
        # Try modern LightRAG graph API (/graph/label/list + /graphs) first,
        # then fall back to legacy /graph endpoint.
        try:
            labels_resp = await self._get("/graph/label/list")
            labels: list = []
            if isinstance(labels_resp, list):
                labels = labels_resp
            elif isinstance(labels_resp, dict):
                labels = labels_resp.get("data", labels_resp.get("labels", []))
            if labels:
                # Fetch a large subgraph starting from the first entity
                seed = labels[0] if isinstance(labels[0], str) else str(labels[0])
                import urllib.parse
                params = urllib.parse.urlencode({
                    "label": seed,
                    "max_depth": 5,
                    "max_nodes": 500,
                })
                data = await self._get(f"/graphs?{params}")
                nodes = data.get("nodes", [])
                edges = data.get("edges", [])
                return {"nodes": nodes, "edges": edges}
        except Exception:
            pass
        # Legacy fallback
        return await self._get("/graph")

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

    async def _post_compatible(self, attempts: list[tuple[str, dict]]) -> dict:
        last_exc: httpx.HTTPStatusError | None = None
        for index, (path, payload) in enumerate(attempts):
            try:
                return await self._post(path, payload)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                is_not_found = exc.response is not None and exc.response.status_code == 404
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
