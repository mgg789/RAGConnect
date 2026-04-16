from __future__ import annotations

from typing import List

import httpx

from shared.models import SearchResult


class LightRAGClient:
    """Thin HTTP client that speaks to a running LightRAG server."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def search(self, query: str) -> List[SearchResult]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/query",
                json={"query": query, "mode": "hybrid"},
            )
            response.raise_for_status()
            return self._normalize(response.json())

    async def write(self, text: str) -> None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/insert",
                json={"text": text},
            )
            response.raise_for_status()

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalize(self, data: object) -> List[SearchResult]:
        """Convert whatever LightRAG returns into a list of SearchResult."""
        if isinstance(data, str):
            return [SearchResult(text=data)]
        if isinstance(data, list):
            return [SearchResult(text=str(item)) for item in data]
        if isinstance(data, dict):
            if "result" in data:
                raw = data["result"]
                if isinstance(raw, str):
                    return [SearchResult(text=raw)]
                if isinstance(raw, list):
                    return [SearchResult(text=str(r)) for r in raw]
            if "results" in data:
                return [
                    SearchResult(
                        text=r.get("text", str(r)),
                        score=r.get("score"),
                        metadata=r.get("metadata"),
                    )
                    for r in data["results"]
                ]
        return [SearchResult(text=str(data))]
