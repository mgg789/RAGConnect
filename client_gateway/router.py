from __future__ import annotations

from typing import Optional

import httpx

from shared import errors
from shared.lightrag_client import LightRAGClient
from shared.models import (
    ErrorInfo,
    SearchResponse,
    SearchResult,
    WarningInfo,
    WriteResponse,
)
from client_gateway.config import ClientConfig, ProjectConfig, find_project
from client_gateway.server_client import ServerGatewayClient


class Router:
    """Routes memory requests to the local LightRAG or a remote Server Gateway.

    Routing rules
    -------------
    search:
      - no label          → local
      - label found       → project server; fallback to local + warning on error
      - label not found   → local + warning

    write:
      - no label          → local
      - label found       → project server; error on failure unless allow_local_fallback=True
      - label not found   → error unless allow_local_fallback=True
    """

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.local_client: Optional[LightRAGClient] = (
            LightRAGClient(config.local_memory.url)
            if config.local_memory.enabled
            else None
        )

    # ------------------------------------------------------------------ search

    async def search(
        self,
        query: str,
        project_label: Optional[str] = None,
    ) -> SearchResponse:
        # Scenario A – no label
        if not project_label:
            return await self._search_local(query)

        # Scenario B – label found in config
        project = find_project(self.config, project_label)
        if project:
            return await self._search_project(query, project)

        # Scenario C – label present but not found in config
        local = await self._search_local(query)
        return _attach_warning(
            local,
            WarningInfo(
                code=errors.WARNING_DESTINATION_NOT_FOUND,
                message=(
                    f"Project '{project_label}' not found in configuration. "
                    "Search was executed against local memory only."
                ),
            ),
        )

    async def _search_local(self, query: str) -> SearchResponse:
        if not self.local_client:
            return SearchResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_LOCAL_MEMORY_UNAVAILABLE,
                    message="Local memory is not configured or is disabled.",
                ),
            )
        try:
            results = await self.local_client.search(query)
            return SearchResponse(status="ok", source="local", results=results)
        except Exception as exc:
            return SearchResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_LOCAL_MEMORY_UNAVAILABLE,
                    message=f"Local memory unavailable: {exc}",
                ),
            )

    async def _search_project(
        self, query: str, project: ProjectConfig
    ) -> SearchResponse:
        client = ServerGatewayClient(project.url, project.token)
        warning: Optional[WarningInfo] = None
        try:
            status_code, data = await client.search(query)
            if status_code == 200 and data.get("status") == "ok":
                results = [
                    SearchResult(**r) if isinstance(r, dict) else SearchResult(text=str(r))
                    for r in data.get("results", [])
                ]
                return SearchResponse(status="ok", source="project", results=results)
            # Server returned a non-OK status → fall through to local fallback
            error_code = (data.get("error") or {}).get("code", "unknown")
            warning = WarningInfo(
                code=_server_error_to_warning_code(error_code, status_code),
                message=(
                    f"Project server returned error '{error_code}'. "
                    "Falling back to local memory."
                ),
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            warning = WarningInfo(
                code=errors.WARNING_DESTINATION_UNAVAILABLE,
                message=f"Project server unavailable: {exc}. Falling back to local memory.",
            )

        local = await self._search_local(query)
        return _attach_warning(local, warning)

    # ------------------------------------------------------------------- write

    async def write(
        self,
        text: str,
        project_label: Optional[str] = None,
        allow_local_fallback: bool = False,
    ) -> WriteResponse:
        # Scenario A – no label
        if not project_label:
            return await self._write_local(text)

        # Scenario B – label found in config
        project = find_project(self.config, project_label)
        if project:
            return await self._write_project(text, project, allow_local_fallback)

        # Scenario C – label present but not found in config
        if allow_local_fallback:
            local = await self._write_local(text)
            return _attach_write_warning(
                local,
                WarningInfo(
                    code=errors.WARNING_WRITE_FALLBACK_TO_LOCAL,
                    message=(
                        f"Project '{project_label}' not found in configuration. "
                        "Writing to local memory (fallback enabled)."
                    ),
                ),
            )
        return WriteResponse(
            status="error",
            error=ErrorInfo(
                code=errors.ERROR_PROJECT_NOT_CONFIGURED,
                message=f"Project '{project_label}' not found in configuration.",
            ),
        )

    async def _write_local(self, text: str) -> WriteResponse:
        if not self.local_client:
            return WriteResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_LOCAL_MEMORY_UNAVAILABLE,
                    message="Local memory is not configured or is disabled.",
                ),
            )
        try:
            await self.local_client.write(text)
            return WriteResponse(
                status="ok",
                source="local",
                message="Memory entry written successfully.",
            )
        except Exception as exc:
            return WriteResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_LOCAL_MEMORY_UNAVAILABLE,
                    message=f"Local memory write failed: {exc}",
                ),
            )

    async def _write_project(
        self,
        text: str,
        project: ProjectConfig,
        allow_local_fallback: bool,
    ) -> WriteResponse:
        client = ServerGatewayClient(project.url, project.token)
        try:
            status_code, data = await client.write(text)
            if status_code == 200 and data.get("status") == "ok":
                return WriteResponse(
                    status="ok",
                    source="project",
                    message="Memory entry written successfully.",
                )
            error_code = (data.get("error") or {}).get("code", "unknown")
            error_msg = (data.get("error") or {}).get("message", "Unknown error from project server.")
            if allow_local_fallback:
                local = await self._write_local(text)
                return _attach_write_warning(
                    local,
                    WarningInfo(
                        code=errors.WARNING_WRITE_FALLBACK_TO_LOCAL,
                        message=f"Project write failed ({error_code}). Falling back to local memory.",
                    ),
                )
            return WriteResponse(
                status="error",
                error=ErrorInfo(code=error_code, message=error_msg),
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            if allow_local_fallback:
                local = await self._write_local(text)
                return _attach_write_warning(
                    local,
                    WarningInfo(
                        code=errors.WARNING_WRITE_FALLBACK_TO_LOCAL,
                        message=f"Project server unavailable ({exc}). Falling back to local memory.",
                    ),
                )
            return WriteResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_DESTINATION_UNAVAILABLE,
                    message=f"Project server unavailable: {exc}",
                ),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _server_error_to_warning_code(error_code: str, status_code: int) -> str:
    mapping = {
        "access_denied": errors.WARNING_DESTINATION_ACCESS_DENIED,
        "invalid_token": errors.WARNING_DESTINATION_INVALID_TOKEN,
        "destination_unavailable": errors.WARNING_DESTINATION_UNAVAILABLE,
    }
    return mapping.get(error_code, errors.WARNING_DESTINATION_UNAVAILABLE)


def _attach_warning(
    response: SearchResponse,
    warning: Optional[WarningInfo],
) -> SearchResponse:
    if warning is None:
        return response
    if response.status == "ok":
        response.status = "ok_with_warning"
        response.warning = warning
    # If local also failed, leave as error — the original error message is more useful.
    return response


def _attach_write_warning(
    response: WriteResponse,
    warning: Optional[WarningInfo],
) -> WriteResponse:
    if warning and response.status == "ok":
        response.status = "ok_with_warning"
        response.warning = warning
    return response
