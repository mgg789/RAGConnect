from __future__ import annotations

from typing import Tuple

import httpx


class ServerGatewayClient:
    """HTTP client that talks to a remote Server Gateway."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def search(self, query: str) -> Tuple[int, dict]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/search",
                json={"query": query},
                headers=self._headers,
            )
            return response.status_code, response.json()

    async def write(self, text: str) -> Tuple[int, dict]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/write",
                json={"text": text},
                headers=self._headers,
            )
            return response.status_code, response.json()

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self.base_url}/health",
                    headers=self._headers,
                )
                return response.status_code == 200
        except Exception:
            return False

    async def documents(self) -> Tuple[int, dict]:
        return await self._get("/documents")

    async def entities(self) -> Tuple[int, dict]:
        return await self._get("/entities")

    async def relations(self) -> Tuple[int, dict]:
        return await self._get("/relations")

    async def graph(self) -> Tuple[int, dict]:
        return await self._get("/graph")

    async def ingest(self, texts: list[str]) -> Tuple[int, dict]:
        return await self._post("/ingest", {"texts": texts})

    async def rebuild(self) -> Tuple[int, dict]:
        return await self._post("/rebuild", {})

    async def _get(self, path: str) -> Tuple[int, dict]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}{path}", headers=self._headers)
            payload = response.json() if response.content else {}
            return response.status_code, payload

    async def _post(self, path: str, payload: dict) -> Tuple[int, dict]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}{path}", json=payload, headers=self._headers)
            data = response.json() if response.content else {}
            return response.status_code, data
