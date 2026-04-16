from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel

from shared.models import TokenRole


class TokenInfo(BaseModel):
    token: Optional[str] = None
    token_hash: Optional[str] = None
    token_id: Optional[str] = None
    role: TokenRole
    enabled: bool = True
    description: Optional[str] = None
    expires_at: Optional[str] = None

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            dt = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        return dt < datetime.now(timezone.utc)


class TokenStore:
    """Loads and validates project tokens from a YAML file.

    The file is re-read on every ``validate()`` call so that token
    revocations take effect without a server restart.
    """

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._cache_raw: Dict[str, TokenInfo] = {}
        self._cache_hash: Dict[str, TokenInfo] = {}

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _reload(self) -> None:
        if not self._config_path.exists():
            self._cache = {}
            return
        with open(self._config_path) as fh:
            data = yaml.safe_load(fh) or {}
        self._cache_raw = {}
        self._cache_hash = {}
        for entry in data.get("tokens", []):
            info = TokenInfo(**entry)
            if info.token:
                self._cache_raw[info.token] = info
            if info.token_hash:
                self._cache_hash[info.token_hash] = info

    def validate(self, token: str) -> Optional[TokenInfo]:
        """Return TokenInfo if the token is valid and enabled, otherwise None."""
        self._reload()
        info = self._cache_raw.get(token)
        if not info:
            info = self._cache_hash.get(self.hash_token(token))
        if not info or not info.enabled or info.is_expired():
            return None
        return info
