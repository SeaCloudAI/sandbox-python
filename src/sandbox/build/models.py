from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class TemplateCreateRequest(TypedDict, total=False):
    name: str
    visibility: str
    baseTemplateID: str
    dockerfile: str
    image: str
    envs: dict[str, str]
    cpuCount: int
    memoryMB: int
    diskSizeMB: int
    ttlSeconds: int
    port: int
    startCmd: str
    readyCmd: str


class TemplateUpdateRequest(TypedDict, total=False):
    name: str
    visibility: str
    baseTemplateID: str
    dockerfile: str
    image: str
    envs: dict[str, str]
    cpuCount: int
    memoryMB: int
    diskSizeMB: int
    ttlSeconds: int
    port: int
    startCmd: str
    readyCmd: str


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
