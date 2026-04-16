from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from shared.models import TokenRole

if TYPE_CHECKING:
    from server_gateway.token_store import TokenInfo, TokenStore


class AuthError(Exception):
    """Raised when a request fails authentication or authorization."""

    def __init__(self, code: str, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def validate_token(raw_token: Optional[str], store: "TokenStore") -> "TokenInfo":
    """Parse and validate a Bearer token string.

    Raises AuthError (401) if the token is missing, malformed, or unknown.
    """
    if not raw_token:
        raise AuthError(
            code="invalid_token",
            message="Missing or invalid Authorization header.",
            http_status=401,
        )
    info = store.validate(raw_token)
    if not info:
        raise AuthError(
            code="invalid_token",
            message="Invalid or expired token.",
            http_status=401,
        )
    return info


def require_write_role(token_info: "TokenInfo") -> None:
    """Raise AuthError (403) if the token does not have the write role."""
    if token_info.role != TokenRole.write:
        raise AuthError(
            code="write_not_allowed",
            message=f"Token role '{token_info.role}' does not allow write operations.",
            http_status=403,
        )
