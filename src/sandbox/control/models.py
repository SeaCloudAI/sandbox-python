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
class ConnectSandboxResponse:
    status_code: int
    sandbox: dict[str, Any]
