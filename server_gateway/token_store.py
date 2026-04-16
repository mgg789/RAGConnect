from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel

from shared.models import TokenRole


class TokenInfo(BaseModel):
    token: str
    role: TokenRole
    enabled: bool = True
    description: Optional[str] = None


class TokenStore:
    """Loads and validates project tokens from a YAML file.

    The file is re-read on every ``validate()`` call so that token
    revocations take effect without a server restart.
    """

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._cache: Dict[str, TokenInfo] = {}

    def _reload(self) -> None:
        if not self._config_path.exists():
            self._cache = {}
            return
        with open(self._config_path) as fh:
            data = yaml.safe_load(fh) or {}
        self._cache = {
            entry["token"]: TokenInfo(**entry)
            for entry in data.get("tokens", [])
        }

    def validate(self, token: str) -> Optional[TokenInfo]:
        """Return TokenInfo if the token is valid and enabled, otherwise None."""
        self._reload()
        info = self._cache.get(token)
        return info if (info and info.enabled) else None
