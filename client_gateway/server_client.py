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
