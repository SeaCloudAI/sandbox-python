from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from .build.service import BuildService
from .cmd.service import CommandService
from .control.service import ControlService
from .control.models import ConnectSandboxResponse
from .core.exceptions import ConfigurationError
from .runtime import Runtime
from .sandbox import SandboxInstance

if TYPE_CHECKING:
    from .facade import Sandbox
    from .template import LogEntry, Template


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

    def create(
        self,
        template_or_options: str | Mapping[str, Any] | None = None,
        **options: Any,
    ) -> Sandbox:
        from .facade import Sandbox

        if isinstance(template_or_options, str):
            body = dict(options)
            body["templateID"] = template_or_options
        else:
            body = dict(template_or_options or {})
            body.update(options)
            if "template" in body and "templateID" not in body:
                body["templateID"] = body["template"]
        created = self.create_sandbox(_filter_create_body(body))
        return Sandbox(self, created)

    def connect(
        self,
        sandbox_id: str,
        *,
        timeout: int = 300,
    ) -> Sandbox:
        from .facade import Sandbox

        response = self.connect_sandbox(sandbox_id, {"timeout": timeout})
        return Sandbox(self, response.sandbox)

    def list(self, **options: Any) -> list[Sandbox]:
        from .facade import Sandbox

        params = {
            key: value
            for key, value in options.items()
            if key in {"metadata", "state", "limit", "next_token"}
        }
        return [Sandbox(self, item) for item in self.list_sandboxes(params or None)]

    def build_template(
        self,
        template: Template,
        name: str,
        *,
        tags: list[str] | None = None,
        base_template_id: str | None = None,
        cpu_count: int | None = None,
        memory_mb: int | None = None,
        wait: bool = True,
        poll_interval: float = 1.0,
        on_build_logs: Any | None = None,
    ) -> dict[str, Any]:
        from .template import _build_with_service

        return _build_with_service(
            self.build,
            template,
            name,
            tags=tags,
            base_template_id=base_template_id,
            cpu_count=cpu_count,
            memory_mb=memory_mb,
            wait=wait,
            poll_interval=poll_interval,
            on_build_logs=on_build_logs,
        )

    def build_template_in_background(
        self,
        template: Template,
        name: str,
        *,
        tags: list[str] | None = None,
        base_template_id: str | None = None,
        cpu_count: int | None = None,
        memory_mb: int | None = None,
        poll_interval: float = 1.0,
        on_build_logs: Any | None = None,
    ) -> dict[str, Any]:
        return self.build_template(
            template,
            name,
            tags=tags,
            base_template_id=base_template_id,
            cpu_count=cpu_count,
            memory_mb=memory_mb,
            wait=False,
            poll_interval=poll_interval,
            on_build_logs=on_build_logs,
        )

    def template_exists(self, ref: str) -> bool:
        from .template import _template_exists_with_service

        return _template_exists_with_service(self.build, ref)

    def template_alias_exists(self, alias: str) -> bool:
        return self.template_exists(alias)

    def get_template_build_status(
        self,
        data: Mapping[str, Any],
        *,
        logs_offset: int | None = None,
        limit: int | None = None,
        level: str | None = None,
    ) -> dict[str, Any]:
        from .template import _get_template_build_status_with_service

        return _get_template_build_status_with_service(
            self.build,
            data,
            logs_offset=logs_offset,
            limit=limit,
            level=level,
        )

    def list_templates(self, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        from .template import _list_templates_with_service

        return _list_templates_with_service(self.build, params)

    def get_template(
        self,
        ref: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .template import _get_template_with_service

        return _get_template_with_service(self.build, ref, params)

    def delete_template(self, ref: str) -> None:
        from .template import _delete_template_with_service

        _delete_template_with_service(self.build, ref)


def _filter_create_body(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in source.items()
        if key in {"templateID", "workspaceId", "timeout", "metadata", "envVars", "volumeMounts", "waitReady"}
    }
