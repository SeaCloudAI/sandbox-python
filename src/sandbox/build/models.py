from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class TemplateCreateRequest(TypedDict, total=False):
    name: str
    tags: list[str]
    alias: str
    teamID: str
    cpuCount: int
    memoryMB: int
    extensions: dict[str, object]


class TemplateUpdateRequest(TypedDict, total=False):
    public: bool
    extensions: dict[str, object]


@dataclass
class ListTemplatesParams:
    visibility: str | None = None
    team_id: str | None = None
    limit: int | None = None
    offset: int | None = None


@dataclass
class GetTemplateParams:
    limit: int | None = None
    next_token: str | None = None


@dataclass
class BuildStatusParams:
    logs_offset: int | None = None
    limit: int | None = None
    level: str | None = None


@dataclass
class BuildLogsParams:
    cursor: int | None = None
    limit: int | None = None
    direction: str | None = None
    level: str | None = None
    source: str | None = None
