from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class TokenRole(str, Enum):
    readonly = "readonly"
    write = "write"


class ResultSource(str, Enum):
    local = "local"
    project = "project"


class SearchResult(BaseModel):
    text: str
    score: Optional[float] = None
    metadata: Optional[dict] = None
    source: Optional[ResultSource] = None


class WarningInfo(BaseModel):
    code: str
    message: str


class ErrorInfo(BaseModel):
    code: str
    message: str


class SearchResponse(BaseModel):
    status: str  # "ok" | "ok_with_warning" | "error"
    source: Optional[str] = None  # "local" | "project"
    results: Optional[List[SearchResult]] = None
    warning: Optional[WarningInfo] = None
    error: Optional[ErrorInfo] = None


class WriteResponse(BaseModel):
    status: str  # "ok" | "ok_with_warning" | "error"
    source: Optional[str] = None  # "local" | "project"
    message: Optional[str] = None
    warning: Optional[WarningInfo] = None
    error: Optional[ErrorInfo] = None


class ProjectInfo(BaseModel):
    label: str
    enabled: bool


class ListProjectsResponse(BaseModel):
    projects: List[ProjectInfo]


class HealthStatus(BaseModel):
    name: str
    status: str  # "ok" | "error" | "disabled"
    message: Optional[str] = None


class HealthResponse(BaseModel):
    status: str  # "ok" | "error"
    components: List[HealthStatus]
