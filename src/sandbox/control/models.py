from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ListSandboxesParams:
    metadata: dict[str, str] | None = None
    state: list[str] | None = None
    limit: int | None = None
    next_token: str | None = None


@dataclass
class SandboxLogsParams:
    cursor: int | None = None
    limit: int | None = None
    direction: str | None = None
    level: str | None = None
    search: str | None = None


@dataclass
class SandboxMetricsParams:
    sandbox_ids: list[str] | None = None
    limit: int | None = None


@dataclass
class SandboxesPage:
    items: list[dict[str, Any]]
    next_token: str
    has_next: bool


@dataclass
class SandboxEventsParams:
    offset: int | None = None
    limit: int | None = None
    order_asc: bool | None = None
    types: list[str] | None = None


@dataclass
class WebhookDeliveriesParams:
    offset: int | None = None
    limit: int | None = None
    order_asc: bool | None = None
    webhook_id: str | None = None
    event_id: str | None = None
    status: str | None = None


@dataclass
class TeamMetricsParams:
    start: int | None = None
    end: int | None = None


@dataclass
class TeamMetricsMaxParams:
    metric: str
    start: int | None = None
    end: int | None = None


@dataclass
class ConnectSandboxResponse:
    status_code: int
    sandbox: dict[str, Any]
