from __future__ import annotations

from typing import List

import httpx

from shared.models import ResultSource, SearchResult


class LightRAGClient:
    """Thin HTTP client that speaks to a running LightRAG server."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def search(self, query: str) -> List[SearchResult]:
        response = await self._post("/query", {"query": query, "mode": "hybrid"})
        return self._normalize(response, ResultSource.local)

    async def write(self, text: str) -> None:
        await self._post("/insert", {"text": text})

    async def ingest(self, texts: list[str]) -> dict:
        return await self._post("/insert", {"texts": texts})

    async def documents(self) -> dict:
        return await self._get("/documents")

    async def entities(self) -> dict:
        return await self._get("/entities")

    async def relations(self) -> dict:
        return await self._get("/relations")

    async def graph(self) -> dict:
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
