from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class TemplateVolumeMount(TypedDict):
    name: str
    path: str


class PublicTemplateExtensions(TypedDict, total=False):
    baseTemplateID: str
    visibility: str
    envs: dict[str, str]
    storageType: str
    storageSizeGB: int
    volumeMounts: list[TemplateVolumeMount]


class TemplateCreateRequest(TypedDict, total=False):
    name: str
    tags: list[str]
    cpuCount: int
    memoryMB: int
    extensions: PublicTemplateExtensions


class TemplateUpdateRequest(TypedDict, total=False):
    extensions: PublicTemplateExtensions


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
