from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import io
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urljoin, urlparse

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

_SANDBOX_LIST_LIMIT_DEFAULT = 100
_SANDBOX_LIST_LIMIT_MAX = 100


class SandboxPaginator:
    def __init__(self, fetch_page, options: Mapping[str, Any] | None = None) -> None:
        self._fetch_page = fetch_page
        self._base_options = dict(options or {})
        self._offset = _decode_sandbox_list_next_token(self._base_options.get("next_token"))
        self._done = False

    def has_next_page(self) -> bool:
        return not self._done

    def next_items(self) -> list[dict[str, Any]]:
        if self._done:
            return []
        limit = _resolve_sandbox_list_limit(self._base_options.get("limit"))
        page_options = dict(self._base_options)
        if self._base_options.get("limit") is not None:
            page_options["limit"] = limit
        page_options["next_token"] = _encode_sandbox_list_next_token(self._offset)
        items = list(self._fetch_page(**page_options))
        self._offset += len(items)
        if len(items) < limit:
            self._done = True
        return items

    def get_next_page(self) -> list[dict[str, Any]]:
        return self.next_items()

    def __iter__(self):
        while self.has_next_page():
            items = self.next_items()
            if not items:
                break
            for item in items:
                yield item


@dataclass(slots=True)
class CommandHandle:
    runtime: Any
    stream: Any
    pid: int
    cmd_id: str | None = None
    pty: bool = False
    on_stdout: Callable[[str], None] | None = None
    on_stderr: Callable[[str], None] | None = None

    def send_stdin(self, data: str | bytes) -> None:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        self.runtime.send_input({
            "process": {"pid": self.pid},
            "input": {"pty": _encode_stream_data(text)} if self.pty else {"stdin": _encode_stream_data(text)},
        })

    def send_input(self, data: str | bytes) -> None:
        self.send_stdin(data)

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
                if stdout_chunk and self.on_stdout is not None:
                    self.on_stdout(stdout_chunk)
                if stderr_chunk and self.on_stderr is not None:
                    self.on_stderr(stderr_chunk)
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


class WatchHandle:
    def __init__(self, stop: Callable[[], None]) -> None:
        self._stop = stop

    def stop(self) -> None:
        self._stop()


class Commands:
    def __init__(self, runtime_factory) -> None:
        self._runtime_factory = runtime_factory

    def run(self, cmd: str, *, background: bool = False, **options: Any) -> dict[str, Any] | CommandHandle:
        _reject_unsupported_timeout_fields(options)
        runtime = self._runtime_factory()
        execution_cmd, execution_args = _build_command_execution(
            cmd,
            options.get("args"),
            options.get("user"),
        )
        request_options = _runtime_request_options(options.get("request_timeout_ms"))
        use_stream = background or options.get("on_stdout") is not None or options.get("on_stderr") is not None or isinstance(options.get("stdin"), bool)
        if use_stream:
            start_body = {
                "process": _build_process_config(
                    execution_cmd,
                    args=execution_args,
                    envs=options.get("envs"),
                    cwd=options.get("cwd"),
                ),
                "timeoutMs": _resolve_runtime_timeout_ms(timeout_ms=options.get("timeout_ms")),
                "stdin": options.get("stdin") is not False,
            }
            stream = runtime.start(start_body) if request_options is None else runtime.start(start_body, request_options)
            started = _expect_start_frame(stream)
            handle = CommandHandle(
                runtime=runtime,
                stream=stream,
                pid=started["pid"],
                cmd_id=started.get("cmdId"),
                on_stdout=options.get("on_stdout"),
                on_stderr=options.get("on_stderr"),
            )
            if isinstance(options.get("stdin"), (str, bytes)):
                handle.send_stdin(options["stdin"])
            if not background:
                result = handle.wait()
                return {
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "exit_code": result.get("exit_code", 0),
                    "duration_ms": 0,
                }
            return handle
        run_body = {
            "cmd": execution_cmd,
            "args": execution_args,
            "cwd": options.get("cwd"),
            "env": options.get("envs"),
            "timeoutMs": _resolve_runtime_timeout_ms(timeout_ms=options.get("timeout_ms")),
            "stdin": options.get("stdin") if isinstance(options.get("stdin"), str) else None,
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

    def connect(
        self,
        pid: int,
        options: CmdRequestOptions | None = None,
        *,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> CommandHandle:
        runtime = self._runtime_factory()
        stream = runtime.connect({"process": {"pid": pid}}, options)
        started = _expect_start_frame(stream)
        return CommandHandle(
            runtime=runtime,
            stream=stream,
            pid=started["pid"],
            cmd_id=started.get("cmdId"),
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )

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

    def exists(self, path: str, options: CmdRequestOptions | None = None, *, user: str | None = None) -> bool:
        try:
            self._runtime_factory().stat({"path": path}, _filesystem_request_options(options, user=user))
            return True
        except NotFoundError:
            return False

    def get_info(self, path: str, options: CmdRequestOptions | None = None, *, user: str | None = None) -> dict[str, Any]:
        return _normalize_entry_info(self._runtime_factory().stat({"path": path}, _filesystem_request_options(options, user=user))["entry"])

    def list(self, path: str, *, depth: int | None = None, options: CmdRequestOptions | None = None, user: str | None = None) -> list[dict[str, Any]]:
        return [_normalize_entry_info(entry) for entry in self._runtime_factory().list_dir({"path": path, "depth": depth}, _filesystem_request_options(options, user=user))["entries"]]

    def make_dir(self, path: str, options: CmdRequestOptions | None = None, *, user: str | None = None) -> bool:
        if self.exists(path, options, user=user):
            return False
        self._runtime_factory().make_dir({"path": path}, _filesystem_request_options(options, user=user))
        return True

    def read(
        self,
        path: str,
        options: CmdRequestOptions | None = None,
        *,
        format: str = "text",
        user: str | None = None,
    ) -> str | bytes | Any:
        response = self._runtime_factory().read_file(FileRequest(path=path), _filesystem_request_options(options, user=user))
        if format == "stream":
            return response
        with response:
            data = response.read()
        if format in {"bytes", "blob"}:
            return data
        return data.decode("utf-8")

    def write(
        self,
        path_or_files: str | list[dict[str, Any]],
        data_or_options: str | bytes | bytearray | io.BufferedIOBase | CmdRequestOptions | None = None,
        options: CmdRequestOptions | None = None,
        *,
        user: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        if not isinstance(path_or_files, str):
            request_options = data_or_options if isinstance(data_or_options, CmdRequestOptions) else options
            response = self._runtime_factory().write_batch({
                "files": [
                    {
                        "path": str(file["path"]),
                        "data": _encode_base64(_normalize_write_bytes(file.get("data", file.get("content")))),
                    }
                    for file in path_or_files
                ],
            }, _filesystem_request_options(request_options, user=user))
            return [_normalize_write_info(str(file["path"])) for file in response.get("files", [])]

        data = data_or_options
        raw = _normalize_write_bytes(data)
        self._runtime_factory().write_file(UploadBytesRequest(path=path_or_files, data=raw), _filesystem_request_options(options, user=user))
        return _normalize_write_info(path_or_files)

    def write_files(
        self,
        files: list[dict[str, Any]],
        options: CmdRequestOptions | None = None,
        *,
        user: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.write(files, options, user=user)  # type: ignore[return-value]

    def remove(self, path: str, options: CmdRequestOptions | None = None, *, user: str | None = None) -> None:
        self._runtime_factory().remove({"path": path}, _filesystem_request_options(options, user=user))

    def rename(self, old_path: str, new_path: str, options: CmdRequestOptions | None = None, *, user: str | None = None) -> dict[str, Any]:
        return _normalize_entry_info(self._runtime_factory().move({"source": old_path, "destination": new_path}, _filesystem_request_options(options, user=user))["entry"])

    def watch_dir(
        self,
        path: str,
        on_event: Callable[[dict[str, Any]], None],
        *,
        recursive: bool = False,
        options: CmdRequestOptions | None = None,
        timeout_ms: int | None = None,
        on_exit: Callable[[Exception | None], None] | None = None,
        user: str | None = None,
    ) -> WatchHandle:
        stream = self._runtime_factory().watch_dir({"path": path, "recursive": recursive}, _filesystem_request_options(options, user=user))
        stopped = threading.Event()
        timer: threading.Timer | None = None
        if timeout_ms is not None:
            if timeout_ms < 0:
                raise ConfigurationError("timeout_ms must be a non-negative number")
            if timeout_ms > 0:
                timer = threading.Timer(timeout_ms / 1000, stream.close)
                timer.daemon = True
                timer.start()

        def consume() -> None:
            exit_error: Exception | None = None
            try:
                while not stopped.is_set():
                    frame = stream.next()
                    if frame is None:
                        return
                    filesystem = frame.get("filesystem")
                    if filesystem is None:
                        continue
                    on_event(_normalize_filesystem_event(filesystem))
            except Exception as exc:
                exit_error = exc
            finally:
                if timer is not None:
                    timer.cancel()
                if not stopped.is_set() and on_exit is not None:
                    on_exit(exit_error)

        worker = threading.Thread(target=consume, daemon=True)
        worker.start()

        def stop() -> None:
            if stopped.is_set():
                return
            stopped.set()
            stream.close()
            worker.join(timeout=1.0)

        return WatchHandle(stop)


class Pty:
    def __init__(self, runtime_factory) -> None:
        self._runtime_factory = runtime_factory

    def create(self, command: str, **options: Any) -> CommandHandle:
        _reject_unsupported_timeout_fields(options)
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
            "timeoutMs": _resolve_runtime_timeout_ms(timeout_ms=options.get("timeout_ms")),
            "stdin": True,
            "pty": {"size": options.get("size") or {"cols": 80, "rows": 24}},
        }
        request_options = _runtime_request_options(options.get("request_timeout_ms"))
        stream = runtime.start(start_body) if request_options is None else runtime.start(start_body, request_options)
        started = _expect_start_frame(stream)
        return CommandHandle(runtime=runtime, stream=stream, pid=started["pid"], cmd_id=started.get("cmdId"), pty=True)

    def connect(
        self,
        pid: int,
        options: CmdRequestOptions | None = None,
        *,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> CommandHandle:
        runtime = self._runtime_factory()
        stream = runtime.connect({"process": {"pid": pid}}, options)
        started = _expect_start_frame(stream)
        return CommandHandle(
            runtime=runtime,
            stream=stream,
            pid=started["pid"],
            cmd_id=started.get("cmdId"),
            pty=True,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )

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

    def send_input(self, pid: int, data: str | bytes, options: CmdRequestOptions | None = None) -> None:
        self.send_stdin(pid, data, options)

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
        timeout_ms: int | None = None,
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
        return self._run("clone", args, cwd=cwd, envs=envs, timeout_ms=timeout_ms, user=user)

    def pull(
        self,
        path: str | None = None,
        *,
        cwd: str | None = None,
        envs: Mapping[str, str] | None = None,
        timeout_ms: int | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        return self._run("pull", [], cwd=path or cwd, envs=envs, timeout_ms=timeout_ms, user=user)

    def checkout(
        self,
        ref: str,
        path: str | None = None,
        *,
        cwd: str | None = None,
        envs: Mapping[str, str] | None = None,
        timeout_ms: int | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        return self._run("checkout", [ref], cwd=path or cwd, envs=envs, timeout_ms=timeout_ms, user=user)

    def status(
        self,
        path: str | None = None,
        *,
        cwd: str | None = None,
        envs: Mapping[str, str] | None = None,
        timeout_ms: int | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        return self._run("status", [], cwd=path or cwd, envs=envs, timeout_ms=timeout_ms, user=user)

    def _run(
        self,
        subcommand: str,
        args: list[str],
        *,
        cwd: str | None,
        envs: Mapping[str, str] | None,
        timeout_ms: int | None,
        user: str | None,
    ) -> dict[str, Any]:
        command, command_args = _build_git_execution(subcommand, args, user)
        return self._commands.run(
            command,
            args=command_args,
            cwd=cwd,
            envs=dict(envs) if envs is not None else None,
            timeout_ms=timeout_ms,
        )


class Sandbox:
    @classmethod
    def create(
        cls,
        template_or_options: str | Mapping[str, Any],
        **options: Any,
    ) -> "Sandbox":
        _reject_high_level_gateway_options(options)
        return GatewayClient().create(template_or_options, **options)

    @classmethod
    def list(cls, **options: Any) -> SandboxPaginator:
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
        token = self._data.get("envdAccessToken")
        return None if token is None else str(token)

    @property
    def raw(self) -> dict[str, Any]:
        return dict(self._data)

    def reload(self, *, request_timeout_ms: int | None = None) -> "Sandbox":
        self._data = dict(self._client.get_sandbox(self.sandbox_id, **_request_timeout_kwargs(request_timeout_ms)))
        return self

    def connect(
        self,
        sandbox_id: str | None = None,
        *,
        timeout: int | None = None,
        request_timeout_ms: int | None = None,
    ) -> "Sandbox":
        if isinstance(self, Sandbox):
            response = self._client.connect_sandbox(
                self.sandbox_id,
                {"timeout": _normalize_connect_timeout_seconds(timeout=timeout)},
                **_request_timeout_kwargs(request_timeout_ms),
            )
            self._data = dict(response.sandbox)
            return self
        target_sandbox_id = str(self if sandbox_id is None else sandbox_id)
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        _reject_high_level_gateway_options(kwargs)
        client_kwargs: dict[str, Any] = {}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        if request_timeout_ms is not None:
            client_kwargs["request_timeout_ms"] = request_timeout_ms
        return GatewayClient().connect(target_sandbox_id, **client_kwargs)

    def resume(self, *, timeout: int | None = None, request_timeout_ms: int | None = None) -> "Sandbox":
        return self.connect(timeout=timeout, request_timeout_ms=request_timeout_ms)

    def get_info(self, sandbox_id: str | None = None, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        if isinstance(self, Sandbox):
            detail = self._client.get_sandbox(self.sandbox_id, **_request_timeout_kwargs(request_timeout_ms))
            self._data = dict(detail)
            return _normalize_sandbox_info(detail)
        target_sandbox_id = str(self if sandbox_id is None else sandbox_id)
        _reject_high_level_gateway_options({})
        return _normalize_sandbox_info(GatewayClient().get_sandbox(target_sandbox_id, **_request_timeout_kwargs(request_timeout_ms)))

    def get_full_info(self, sandbox_id: str | None = None, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        if isinstance(self, Sandbox):
            detail = self._client.get_sandbox(self.sandbox_id, **_request_timeout_kwargs(request_timeout_ms))
            self._data = dict(detail)
            return _normalize_sandbox_info(detail)
        target_sandbox_id = str(self if sandbox_id is None else sandbox_id)
        _reject_high_level_gateway_options({})
        return _normalize_sandbox_info(GatewayClient().get_sandbox(target_sandbox_id, **_request_timeout_kwargs(request_timeout_ms)))

    def get_metrics(self) -> dict[str, Any]:
        return self._runtime().metrics()

    def run_code(
        self,
        code: str,
        *,
        language: str | None = None,
        cwd: str | None = None,
        timeout_ms: int | None = None,
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
            "timeout_ms": timeout_ms,
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
                    timeout_ms=timeout_ms or context.timeout_ms,
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
            timeout_ms=timeout_ms,
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
        timeout_ms: int | None = None,
    ) -> CodeContext:
        if not is_python_language(language):
            context = CodeContext(
                cwd=cwd,
                language=_normalize_language(language),
                timeout_ms=timeout_ms,
            )
            self._stateless_code_contexts[context.context_id] = context
            return context
        return self._code_context_manager().create_context(
            cwd=cwd,
            language=language,
            timeout_ms=timeout_ms,
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

    def download_url(
        self,
        path: str,
        *,
        user: str | None = None,
        use_signature_expiration: int | None = None,
    ) -> str:
        return _build_sandbox_file_url(
            str(self._data.get("envdUrl") or ""),
            str(self._data.get("envdAccessToken") or ""),
            path,
            "read",
            user=user,
            use_signature_expiration=use_signature_expiration,
        )

    def upload_url(
        self,
        path: str | None = None,
        *,
        user: str | None = None,
        use_signature_expiration: int | None = None,
    ) -> str:
        return _build_sandbox_file_url(
            str(self._data.get("envdUrl") or ""),
            str(self._data.get("envdAccessToken") or ""),
            path,
            "write",
            user=user,
            use_signature_expiration=use_signature_expiration,
        )

    def proxy(self, request) -> Any:
        return self._runtime().proxy(request)

    def logs(self, params: Mapping[str, Any] | None = None, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        return self._client.get_sandbox_logs(self.sandbox_id, params, **_request_timeout_kwargs(request_timeout_ms))

    def pause(self, sandbox_id: str | None = None, *, request_timeout_ms: int | None = None) -> bool:
        if isinstance(self, Sandbox):
            if _is_paused_sandbox_state(self._data):
                return False
            self._client.pause_sandbox(self.sandbox_id, **_request_timeout_kwargs(request_timeout_ms))
            self._data["state"] = "paused"
            self._data["status"] = "paused"
            return True
        target_sandbox_id = str(self if sandbox_id is None else sandbox_id)
        client = GatewayClient()
        if _is_paused_sandbox_state(client.get_sandbox(target_sandbox_id, **_request_timeout_kwargs(request_timeout_ms))):
            return False
        client.pause_sandbox(target_sandbox_id, **_request_timeout_kwargs(request_timeout_ms))
        return True

    def kill(self, sandbox_id: str | None = None, *, request_timeout_ms: int | None = None) -> bool:
        try:
            if isinstance(self, Sandbox):
                self._stateless_code_contexts.clear()
                if self._code_contexts is not None:
                    self._code_contexts.close_all()
                    self._code_contexts = None
                self._client.delete_sandbox(self.sandbox_id, **_request_timeout_kwargs(request_timeout_ms))
                return True
            target_sandbox_id = str(self if sandbox_id is None else sandbox_id)
            _reject_high_level_gateway_options({})
            GatewayClient().delete_sandbox(target_sandbox_id, **_request_timeout_kwargs(request_timeout_ms))
            return True
        except NotFoundError:
            return False

    def delete(self, *, request_timeout_ms: int | None = None) -> None:
        self.kill(request_timeout_ms=request_timeout_ms)

    def refresh(self, body: Mapping[str, Any] | None = None, *, request_timeout_ms: int | None = None) -> None:
        self._client.refresh_sandbox(self.sandbox_id, body, **_request_timeout_kwargs(request_timeout_ms))

    def set_timeout(
        self,
        timeout_or_sandbox_id: int | str,
        maybe_timeout: int | None = None,
        *,
        request_timeout_ms: int | None = None,
    ) -> None:
        if isinstance(self, Sandbox):
            resolved_timeout = int(timeout_or_sandbox_id)
            self._client.set_sandbox_timeout(
                self.sandbox_id,
                {"timeout": _normalize_timeout_seconds(timeout=resolved_timeout, allow_zero=True)},
                **_request_timeout_kwargs(request_timeout_ms),
            )
            return
        target_sandbox_id = str(self)
        resolved_timeout = int(timeout_or_sandbox_id if maybe_timeout is None else maybe_timeout)
        _reject_high_level_gateway_options({})
        GatewayClient().set_sandbox_timeout(
            target_sandbox_id,
            {"timeout": _normalize_timeout_seconds(timeout=resolved_timeout, allow_zero=True)},
            **_request_timeout_kwargs(request_timeout_ms),
        )

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
 

def _runtime_request_options(request_timeout_ms: Any) -> CmdRequestOptions | None:
    if request_timeout_ms is None:
        return None
    return CmdRequestOptions(request_timeout_ms=int(request_timeout_ms))


def _filesystem_request_options(options: CmdRequestOptions | None, *, user: str | None = None) -> CmdRequestOptions | None:
    if not user or not str(user).strip():
        return options
    if options is None:
        return CmdRequestOptions(username=str(user).strip())
    return CmdRequestOptions(
        username=str(user).strip(),
        signature=options.signature,
        signature_expiration=options.signature_expiration,
        range=options.range,
        request_timeout_ms=options.request_timeout_ms,
        headers=dict(options.headers),
    )


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


def _normalize_write_info(path: str) -> dict[str, Any]:
    name = path.rstrip("/").rsplit("/", 1)[-1] if path else ""
    return {
        "name": name,
        "path": path,
        "type": "file",
    }


def _encode_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _normalize_entry_info(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(entry.get("name") or ""),
        "type": _normalize_file_type(entry.get("type")),
        "path": str(entry.get("path") or ""),
        "size": int(entry.get("size") or 0),
        "mode": int(entry.get("mode") or 0),
        "permissions": str(entry.get("permissions") or ""),
        "owner": str(entry.get("owner") or ""),
        "group": str(entry.get("group") or ""),
        "modified_time": _parse_datetime(entry.get("modifiedTime")),
        "symlink_target": str(entry.get("symlinkTarget") or "") or None,
    }


def _normalize_filesystem_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(event.get("name") or ""),
        "type": _normalize_filesystem_event_type(event.get("type")),
    }


def _normalize_file_type(value: Any) -> str:
    raw = str(value or "").strip()
    if raw == "FILE_TYPE_DIRECTORY":
        return "dir"
    if raw == "FILE_TYPE_SYMLINK":
        return "symlink"
    return "file"


def _normalize_filesystem_event_type(value: Any) -> str:
    raw = str(value or "").strip()
    if raw == "EVENT_TYPE_CREATE":
        return "create"
    if raw == "EVENT_TYPE_REMOVE":
        return "remove"
    if raw == "EVENT_TYPE_RENAME":
        return "rename"
    if raw == "EVENT_TYPE_CHMOD":
        return "chmod"
    return "write"


def _normalize_write_bytes(data: Any) -> bytes:
    if data is None:
        raise ConfigurationError("data is required")
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    if hasattr(data, "read"):
        payload = data.read()
        if isinstance(payload, str):
            return payload.encode("utf-8")
        if isinstance(payload, bytearray):
            return bytes(payload)
        if isinstance(payload, bytes):
            return payload
    raise ConfigurationError("data must be str, bytes, bytearray, or a readable binary stream")


def _normalize_sandbox_info(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sandbox_id": str(data.get("sandboxID") or ""),
        "template_id": str(data.get("templateID") or "") or None,
        "sandbox_domain": _sandbox_domain_from_envd_url(data.get("envdUrl")),
        "started_at": _parse_datetime(data.get("startedAt")),
        "end_at": _parse_datetime(data.get("endAt")),
        "state": str(data.get("state") or data.get("status") or "").lower(),
        "metadata": data.get("metadata") or None,
        "name": str(data.get("alias") or "") or None,
        "cpu_count": data.get("cpuCount"),
        "memory_mb": data.get("memoryMB"),
        "envd_access_token": str(data.get("envdAccessToken") or "") or None,
    }


def _sandbox_domain_from_envd_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return urlparse(raw).netloc or None


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _build_sandbox_file_url(
    runtime_base_url: str,
    access_token: str,
    path: str | None,
    operation: str,
    *,
    user: str | None,
    use_signature_expiration: int | None,
) -> str:
    base_url = urljoin(runtime_base_url.rstrip("/") + "/", "files")
    query: dict[str, str] = {}
    normalized_path = str(path or "").strip()
    if normalized_path:
        query["path"] = normalized_path
    username = str(user or "").strip()
    if username:
        query["username"] = username
    expiration = _normalize_signature_expiration(use_signature_expiration)
    secret = access_token.strip()
    if secret:
        if expiration is not None:
            query["signature_expiration"] = str(expiration)
        query["signature"] = _sign_sandbox_file_url(normalized_path, operation, username, secret, expiration)
    suffix = urlencode(query)
    return base_url if not suffix else f"{base_url}?{suffix}"


def _normalize_signature_expiration(value: int | None) -> int | None:
    if value is None:
        return None
    expiration = int(value)
    if expiration <= 0:
        raise ConfigurationError("use_signature_expiration must be a positive integer")
    return expiration


def _sign_sandbox_file_url(
    path: str,
    operation: str,
    username: str,
    secret: str,
    expiration: int | None,
) -> str:
    raw = (
        f"{path}:{operation}:{username}:{secret}"
        if expiration is None
        else f"{path}:{operation}:{username}:{secret}:{expiration}"
    )
    digest = base64.b64encode(hashlib.sha256(raw.encode("utf-8")).digest()).decode("ascii").rstrip("=")
    return f"v1_{digest}"


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


def _is_paused_sandbox_state(data: Mapping[str, Any]) -> bool:
    return str(data.get("state") or data.get("status") or "").lower() == "paused"


def _normalize_timeout_seconds(
    *,
    timeout: Any = None,
    allow_zero: bool = False,
) -> int | None:
    if timeout is None:
        return None
    timeout_value = int(timeout)
    if timeout_value < 0 or (timeout_value == 0 and not allow_zero):
        message = "timeout must be a non-negative integer" if allow_zero else "timeout must be a positive integer"
        raise ConfigurationError(message)
    return timeout_value


def _normalize_connect_timeout_seconds(*, timeout: int | None) -> int:
    if timeout is None:
        return 300
    return _normalize_timeout_seconds(timeout=timeout, allow_zero=True) or 0


def _resolve_runtime_timeout_ms(*, timeout_ms: Any = None) -> int | None:
    return _normalize_timeout_seconds(timeout=timeout_ms, allow_zero=False)


def _reject_high_level_gateway_options(options: Mapping[str, Any]) -> None:
    for key in ("base_url", "api_key", "domain", "project_id"):
        if options.get(key) is not None:
            raise ConfigurationError(
                f"{key} is not supported on high-level Sandbox helpers; use SEACLOUD_BASE_URL/SEACLOUD_API_KEY env vars",
            )


def _reject_unsupported_timeout_fields(options: Mapping[str, Any]) -> None:
    if options.get("timeout") is not None:
        raise ConfigurationError("timeout is not supported; use timeout_ms")


def _request_timeout_kwargs(request_timeout_ms: int | None) -> dict[str, int]:
    return {} if request_timeout_ms is None else {"request_timeout_ms": int(request_timeout_ms)}


def _resolve_sandbox_list_limit(limit: Any) -> int:
    if limit is None:
        return _SANDBOX_LIST_LIMIT_DEFAULT
    value = int(limit)
    if value <= 0 or value > _SANDBOX_LIST_LIMIT_MAX:
        raise ConfigurationError(f"limit must be an integer between 1 and {_SANDBOX_LIST_LIMIT_MAX}")
    return value


def _encode_sandbox_list_next_token(offset: int) -> str | None:
    if offset <= 0:
        return None
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("ascii").rstrip("=")


def _decode_sandbox_list_next_token(next_token: Any) -> int:
    token = str(next_token or "").strip()
    if not token:
        return 0
    padding = "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode((token + padding).encode("ascii")).decode("utf-8")
        value = int(decoded)
    except Exception as exc:
        raise ConfigurationError("next_token must be a valid sandbox list cursor") from exc
    if value < 0:
        raise ConfigurationError("next_token must be a valid sandbox list cursor")
    return value
