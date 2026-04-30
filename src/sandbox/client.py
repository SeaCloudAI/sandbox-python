from __future__ import annotations

from typing import Any, Mapping

from .build.service import BuildService
from .cmd.service import CommandService
from .control.service import ControlService
from .control.models import ConnectSandboxResponse
from .core.exceptions import ConfigurationError
from .runtime import Runtime
from .sandbox import SandboxInstance


class Client(ControlService):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        project_id: str = "",
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            project_id=project_id,
            timeout=timeout,
        )
        self.build = BuildService(
            base_url=base_url,
            api_key=api_key,
            project_id=project_id,
            timeout=timeout,
        )

    def cmd(self, *, base_url: str, access_token: str = "", timeout: float = 30.0) -> CommandService:
        return self.runtime(base_url=base_url, access_token=access_token, timeout=timeout)

    def create_sandbox(self, body: Mapping[str, Any]) -> SandboxInstance:
        return SandboxInstance(self, super().create_sandbox(body))

    def get_sandbox(self, sandbox_id: str) -> SandboxInstance:
        return SandboxInstance(self, super().get_sandbox(sandbox_id))

    def list_sandboxes(
        self,
        params: Mapping[str, Any] | None = None,
    ) -> list[SandboxInstance]:
        return [SandboxInstance(self, item) for item in super().list_sandboxes(params)]

    def connect_sandbox(
        self,
        sandbox_id: str,
        body: Mapping[str, Any],
    ) -> ConnectSandboxResponse:
        response = super().connect_sandbox(sandbox_id, body)
        return ConnectSandboxResponse(
            status_code=response.status_code,
            sandbox=SandboxInstance(self, dict(response.sandbox)),
        )

    def runtime(self, *, base_url: str, access_token: str = "", timeout: float | None = None) -> Runtime:
        return Runtime(
            base_url=base_url,
            access_token=access_token,
            timeout=self.timeout if timeout is None else timeout,
        )

    def cmd_from_sandbox(
        self,
        sandbox: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> CommandService:
        return self.runtime_from_sandbox(sandbox, timeout=timeout)

    def runtime_from_sandbox(
        self,
        sandbox: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> Runtime:
        base_url = str(sandbox.get("envdUrl") or "").strip()
        if not base_url:
            raise ConfigurationError("envdUrl is required")

        return Runtime(
            base_url=base_url,
            access_token=str(sandbox.get("envdAccessToken") or ""),
            timeout=self.timeout if timeout is None else timeout,
        )
