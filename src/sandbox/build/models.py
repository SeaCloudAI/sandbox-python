from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class TemplateVolumeMount(TypedDict, total=False):
    name: str
    path: str
    storageType: str
    hostPath: str
    nfsHostPath: str
    storageClass: str
    storageSizeGB: int
    persistentVolumeClaim: str
    emptyDirSizeLimit: str
    emptyDirMedium: str
    objectBucket: str
    objectKeyPrefix: str
    readOnly: bool
    subPath: str


class PublicTemplateExtensions(TypedDict, total=False):
    baseTemplateID: str
    visibility: str
    envs: dict[str, str]
    volumeMounts: list[TemplateVolumeMount]
    workdir: str


class TemplateCreateRequest(TypedDict, total=False):
    name: str
    tags: list[str]
    cpuCount: int
    memoryMB: int
    extensions: PublicTemplateExtensions


class TemplateUpdateRequest(TypedDict, total=False):
    public: bool


@dataclass
class ListTemplatesParams:
    visibility: str | None = None
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
