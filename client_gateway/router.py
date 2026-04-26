from __future__ import annotations

from typing import Optional

import httpx

from client_gateway.config import ClientConfig, DestinationConfig, find_local, find_project
from client_gateway.context import ResolvedContext, resolve_project_context
from shared import errors
from shared.lightrag_client import LightRAGClient
from shared.models import ErrorInfo, ResultSource, SearchResponse, SearchResult, WarningInfo, WriteResponse
from client_gateway.server_client import ServerGatewayClient


class Router:
    def __init__(self, config: ClientConfig) -> None:
        self.config = config

    async def search(
        self,
        query: str,
        project_label: Optional[str] = None,
        roots: list[str] | None = None,
    ) -> SearchResponse:
        context = resolve_project_context(self.config, project_label, roots=roots)
        effective_label = context.resolved_project_label

        if not effective_label:
            if self.config.remote_only_mode:
                return _with_context(
                    SearchResponse(
                        status="error",
                        error=ErrorInfo(
                            code=errors.ERROR_REMOTE_ONLY_MODE,
                            message="No project_label provided while remote_only_mode is enabled.",
                        ),
                    ),
                    context,
                )
            return _with_context(await self._search_native(query), context)

        destination = find_project(self.config, effective_label)
        if destination:
            response = await self._search_via_gateway(query, destination)
            response = _with_context(response, context)
            if context.warnings:
                response = _attach_warning(
                    response,
                    WarningInfo(code=errors.WARNING_DESTINATION_NOT_FOUND, message=" ".join(context.warnings)),
                )
            return response

        if self.config.strict_project_routing:
            return _with_context(
                SearchResponse(
                    status="error",
                    error=ErrorInfo(
                        code=errors.ERROR_PROJECT_NOT_CONFIGURED,
                        message=f"Project '{effective_label}' not found in configuration.",
                    ),
                ),
                context,
            )

        result = await self._search_native(query)
        result = _with_context(result, context)
        return _attach_warning(
            result,
            WarningInfo(
                code=errors.WARNING_DESTINATION_NOT_FOUND,
                message=(
                    f"Project '{effective_label}' not found in configuration. "
                    "Search executed against local LightRAG due to non-strict routing."
                ),
            ),
        )

    async def write(
        self,
        text: str,
        project_label: Optional[str] = None,
        allow_local_fallback: bool = False,
        roots: list[str] | None = None,
    ) -> WriteResponse:
        context = resolve_project_context(self.config, project_label, roots=roots)
        effective_label = context.resolved_project_label

        if not effective_label:
            if self.config.remote_only_mode:
                return _with_context(
                    WriteResponse(
                        status="error",
                        error=ErrorInfo(
                            code=errors.ERROR_REMOTE_ONLY_MODE,
                            message="No project_label provided while remote_only_mode is enabled.",
                        ),
                    ),
                    context,
                )
            return _with_context(await self._write_native(text), context)

        destination = find_project(self.config, effective_label)
        if destination:
            return _with_context(await self._write_via_gateway(text, destination, allow_local_fallback), context)

        if allow_local_fallback:
            result = _with_context(await self._write_native(text), context)
            return _attach_write_warning(
                result,
                WarningInfo(
                    code=errors.WARNING_WRITE_FALLBACK_TO_LOCAL,
                    message=f"Project '{effective_label}' not found. Wrote to local LightRAG (fallback enabled).",
                ),
            )

        return _with_context(
            WriteResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_PROJECT_NOT_CONFIGURED,
                    message=f"Project '{effective_label}' not found in configuration.",
                ),
            ),
            context,
        )

    async def local_health(self) -> bool:
        local = find_local(self.config)
        if not local:
            return False
        return await LightRAGClient(local.url).health()

    async def _search_native(self, query: str) -> SearchResponse:
        local = find_local(self.config)
        if not local:
            return SearchResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_LOCAL_MEMORY_UNAVAILABLE,
                    message="No local LightRAG destination configured.",
                ),
            )
        try:
            results = await LightRAGClient(local.url).search(query)
            return SearchResponse(status="ok", source="local", results=results)
        except Exception as exc:
            return SearchResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_LOCAL_MEMORY_UNAVAILABLE,
                    message=f"Local LightRAG unavailable: {exc}",
                ),
            )

    async def _search_via_gateway(self, query: str, dest: DestinationConfig) -> SearchResponse:
        client = ServerGatewayClient(dest.url, dest.token or "")
        warning: Optional[WarningInfo] = None
        try:
            status_code, data = await client.search(query)
            if status_code == 200 and data.get("status") == "ok":
                results = [
                    SearchResult(**{**result, "source": ResultSource.project})
                    if isinstance(result, dict)
                    else SearchResult(text=str(result), source=ResultSource.project)
                    for result in data.get("results", [])
                ]
                return SearchResponse(status="ok", source="project", results=results)
            error_code = (data.get("error") or {}).get("code", "unknown")
            warning = WarningInfo(
                code=_server_error_to_warning(error_code),
                message=f"Project server '{dest.label}' returned error '{error_code}'. Falling back to local LightRAG.",
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            warning = WarningInfo(
                code=errors.WARNING_DESTINATION_UNAVAILABLE,
                message=f"Project server '{dest.label}' unavailable: {exc}. Falling back to local LightRAG.",
            )

        if self.config.remote_only_mode:
            return SearchResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_PROJECT_DESTINATION_UNAVAILABLE,
                    message=warning.message if warning else "Project destination unavailable.",
                ),
            )

        result = await self._search_native(query)
        return _attach_warning(result, warning)

    async def _write_native(self, text: str) -> WriteResponse:
        local = find_local(self.config)
        if not local:
            return WriteResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_LOCAL_MEMORY_UNAVAILABLE,
                    message="No local LightRAG destination configured.",
                ),
            )
        try:
            await LightRAGClient(local.url).write(text)
            return WriteResponse(status="ok", source="local", message="Memory entry written successfully.")
        except Exception as exc:
            return WriteResponse(
                status="error",
                error=ErrorInfo(
                    code=errors.ERROR_LOCAL_MEMORY_UNAVAILABLE,
                    message=f"Local LightRAG write failed: {exc}",
                ),
            )

    async def _write_via_gateway(
        self,
        text: str,
        dest: DestinationConfig,
        allow_local_fallback: bool,
    ) -> WriteResponse:
        client = ServerGatewayClient(dest.url, dest.token or "")
        try:
            status_code, data = await client.write(text)
            if status_code == 200 and data.get("status") == "ok":
                return WriteResponse(status="ok", source="project", message="Memory entry written successfully.")
            error_code = (data.get("error") or {}).get("code", "unknown")
            error_msg = (data.get("error") or {}).get("message", "Unknown error.")
            if allow_local_fallback:
                result = await self._write_native(text)
                return _attach_write_warning(
                    result,
                    WarningInfo(
                        code=errors.WARNING_WRITE_FALLBACK_TO_LOCAL,
                        message=f"Project write failed ({error_code}). Fell back to local LightRAG.",
                    ),
                )
            return WriteResponse(status="error", error=ErrorInfo(code=error_code, message=error_msg))
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            if allow_local_fallback:
                result = await self._write_native(text)
                return _attach_write_warning(
                    result,
                    WarningInfo(
                        code=errors.WARNING_WRITE_FALLBACK_TO_LOCAL,
                        message=f"Project server unavailable ({exc}). Fell back to local LightRAG.",
                    ),
                )
            return WriteResponse(
                status="error",
                error=ErrorInfo(code=errors.ERROR_DESTINATION_UNAVAILABLE, message=f"Project server unavailable: {exc}"),
            )


def _server_error_to_warning(error_code: str) -> str:
    return {
        "access_denied": errors.WARNING_DESTINATION_ACCESS_DENIED,
        "invalid_token": errors.WARNING_DESTINATION_INVALID_TOKEN,
        "destination_unavailable": errors.WARNING_DESTINATION_UNAVAILABLE,
    }.get(error_code, errors.WARNING_DESTINATION_UNAVAILABLE)


def _attach_warning(response: SearchResponse, warning: Optional[WarningInfo]) -> SearchResponse:
    if warning and response.status == "ok":
        response.status = "ok_with_warning"
        response.warning = warning
    return response


def _attach_write_warning(response: WriteResponse, warning: Optional[WarningInfo]) -> WriteResponse:
    if warning and response.status == "ok":
        response.status = "ok_with_warning"
        response.warning = warning
    return response


def _with_context(response: SearchResponse | WriteResponse, context: ResolvedContext) -> SearchResponse | WriteResponse:
    response.context = context.model_dump(exclude_none=True)
    return response
