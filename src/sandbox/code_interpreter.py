from __future__ import annotations

import base64
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping

from .cmd import FileRequest, UploadBytesRequest
from .core.exceptions import APIError, ConfigurationError, NotFoundError


@dataclass(slots=True)
class CodeOutputChunk:
    error: bool
    line: str
    timestamp: int


@dataclass(slots=True)
class CodeExecutionError:
    message: str
    name: str | None = None
    traceback: str | None = None


@dataclass(slots=True)
class CodeExecutionResult:
    text: str | None = None
    png: str | None = None
    chart: dict[str, Any] | None = None
    json: Any = None


@dataclass(slots=True)
class CodeExecutionLogs:
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CodeExecution:
    results: list[CodeExecutionResult] = field(default_factory=list)
    logs: CodeExecutionLogs = field(default_factory=CodeExecutionLogs)
    error: CodeExecutionError | None = None
    execution_count: int = 1

    @property
    def executionCount(self) -> int:
        return self.execution_count

    @property
    def text(self) -> str:
        result_text = [item.text for item in self.results if item.text]
        if result_text:
            return "\n".join(result_text)
        return "".join([*self.logs.stdout, *self.logs.stderr])

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [asdict(item) for item in self.results],
            "logs": {
                "stdout": list(self.logs.stdout),
                "stderr": list(self.logs.stderr),
            },
            "error": None if self.error is None else asdict(self.error),
            "executionCount": self.execution_count,
        }


@dataclass(slots=True)
class CodeContext:
    context_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cwd: str | None = None
    language: str = "python"
    timeout: int | None = None

    @property
    def contextId(self) -> str:
        return self.context_id


CODE_FILE_BASE = "/root/workspace/.seacloud-code-interpreter"
CONTEXT_FILE_BASE = "/root/workspace/.seacloud-code-context"
CONTEXT_PAYLOAD_PREFIX = "__SEACLOUD_CODE_CONTEXT__"


def run_code_with_runtime(
    runtime: Any,
    code: str,
    *,
    language: str | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    envs: Mapping[str, str] | None = None,
    on_stdout: Callable[[CodeOutputChunk], None] | None = None,
    on_stderr: Callable[[CodeOutputChunk], None] | None = None,
    on_result: Callable[[CodeExecutionResult], None] | None = None,
    on_results: Callable[[CodeExecutionResult], None] | None = None,
    on_error: Callable[[CodeExecutionError], None] | None = None,
) -> CodeExecution:
    if not code.strip():
        raise ConfigurationError("code is required")

    spec = _language_spec(language)
    request_id = str(uuid.uuid4())
    script_path = f"{CODE_FILE_BASE}-{request_id}{spec['extension']}"
    result_path = f"{CODE_FILE_BASE}-{request_id}.result.json"

    runtime.write_file(UploadBytesRequest(
        path=script_path,
        data=spec["build_source"](code, result_path).encode("utf-8"),
    ))

    process = {
        "cmd": spec["command"],
        "args": spec["args"](script_path),
    }
    if envs is not None:
        process["envs"] = dict(envs)
    if cwd is not None:
        process["cwd"] = cwd

    stream = runtime.start({
        "process": process,
        "timeout": timeout,
    })

    cmd_id = ""
    streamed_stdout: list[str] = []
    streamed_stderr: list[str] = []
    end_event: dict[str, Any] = {}
    result_callback = on_result or on_results

    try:
        while True:
            frame = stream.next()
            if frame is None:
                break
            event = frame.get("event", {})
            if "start" in event:
                cmd_id = str(event["start"].get("cmdId") or "")
                continue
            if "data" in event:
                now = time.time_ns() // 1000
                stdout_chunk = _decode_stream_data(event["data"].get("stdout"))
                stderr_chunk = _decode_stream_data(event["data"].get("stderr"))
                if stdout_chunk:
                    streamed_stdout.append(stdout_chunk)
                    if on_stdout is not None:
                        on_stdout(CodeOutputChunk(error=False, line=stdout_chunk, timestamp=now))
                if stderr_chunk:
                    streamed_stderr.append(stderr_chunk)
                    if on_stderr is not None:
                        on_stderr(CodeOutputChunk(error=True, line=stderr_chunk, timestamp=now))
            if "end" in event:
                end_event = dict(event["end"])
                break

        if cmd_id and spec["result_file"]:
            result = _get_result_with_retry(runtime, cmd_id)
        else:
            result = {
                "stdout": "".join(streamed_stdout),
                "stderr": "".join(streamed_stderr),
                "exit_code": _exit_code_from_end_event(end_event),
                "error": end_event.get("error"),
            }

        payload = _read_result_payload(runtime, result_path) if spec["result_file"] else {
            "results": [],
            "error": None,
        }
        error = payload["error"] or _build_execution_error(
            result.get("exit_code"),
            str(result.get("stderr") or ""),
            result.get("error"),
        )
        if error is not None and on_error is not None:
            on_error(error)
        for item in payload["results"]:
            if result_callback is not None:
                result_callback(item)

        return CodeExecution(
            results=payload["results"],
            logs=CodeExecutionLogs(
                stdout=_split_log_lines(str(result.get("stdout") or "".join(streamed_stdout))),
                stderr=_split_log_lines(str(result.get("stderr") or "".join(streamed_stderr))),
            ),
            error=error,
            execution_count=1,
        )
    finally:
        try:
            runtime.remove({"path": script_path})
        except Exception:
            pass
        try:
            runtime.remove({"path": result_path})
        except Exception:
            pass
        close = getattr(stream, "close", None)
        if callable(close):
            close()


class PythonCodeContextManager:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._contexts: dict[str, _PythonCodeContextSession] = {}
        self._default_context: _PythonCodeContextSession | None = None

    def run_default(self, code: str, *, options: Mapping[str, Any]) -> CodeExecution:
        if self._default_context is None:
            self._default_context = _PythonCodeContextSession(
                self._runtime,
                CodeContext(
                    context_id="default",
                    cwd=options.get("cwd"),
                    language=options.get("language") or "python",
                    timeout=options.get("timeout"),
                ),
                default_context=True,
            )
        return self._default_context.execute(code, options=options)

    def create_context(
        self,
        *,
        cwd: str | None = None,
        language: str | None = None,
        timeout: int | None = None,
    ) -> CodeContext:
        context = CodeContext(
            cwd=cwd,
            language=_normalize_language(language),
            timeout=timeout,
        )
        if not is_python_language(context.language):
            raise ConfigurationError("code contexts currently support python only")
        session = _PythonCodeContextSession(self._runtime, context)
        self._contexts[context.context_id] = session
        session.ensure_started()
        return session.context

    def list_contexts(self) -> list[CodeContext]:
        return [session.context for session in self._contexts.values()]

    def restart_context(self, context_or_id: str | CodeContext) -> CodeContext:
        session = self._resolve_context(context_or_id)
        session.restart()
        return session.context

    def remove_context(self, context_or_id: str | CodeContext) -> None:
        session = self._resolve_context(context_or_id)
        self._contexts.pop(session.context.context_id, None)
        session.close()

    def run_in_context(self, context: CodeContext, code: str, *, options: Mapping[str, Any]) -> CodeExecution:
        return self._resolve_context(context).execute(code, options=options)

    def close_all(self) -> None:
        contexts = list(self._contexts.values())
        self._contexts.clear()
        for session in contexts:
            session.close()
        if self._default_context is not None:
            session = self._default_context
            self._default_context = None
            session.close()

    def _resolve_context(self, context_or_id: str | CodeContext) -> "_PythonCodeContextSession":
        context_id = context_or_id if isinstance(context_or_id, str) else context_or_id.context_id
        session = self._contexts.get(context_id)
        if session is None:
            raise NotFoundError(f"code context not found: {context_id}", status_code=404)
        return session


class _PythonCodeContextSession:
    def __init__(self, runtime: Any, context: CodeContext, *, default_context: bool = False) -> None:
        self._runtime = runtime
        self.context = context
        self._default_context = default_context
        self._script_path = ""
        self._pid = 0
        self._stream = None
        self._buffer = ""
        self._closed = False

    def ensure_started(self) -> None:
        if self._stream is not None:
            return
        self._start()

    def execute(self, code: str, *, options: Mapping[str, Any]) -> CodeExecution:
        if not code.strip():
            raise ConfigurationError("code is required")
        self.ensure_started()
        language = _normalize_language(options.get("language") or self.context.language)
        if not is_python_language(language):
            raise ConfigurationError("code contexts currently support python only")

        self._runtime.send_input({
            "process": {"pid": self._pid},
            "input": {"stdin": _encode_stream_data(json.dumps({
                "code": base64.b64encode(code.encode("utf-8")).decode("ascii"),
                "cwd": options.get("cwd") or self.context.cwd,
                "timeout": options.get("timeout") or self.context.timeout,
            }) + "\n")},
        })

        payload = self._read_execution_payload()
        execution = _code_execution_from_payload(payload)
        _emit_callbacks(
            execution,
            on_stdout=options.get("on_stdout"),
            on_stderr=options.get("on_stderr"),
            on_result=options.get("on_result") or options.get("on_results"),
            on_error=options.get("on_error"),
        )
        return execution

    def restart(self) -> None:
        self.close()
        self._closed = False
        self._start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._pid:
                self._runtime.send_signal({"process": {"pid": self._pid}, "signal": "SIGNAL_SIGKILL"})
        except Exception as exc:
            if not _is_missing_process_error(exc):
                raise
        finally:
            if self._stream is not None:
                close = getattr(self._stream, "close", None)
                if callable(close):
                    close()
            if self._script_path:
                try:
                    self._runtime.remove({"path": self._script_path})
                except Exception:
                    pass
            self._script_path = ""
            self._pid = 0
            self._stream = None

    def _start(self) -> None:
        context_name = "default" if self._default_context else self.context.context_id
        self._script_path = f"{CONTEXT_FILE_BASE}-{context_name}.py"
        self._runtime.write_file(UploadBytesRequest(
            path=self._script_path,
            data=_build_python_context_server().encode("utf-8"),
        ))
        process = {
            "cmd": "python3",
            "args": ["-u", self._script_path],
        }
        if self.context.cwd is not None:
            process["cwd"] = self.context.cwd

        self._stream = self._runtime.start({
            "process": process,
            "stdin": True,
            "timeout": self.context.timeout,
        })
        while True:
            frame = self._stream.next()
            if frame is None:
                raise ConfigurationError("code context stream ended before start frame")
            event = frame.get("event", {})
            if "start" in event:
                self._pid = int(event["start"]["pid"])
                return

    def _read_execution_payload(self) -> dict[str, Any]:
        while True:
            newline = self._buffer.find("\n")
            if newline >= 0:
                line = self._buffer[:newline]
                self._buffer = self._buffer[newline + 1:]
                if line.startswith(CONTEXT_PAYLOAD_PREFIX):
                    return json.loads(line[len(CONTEXT_PAYLOAD_PREFIX):])
                continue

            frame = self._stream.next()
            if frame is None:
                raise APIError("code context stream closed")
            event = frame.get("event", {})
            if "data" in event:
                self._buffer += _decode_stream_data(event["data"].get("stdout"))
                stderr = _decode_stream_data(event["data"].get("stderr"))
                if stderr.strip():
                    raise APIError(_first_non_empty_line(stderr) or "code context execution failed")
            if "end" in event:
                raise APIError("code context stream closed")


def _language_spec(language: str | None) -> dict[str, Any]:
    normalized = _normalize_language(language)
    if normalized in {"python", "py"}:
        return {
            "extension": ".py",
            "command": "python3",
            "args": lambda script_path: ["-u", script_path],
            "build_source": _build_python_wrapper,
            "result_file": True,
        }
    if normalized in {"javascript", "js"}:
        return {
            "extension": ".mjs",
            "command": "node",
            "args": lambda script_path: [script_path],
            "build_source": lambda code, _result_path: code,
            "result_file": False,
        }
    if normalized in {"typescript", "ts"}:
        return {
            "extension": ".ts",
            "command": "tsx",
            "args": lambda script_path: [script_path],
            "build_source": lambda code, _result_path: code,
            "result_file": False,
        }
    if normalized in {"bash", "sh"}:
        return {
            "extension": ".sh",
            "command": "bash",
            "args": lambda script_path: [script_path],
            "build_source": lambda code, _result_path: code,
            "result_file": False,
        }
    if normalized == "r":
        return {
            "extension": ".R",
            "command": "Rscript",
            "args": lambda script_path: [script_path],
            "build_source": lambda code, _result_path: code,
            "result_file": False,
        }
    if normalized == "java":
        return {
            "extension": ".jsh",
            "command": "jshell",
            "args": lambda script_path: ["--execution", "local", script_path],
            "build_source": lambda code, _result_path: code,
            "result_file": False,
        }
    raise ConfigurationError(f"unsupported code language: {language}")


def _normalize_language(language: str | None) -> str:
    return (language or "python").strip().lower()


def is_python_language(language: str | None) -> bool:
    normalized = _normalize_language(language)
    return normalized in {"python", "py"}


def _build_execution_error(exit_code: int | None, stderr: str, runtime_error: Any) -> CodeExecutionError | None:
    if (exit_code or 0) == 0 and not runtime_error:
        return None
    message = _first_non_empty_line(str(runtime_error or stderr)) or f"code execution failed with exit code {exit_code or 1}"
    return CodeExecutionError(message=message)


def _get_result_with_retry(runtime: Any, cmd_id: str, *, attempts: int = 40, delay_seconds: float = 0.05) -> dict[str, Any]:
    last_error: NotFoundError | None = None
    for attempt in range(attempts):
        try:
            return runtime.get_result({"cmdId": cmd_id})
        except NotFoundError as exc:
            message = str(exc).lower()
            if "process not found" not in message and "not finished" not in message:
                raise
            last_error = exc
            if attempt == attempts - 1:
                raise
            time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error
    raise ConfigurationError("cmdId is required")


def _exit_code_from_end_event(end_event: Mapping[str, Any]) -> int:
    status = str(end_event.get("status") or "")
    match = re.search(r"exit status (\d+)", status, re.IGNORECASE)
    if match is not None:
        return int(match.group(1))
    if end_event.get("error"):
        return 1
    if end_event.get("exited") is False:
        return 1
    return 0


def _read_result_payload(runtime: Any, path: str) -> dict[str, Any]:
    try:
        with runtime.read_file(FileRequest(path=path)) as response:
            body = response.read().decode("utf-8")
        if not body.strip():
            return {"results": [], "error": None}
        parsed = json.loads(body)
        results = [CodeExecutionResult(**item) for item in parsed.get("results", []) if isinstance(item, dict)]
        error = parsed.get("error")
        return {
            "results": results,
            "error": CodeExecutionError(**error) if isinstance(error, dict) else None,
        }
    except Exception:
        return {"results": [], "error": None}


def _split_log_lines(value: str) -> list[str]:
    if not value:
        return []
    lines = value.splitlines(keepends=True)
    if lines:
        return lines
    return [value]


def _first_non_empty_line(value: str) -> str:
    for line in value.splitlines():
        trimmed = line.strip()
        if trimmed:
            return trimmed
    return ""


def _decode_stream_data(value: Any) -> str:
    if not value:
        return ""
    return base64.b64decode(str(value)).decode("utf-8")


def _encode_stream_data(data: str) -> str:
    return base64.b64encode(data.encode("utf-8")).decode("ascii")


def _is_missing_process_error(error: Exception) -> bool:
    if isinstance(error, NotFoundError):
        return True
    if not isinstance(error, APIError):
        return False
    message = " ".join(str(part) for part in (error, error.detail, error.body) if part).lower()
    return "no such process" in message or "esrch" in message


def _emit_callbacks(
    execution: CodeExecution,
    *,
    on_stdout=None,
    on_stderr=None,
    on_result=None,
    on_error=None,
) -> None:
    timestamp = time.time_ns() // 1000
    for line in execution.logs.stdout:
        if on_stdout is not None:
            on_stdout(CodeOutputChunk(error=False, line=line, timestamp=timestamp))
        timestamp += 1
    for line in execution.logs.stderr:
        if on_stderr is not None:
            on_stderr(CodeOutputChunk(error=True, line=line, timestamp=timestamp))
        timestamp += 1
    for item in execution.results:
        if on_result is not None:
            on_result(item)
    if execution.error is not None and on_error is not None:
        on_error(execution.error)


def _code_execution_from_payload(payload: Mapping[str, Any]) -> CodeExecution:
    return CodeExecution(
        results=[
            CodeExecutionResult(**item)
            for item in payload.get("results", [])
            if isinstance(item, dict)
        ],
        logs=CodeExecutionLogs(
            stdout=list(payload.get("logs", {}).get("stdout", []) or []),
            stderr=list(payload.get("logs", {}).get("stderr", []) or []),
        ),
        error=CodeExecutionError(**payload["error"]) if isinstance(payload.get("error"), dict) else None,
        execution_count=int(payload.get("executionCount") or 1),
    )


def _build_python_wrapper(code: str, result_path: str) -> str:
    encoded_code = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return f"""import ast
import base64
import io
import json
import os
import traceback

os.environ.setdefault("MPLBACKEND", "Agg")

RESULT_PATH = {result_path!r}
USER_CODE = base64.b64decode({encoded_code!r}).decode("utf-8")
payload = {{"results": [], "error": None}}


def _write_payload():
    with open(RESULT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


{_python_result_helpers()}

namespace = {{"__name__": "__main__", "display": display}}
_install_matplotlib_hook()

try:
    tree = ast.parse(USER_CODE, filename="<seacloud-code>", mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_value = tree.body[-1].value
        tree.body[-1] = ast.Assign(
            targets=[ast.Name(id="__seacloud_last_result", ctx=ast.Store())],
            value=last_value,
        )
        tree.body.append(
            ast.Expr(
                value=ast.Call(
                    func=ast.Name(id="_emit_result", ctx=ast.Load()),
                    args=[ast.Name(id="__seacloud_last_result", ctx=ast.Load())],
                    keywords=[],
                )
            )
        )
    ast.fix_missing_locations(tree)
    exec(compile(tree, "<seacloud-code>", "exec"), namespace, namespace)
except Exception as exc:
    payload["error"] = {{
        "name": exc.__class__.__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
    }}
    _write_payload()
    raise
else:
    _write_payload()
"""


def _build_python_context_server() -> str:
    return f"""import ast
import base64
import contextlib
import io
import json
import os
import sys
import traceback

os.environ.setdefault("MPLBACKEND", "Agg")

SENTINEL = {CONTEXT_PAYLOAD_PREFIX!r}
namespace = {{"__name__": "__main__"}}
execution_count = 0


def _split_log_lines(value):
    if not value:
        return []
    return value.splitlines(True) or [value]


{_python_result_helpers()}

while True:
    line = sys.stdin.readline()
    if not line:
        break
    request = json.loads(line)
    user_code = base64.b64decode(request["code"]).decode("utf-8")
    cwd = request.get("cwd")
    payload = {{
        "results": [],
        "logs": {{"stdout": [], "stderr": []}},
        "error": None,
        "executionCount": execution_count + 1,
    }}
    namespace["display"] = display
    namespace["_emit_result"] = _emit_result
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    previous_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        globals()["payload"] = payload
        globals()["_install_matplotlib_hook"]()
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            tree = ast.parse(user_code, filename="<seacloud-context>", mode="exec")
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last_value = tree.body[-1].value
                tree.body[-1] = ast.Assign(
                    targets=[ast.Name(id="__seacloud_last_result", ctx=ast.Store())],
                    value=last_value,
                )
                tree.body.append(
                    ast.Expr(
                        value=ast.Call(
                            func=ast.Name(id="_emit_result", ctx=ast.Load()),
                            args=[ast.Name(id="__seacloud_last_result", ctx=ast.Load())],
                            keywords=[],
                        )
                    )
                )
            ast.fix_missing_locations(tree)
            execution_count += 1
            payload["executionCount"] = execution_count
            exec(compile(tree, "<seacloud-context>", "exec"), namespace, namespace)
    except Exception as exc:
        payload["error"] = {{
            "name": exc.__class__.__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }}
    finally:
        if cwd:
            os.chdir(previous_cwd)
    payload["logs"]["stdout"] = _split_log_lines(stdout_buffer.getvalue())
    payload["logs"]["stderr"] = _split_log_lines(stderr_buffer.getvalue())
    sys.stdout.write(SENTINEL + json.dumps(payload) + "\\n")
    sys.stdout.flush()
"""


def _python_result_helpers() -> str:
    return """def _chart_payload(figure):
    chart = {
        "type": "unknown",
        "title": None,
        "x_label": None,
        "y_label": None,
        "x_unit": None,
        "y_unit": None,
        "elements": [],
    }
    axes = figure.axes[0] if getattr(figure, "axes", None) else None
    if axes is None:
        return chart
    chart["title"] = axes.get_title() or None
    chart["x_label"] = axes.get_xlabel() or None
    chart["y_label"] = axes.get_ylabel() or None

    if getattr(axes, "containers", None):
        chart["type"] = "bar"
        for container in axes.containers:
            label = container.get_label()
            for patch in getattr(container, "patches", []):
                chart["elements"].append({
                    "label": str(getattr(patch, "get_x", lambda: 0)() + getattr(patch, "get_width", lambda: 0)() / 2),
                    "group": None if label == "_nolegend_" else label,
                    "value": float(getattr(patch, "get_height", lambda: 0)()),
                })
        tick_labels = [tick.get_text() for tick in axes.get_xticklabels()]
        for index, tick in enumerate(tick_labels):
            if index < len(chart["elements"]) and tick:
                chart["elements"][index]["label"] = tick
        return chart

    if getattr(axes, "lines", None):
        chart["type"] = "line"
        for line in axes.lines:
            group = line.get_label()
            x_values = list(line.get_xdata())
            y_values = list(line.get_ydata())
            for x_value, y_value in zip(x_values, y_values):
                chart["elements"].append({
                    "label": str(x_value),
                    "group": None if group == "_nolegend_" else group,
                    "value": float(y_value),
                })
        return chart

    if getattr(axes, "collections", None):
        chart["type"] = "scatter"
        for collection in axes.collections:
            offsets = getattr(collection, "get_offsets", lambda: [])()
            for point in offsets:
                try:
                    x_value = float(point[0])
                    y_value = float(point[1])
                except Exception:
                    continue
                chart["elements"].append({
                    "label": str(x_value),
                    "group": None,
                    "value": y_value,
                })
        return chart

    return chart


def _emit_result(value):
    if value is None:
        return

    try:
        import matplotlib.figure

        if isinstance(value, matplotlib.figure.Figure):
            buffer = io.BytesIO()
            value.savefig(buffer, format="png", bbox_inches="tight")
            payload["results"].append({
                "png": base64.b64encode(buffer.getvalue()).decode("ascii"),
                "chart": _chart_payload(value),
            })
            return
    except Exception:
        pass

    try:
        from PIL import Image

        if isinstance(value, Image.Image):
            buffer = io.BytesIO()
            value.save(buffer, format="PNG")
            payload["results"].append({
                "png": base64.b64encode(buffer.getvalue()).decode("ascii"),
            })
            return
    except Exception:
        pass

    try:
        import pandas as pd

        if isinstance(value, pd.DataFrame):
            payload["results"].append({
                "text": value.to_string(),
                "json": value.to_dict(orient="records"),
            })
            return
    except Exception:
        pass

    if isinstance(value, (str, int, float, bool, list, dict)):
        payload["results"].append({
            "text": value if isinstance(value, str) else repr(value),
            "json": value,
        })
        return

    payload["results"].append({"text": repr(value)})


def display(*values):
    for value in values:
        _emit_result(value)


def _install_matplotlib_hook():
    try:
        import matplotlib._pylab_helpers
        import matplotlib.pyplot as plt

        def _patched_show(*args, **kwargs):
            managers = matplotlib._pylab_helpers.Gcf.get_all_fig_managers()
            for manager in managers:
                figure = getattr(getattr(manager, "canvas", None), "figure", None)
                if figure is not None:
                    _emit_result(figure)
            plt.close("all")
            return None

        plt.show = _patched_show
    except Exception:
        pass"""
