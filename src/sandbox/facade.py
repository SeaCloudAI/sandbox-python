from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from ._client import GatewayClient
from .cmd import CmdRequestOptions, FileRequest, UploadBytesRequest
from .code_interpreter import (
    CodeContext,
    CodeExecution,
    PythonCodeContextManager,
    _get_result_with_retry,
    _normalize_language,
    is_python_language,
    run_code_with_runtime,
)
from .core.exceptions import APIError, ConfigurationError, NotFoundError


@dataclass(slots=True)
class CommandHandle:
    runtime: Any
    stream: Any
    pid: int
    cmd_id: str | None = None
    pty: bool = False

    def send_stdin(self, data: str | bytes) -> None:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        self.runtime.send_input({
            "process": {"pid": self.pid},
            "input": {"pty": _encode_stream_data(text)} if self.pty else {"stdin": _encode_stream_data(text)},
        })

    def kill(self) -> bool:
        try:
            self.runtime.send_signal({"process": {"pid": self.pid}, "signal": "SIGNAL_SIGKILL"})
            return True
        except Exception as exc:
            if _is_missing_process_error(exc):
                return False
            raise

    def wait(self) -> dict[str, Any]:
        stdout = ""
        stderr = ""
        pty = ""
        while True:
            frame = self.stream.next()
            if frame is None:
                break
            event = frame.get("event", {})
            if "data" in event:
                stdout_chunk = _decode_stream_data(event["data"].get("stdout"))
                stderr_chunk = _decode_stream_data(event["data"].get("stderr"))
                pty_chunk = _decode_stream_data(event["data"].get("pty"))
                stdout += stdout_chunk
                stderr += stderr_chunk
                pty += pty_chunk
                # Some runtimes stream PTY reconnect output through stdout/stderr instead of pty.
                if self.pty and not pty_chunk:
                    pty += stdout_chunk + stderr_chunk
            if "end" in event:
                break
        if not self.cmd_id:
            return {"stdout": stdout, "stderr": stderr, "pty": pty}
        result = _get_result_with_retry(self.runtime, self.cmd_id)
        return {
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "pty": pty,
            "exit_code": result["exit_code"],
        }


class Commands:
    def __init__(self, runtime_factory) -> None:
        self._runtime_factory = runtime_factory

    def run(self, cmd: str, *, background: bool = False, **options: Any) -> dict[str, Any] | CommandHandle:
        runtime = self._runtime_factory()
        execution_cmd, execution_args = _build_command_execution(
            cmd,
            options.get("args"),
            options.get("user"),
        )
        request_options = _runtime_request_options(options.get("request_timeout"))
        if background:
            start_body = {
                "process": _build_process_config(
                    execution_cmd,
                    args=execution_args,
                    envs=options.get("envs"),
                    cwd=options.get("cwd"),
                ),
                "timeout": options.get("timeout"),
                "stdin": True,
            }
            stream = runtime.start(start_body) if request_options is None else runtime.start(start_body, request_options)
            started = _expect_start_frame(stream)
            handle = CommandHandle(runtime=runtime, stream=stream, pid=started["pid"], cmd_id=started.get("cmdId"))
            if options.get("stdin"):
                handle.send_stdin(options["stdin"])
            return handle
        run_body = {
            "cmd": execution_cmd,
            "args": execution_args,
            "cwd": options.get("cwd"),
            "env": options.get("envs"),
            "timeout": options.get("timeout"),
            "stdin": options.get("stdin"),
        }
        result = runtime.run(run_body) if request_options is None else runtime.run(run_body, request_options)
        return {
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "exit_code": result["exit_code"],
            "duration_ms": result["duration_ms"],
            "error": result.get("error"),
        }

    def exec(self, cmd: str, *, background: bool = False, **options: Any) -> dict[str, Any] | CommandHandle:
        return self.run(cmd, background=background, **options)

    def list(self, options: CmdRequestOptions | None = None) -> list[dict[str, Any]]:
        return self._runtime_factory().list_processes(options).get("processes", [])

    def connect(self, pid: int, options: CmdRequestOptions | None = None) -> CommandHandle:
        runtime = self._runtime_factory()
        stream = runtime.connect({"process": {"pid": pid}}, options)
        started = _expect_start_frame(stream)
        return CommandHandle(runtime=runtime, stream=stream, pid=started["pid"], cmd_id=started.get("cmdId"))

    def kill(self, pid: int, options: CmdRequestOptions | None = None) -> bool:
        try:
            self._runtime_factory().send_signal({"process": {"pid": pid}, "signal": "SIGNAL_SIGKILL"}, options)
            return True
        except Exception as exc:
            if _is_missing_process_error(exc):
                return False
            raise

    def send_stdin(self, pid: int, data: str | bytes, options: CmdRequestOptions | None = None) -> None:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        self._runtime_factory().send_input({"process": {"pid": pid}, "input": {"stdin": _encode_stream_data(text)}}, options)


class Filesystem:
    def __init__(self, runtime_factory) -> None:
        self._runtime_factory = runtime_factory

    def exists(self, path: str, options: CmdRequestOptions | None = None) -> bool:
        try:
            self._runtime_factory().stat({"path": path}, options)
            return True
        except NotFoundError:
            return False

    def get_info(self, path: str, options: CmdRequestOptions | None = None) -> dict[str, Any]:
        return self._runtime_factory().stat({"path": path}, options)["entry"]

    def list(self, path: str, *, depth: int | None = None, options: CmdRequestOptions | None = None) -> list[dict[str, Any]]:
        return self._runtime_factory().list_dir({"path": path, "depth": depth}, options)["entries"]

    def make_dir(self, path: str, options: CmdRequestOptions | None = None) -> bool:
        self._runtime_factory().make_dir({"path": path}, options)
        return True

    def read(self, path: str, options: CmdRequestOptions | None = None) -> str:
        with self._runtime_factory().read_file(FileRequest(path=path), options) as response:
            return response.read().decode("utf-8")

    def write(
        self,
        path_or_files: str | list[dict[str, Any]],
        data_or_options: str | bytes | CmdRequestOptions | None = None,
        options: CmdRequestOptions | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        if not isinstance(path_or_files, str):
            response = self._runtime_factory().write_batch({
                "files": [
                    {
                        "path": str(file["path"]),
                        "content": file.get("content"),
                        "data": _encode_base64(file["data"]) if file.get("data") is not None else None,
                    }
                    for file in path_or_files
                ],
            }, data_or_options if isinstance(data_or_options, CmdRequestOptions) else options)
            return [_normalize_write_info(file["path"], int(file["bytes_written"])) for file in response.get("files", [])]

        data = data_or_options
        raw = data.encode("utf-8") if isinstance(data, str) else data
        if raw is None:
            raise ConfigurationError("data is required")
        self._runtime_factory().write_file(UploadBytesRequest(path=path_or_files, data=raw), options)
        return _normalize_write_info(path_or_files, len(raw))

    def write_files(
        self,
        files: list[dict[str, Any]],
        options: CmdRequestOptions | None = None,
    ) -> list[dict[str, Any]]:
        return self.write(files, options)  # type: ignore[return-value]

    def remove(self, path: str, options: CmdRequestOptions | None = None) -> None:
        self._runtime_factory().remove({"path": path}, options)

    def rename(self, old_path: str, new_path: str, options: CmdRequestOptions | None = None) -> dict[str, Any]:
        return self._runtime_factory().move({"source": old_path, "destination": new_path}, options)["entry"]

    def watch_dir(self, path: str, *, recursive: bool = False, options: CmdRequestOptions | None = None):
        return self._runtime_factory().watch_dir({"path": path, "recursive": recursive}, options)


class Pty:
    def __init__(self, runtime_factory) -> None:
        self._runtime_factory = runtime_factory

    def create(self, command: str, **options: Any) -> CommandHandle:
        runtime = self._runtime_factory()
        execution_cmd, execution_args = _build_command_execution(
            command,
            options.get("args"),
            options.get("user"),
        )
        start_body = {
            "process": _build_process_config(
                execution_cmd,
                args=execution_args,
                envs=options.get("envs"),
                cwd=options.get("cwd"),
            ),
            "timeout": options.get("timeout"),
            "stdin": True,
            "pty": {"size": options.get("size") or {"cols": 80, "rows": 24}},
        }
        request_options = _runtime_request_options(options.get("request_timeout"))
        stream = runtime.start(start_body) if request_options is None else runtime.start(start_body, request_options)
        started = _expect_start_frame(stream)
        return CommandHandle(runtime=runtime, stream=stream, pid=started["pid"], cmd_id=started.get("cmdId"), pty=True)

    def connect(self, pid: int, options: CmdRequestOptions | None = None) -> CommandHandle:
        runtime = self._runtime_factory()
        stream = runtime.connect({"process": {"pid": pid}}, options)
        started = _expect_start_frame(stream)
        return CommandHandle(runtime=runtime, stream=stream, pid=started["pid"], cmd_id=started.get("cmdId"), pty=True)

    def kill(self, pid: int, options: CmdRequestOptions | None = None) -> bool:
        try:
            self._runtime_factory().send_signal({"process": {"pid": pid}, "signal": "SIGNAL_SIGKILL"}, options)
            return True
        except Exception as exc:
            if _is_missing_process_error(exc):
                return False
            raise

    def send_stdin(self, pid: int, data: str | bytes, options: CmdRequestOptions | None = None) -> None:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        self._runtime_factory().send_input({"process": {"pid": pid}, "input": {"pty": _encode_stream_data(text)}}, options)

    def resize(self, pid: int, size: dict[str, int], options: CmdRequestOptions | None = None) -> None:
        self._runtime_factory().update({"process": {"pid": pid}, "pty": {"size": size}}, options)


class Git:
    def __init__(self, commands: Commands) -> None:
        self._commands = commands

    def clone(
        self,
        repo_url: str,
        path: str | None = None,
        *,
        branch: str | None = None,
        depth: int | None = None,
        cwd: str | None = None,
        envs: Mapping[str, str] | None = None,
        timeout: int | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        args: list[str] = []
        if branch:
            args.extend(["--branch", branch])
        if depth is not None:
            args.extend(["--depth", str(depth)])
        args.append(repo_url)
        if path:
            args.append(path)
        return self._run("clone", args, cwd=cwd, envs=envs, timeout=timeout, user=user)

    def pull(
        self,
        path: str | None = None,
        *,
        cwd: str | None = None,
        envs: Mapping[str, str] | None = None,
        timeout: int | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        return self._run("pull", [], cwd=path or cwd, envs=envs, timeout=timeout, user=user)

    def checkout(
        self,
        ref: str,
        path: str | None = None,
        *,
        cwd: str | None = None,
        envs: Mapping[str, str] | None = None,
        timeout: int | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        return self._run("checkout", [ref], cwd=path or cwd, envs=envs, timeout=timeout, user=user)

    def status(
        self,
        path: str | None = None,
        *,
        cwd: str | None = None,
        envs: Mapping[str, str] | None = None,
        timeout: int | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        return self._run("status", [], cwd=path or cwd, envs=envs, timeout=timeout, user=user)

    def _run(
        self,
        subcommand: str,
        args: list[str],
        *,
        cwd: str | None,
        envs: Mapping[str, str] | None,
        timeout: int | None,
        user: str | None,
    ) -> dict[str, Any]:
        command, command_args = _build_git_execution(subcommand, args, user)
        return self._commands.run(
            command,
            args=command_args,
            cwd=cwd,
            envs=dict(envs) if envs is not None else None,
            timeout=timeout,
        )


class Sandbox:
    @classmethod
    def create(
        cls,
        template_or_options: str | Mapping[str, Any] | None = None,
        **options: Any,
    ) -> "Sandbox":
        _reject_high_level_gateway_options(options)
        return GatewayClient().create(template_or_options, **options)

    @classmethod
    def list(cls, **options: Any) -> list[dict[str, Any]]:
        _reject_high_level_gateway_options(options)
        return GatewayClient().list(**options)

    def __init__(self, client: GatewayClient, data: Mapping[str, Any]) -> None:
        self._client = client
        self._data = dict(data)
        self._code_contexts: PythonCodeContextManager | None = None
        self._stateless_code_contexts: dict[str, CodeContext] = {}
        self.commands = Commands(self._runtime)
        self.files = Filesystem(self._runtime)
        self.git = Git(self.commands)
        self.pty = Pty(self._runtime)

    @property
    def sandbox_id(self) -> str:
        return str(self._data["sandboxID"])

    @property
    def sandbox_domain(self) -> str:
        raw = str(self._data.get("envdUrl") or "").strip()
        return urlparse(raw).netloc if raw else ""

    @property
    def traffic_access_token(self) -> str | None:
        value = self._data.get("trafficAccessToken")
        return str(value) if value is not None else None

    @property
    def raw(self) -> dict[str, Any]:
        return dict(self._data)

    def reload(self) -> "Sandbox":
        self._data = dict(self._client.get_sandbox(self.sandbox_id))
        return self

    def connect(
        self,
        sandbox_id: str | None = None,
        *,
        timeout: int | None = None,
    ) -> "Sandbox":
        if isinstance(self, Sandbox):
            response = self._client.connect_sandbox(
                self.sandbox_id,
                {"timeout": _normalize_connect_timeout(timeout=timeout)},
            )
            self._data = dict(response.sandbox)
            return self
        target_sandbox_id = str(self if sandbox_id is None else sandbox_id)
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        _reject_high_level_gateway_options(kwargs)
        return GatewayClient().connect(target_sandbox_id, **kwargs)

    def resume(self, *, timeout: int | None = None) -> "Sandbox":
        return self.connect(timeout=timeout)

    def get_info(self, sandbox_id: str | None = None) -> dict[str, Any]:
        if isinstance(self, Sandbox):
            detail = self._client.get_sandbox(self.sandbox_id)
            self._data = dict(detail)
            return dict(detail)
        target_sandbox_id = str(self if sandbox_id is None else sandbox_id)
        _reject_high_level_gateway_options({})
        return dict(GatewayClient().get_sandbox(target_sandbox_id))

    def get_metrics(self) -> dict[str, Any]:
        return self._runtime().metrics()

    def run_code(
        self,
        code: str,
        *,
        language: str | None = None,
        cwd: str | None = None,
        timeout: int | None = None,
        envs: Mapping[str, str] | None = None,
        context: CodeContext | None = None,
        on_stdout=None,
        on_stderr=None,
        on_result=None,
        on_results=None,
        on_error=None,
    ) -> CodeExecution:
        options = {
            "language": language,
            "cwd": cwd,
            "timeout": timeout,
            "envs": envs,
            "context": context,
            "on_stdout": on_stdout,
            "on_stderr": on_stderr,
            "on_result": on_result,
            "on_results": on_results,
            "on_error": on_error,
        }
        if context is not None:
            if not is_python_language(context.language):
                return run_code_with_runtime(
                    self._runtime(),
                    code,
                    language=language or context.language,
                    cwd=cwd or context.cwd,
                    timeout=timeout or context.timeout,
                    envs=envs,
                    on_stdout=on_stdout,
                    on_stderr=on_stderr,
                    on_result=on_result,
                    on_results=on_results,
                    on_error=on_error,
                )
            return self._code_context_manager().run_in_context(context, code, options=options)
        if is_python_language(language):
            return self._code_context_manager().run_default(code, options=options)
        return run_code_with_runtime(
            self._runtime(),
            code,
            language=language,
            cwd=cwd,
            timeout=timeout,
            envs=envs,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            on_result=on_result,
            on_results=on_results,
            on_error=on_error,
        )

    def create_code_context(
        self,
        *,
        cwd: str | None = None,
        language: str | None = None,
        timeout: int | None = None,
    ) -> CodeContext:
        if not is_python_language(language):
            context = CodeContext(
                cwd=cwd,
                language=_normalize_language(language),
                timeout=timeout,
            )
            self._stateless_code_contexts[context.context_id] = context
            return context
        return self._code_context_manager().create_context(
            cwd=cwd,
            language=language,
            timeout=timeout,
        )

    def list_code_contexts(self) -> list[CodeContext]:
        contexts = list(self._stateless_code_contexts.values())
        if self._code_contexts is not None:
            contexts.extend(self._code_contexts.list_contexts())
        return contexts

    def restart_code_context(self, context_or_id: str | CodeContext) -> CodeContext:
        context_id = context_or_id if isinstance(context_or_id, str) else context_or_id.context_id
        stateless = self._stateless_code_contexts.get(context_id)
        if stateless is not None:
            return stateless
        if self._code_contexts is None:
            raise NotFoundError(f"code context not found: {context_id}", status_code=404)
        return self._code_context_manager().restart_context(context_or_id)

    def remove_code_context(self, context_or_id: str | CodeContext) -> None:
        context_id = context_or_id if isinstance(context_or_id, str) else context_or_id.context_id
        if self._stateless_code_contexts.pop(context_id, None) is not None:
            return
        if self._code_contexts is None:
            raise NotFoundError(f"code context not found: {context_id}", status_code=404)
        self._code_context_manager().remove_context(context_or_id)

    def get_host(self, port: int) -> str:
        if port <= 0:
            raise ConfigurationError("port must be a positive integer")
        base_url = self._runtime().base_url.rstrip("/") + "/"
        return urljoin(base_url, f"proxy/{port}/")

    def proxy(self, request) -> Any:
        return self._runtime().proxy(request)

    def logs(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._client.get_sandbox_logs(self.sandbox_id, params)

    def pause(self) -> None:
        self._client.pause_sandbox(self.sandbox_id)

    def kill(self, sandbox_id: str | None = None) -> bool:
        try:
            if isinstance(self, Sandbox):
                self._stateless_code_contexts.clear()
                if self._code_contexts is not None:
                    self._code_contexts.close_all()
                    self._code_contexts = None
                self._client.delete_sandbox(self.sandbox_id)
                return True
            target_sandbox_id = str(self if sandbox_id is None else sandbox_id)
            _reject_high_level_gateway_options({})
            GatewayClient().delete_sandbox(target_sandbox_id)
            return True
        except NotFoundError:
            return False

    def delete(self) -> None:
        self.kill()

    def refresh(self, body: Mapping[str, Any] | None = None) -> None:
        self._client.refresh_sandbox(self.sandbox_id, body)

    def set_timeout(
        self,
        timeout_or_sandbox_id: int | str,
        maybe_timeout: int | None = None,
    ) -> None:
        if isinstance(self, Sandbox):
            self._client.set_sandbox_timeout(self.sandbox_id, {"timeout": int(timeout_or_sandbox_id)})
            return
        target_sandbox_id = str(self)
        resolved_timeout = int(timeout_or_sandbox_id if maybe_timeout is None else maybe_timeout)
        _reject_high_level_gateway_options({})
        GatewayClient().set_sandbox_timeout(target_sandbox_id, {"timeout": resolved_timeout})

    def is_running(self) -> bool:
        value = str(self._data.get("state") or self._data.get("status") or "").lower()
        return value not in {"paused", "stopped", "deleted"}

    def _runtime(self):
        envd_url = str(self._data.get("envdUrl") or "").strip()
        if not envd_url:
            raise ConfigurationError("envdUrl is required")
        return self._client.runtime_from_sandbox(self._data)

    def _code_context_manager(self) -> PythonCodeContextManager:
        if self._code_contexts is None:
            self._code_contexts = PythonCodeContextManager(self._runtime())
        return self._code_contexts


def _expect_start_frame(stream) -> dict[str, Any]:
    while True:
        frame = stream.next()
        if frame is None:
            raise ConfigurationError("process stream ended before start frame")
        event = frame.get("event", {})
        if "start" in event:
            return dict(event["start"])


def _build_git_execution(subcommand: str, args: list[str], user: str | None) -> tuple[str, list[str]]:
    git_args = [subcommand, *args]
    if not user:
        return "git", git_args
    return (
        "sh",
        [
            "-lc",
            f"su -s /bin/sh {_shell_quote(user)} -c {_shell_quote(_shell_join(['git', *git_args]))}",
        ],
    )


def _build_command_execution(command: str, args: Any, user: str | None) -> tuple[str, list[str]]:
    command_args = list(args or [])
    if not user:
        return command, command_args
    return (
        "sh",
        [
            "-lc",
            f"su -s /bin/sh {_shell_quote(user)} -c {_shell_quote(_shell_join([command, *command_args]))}",
        ],
    )
 

def _runtime_request_options(request_timeout: Any) -> CmdRequestOptions | None:
    if request_timeout is None:
        return None
    return CmdRequestOptions(request_timeout=float(request_timeout))


def _build_process_config(
    cmd: str,
    *,
    args: Any = None,
    envs: Any = None,
    cwd: Any = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {"cmd": cmd}
    if args is not None:
        config["args"] = args
    if envs is not None:
        config["envs"] = envs
    if cwd is not None:
        config["cwd"] = cwd
    return config


def _shell_join(values: list[str]) -> str:
    return " ".join(_shell_quote(value) for value in values)


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _normalize_write_info(path: str, bytes_written: int) -> dict[str, Any]:
    return {
        "path": path,
        "bytes_written": bytes_written,
    }


def _encode_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _encode_stream_data(data: str) -> str:
    return base64.b64encode(data.encode("utf-8")).decode("ascii")


def _decode_stream_data(data: Any) -> str:
    if not data:
        return ""
    return base64.b64decode(str(data)).decode("utf-8")


def _is_missing_process_error(error: Exception) -> bool:
    if isinstance(error, NotFoundError):
        return True
    if not isinstance(error, APIError):
        return False
    message = " ".join(str(part) for part in (error, error.detail, error.body) if part).lower()
    return "no such process" in message or "esrch" in message


def _normalize_connect_timeout(*, timeout: int | None) -> int:
    if timeout is None:
        return 300
    if timeout < 0:
        raise ConfigurationError("timeout must be a non-negative integer")
    return int(timeout)


def _reject_high_level_gateway_options(options: Mapping[str, Any]) -> None:
    for key in ("base_url", "api_key", "domain", "project_id"):
        if options.get(key) is not None:
            raise ConfigurationError(
                f"{key} is not supported on high-level Sandbox helpers; use E2B_DOMAIN/E2B_API_KEY env vars",
            )
