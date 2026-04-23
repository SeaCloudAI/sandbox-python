from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .core.exceptions import ConfigurationError
from .runtime import Runtime

if TYPE_CHECKING:
    from .client import Client
    from .control import SandboxLogsParams
    from .control.models import ConnectSandboxResponse


class SandboxInstance(dict[str, Any]):
    """A bound sandbox object with attached control-plane and runtime helpers."""

    def __init__(self, client: Client, data: dict[str, Any]) -> None:
        super().__init__(data)
        self._client = client
        self._runtime: Runtime | None = None

    @property
    def runtime(self) -> Runtime:
        envd_url = str(self.get("envdUrl") or "").strip()
        if not envd_url:
            raise ConfigurationError("envdUrl is required")
        if self._runtime is None:
            self._runtime = self._client.runtime_from_sandbox(self)
        return self._runtime

    def reload(self) -> SandboxInstance:
        return self._client.get_sandbox(str(self["sandboxID"]))

    def logs(self, params: SandboxLogsParams | None = None) -> dict[str, Any]:
        return self._client.get_sandbox_logs(str(self["sandboxID"]), params)

    def pause(self) -> None:
        self._client.pause_sandbox(str(self["sandboxID"]))

    def delete(self) -> None:
        self._client.delete_sandbox(str(self["sandboxID"]))

    def refresh(self, body: dict[str, Any] | None = None) -> None:
        self._client.refresh_sandbox(str(self["sandboxID"]), body)

    def set_timeout(self, timeout: int) -> None:
        self._client.set_sandbox_timeout(str(self["sandboxID"]), {"timeout": timeout})

    def connect(self, body: dict[str, Any]) -> ConnectSandboxResponse:
        return self._client.connect_sandbox(str(self["sandboxID"]), body)
