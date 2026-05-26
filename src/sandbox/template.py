from __future__ import annotations

import calendar
import gzip
import hashlib
import io
import json
import os
import shlex
import tarfile
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen

from .build.builder import TemplateBuildBuilder
from .build.models import BuildStatusParams, GetTemplateParams, ListTemplatesParams
from .build.service import BuildService
from .core.exceptions import NotFoundError, ValidationError

_TERMINAL_BUILD_STATUSES = {"ready", "failed", "error", "cancelled"}
_LOG_LEVEL_ORDER = ("debug", "info", "warn", "error")
_AUTO_COPY_PREFIX = "__auto_copy__:"


@dataclass(slots=True)
class ReadyCmd:
    cmd: str

    def get_cmd(self) -> str:
        return self.cmd


@dataclass(slots=True)
class LogEntry:
    timestamp: float
    level: str
    message: str

    def __str__(self) -> str:
        return f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.timestamp))}] {self.level.upper()} {self.message}"


class LogEntryStart(LogEntry):
    def __init__(self, timestamp: float, message: str) -> None:
        super().__init__(timestamp=timestamp, level="info", message=message)


class LogEntryEnd(LogEntry):
    def __init__(self, timestamp: float, message: str) -> None:
        super().__init__(timestamp=timestamp, level="info", message=message)


class Template:
    """High-level template builder with E2B-style helpers."""

    @classmethod
    def build(
        cls,
        template: "Template",
        name: str,
        **options: Any,
    ) -> dict[str, Any]:
        client = _new_high_level_client(options)
        return client.build_template(template, name, **options)

    @classmethod
    def build_in_background(
        cls,
        template: "Template",
        name: str,
        **options: Any,
    ) -> dict[str, Any]:
        client = _new_high_level_client(options)
        return client.build_template_in_background(template, name, **options)

    @classmethod
    def list(cls, **options: Any) -> list[dict[str, Any]]:
        client = _new_high_level_client(options)
        return client.list_templates(options or None)

    @classmethod
    def get(
        cls,
        ref: str,
        *,
        params: Mapping[str, Any] | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        client = _new_high_level_client(options)
        return client.get_template(ref, params)

    @classmethod
    def delete(cls, ref: str, **options: Any) -> None:
        client = _new_high_level_client(options)
        client.delete_template(ref)

    @classmethod
    def assign_tags(
        cls,
        target_name: str,
        tags: str | list[str],
        **options: Any,
    ) -> dict[str, Any]:
        client = _new_high_level_client(options)
        return client.assign_template_tags(target_name, tags)

    @classmethod
    def get_tags(
        cls,
        template_id: str,
        **options: Any,
    ) -> list[dict[str, Any]]:
        client = _new_high_level_client(options)
        return client.get_template_tags(template_id)

    @classmethod
    def remove_tags(
        cls,
        name: str,
        tags: str | list[str],
        **options: Any,
    ) -> None:
        client = _new_high_level_client(options)
        client.remove_template_tags(name, tags)

    @classmethod
    def exists(cls, ref: str, **options: Any) -> bool:
        client = _new_high_level_client(options)
        return client.template_exists(ref)

    @classmethod
    def get_build_status(
        cls,
        data: Mapping[str, Any],
        **options: Any,
    ) -> dict[str, Any]:
        client = _new_high_level_client(options)
        return client.get_template_build_status(data, **options)

    def __init__(self) -> None:
        self._builder = TemplateBuildBuilder()
        self._auto_copies: dict[str, dict[str, Any]] = {}
        self._skip_cache = False

    def from_image(self, image: str, credentials: dict[str, str] | None = None) -> "Template":
        self._builder.from_image(image)
        if credentials is not None:
            self._builder.from_image_registry({
                "type": "registry",
                "username": credentials["username"],
                "password": credentials["password"],
            })
        return self

    def from_base_image(self) -> "Template":
        return self.from_image("e2bdev/base:latest")

    def from_node_image(self, variant: str = "lts") -> "Template":
        return self.from_image(f"node:{variant}")

    def from_python_image(self, version: str = "3") -> "Template":
        return self.from_image(f"python:{version}")

    def from_bun_image(self, variant: str = "latest") -> "Template":
        return self.from_image(f"oven/bun:{variant}")

    def from_ubuntu_image(self, variant: str = "latest") -> "Template":
        return self.from_image(f"ubuntu:{variant}")

    def from_debian_image(self, variant: str = "stable") -> "Template":
        return self.from_image(f"debian:{variant}")

    def from_aws_registry(self, image: str, credentials: dict[str, str]) -> "Template":
        self._builder.from_image(image)
        self._builder.from_image_registry({
            "type": "aws",
            "awsAccessKeyId": credentials["accessKeyId"],
            "awsSecretAccessKey": credentials["secretAccessKey"],
            "awsRegion": credentials["region"],
        })
        return self

    def from_gcp_registry(self, image: str, credentials: dict[str, Any]) -> "Template":
        self._builder.from_image(image)
        service_account = credentials["serviceAccountJSON"]
        self._builder.from_image_registry({
            "type": "gcp",
            "serviceAccountJson": service_account if isinstance(service_account, str) else json.dumps(service_account),
        })
        return self

    def from_template(self, template: str) -> "Template":
        self._builder.from_template(template)
        return self

    def from_dockerfile(self, dockerfile_content_or_path: str) -> "Template":
        """Parse a supported Dockerfile subset into template build steps."""
        content, context_dir = _resolve_dockerfile_input(dockerfile_content_or_path)
        seen_from = False
        for instruction, value in _parse_dockerfile_instructions(content):
            if instruction == "FROM":
                if seen_from:
                    raise ValidationError("Dockerfile multi-stage builds are not supported")
                tokens = _tokenize_shell_like(value)
                if len(tokens) != 1:
                    raise ValidationError("FROM only supports a single base image")
                self.from_image(tokens[0])
                seen_from = True
            elif instruction == "RUN":
                _ensure_dockerfile_base_image(seen_from)
                self.run_cmd(_require_dockerfile_value(instruction, value))
            elif instruction == "ENV":
                _ensure_dockerfile_base_image(seen_from)
                for name, env_value in _parse_dockerfile_env(value):
                    self._builder.env(name, env_value)
            elif instruction == "WORKDIR":
                _ensure_dockerfile_base_image(seen_from)
                self.set_workdir(_require_dockerfile_value(instruction, value))
            elif instruction == "USER":
                _ensure_dockerfile_base_image(seen_from)
                self.set_user(_require_dockerfile_value(instruction, value))
            elif instruction == "COPY":
                _ensure_dockerfile_base_image(seen_from)
                sources, dest = _parse_dockerfile_copy(value)
                for source in sources:
                    self.copy(_resolve_dockerfile_copy_path(source, context_dir), dest)
            elif instruction == "CMD":
                _ensure_dockerfile_base_image(seen_from)
                self._builder.start_cmd(_parse_dockerfile_cmd(value))
            else:
                raise ValidationError(f"unsupported Dockerfile instruction: {instruction}")

        if not seen_from:
            raise ValidationError("Dockerfile must include a FROM instruction")
        return self

    def copy(
        self,
        src: str | list[str],
        dest: str,
        *,
        files_hash: str | None = None,
        force_upload: bool | None = None,
        mode: int | None = None,
        resolve_symlinks: bool | None = None,
        user: str | None = None,
    ) -> "Template":
        """Copy one or more local sources into the template build context."""
        sources = src if isinstance(src, list) else [src]
        for source in sources:
            resolved_hash = files_hash or self._register_auto_copy(
                source,
                force_upload=bool(force_upload),
                mode=mode,
                resolve_symlinks=bool(resolve_symlinks),
            )
            self._builder.copy(source, dest, resolved_hash, force=self._step_force())
            if user and user.strip():
                self._builder.run(_build_copy_ownership_command(dest, user), force=self._step_force())
        return self

    def copy_items(self, items: list[dict[str, Any]]) -> "Template":
        for item in items:
            self.copy(
                item["src"],
                item["dest"],
                files_hash=item.get("files_hash"),
                force_upload=item.get("force_upload"),
                mode=item.get("mode"),
                resolve_symlinks=item.get("resolve_symlinks"),
                user=item.get("user"),
            )
        return self

    def run_cmd(
        self,
        command_or_commands: str | list[str],
        *,
        force: bool | None = None,
        user: str | None = None,
    ) -> "Template":
        """Add one or more RUN steps, optionally wrapped to execute as a specific user."""
        commands = command_or_commands if isinstance(command_or_commands, list) else [command_or_commands]
        for command in commands:
            self._builder.run(_maybe_run_as_user(command, user), force=self._step_force(force))
        return self

    def apt_install(
        self,
        packages: str | list[str],
        *,
        no_install_recommends: bool = False,
        force: bool | None = None,
    ) -> "Template":
        names = _normalize_template_items(packages, "package")
        return self.run_cmd(
            _build_apt_install_command(names, no_install_recommends=no_install_recommends),
            force=force,
        )

    def git_clone(
        self,
        repo_url: str,
        path: str | None = None,
        *,
        branch: str | None = None,
        depth: int | None = None,
        user: str | None = None,
        force: bool | None = None,
        ) -> "Template":
        return self.run_cmd(
            _build_git_clone_command(
                repo_url,
                path,
                branch=branch,
                depth=depth,
                user=user,
            ),
            force=force,
        )

    def make_dir(
        self,
        path_or_paths: str | list[str],
        *,
        mode: int | None = None,
        user: str | None = None,
        force: bool | None = None,
    ) -> "Template":
        for path in _normalize_template_items(path_or_paths, "path"):
            self.run_cmd(_build_make_dir_command(path, mode=mode, user=user), force=force)
        return self

    def make_symlink(
        self,
        src: str,
        dest: str,
        *,
        user: str | None = None,
        force: bool | None = None,
    ) -> "Template":
        return self.run_cmd(_build_make_symlink_command(src, dest, user=user, force=bool(force)), force=force)

    def npm_install(
        self,
        packages: str | list[str] | None = None,
        *,
        dev: bool = False,
        g: bool = False,
        force: bool | None = None,
    ) -> "Template":
        return self.run_cmd(_build_npm_install_command(packages, dev=dev, g=g), force=force)

    def pip_install(
        self,
        packages: str | list[str] | None = None,
        *,
        g: bool = True,
        force: bool | None = None,
    ) -> "Template":
        return self.run_cmd(_build_pip_install_command(packages, g=g), force=force)

    def bun_install(
        self,
        packages: str | list[str] | None = None,
        *,
        dev: bool = False,
        g: bool = False,
        force: bool | None = None,
    ) -> "Template":
        return self.run_cmd(_build_bun_install_command(packages, dev=dev, g=g), force=force)

    def set_envs(self, envs: dict[str, str]) -> "Template":
        self._builder.env(envs)
        return self

    def set_workdir(self, path: str, *, force: bool | None = None) -> "Template":
        self._builder.workdir(path, force=self._step_force(force))
        return self

    def set_user(self, user: str, *, force: bool | None = None) -> "Template":
        self._builder.user(user, force=self._step_force(force))
        return self

    def remove(
        self,
        path_or_paths: str | list[str],
        *,
        recursive: bool = False,
        user: str | None = None,
        force: bool | None = None,
    ) -> "Template":
        for path in _normalize_template_items(path_or_paths, "path"):
            self.run_cmd(_build_remove_command(path, recursive=recursive, user=user, force=bool(force)), force=force)
        return self

    def rename(
        self,
        src: str,
        dest: str,
        *,
        user: str | None = None,
        force: bool | None = None,
    ) -> "Template":
        return self.run_cmd(_build_rename_command(src, dest, user=user, force=bool(force)), force=force)

    def skip_cache(self) -> "Template":
        self._skip_cache = True
        return self

    def set_start_cmd(self, start_command: str, ready_command: str | ReadyCmd) -> "Template":
        self._builder.start_cmd(start_command)
        return self.set_ready_cmd(ready_command)

    def set_ready_cmd(self, ready_command: str | ReadyCmd) -> "Template":
        self._builder.ready_cmd(_ready_command_to_str(ready_command))
        return self

    def request(self) -> dict[str, Any]:
        request = self._builder.to_request()
        for step in request.get("steps", []):
            if step.get("type") == "COPY" and str(step.get("filesHash") or "").startswith(_AUTO_COPY_PREFIX):
                raise ValidationError("copy steps without files_hash require Template.build()")
        return request

    def build_with_service(
        self,
        service: BuildService,
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
        on_build_logs: Callable[[LogEntry], None] | None = None,
    ) -> dict[str, Any]:
        template_name, parsed_tags = _parse_template_name(name)
        final_tags = list(dict.fromkeys(parsed_tags + (tags or [])))
        create_body: dict[str, Any] = {
            "name": template_name,
            "tags": final_tags,
            "cpuCount": cpu_count,
            "memoryMB": memory_mb,
        }
        extensions = _build_create_template_extensions(
            base_template_id=base_template_id,
            visibility=visibility,
            envs=envs,
            volume_mounts=volume_mounts,
            workdir=workdir,
        )
        if extensions:
            create_body["extensions"] = extensions
        created = service.create_template(_drop_none(create_body))
        build_id = f"build-{int(time.time() * 1000):x}"
        request = _resolve_template_request(
            self._builder.to_request(),
            self._auto_copies,
            created["templateID"],
            service,
            timeout=float(getattr(service, "timeout", 30.0)),
        )
        if on_build_logs is not None:
            on_build_logs(LogEntryStart(time.time(), f"Starting build {build_id}"))
        service.create_build(created["templateID"], build_id, request)
        if not wait:
            return {
                "template_id": created["templateID"],
                "build_id": build_id,
                "name": template_name,
                "tags": final_tags,
                "alias": (created.get("aliases") or [None])[0],
            }

        logs_offset = 0
        status: dict[str, Any] | None = None
        while True:
            status = _normalize_build_status_response(service.get_build_status(
                created["templateID"],
                build_id,
                BuildStatusParams(logs_offset=logs_offset, limit=100),
            ))
            log_entries = status.get("logEntries") or []
            logs_offset += len(log_entries)
            if on_build_logs is not None:
                for entry in log_entries:
                    on_build_logs(LogEntry(
                        timestamp=_parse_timestamp(entry.get("timestamp")),
                        level=_normalize_log_level(str(entry.get("level") or "info")),
                        message=f"{entry.get('step')}: {entry.get('message')}",
                    ))
            if str(status.get("status") or "") in _TERMINAL_BUILD_STATUSES:
                break
            time.sleep(poll_interval)

        if on_build_logs is not None and status is not None:
            on_build_logs(LogEntryEnd(time.time(), f"Build {build_id} finished with status {status.get('status')}"))

        return {
            "template_id": created["templateID"],
            "build_id": build_id,
            "name": template_name,
            "tags": final_tags,
            "alias": (created.get("aliases") or [None])[0],
        }

    @classmethod
    def to_json(cls, template: "Template", compute_hashes: bool = True) -> str:
        """Serialize the currently supported template subset into build-request JSON."""
        request = _serialize_template_request(template, compute_hashes)
        return json.dumps(request, indent=2)

    @classmethod
    def to_dockerfile(cls, template: "Template") -> str:
        """Convert the currently supported template subset into a Dockerfile string."""
        request = template._builder.to_request()
        if request.get("fromTemplate"):
            raise ValidationError("templates based on other templates cannot be converted to Dockerfile")
        base_image = str(request.get("fromImage") or "").strip()
        if not base_image:
            raise ValidationError("template must define a base image to convert to Dockerfile")
        lines = [f"FROM {base_image}"]
        for step in request.get("steps", []):
            step_type = step.get("type")
            args = list(step.get("args") or [])
            if step_type == "COPY" and len(args) >= 2:
                lines.append(f"COPY {args[0]} {args[1]}")
            elif step_type == "RUN" and args:
                lines.append(_dockerfile_run_line(args[0]))
            elif step_type == "ENV":
                lines.extend(_dockerfile_env_lines(args))
            elif step_type == "WORKDIR" and args:
                lines.append(f"WORKDIR {args[0]}")
            elif step_type == "USER" and args:
                lines.append(f"USER {args[0]}")
        start_cmd = str(request.get("startCmd") or "").strip()
        if start_cmd:
            lines.append(f"CMD [\"sh\", \"-lc\", {json.dumps(start_cmd)}]")
        ready_cmd = str(request.get("readyCmd") or "").strip()
        if ready_cmd:
            lines.append(f"# Ready command: {ready_cmd}")
        return "\n".join(lines) + "\n"

    def _register_auto_copy(
        self,
        source: str,
        *,
        force_upload: bool,
        mode: int | None,
        resolve_symlinks: bool,
    ) -> str:
        token = f"{_AUTO_COPY_PREFIX}{len(self._auto_copies) + 1}"
        self._auto_copies[token] = {
            "src": source,
            "force_upload": force_upload,
            "mode": mode,
            "resolve_symlinks": resolve_symlinks,
        }
        return token

    def _step_force(self, force: bool | None = None) -> bool | None:
        return self._skip_cache if force is None and self._skip_cache else force


def _build_with_service(
    service: BuildService,
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
    on_build_logs: Callable[[LogEntry], None] | None = None,
) -> dict[str, Any]:
    return template.build_with_service(
        service,
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


def _new_high_level_client(options: dict[str, Any]):
    from ._client import GatewayClient

    request_timeout_ms = _pop_high_level_request_timeout_ms(options)
    if request_timeout_ms is None:
        return GatewayClient()
    return GatewayClient(request_timeout_ms=request_timeout_ms)


def _pop_high_level_request_timeout_ms(options: dict[str, Any]) -> int | None:
    for key in ("base_url", "api_key", "domain", "project_id"):
        if options.get(key) is not None:
            raise ValidationError(
                f"{key} is not supported on high-level Template helpers; use SEACLOUD_BASE_URL/SEACLOUD_API_KEY env vars",
            )
    request_timeout_ms = options.pop("request_timeout_ms", None)
    return None if request_timeout_ms is None else int(request_timeout_ms)


def _template_exists_with_service(service: BuildService, ref: str) -> bool:
    try:
        _get_template_with_service(service, ref)
        return True
    except NotFoundError:
        return False


def _assign_template_tags_with_service(
    service: BuildService,
    target_name: str,
    tags: str | list[str],
) -> dict[str, Any]:
    response = service.assign_template_tags({
        "target": target_name,
        "tags": _normalize_template_tag_input(tags),
    })
    return {
        "build_id": str(response.get("buildID") or ""),
        "tags": list(response.get("tags") or []),
    }


def _get_template_tags_with_service(
    service: BuildService,
    template_id: str,
) -> list[dict[str, Any]]:
    return [
        {
            "build_id": str(tag.get("buildID") or ""),
            "created_at": str(tag.get("createdAt") or ""),
            "tag": str(tag.get("tag") or ""),
        }
        for tag in service.list_template_tags(template_id)
    ]


def _remove_template_tags_with_service(
    service: BuildService,
    name: str,
    tags: str | list[str],
) -> None:
    service.delete_template_tags({
        "name": name,
        "tags": _normalize_template_tag_input(tags),
    })


def _get_template_build_status_with_service(
    service: BuildService,
    data: Mapping[str, Any],
    *,
    logs_offset: int | None = None,
    limit: int | None = None,
    level: str | None = None,
) -> dict[str, Any]:
    template_id = str(data.get("template_id") or "").strip()
    build_id = str(data.get("build_id") or "").strip()
    if not template_id:
        raise ValidationError("template_id is required")
    if not build_id:
        raise ValidationError("build_id is required")
    return _normalize_build_status_response(service.get_build_status(
        template_id,
        build_id,
        BuildStatusParams(logs_offset=logs_offset, limit=limit, level=level),
    ))


def _normalize_build_status_response(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **response,
        "template_id": str(response.get("templateID") or ""),
        "build_id": str(response.get("buildID") or ""),
    }


def _list_templates_with_service(
    service: BuildService,
    params: Mapping[str, Any] | ListTemplatesParams | None = None,
) -> list[dict[str, Any]]:
    if params is None or isinstance(params, ListTemplatesParams):
        return service.list_templates(params)
    return service.list_templates(ListTemplatesParams(
            visibility=params.get("visibility"),
            limit=params.get("limit"),
            offset=params.get("offset"),
        ))


def _get_template_with_service(
    service: BuildService,
    ref: str,
    params: Mapping[str, Any] | GetTemplateParams | None = None,
) -> dict[str, Any]:
    template_id = ref if ref.startswith("tpl-") else service.resolve_template_ref(ref)["templateID"]
    if params is None or isinstance(params, GetTemplateParams):
        return service.get_template(template_id, params)
    resolved_params = GetTemplateParams(
        limit=params.get("limit"),
        next_token=params.get("next_token"),
    )
    return service.get_template(template_id, resolved_params)


def _delete_template_with_service(service: BuildService, ref: str) -> None:
    template_id = ref if ref.startswith("tpl-") else service.resolve_template_ref(ref)["templateID"]
    service.delete_template(template_id)


def _get_template_for_tag_mutation_with_service(service: BuildService, ref: str) -> dict[str, Any]:
    original_error: NotFoundError | None = None
    try:
        return _get_template_with_service(service, ref)
    except NotFoundError as exc:
        original_error = exc
    name, _ = _split_template_name_and_tags(ref)
    if name == ref:
        raise original_error if original_error is not None else NotFoundError(f"template {ref} not found")
    return _get_template_with_service(service, name)


def default_build_logger(*, min_level: str = "info") -> Callable[[LogEntry], None]:
    min_index = _LOG_LEVEL_ORDER.index(min_level) if min_level in _LOG_LEVEL_ORDER else 1

    def log(entry: LogEntry) -> None:
        level_index = _LOG_LEVEL_ORDER.index(entry.level) if entry.level in _LOG_LEVEL_ORDER else 1
        if level_index < min_index:
            return
        print(str(entry))

    return log


def wait_for_file(filename: str) -> ReadyCmd:
    return ReadyCmd(f"test -f {_shell_quote(filename)}")


def wait_for_port(port: int) -> ReadyCmd:
    if port <= 0:
        raise ValidationError("port must be a positive integer")
    return ReadyCmd(f"sh -lc \"ss -ltn | grep -q ':{port} '\"")


def wait_for_process(process_name: str) -> ReadyCmd:
    return ReadyCmd(f"pgrep -f {_shell_quote(process_name)} >/dev/null")


def wait_for_timeout(timeout: int) -> ReadyCmd:
    if timeout <= 0:
        raise ValidationError("timeout must be a positive integer")
    return ReadyCmd(f"sleep {int(timeout)}")


def wait_for_url(url: str, status_code: int = 200) -> ReadyCmd:
    if not url.strip():
        raise ValidationError("url is required")
    return ReadyCmd(
        f"test \"$(curl -o /dev/null -s -w '%{{http_code}}' {_shell_quote(url)})\" = \"{status_code}\"",
    )


def _ready_command_to_str(command: str | ReadyCmd) -> str:
    return command if isinstance(command, str) else command.get_cmd()


def _normalize_template_items(values: str | list[str], label: str) -> list[str]:
    items = [item.strip() for item in (values if isinstance(values, list) else [values]) if item.strip()]
    if not items:
        raise ValidationError(f"{label} is required")
    return items


def _normalize_optional_template_items(values: str | list[str] | None) -> list[str]:
    if values is None:
        return []
    return _normalize_template_items(values, "value")


def _normalize_template_tag_input(values: str | list[str]) -> list[str]:
    tags = _dedupe_template_tags(_normalize_template_items(values, "tags"))
    if not tags:
        raise ValidationError("tags are required")
    return tags


def _split_template_name_and_tags(name: str) -> tuple[str, list[str]]:
    trimmed = name.strip()
    if not trimmed or trimmed.count(":") != 1:
        return trimmed, []
    base, tag = trimmed.split(":", 1)
    base = base.strip()
    tag = tag.strip()
    if not base or not tag:
        return trimmed, []
    return base, [tag]


def _dedupe_template_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw_tag in tags:
        tag = str(raw_tag).strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def _build_apt_install_command(packages: list[str], *, no_install_recommends: bool) -> str:
    install_args = ["apt-get", "install", "-y"]
    if no_install_recommends:
        install_args.append("--no-install-recommends")
    install_args.extend(packages)
    return f"{_shell_join(['apt-get', 'update'])} && DEBIAN_FRONTEND=noninteractive {_shell_join(install_args)}"


def _build_git_clone_command(
    repo_url: str,
    path: str | None,
    *,
    branch: str | None,
    depth: int | None,
    user: str | None,
) -> str:
    trimmed_url = repo_url.strip()
    if not trimmed_url:
        raise ValidationError("repo url is required")
    args = ["git", "clone"]
    if branch:
        args.extend(["--branch", branch])
    if depth is not None:
        args.extend(["--depth", str(depth)])
    args.append(trimmed_url)
    if path and path.strip():
        args.append(path.strip())
    command = _shell_join(args)
    if not user:
        return command
    return f"su -s /bin/sh {_shell_quote(user)} -c {_shell_quote(command)}"


def _build_make_dir_command(path: str, *, mode: int | None, user: str | None) -> str:
    args = ["mkdir", "-p"]
    if mode is not None:
        args.extend(["-m", _format_file_mode(mode)])
    args.append(path)
    return _maybe_run_as_user(_shell_join(args), user)


def _build_copy_ownership_command(path: str, user: str) -> str:
    return _shell_join(["chown", "-R", user.strip(), path.strip()])


def _build_make_symlink_command(src: str, dest: str, *, user: str | None, force: bool) -> str:
    args = ["ln", "-s"]
    if force:
        args.append("-f")
    args.extend([src, dest])
    return _maybe_run_as_user(_shell_join(args), user)


def _build_remove_command(path: str, *, recursive: bool, user: str | None, force: bool) -> str:
    args = ["rm"]
    if recursive:
        args.append("-r")
    if force:
        args.append("-f")
    args.append(path)
    return _maybe_run_as_user(_shell_join(args), user)


def _build_rename_command(src: str, dest: str, *, user: str | None, force: bool) -> str:
    args = ["mv"]
    if force:
        args.append("-f")
    args.extend([src, dest])
    return _maybe_run_as_user(_shell_join(args), user)


def _dockerfile_env_lines(args: list[str]) -> list[str]:
    lines: list[str] = []
    for index in range(0, len(args), 2):
        name = args[index]
        value = args[index + 1] if index + 1 < len(args) else ""
        if name:
            lines.append(f"ENV {name}={json.dumps(value)}")
    return lines


def _dockerfile_run_line(command: str) -> str:
    return f"RUN [\"sh\", \"-lc\", {json.dumps(command)}]"


def _build_npm_install_command(packages: str | list[str] | None, *, dev: bool, g: bool) -> str:
    args = ["npm", "install"]
    if dev:
        args.append("--save-dev")
    if g:
        args.append("-g")
    args.extend(_normalize_optional_template_items(packages))
    return _shell_join(args)


def _build_pip_install_command(packages: str | list[str] | None, *, g: bool) -> str:
    args = ["pip", "install"]
    if not g:
        args.append("--user")
    names = _normalize_optional_template_items(packages)
    args.extend(names if names else ["."])
    return _shell_join(args)


def _build_bun_install_command(packages: str | list[str] | None, *, dev: bool, g: bool) -> str:
    args = ["bun", "install"]
    if dev:
        args.append("--dev")
    if g:
        args.append("-g")
    args.extend(_normalize_optional_template_items(packages))
    return _shell_join(args)


def _resolve_dockerfile_input(dockerfile_content_or_path: str) -> tuple[str, str | None]:
    raw = dockerfile_content_or_path
    trimmed = raw.strip()
    if not trimmed:
        raise ValidationError("dockerfile content or path is required")
    resolved_path = os.path.abspath(trimmed)
    if "\n" not in trimmed and os.path.exists(resolved_path):
        if not os.path.isfile(resolved_path):
            raise ValidationError("dockerfile path must point to a file")
        with open(resolved_path, "r", encoding="utf-8") as handle:
            return handle.read(), os.path.dirname(resolved_path)
    return raw, None


def _parse_dockerfile_instructions(content: str) -> list[tuple[str, str]]:
    instructions: list[tuple[str, str]] = []
    for line in _join_dockerfile_lines(content):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        instruction = parts[0].upper()
        value = parts[1] if len(parts) > 1 else ""
        instructions.append((instruction, value))
    return instructions


def _join_dockerfile_lines(content: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for raw_line in content.splitlines():
        trimmed_right = raw_line.rstrip()
        if trimmed_right.endswith("\\"):
            current += trimmed_right[:-1] + " "
            continue
        current += trimmed_right
        lines.append(current)
        current = ""
    if current.strip():
        lines.append(current)
    return lines


def _ensure_dockerfile_base_image(seen_from: bool) -> None:
    if not seen_from:
        raise ValidationError("Dockerfile instructions must appear after FROM")


def _require_dockerfile_value(instruction: str, value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValidationError(f"{instruction} requires a value")
    return trimmed


def _parse_dockerfile_env(value: str) -> list[tuple[str, str]]:
    trimmed = _require_dockerfile_value("ENV", value)
    tokens = _tokenize_shell_like(trimmed)
    if not tokens:
        raise ValidationError("ENV requires at least one variable")
    if any("=" in token for token in tokens):
        pairs: list[tuple[str, str]] = []
        for token in tokens:
            separator = token.find("=")
            if separator <= 0:
                raise ValidationError(f"invalid ENV assignment: {token}")
            pairs.append((token[:separator], token[separator + 1:]))
        return pairs
    if len(tokens) < 2:
        raise ValidationError("ENV requires a key and value")
    value_index = trimmed.find(tokens[1])
    return [(tokens[0], _strip_matching_quotes(trimmed[value_index:]))]


def _parse_dockerfile_copy(value: str) -> tuple[list[str], str]:
    trimmed = _require_dockerfile_value("COPY", value)
    if trimmed.startswith("--"):
        raise ValidationError("COPY flags are not supported")
    if trimmed.startswith("["):
        try:
            items = json.loads(trimmed)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid COPY JSON array: {exc.msg}") from exc
        if (not isinstance(items, list) or len(items) < 2
                or any(not isinstance(item, str) or not item.strip() for item in items)):
            raise ValidationError("COPY JSON array must contain at least one source and one destination")
        values = [item.strip() for item in items]
        return values[:-1], values[-1]
    tokens = _tokenize_shell_like(trimmed)
    if len(tokens) < 2:
        raise ValidationError("COPY requires at least one source and one destination")
    if any(token.startswith("--") for token in tokens):
        raise ValidationError("COPY flags are not supported")
    return tokens[:-1], tokens[-1]


def _parse_dockerfile_cmd(value: str) -> str:
    trimmed = _require_dockerfile_value("CMD", value)
    if not trimmed.startswith("["):
        return trimmed
    try:
        items = json.loads(trimmed)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid CMD JSON array: {exc.msg}") from exc
    if not isinstance(items, list) or not items or any(not isinstance(item, str) for item in items):
        raise ValidationError("CMD JSON array must contain one or more strings")
    return _shell_join([str(item) for item in items])


def _resolve_dockerfile_copy_path(source: str, context_dir: str | None) -> str:
    if context_dir is None or os.path.isabs(source):
        return source
    return os.path.abspath(os.path.join(context_dir, source))


def _tokenize_shell_like(value: str) -> list[str]:
    try:
        return shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValidationError(f"invalid Dockerfile value: {exc}") from exc


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and ((value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'"))):
        return value[1:-1]
    return value


def _resolve_template_request(
    request: dict[str, Any],
    auto_copies: dict[str, dict[str, Any]],
    template_id: str,
    service: BuildService,
    *,
    timeout: float,
) -> dict[str, Any]:
    steps = [dict(step, args=list(step.get("args") or [])) for step in request.get("steps", [])]
    uploaded: set[str] = set()
    for step in steps:
        token = str(step.get("filesHash") or "")
        if step.get("type") != "COPY" or not token.startswith(_AUTO_COPY_PREFIX):
            continue
        copy = auto_copies.get(token)
        if copy is None:
            raise ValidationError(f"unknown copy token {token}")
        archive_path = _normalize_archive_source(str(copy["src"]))
        if step.get("args"):
            step["args"][0] = archive_path
        tar_bytes = _pack_template_source(
            str(copy["src"]),
            archive_path,
            mode=copy.get("mode"),
            resolve_symlinks=bool(copy.get("resolve_symlinks")),
        )
        files_hash = hashlib.sha256(tar_bytes).hexdigest()
        if files_hash not in uploaded:
            response = service.get_build_file(template_id, files_hash)
            if not bool(response.get("present")) or bool(copy.get("force_upload")):
                upload_url = str(response.get("url") or "").strip()
                if not upload_url:
                    raise ValidationError(f"build file upload URL is missing for hash {files_hash}")
                max_context_bytes = _parse_max_context_bytes(response.get("maxContextBytes"))
                _validate_build_context_size(len(tar_bytes), max_context_bytes)
                _upload_build_file(upload_url, tar_bytes, max_context_bytes=max_context_bytes, timeout=timeout)
            uploaded.add(files_hash)
        step["filesHash"] = files_hash
    resolved = dict(request)
    resolved["steps"] = steps
    return resolved


def _serialize_template_request(template: Template, compute_hashes: bool) -> dict[str, Any]:
    request = template._builder.to_request()
    if not compute_hashes:
        return request
    steps = [dict(step, args=list(step.get("args") or [])) for step in request.get("steps", [])]
    for step in steps:
        token = str(step.get("filesHash") or "")
        if step.get("type") != "COPY" or not token.startswith(_AUTO_COPY_PREFIX):
            continue
        copy = template._auto_copies.get(token)
        if copy is None:
            raise ValidationError(f"unknown copy token {token}")
        archive_path = _normalize_archive_source(str(copy["src"]))
        step["filesHash"] = hashlib.sha256(_pack_template_source(
            str(copy["src"]),
            archive_path,
            mode=copy.get("mode"),
            resolve_symlinks=bool(copy.get("resolve_symlinks")),
        )).hexdigest()
    resolved = dict(request)
    resolved["steps"] = steps
    return resolved


def _pack_template_source(
    source: str,
    archive_path: str,
    *,
    mode: int | None = None,
    resolve_symlinks: bool = False,
) -> bytes:
    source_path = os.path.abspath(source)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        _append_tar_entry(tar, source_path, archive_path, mode=mode, resolve_symlinks=resolve_symlinks)
    return gzip.compress(buffer.getvalue())


def _append_tar_entry(
    tar: tarfile.TarFile,
    disk_path: str,
    archive_path: str,
    *,
    mode: int | None,
    resolve_symlinks: bool,
) -> None:
    stat_result = os.stat(disk_path) if resolve_symlinks else os.lstat(disk_path)
    normalized_archive_path = archive_path.replace("\\", "/").lstrip("/")
    if not normalized_archive_path:
        raise ValidationError("copy source path must not resolve to an empty archive path")
    entry_mode = mode if mode is not None else stat_result.st_mode & 0o777

    if os.path.isdir(disk_path):
        info = tarfile.TarInfo(_ensure_trailing_slash(normalized_archive_path))
        info.type = tarfile.DIRTYPE
        info.mode = entry_mode
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        tar.addfile(info)
        for entry in sorted(os.listdir(disk_path)):
            _append_tar_entry(
                tar,
                os.path.join(disk_path, entry),
                f"{normalized_archive_path}/{entry}",
                mode=mode,
                resolve_symlinks=resolve_symlinks,
            )
        return

    if not resolve_symlinks and os.path.islink(disk_path):
        info = tarfile.TarInfo(normalized_archive_path)
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(disk_path)
        info.mode = entry_mode
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        tar.addfile(info)
        return

    if not os.path.isfile(disk_path):
        raise ValidationError(f"unsupported copy source type for {disk_path}")

    with open(disk_path, "rb") as handle:
        data = handle.read()
    info = tarfile.TarInfo(normalized_archive_path)
    info.size = len(data)
    info.mode = entry_mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    tar.addfile(info, io.BytesIO(data))


def _normalize_archive_source(source: str) -> str:
    trimmed = source.strip()
    if not trimmed:
        raise ValidationError("copy source path is required")
    if os.path.isabs(trimmed):
        return os.path.basename(trimmed)
    normalized = trimmed.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    return normalized or os.path.basename(trimmed)


def _ensure_trailing_slash(value: str) -> str:
    return value if value.endswith("/") else f"{value}/"


def _upload_build_file(upload_url: str, data: bytes, *, max_context_bytes: int, timeout: float) -> None:
    headers = {"Content-Type": "application/x-tar"}
    if max_context_bytes > 0:
        headers["x-goog-content-length-range"] = f"0,{max_context_bytes}"
    request = Request(upload_url, data=data, headers=headers, method="PUT")
    with urlopen(request, timeout=timeout) as response:
        status_code = getattr(response, "status", response.getcode())
        if status_code < 200 or status_code >= 300:
            raise ValidationError(f"build file upload failed with status {status_code}")


def _parse_max_context_bytes(value: Any) -> int:
    if value is None:
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _validate_build_context_size(size_bytes: int, max_context_bytes: int) -> None:
    if max_context_bytes <= 0 or size_bytes <= max_context_bytes:
        return
    raise ValidationError(
        f"build context archive size {_format_byte_size(size_bytes)} exceeds limit {_format_byte_size(max_context_bytes)}"
    )


def _format_byte_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    value = float(size_bytes)
    unit = "B"
    for candidate in ("KiB", "MiB", "GiB", "TiB"):
        value /= 1024
        unit = candidate
        if value < 1024:
            break
    return f"{value:.1f}{unit}"


def _parse_template_name(name: str) -> tuple[str, list[str]]:
    trimmed = name.strip()
    if not trimmed:
        raise ValidationError("template name is required")
    if ":" not in trimmed:
        return trimmed, []
    base_name, tag = trimmed.rsplit(":", 1)
    base_name = base_name.strip()
    tag = tag.strip()
    if not base_name or not tag:
        raise ValidationError("template name must be in name or name:tag format")
    return base_name, [tag]


def _resolve_template_ref_id(service: BuildService, ref: str) -> str:
    trimmed = ref.strip()
    if not trimmed:
        raise ValidationError("template ref is required")
    if trimmed.startswith("tpl-"):
        return trimmed
    return str(service.resolve_template_ref(trimmed)["templateID"])


def _require_non_empty(value: str, label: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValidationError(f"{label} is required")
    return trimmed


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _shell_join(values: list[str]) -> str:
    return " ".join(_shell_quote(value) for value in values)


def _maybe_run_as_user(command: str, user: str | None) -> str:
    if not user:
        return command
    return f"su -s /bin/sh {_shell_quote(user)} -c {_shell_quote(command)}"


def _format_file_mode(mode: int) -> str:
    if mode < 0:
        raise ValidationError("mode must be a non-negative integer")
    return format(mode, "o")


def _parse_timestamp(value: Any) -> float:
    if not value:
        return time.time()
    return float(calendar.timegm(time.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ")))


def _normalize_log_level(level: str) -> str:
    return level if level in _LOG_LEVEL_ORDER else "info"


def _build_create_template_extensions(
    *,
    base_template_id: str | None = None,
    visibility: str | None = None,
    envs: dict[str, str] | None = None,
    volume_mounts: list[dict[str, Any]] | None = None,
    workdir: str | None = None,
) -> dict[str, Any]:
    extensions: dict[str, Any] = {}
    if base_template_id is not None and base_template_id.strip():
        extensions["baseTemplateID"] = base_template_id.strip()
    if visibility is not None and visibility.strip():
        extensions["visibility"] = visibility.strip()
    if envs:
        extensions["envs"] = dict(envs)
    if volume_mounts:
        extensions["volumeMounts"] = [dict(mount) for mount in volume_mounts]
    if workdir is not None and workdir.strip():
        extensions["workdir"] = workdir.strip()
    return extensions


def _drop_none(body: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in body.items():
        if value is None:
            continue
        if isinstance(value, Mapping):
            nested = _drop_none(value)
            if nested:
                cleaned[key] = nested
            continue
        cleaned[key] = value
    return cleaned
