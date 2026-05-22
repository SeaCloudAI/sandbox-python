from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Mapping

from .build.service import BuildService
from .cmd.service import CommandService
from .control.service import ControlService
from .control.models import ConnectSandboxResponse, ListSandboxesParams
from .core.exceptions import ConfigurationError
from .core.transport import SDKLogger
from .runtime import Runtime
from .sandbox import SandboxInstance

if TYPE_CHECKING:
    from .facade import Sandbox
    from .template import LogEntry, Template


class GatewayClient(ControlService):
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        domain: str | None = None,
        project_id: str | None = None,
        timeout: float = 30.0,
        request_timeout_ms: int | None = None,
        debug: bool = False,
        logger: SDKLogger | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            domain=domain,
            project_id=project_id,
            timeout=timeout,
            request_timeout_ms=request_timeout_ms,
            debug=debug,
            logger=logger,
        )
        self.build = BuildService(
            base_url=base_url,
            api_key=api_key,
            domain=domain,
            project_id=project_id,
            timeout=timeout,
            request_timeout_ms=request_timeout_ms,
            debug=debug,
            logger=logger,
        )

    def cmd(self, *, base_url: str, access_token: str = "", timeout: float = 30.0) -> CommandService:
        return self.runtime(base_url=base_url, access_token=access_token, timeout=timeout)

    def create_sandbox(self, body: Mapping[str, Any], *, request_timeout_ms: int | None = None) -> SandboxInstance:
        return SandboxInstance(self, super().create_sandbox(body, request_timeout_ms=request_timeout_ms))

    def get_sandbox(self, sandbox_id: str, *, request_timeout_ms: int | None = None) -> SandboxInstance:
        return SandboxInstance(self, super().get_sandbox(sandbox_id, request_timeout_ms=request_timeout_ms))

    def list_sandboxes(
        self,
        params: Mapping[str, Any] | None = None,
        request_timeout_ms: int | None = None,
    ) -> list[SandboxInstance]:
        return [SandboxInstance(self, item) for item in super().list_sandboxes(params, request_timeout_ms=request_timeout_ms)]

    def connect_sandbox(
        self,
        sandbox_id: str,
        body: Mapping[str, Any],
        *,
        request_timeout_ms: int | None = None,
    ) -> ConnectSandboxResponse:
        response = super().connect_sandbox(sandbox_id, body, request_timeout_ms=request_timeout_ms)
        return ConnectSandboxResponse(
            status_code=response.status_code,
            sandbox=SandboxInstance(self, dict(response.sandbox)),
        )

    def runtime(self, *, base_url: str, access_token: str = "", timeout: float | None = None) -> Runtime:
        return Runtime(
            base_url=base_url,
            access_token=access_token,
            timeout=self.timeout if timeout is None else timeout,
            debug=self.debug,
            logger=self.logger,
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
            debug=self.debug,
            logger=self.logger,
        )

    def create(
        self,
        template_or_options: str | Mapping[str, Any],
        **options: Any,
    ) -> Sandbox:
        from .facade import Sandbox

        if isinstance(template_or_options, str):
            body = dict(options)
            body["template"] = template_or_options
        else:
            body = dict(template_or_options or {})
            body.update(options)
        request_timeout_ms = body.get("request_timeout_ms")
        kwargs = {} if request_timeout_ms is None else {"request_timeout_ms": int(request_timeout_ms)}
        created = self.create_sandbox(_filter_create_body(body), **kwargs)
        return Sandbox(self, created)

    def connect(
        self,
        sandbox_id: str,
        *,
        timeout: int | None = None,
        request_timeout_ms: int | None = None,
    ) -> Sandbox:
        from .facade import Sandbox

        response = self.connect_sandbox(
            sandbox_id,
            {"timeout": _normalize_connect_timeout_seconds(timeout=timeout)},
            request_timeout_ms=request_timeout_ms,
        )
        return Sandbox(self, response.sandbox)

    def list(self, **options: Any):
        from .facade import SandboxPaginator

        params = {
            key: value
            for key, value in options.items()
            if key in {"metadata", "state", "limit", "next_token"}
        }
        return SandboxPaginator(
            lambda **page_options: self.list_sandboxes(
                params=ListSandboxesParams(**page_options),
                request_timeout_ms=options.get("request_timeout_ms"),
            ),
            params,
        )

    def build_template(
        self,
        template: Template,
        name: str,
        *,
        tags: list[str] | None = None,
        base_template_id: str | None = None,
        visibility: str | None = None,
        envs: dict[str, str] | None = None,
        volume_mounts: list[dict[str, Any]] | None = None,
        workdir: str | None = None,
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
            visibility=visibility,
            envs=envs,
            volume_mounts=volume_mounts,
            workdir=workdir,
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
        visibility: str | None = None,
        envs: dict[str, str] | None = None,
        volume_mounts: list[dict[str, Any]] | None = None,
        workdir: str | None = None,
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
            visibility=visibility,
            envs=envs,
            volume_mounts=volume_mounts,
            workdir=workdir,
            cpu_count=cpu_count,
            memory_mb=memory_mb,
            wait=False,
            poll_interval=poll_interval,
            on_build_logs=on_build_logs,
        )

    def template_exists(self, ref: str) -> bool:
        from .template import _template_exists_with_service

        return _template_exists_with_service(self.build, ref)

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

    def assign_template_tags(self, target_name: str, tags: str | list[str]) -> dict[str, Any]:
        from .template import _assign_template_tags_with_service

        return _assign_template_tags_with_service(self.build, target_name, tags)

    def get_template_tags(self, template_id: str) -> list[dict[str, Any]]:
        from .template import _get_template_tags_with_service

        return _get_template_tags_with_service(self.build, template_id)

    def remove_template_tags(self, name: str, tags: str | list[str]) -> None:
        from .template import _remove_template_tags_with_service

        _remove_template_tags_with_service(self.build, name, tags)


def _filter_create_body(source: Mapping[str, Any]) -> dict[str, Any]:
    _reject_unsupported_create_fields(source)
    template_id = source.get("template")
    normalized_template_id = str(template_id).strip() if template_id is not None else ""
    if not normalized_template_id:
        raise ConfigurationError("templateID is required")
    body = {
        "templateID": normalized_template_id,
        "timeout": _normalize_lifecycle_timeout_seconds(source),
        "autoPause": source.get("autoPause"),
        "metadata": source.get("metadata"),
        "envVars": source.get("envs"),
        "waitReady": source.get("waitReady"),
    }
    return {key: value for key, value in body.items() if value is not None}

def _reject_unsupported_create_fields(source: Mapping[str, Any]) -> None:
    for key in ("autoResume", "secure", "allow_internet_access", "network", "mcp", "volumeMounts"):
        if key in source:
            raise ConfigurationError(f"{key} is not supported")

def _normalize_timeout_seconds(timeout: Any = None, *, allow_zero: bool = False) -> int | None:
    if timeout is None:
        return None
    timeout_value = int(timeout)
    if timeout_value < 0 or (timeout_value == 0 and not allow_zero):
        message = "timeout must be a non-negative integer" if allow_zero else "timeout must be a positive integer"
        raise ConfigurationError(message)
    return timeout_value


def _normalize_lifecycle_timeout_seconds(source: Mapping[str, Any]) -> int | None:
    return _normalize_timeout_seconds(source.get("timeout"), allow_zero=True)


def _normalize_connect_timeout_seconds(*, timeout: int | None) -> int:
    if timeout is None:
        return 300
    return _normalize_timeout_seconds(timeout, allow_zero=True) or 0
