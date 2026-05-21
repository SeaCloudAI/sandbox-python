from __future__ import annotations

import base64
import json
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY

import sandbox.template as template_module
import sandbox._client as client_module
from sandbox import LogEntry, Sandbox, Template, wait_for_port
from sandbox._client import GatewayClient
from sandbox.code_interpreter import CodeContext, CodeExecution
from sandbox.core import APIError, NotFoundError, ServerError, ValidationError


class FacadeSandboxTest(unittest.TestCase):
    def test_sandbox_class_helpers_use_env_first_client(self) -> None:
        calls: list[tuple[str, object]] = []

        class MockClient:
            def __init__(self, *args, **kwargs) -> None:
                calls.append(("init", kwargs))

            def create(self, template_or_options=None, **options):
                calls.append(("create", {"template_or_options": template_or_options, "options": options}))
                return "created"

            def connect(self, sandbox_id, *, timeout=300):
                calls.append(("connect", {"sandbox_id": sandbox_id, "timeout": timeout}))
                return "connected"

            def list(self, **options):
                calls.append(("list", options))
                return type("MockPaginator", (), {"next_items": lambda self: ["listed"]})()

            def get_sandbox(self, sandbox_id):
                calls.append(("get_sandbox", sandbox_id))
                return {"sandboxID": sandbox_id}

        import sandbox.facade as facade_module

        original_facade_client = facade_module.GatewayClient
        facade_module.GatewayClient = MockClient
        try:
            self.assertEqual(Sandbox.create("base", waitReady=True), "created")
            self.assertEqual(Sandbox.connect("sb-1", timeout=60), "connected")
            self.assertEqual(Sandbox.list(limit=10).next_items(), ["listed"])
            self.assertEqual(Sandbox.get_info("sb-1"), {
                "sandbox_id": "sb-1",
                "template_id": None,
                "sandbox_domain": None,
                "started_at": None,
                "end_at": None,
                "state": "",
                "metadata": None,
                "name": None,
                "cpu_count": None,
                "memory_mb": None,
                "envd_access_token": None,
            })
            self.assertEqual(Sandbox.get_full_info("sb-1"), {
                "sandbox_id": "sb-1",
                "template_id": None,
                "sandbox_domain": None,
                "started_at": None,
                "end_at": None,
                "state": "",
                "metadata": None,
                "name": None,
                "cpu_count": None,
                "memory_mb": None,
                "envd_access_token": None,
            })
        finally:
            facade_module.GatewayClient = original_facade_client

        self.assertEqual(calls[1], ("create", {"template_or_options": "base", "options": {"waitReady": True}}))
        self.assertEqual(calls[3], ("connect", {"sandbox_id": "sb-1", "timeout": 60}))
        self.assertEqual(calls[5], ("list", {"limit": 10}))
        self.assertEqual(calls[7], ("get_sandbox", "sb-1"))
        self.assertEqual(calls[9], ("get_sandbox", "sb-1"))

    def test_gateway_client_create_requires_template_id(self) -> None:
        class MockClient(GatewayClient):
            def __init__(self) -> None:
                super().__init__(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")

            def create_sandbox(self, body):
                self.body = dict(body)
                return {
                    "sandboxID": "sb-1",
                    "templateID": body.get("templateID"),
                }

        client = MockClient()
        created = client.create("base", waitReady=True)
        self.assertEqual(client.body, {"templateID": "base", "waitReady": True})
        self.assertEqual(created.raw["templateID"], "base")

    def test_bound_sandbox_uses_attached_client(self) -> None:
        class MockRuntime:
            def metrics(self):
                return {"cpu": 1}

            def run(self, body):
                return {
                    "stdout": "",
                    "stderr": "",
                    "exit_code": 0,
                    "duration_ms": 1,
                }

        class MockClient:
            def __init__(self) -> None:
                self.connect_calls = []

            def connect_sandbox(self, sandbox_id, body):
                self.connect_calls.append((sandbox_id, body))
                return type("ConnectedSandbox", (), {
                    "sandbox": {
                        "sandboxID": sandbox_id,
                        "templateID": "base",
                        "envdUrl": "https://runtime.cloud.seaart.ai/sb-1",
                        "envdAccessToken": "unit-runtime-auth",
                        "status": "running",
                        "state": "running",
                    },
                })()

            def runtime_from_sandbox(self, sandbox):
                return MockRuntime()

        mock_client = MockClient()
        created = Sandbox(mock_client, {
            "sandboxID": "sb-1",
            "templateID": "base",
            "envdUrl": "https://runtime.cloud.seaart.ai/sb-1",
            "envdAccessToken": "unit-runtime-auth",
            "status": "running",
            "state": "running",
        })
        reconnected = created.connect(timeout=120)
        listed = [Sandbox(mock_client, {
            "sandboxID": "sb-2",
            "templateID": "base",
            "status": "paused",
            "state": "paused",
        })]

        self.assertEqual(created.sandbox_id, "sb-1")
        self.assertEqual(reconnected.sandbox_id, "sb-1")
        self.assertEqual(created.sandbox_domain, "runtime.cloud.seaart.ai")
        self.assertEqual(created.traffic_access_token, "unit-runtime-auth")
        self.assertTrue(created.is_running())
        self.assertEqual(created.get_metrics()["cpu"], 1)
        self.assertEqual(created.commands.exec("echo hi")["exit_code"], 0)
        self.assertEqual(mock_client.connect_calls[0], ("sb-1", {"timeout": 120}))
        self.assertEqual(len(listed), 1)
        self.assertFalse(listed[0].is_running())

    def test_bound_sandbox_allows_zero_timeout_lifecycle_semantics(self) -> None:
        class MockClient:
            def __init__(self) -> None:
                self.connect_calls = []
                self.timeout_calls = []

            def connect_sandbox(self, sandbox_id, body):
                self.connect_calls.append((sandbox_id, body))
                return type("ConnectedSandbox", (), {
                    "sandbox": {
                        "sandboxID": sandbox_id,
                        "templateID": "base",
                        "status": "running",
                        "state": "running",
                    },
                })()

            def set_sandbox_timeout(self, sandbox_id, body):
                self.timeout_calls.append((sandbox_id, body))

        sandbox = Sandbox(MockClient(), {
            "sandboxID": "sb-zero",
            "templateID": "base",
            "status": "paused",
            "state": "paused",
        })

        sandbox.connect(timeout=0)
        sandbox.set_timeout(0)

        self.assertEqual(sandbox._client.connect_calls, [("sb-zero", {"timeout": 0})])
        self.assertEqual(sandbox._client.timeout_calls, [("sb-zero", {"timeout": 0})])

    def test_git_helpers_delegate_to_runtime_commands(self) -> None:
        class MockRuntime:
            def __init__(self) -> None:
                self.calls = []

            def run(self, body):
                self.calls.append(body)
                return {
                    "stdout": "ok\n",
                    "stderr": "",
                    "exit_code": 0,
                    "duration_ms": 4,
                }

            def write_file(self, request, options=None):
                self.calls.append(("write_file", request.path, request.data))

            def write_batch(self, body, options=None):
                self.calls.append(("write_batch", body))
                return {"files": [
                    {"path": "/tmp/a.txt", "bytes_written": 1},
                    {"path": "/tmp/b.txt", "bytes_written": 2},
                ]}

        class MockClient:
            def __init__(self) -> None:
                self.runtime = MockRuntime()

            def runtime_from_sandbox(self, sandbox):
                return self.runtime

        mock_client = MockClient()
        created = Sandbox(mock_client, {
            "sandboxID": "sb-git",
            "templateID": "base",
            "envdUrl": "https://runtime.cloud.seaart.ai/sb-git",
            "envdAccessToken": "unit-runtime-auth",
            "status": "running",
            "state": "running",
        })
        result = created.git.clone(
            "https://github.com/acme/repo.git",
            "/workspace/repo",
            branch="main",
            depth=1,
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "ok\n")
        self.assertEqual(mock_client.runtime.calls[0], {
            "cmd": "git",
            "args": ["clone", "--branch", "main", "--depth", "1", "https://github.com/acme/repo.git", "/workspace/repo"],
            "cwd": None,
            "env": None,
            "timeoutMs": None,
            "stdin": None,
        })
        self.assertEqual(created.files.write("/tmp/hello.txt", "hello"), {
            "name": "hello.txt",
            "path": "/tmp/hello.txt",
            "type": "file",
        })
        self.assertEqual(created.files.write_files([
            {"path": "/tmp/a.txt", "content": "a"},
            {"path": "/tmp/b.txt", "content": "bb"},
        ]), [
            {"name": "a.txt", "path": "/tmp/a.txt", "type": "file"},
            {"name": "b.txt", "path": "/tmp/b.txt", "type": "file"},
        ])

    def test_filesystem_pty_proxy_and_extra_git_helpers(self) -> None:
        class MockStream:
            def __init__(self, frames) -> None:
                self.frames = list(frames)
                self.closed = False

            def next(self):
                return self.frames.pop(0) if self.frames else None

            def close(self):
                self.closed = True

        class MockResponse:
            def __init__(self, data: bytes) -> None:
                self._data = data
                self.closed = False

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.close()
                return False

            def read(self):
                return self._data

            def close(self):
                self.closed = True

        class MockRuntime:
            def __init__(self) -> None:
                self.calls = []

            def stat(self, body, options=None):
                self.calls.append(("stat", body))
                if body["path"] in {"/tmp/missing", "/tmp/new"}:
                    raise NotFoundError("not found", status_code=404)
                return {"entry": {"path": body["path"], "type": "FILE_TYPE_FILE"}}

            def list_dir(self, body, options=None):
                self.calls.append(("list_dir", body))
                return {"entries": [{"path": "/tmp/a.txt", "type": "FILE_TYPE_FILE"}]}

            def make_dir(self, body, options=None):
                self.calls.append(("make_dir", body))
                return {"entry": {"path": body["path"], "type": "FILE_TYPE_DIRECTORY"}}

            def remove(self, body, options=None):
                self.calls.append(("remove", body))

            def move(self, body, options=None):
                self.calls.append(("move", body))
                return {"entry": {"path": body["destination"], "type": "FILE_TYPE_FILE"}}

            def read_file(self, request, options=None):
                self.calls.append(("read_file", request.path))
                return MockResponse(b"hello")

            def watch_dir(self, body, options=None):
                self.calls.append(("watch_dir", body))
                return MockStream([{"filesystem": {"name": "a.txt", "type": "EVENT_TYPE_WRITE"}}])

            def start(self, body, options=None):
                self.calls.append(("start", body))
                return MockStream([{"event": {"start": {"pid": 77}}}])

            def connect(self, body, options=None):
                self.calls.append(("connect", body))
                return MockStream([{"event": {"start": {"pid": 77}}}])

            def update(self, body, options=None):
                self.calls.append(("update", body))

            def send_signal(self, body, options=None):
                self.calls.append(("send_signal", body))
                if body["process"]["pid"] == 404:
                    raise NotFoundError("not found", status_code=404)
                if body["process"]["pid"] == 405:
                    raise ServerError("kill failed: ESRCH: No such process", status_code=500)

            def run(self, body):
                self.calls.append(("run", body))
                return {
                    "stdout": "ok\n",
                    "stderr": "",
                    "exit_code": 0,
                    "duration_ms": 1,
                }

            def proxy(self, request):
                self.calls.append(("proxy", request))
                return {"status": 200}

        class MockClient:
            def __init__(self) -> None:
                self.runtime = MockRuntime()

            def runtime_from_sandbox(self, sandbox):
                return self.runtime

        mock_client = MockClient()
        created = Sandbox(mock_client, {
            "sandboxID": "sb-ops",
            "templateID": "base",
            "envdUrl": "https://runtime.cloud.seaart.ai/sb-ops",
            "envdAccessToken": "unit-runtime-auth",
            "status": "running",
            "state": "running",
        })

        self.assertFalse(created.files.exists("/tmp/missing"))
        self.assertTrue(created.files.exists("/tmp/a.txt"))
        self.assertEqual(created.files.get_info("/tmp/a.txt")["path"], "/tmp/a.txt")
        self.assertEqual(created.files.get_info("/tmp/a.txt")["type"], "file")
        self.assertEqual(created.files.list("/tmp", depth=1)[0]["path"], "/tmp/a.txt")
        self.assertEqual(created.files.list("/tmp", depth=1)[0]["type"], "file")
        self.assertTrue(created.files.make_dir("/tmp/new"))
        self.assertFalse(created.files.make_dir("/tmp/a.txt"))
        self.assertEqual(created.files.read("/tmp/a.txt"), "hello")
        self.assertEqual(created.files.read("/tmp/a.txt", format="bytes"), b"hello")
        self.assertIsNotNone(created.files.read("/tmp/a.txt", format="stream"))
        created.files.remove("/tmp/old")
        self.assertEqual(created.files.rename("/tmp/a.txt", "/tmp/b.txt")["path"], "/tmp/b.txt")
        self.assertEqual(created.files.rename("/tmp/a.txt", "/tmp/b.txt")["type"], "file")
        watch_events = []
        watch_seen = threading.Event()
        watch = created.files.watch_dir(
            "/tmp",
            lambda event: (watch_events.append(event), watch_seen.set()),
            recursive=True,
        )
        self.assertTrue(watch_seen.wait(timeout=1.0))
        watch.stop()
        self.assertEqual(watch_events, [{"name": "a.txt", "type": "write"}])

        download_url = created.download_url("/tmp/demo.txt", user="root", use_signature_expiration=3600)
        upload_url = created.upload_url("/tmp/demo.txt", user="root")
        self.assertIn("/sb-ops/files?", download_url)
        self.assertIn("path=%2Ftmp%2Fdemo.txt", download_url)
        self.assertIn("username=root", download_url)
        self.assertIn("signature_expiration=3600", download_url)
        self.assertIn("signature=v1_", download_url)
        self.assertIn("/sb-ops/files?", upload_url)
        self.assertIn("signature=v1_", upload_url)

        pty_handle = created.pty.create("bash", size={"cols": 90, "rows": 30})
        connected = created.pty.connect(77)
        created.pty.resize(77, {"cols": 100, "rows": 40})
        self.assertEqual(pty_handle.pid, 77)
        self.assertEqual(connected.pid, 77)
        self.assertTrue(created.pty.kill(77))
        self.assertFalse(created.pty.kill(404))
        self.assertFalse(created.pty.kill(405))

        created.git.pull("/workspace/repo", envs={"A": "1"}, timeout_ms=5_000)
        created.git.checkout("main", "/workspace/repo")
        created.git.status("/workspace/repo")
        self.assertEqual(created.proxy(object()), {"status": 200})

        self.assertIn(("watch_dir", {"path": "/tmp", "recursive": True}), mock_client.runtime.calls)
        self.assertIn(("connect", {"process": {"pid": 77}}), mock_client.runtime.calls)
        self.assertIn(("update", {"process": {"pid": 77}, "pty": {"size": {"cols": 100, "rows": 40}}}), mock_client.runtime.calls)
        self.assertEqual(mock_client.runtime.calls[-4], ("run", {
            "cmd": "git",
            "args": ["pull"],
            "cwd": "/workspace/repo",
            "env": {"A": "1"},
            "timeoutMs": 5000,
            "stdin": None,
        }))
        self.assertEqual(mock_client.runtime.calls[-3], ("run", {
            "cmd": "git",
            "args": ["checkout", "main"],
            "cwd": "/workspace/repo",
            "env": None,
            "timeoutMs": None,
            "stdin": None,
        }))
        self.assertEqual(mock_client.runtime.calls[-2], ("run", {
            "cmd": "git",
            "args": ["status"],
            "cwd": "/workspace/repo",
            "env": None,
            "timeoutMs": None,
            "stdin": None,
        }))

    def test_command_and_pty_handles_encode_stdin_and_decode_wait_output(self) -> None:
        class MockStream:
            def __init__(self, frames) -> None:
                self.frames = list(frames)

            def next(self):
                return self.frames.pop(0) if self.frames else None

            def close(self):
                return None

        class MockRuntime:
            def __init__(self) -> None:
                self.calls = []

            def start(self, body, options=None):
                self.calls.append(("start", body))
                if body.get("pty") is not None:
                    return MockStream([
                        {"event": {"start": {"pid": 42, "cmdId": "cmd-pty"}}},
                        {"event": {"data": {"stdout": base64.b64encode(b"shell$ ").decode("ascii")}}},
                        {"event": {"end": {"exited": True, "status": "exited", "error": None}}},
                    ])
                return MockStream([
                    {"event": {"start": {"pid": 41, "cmdId": "cmd-bg"}}},
                    {"event": {"data": {"stdout": base64.b64encode(b"live\n").decode("ascii")}}},
                    {"event": {"end": {"exited": True, "status": "exited", "error": None}}},
                ])

            def send_input(self, body, options=None):
                self.calls.append(("send_input", body))

            def connect(self, body, options=None):
                self.calls.append(("connect", body))
                return MockStream([
                    {"event": {"start": {"pid": 41, "cmdId": "cmd-bg"}}},
                    {"event": {"data": {"stdout": base64.b64encode(b"live\n").decode("ascii")}}},
                    {"event": {"end": {"exited": True, "status": "exited", "error": None}}},
                ])

            def get_result(self, body, options=None):
                if body["cmdId"] == "cmd-bg":
                    return {"exit_code": 0, "stdout": "ping\n", "stderr": ""}
                return {"exit_code": 0, "stdout": "", "stderr": ""}

        class MockClient:
            def __init__(self) -> None:
                self.runtime = MockRuntime()

            def runtime_from_sandbox(self, sandbox):
                return self.runtime

        created = Sandbox(MockClient(), {
            "sandboxID": "sb-handle",
            "templateID": "base",
            "envdUrl": "https://runtime.cloud.seaart.ai/sb-handle",
            "envdAccessToken": "unit-runtime-auth",
            "status": "running",
            "state": "running",
        })

        handle = created.commands.run("cat", background=True)
        handle.send_stdin("ping\n")
        waited = handle.wait()
        stdout_chunks = []
        streamed = created.commands.run("cat", stdin=False, on_stdout=lambda chunk: stdout_chunks.append(chunk))
        connect_chunks = []
        connected = created.commands.connect(41, on_stdout=lambda chunk: connect_chunks.append(chunk))
        connected.wait()

        pty_handle = created.pty.create("bash")
        pty_handle.send_input("ls\n")
        pty_waited = pty_handle.wait()

        self.assertEqual(waited["stdout"], "ping\n")
        self.assertEqual(streamed["stdout"], "ping\n")
        self.assertEqual(stdout_chunks, ["live\n"])
        self.assertEqual(connect_chunks, ["live\n"])
        self.assertEqual(pty_waited["pty"], "shell$ ")
        send_inputs = [call for call in created._client.runtime.calls if call[0] == "send_input"]
        self.assertEqual(send_inputs[0], ("send_input", {
            "process": {"pid": 41},
            "input": {"stdin": base64.b64encode(b"ping\n").decode("ascii")},
        }))
        self.assertEqual(send_inputs[1], ("send_input", {
            "process": {"pid": 42},
            "input": {"pty": base64.b64encode(b"ls\n").decode("ascii")},
        }))

    def test_commands_and_pty_accept_request_timeout_and_user(self) -> None:
        class MockStream:
            def __init__(self, frames) -> None:
                self.frames = list(frames)

            def next(self):
                return self.frames.pop(0) if self.frames else None

        class MockRuntime:
            def __init__(self) -> None:
                self.calls = []

            def run(self, body, options=None):
                self.calls.append(("run", body, options))
                return {"stdout": "ok\n", "stderr": "", "exit_code": 0, "duration_ms": 1}

            def start(self, body, options=None):
                self.calls.append(("start", body, options))
                return MockStream([{"event": {"start": {"pid": 77}}}])

        class MockClient:
            def __init__(self) -> None:
                self.runtime = MockRuntime()

            def runtime_from_sandbox(self, sandbox):
                return self.runtime

        created = Sandbox(MockClient(), {
            "sandboxID": "sb-user",
            "templateID": "base",
            "envdUrl": "https://runtime.cloud.seaart.ai/sb-user",
            "envdAccessToken": "unit-runtime-auth",
            "status": "running",
            "state": "running",
        })

        created.commands.run("echo", args=["hello"], timeout_ms=2_000, request_timeout_ms=3_500, user="app")
        created.pty.create("bash", timeout_ms=4_000, request_timeout_ms=5_500, user="root")

        run_call = created._client.runtime.calls[0]
        start_call = created._client.runtime.calls[1]
        self.assertEqual(run_call[1]["cmd"], "sh")
        self.assertIn("su -s /bin/sh 'app'", run_call[1]["args"][1])
        self.assertEqual(run_call[2].request_timeout_ms, 3_500)
        self.assertEqual(start_call[1]["process"]["cmd"], "sh")
        self.assertIn("su -s /bin/sh 'root'", start_call[1]["process"]["args"][1])
        self.assertEqual(start_call[2].request_timeout_ms, 5_500)

    def test_pause_returns_boolean_and_timeout_helpers_use_millisecond_protocol(self) -> None:
        class MockStream:
            def __init__(self, frames) -> None:
                self.frames = list(frames)

            def next(self):
                return self.frames.pop(0) if self.frames else None

        class MockRuntime:
            def __init__(self) -> None:
                self.calls = []

            def run(self, body, options=None):
                self.calls.append(("run", body, options))
                return {"stdout": "ok\n", "stderr": "", "exit_code": 0, "duration_ms": 1}

            def start(self, body, options=None):
                self.calls.append(("start", body, options))
                return MockStream([{"event": {"start": {"pid": 77}}}])

        class MockClient:
            def __init__(self) -> None:
                self.runtime = MockRuntime()
                self.calls = []

            def runtime_from_sandbox(self, sandbox):
                return self.runtime

            def pause_sandbox(self, sandbox_id):
                self.calls.append(("pause_sandbox", sandbox_id))

            def set_sandbox_timeout(self, sandbox_id, body):
                self.calls.append(("set_sandbox_timeout", sandbox_id, body))

        created = Sandbox(MockClient(), {
            "sandboxID": "sb-timeout-ms",
            "templateID": "base",
            "envdUrl": "https://runtime.cloud.seaart.ai/sb-timeout-ms",
            "envdAccessToken": "unit-runtime-auth",
            "status": "running",
            "state": "running",
        })

        self.assertTrue(created.pause())
        self.assertFalse(created.pause())
        created.commands.run("echo", timeout_ms=1_000)
        created.pty.create("bash", timeout_ms=2_000)
        created.set_timeout(1_000)

        run_call = created._client.runtime.calls[0]
        start_call = created._client.runtime.calls[1]
        timeout_call = created._client.calls[-1]
        self.assertEqual(run_call[1]["timeoutMs"], 1000)
        self.assertEqual(start_call[1]["timeoutMs"], 2000)
        self.assertEqual(timeout_call, ("set_sandbox_timeout", "sb-timeout-ms", {"timeout": 1000}))

    def test_run_code_uses_default_python_context_and_preserves_execution_state(self) -> None:
        class MockResponse:
            def __init__(self, body: str) -> None:
                self._body = body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def read(self) -> bytes:
                return self._body

        class MockStream:
            def __init__(self, frames) -> None:
                self.frames = list(frames)
                self.closed = False

            def next(self):
                return self.frames.pop(0) if self.frames else None

            def close(self):
                self.closed = True

        class MockRuntime:
            def __init__(self) -> None:
                self.calls = []
                self.stream = MockStream([
                    {"event": {"start": {"pid": 51}}},
                    {"event": {"data": {
                        "stdout": base64.b64encode((
                            "__SEACLOUD_CODE_CONTEXT__" + json.dumps({
                                "results": [{"text": "1", "json": 1}],
                                "logs": {"stdout": ["hello\n"], "stderr": []},
                                "executionCount": 1,
                            }) + "\n"
                        ).encode("utf-8")).decode("ascii"),
                    }}},
                    {"event": {"data": {
                        "stdout": base64.b64encode((
                            "__SEACLOUD_CODE_CONTEXT__" + json.dumps({
                                "results": [{"text": "2", "json": 2}],
                                "logs": {"stdout": [], "stderr": ["warn\n"]},
                                "executionCount": 2,
                            }) + "\n"
                        ).encode("utf-8")).decode("ascii"),
                    }}},
                ])

            def write_file(self, request, options=None):
                self.calls.append(("write_file", request.path))

            def start(self, body, options=None):
                self.calls.append(("start", body))
                return self.stream

            def send_input(self, body, options=None):
                self.calls.append(("send_input", body))

            def remove(self, body, options=None):
                self.calls.append(("remove", body["path"]))

        class MockClient:
            def __init__(self) -> None:
                self.runtime = MockRuntime()

            def runtime_from_sandbox(self, sandbox):
                return self.runtime

        stdout = []
        stderr = []
        results = []
        errors = []
        created = Sandbox(MockClient(), {
            "sandboxID": "sb-code",
            "templateID": "base",
            "envdUrl": "https://runtime.cloud.seaart.ai/sb-code",
            "envdAccessToken": "unit-runtime-auth",
            "status": "running",
            "state": "running",
        })

        execution1 = created.run_code(
            "print(42)",
            cwd="/workspace",
            timeout_ms=30_000,
            on_stdout=lambda chunk: stdout.append(chunk),
            on_stderr=lambda chunk: stderr.append(chunk),
            on_result=lambda result: results.append(result),
            on_error=lambda error: errors.append(error),
        )
        execution2 = created.run_code(
            "print(43)",
            on_stdout=lambda chunk: stdout.append(chunk),
            on_stderr=lambda chunk: stderr.append(chunk),
            on_result=lambda result: results.append(result),
            on_error=lambda error: errors.append(error),
        )

        self.assertIsInstance(execution1, CodeExecution)
        self.assertEqual(execution1.results[0].text, "1")
        self.assertEqual(execution2.results[0].text, "2")
        self.assertEqual(execution1.logs.stdout, ["hello\n"])
        self.assertEqual(execution2.logs.stderr, ["warn\n"])
        self.assertEqual(stdout[0].line, "hello\n")
        self.assertEqual(stderr[0].line, "warn\n")
        self.assertEqual(results[0].json, 1)
        self.assertEqual(results[1].json, 2)
        self.assertEqual(errors, [])
        self.assertEqual(created._client.runtime.calls[1], ("start", {
            "process": {
                "cmd": "python3",
                "args": [ANY, ANY],
                "cwd": "/workspace",
            },
            "stdin": True,
            "timeoutMs": 30000,
        }))
        send_inputs = [call for call in created._client.runtime.calls if call[0] == "send_input"]
        self.assertEqual(len(send_inputs), 2)

    def test_explicit_code_context_lifecycle(self) -> None:
        class MockStream:
            def __init__(self) -> None:
                self.frames = [{"event": {"start": {"pid": 77}}}]
                self.closed = False

            def next(self):
                return self.frames.pop(0) if self.frames else None

            def close(self):
                self.closed = True

        class MockRuntime:
            def __init__(self) -> None:
                self.calls = []
                self.streams = [MockStream(), MockStream()]

            def write_file(self, request, options=None):
                self.calls.append(("write_file", request.path))

            def start(self, body, options=None):
                self.calls.append(("start", body))
                return self.streams.pop(0)

            def send_signal(self, body, options=None):
                self.calls.append(("send_signal", body))

            def remove(self, body, options=None):
                self.calls.append(("remove", body["path"]))

        class MockClient:
            def __init__(self) -> None:
                self.runtime = MockRuntime()

            def runtime_from_sandbox(self, sandbox):
                return self.runtime

        created = Sandbox(MockClient(), {
            "sandboxID": "sb-context",
            "templateID": "base",
            "envdUrl": "https://runtime.cloud.seaart.ai/sb-context",
            "envdAccessToken": "unit-runtime-auth",
            "status": "running",
            "state": "running",
        })

        context = created.create_code_context(cwd="/workspace", language="python", timeout_ms=10_000)
        self.assertIsInstance(context, CodeContext)
        self.assertEqual(len(created.list_code_contexts()), 1)
        restarted = created.restart_code_context(context)
        self.assertEqual(restarted.context_id, context.context_id)
        created.remove_code_context(context.context_id)

        starts = [call for call in created._client.runtime.calls if call[0] == "start"]
        signals = [call for call in created._client.runtime.calls if call[0] == "send_signal"]
        removes = [call for call in created._client.runtime.calls if call[0] == "remove"]
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(signals), 2)
        self.assertEqual(len(removes), 2)

    def test_non_python_code_context_behaves_as_stateless_execution_profile(self) -> None:
        class MockStream:
            def __init__(self, frames) -> None:
                self.frames = list(frames)
                self.closed = False

            def next(self):
                return self.frames.pop(0) if self.frames else None

            def close(self):
                self.closed = True

        class MockRuntime:
            def __init__(self) -> None:
                self.calls = []

            def write_file(self, request, options=None):
                self.calls.append(("write_file", request.path))

            def start(self, body, options=None):
                self.calls.append(("start", body))
                return MockStream([
                    {"event": {"start": {"pid": 88, "cmdId": "cmd-bash"}}},
                    {"event": {"data": {"stdout": base64.b64encode(b"hi\n").decode("ascii")}}},
                    {"event": {"end": {"exited": True, "status": "exit status 0", "error": None}}},
                ])

            def get_result(self, body, options=None):
                self.calls.append(("get_result", body))
                return {"exit_code": 0, "stdout": "hi\n", "stderr": ""}

            def remove(self, body, options=None):
                self.calls.append(("remove", body["path"]))

        class MockClient:
            def __init__(self) -> None:
                self.runtime = MockRuntime()
                self.deleted = []

            def runtime_from_sandbox(self, sandbox):
                return self.runtime

            def delete_sandbox(self, sandbox_id):
                self.deleted.append(sandbox_id)

        created = Sandbox(MockClient(), {
            "sandboxID": "sb-stateless-context",
            "templateID": "base",
            "envdUrl": "https://runtime.cloud.seaart.ai/sb-stateless-context",
            "envdAccessToken": "unit-runtime-auth",
            "status": "running",
            "state": "running",
        })

        context = created.create_code_context(cwd="/workspace/app", language="bash", timeout_ms=12_000)
        execution = created.run_code("echo hi", context=context)

        self.assertEqual(context.language, "bash")
        self.assertEqual(len(created.list_code_contexts()), 1)
        self.assertEqual(created.restart_code_context(context).context_id, context.context_id)
        self.assertEqual(execution.text, "hi\n")
        self.assertEqual(created._client.runtime.calls[1], ("start", {
            "process": {
                "cmd": "bash",
                "args": [ANY],
                "cwd": "/workspace/app",
            },
            "timeoutMs": 12000,
        }))
        created.remove_code_context(context)
        self.assertEqual(created.list_code_contexts(), [])
        created.kill()
        self.assertEqual(created._client.deleted, ["sb-stateless-context"])


class FacadeTemplateTest(unittest.TestCase):
    def test_build_uses_template_dsl_and_polls_until_ready(self) -> None:
        calls: list[tuple[str, object]] = []

        class MockBuildService:
            def __init__(self, *args, **kwargs) -> None:
                calls.append(("init", kwargs))

            def create_template(self, body):
                calls.append(("create_template", body))
                return {"templateID": "tpl-1"}

            def create_build(self, template_id, build_id, body):
                calls.append(("create_build", {"template_id": template_id, "build_id": build_id, "body": body}))
                return {}

            def get_build_status(self, template_id, build_id, params):
                calls.append(("get_build_status", params))
                return {
                    "buildID": build_id,
                    "templateID": template_id,
                    "status": "ready",
                    "logEntries": [{
                        "timestamp": "2026-01-01T00:00:00Z",
                        "level": "info",
                        "step": "RUN",
                        "message": "installed dependencies",
                    }],
                }

            def get_template(self, template_id):
                return {"templateID": template_id, "buildStatus": "ready"}

            def get_build(self, template_id, build_id):
                return {"templateID": template_id, "buildID": build_id, "status": "ready"}

        logs: list[str] = []
        client = GatewayClient(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        built = client.build_template(
            Template().from_image("docker.io/library/node:20").run_cmd("npm install").set_start_cmd("npm start", wait_for_port(3000)),
            "demo:v1",
            base_template_id="tpl-base-1",
            poll_interval=0.0,
            on_build_logs=lambda entry: logs.append(str(entry)),
        )

        self.assertEqual(built["template_id"], "tpl-1")
        self.assertEqual(calls[1][1], {
            "name": "demo",
            "tags": ["v1"],
            "extensions": {"baseTemplateID": "tpl-base-1"},
        })
        create_build_body = calls[2][1]["body"]
        self.assertEqual(create_build_body["fromImage"], "docker.io/library/node:20")
        self.assertEqual(create_build_body["startCmd"], "npm start")
        self.assertIn("3000", create_build_body["readyCmd"])
        self.assertTrue(any("installed dependencies" in line for line in logs))
        self.assertIn("hello", str(LogEntry(timestamp=0.0, level="info", message="hello")))

    def test_build_in_background_skips_polling(self) -> None:
        calls: list[tuple[str, object]] = []

        class MockBuildService:
            def __init__(self, *args, **kwargs) -> None:
                calls.append(("init", kwargs))

            def create_template(self, body):
                calls.append(("create_template", body))
                return {"templateID": "tpl-bg"}

            def create_build(self, template_id, build_id, body):
                calls.append(("create_build", {"template_id": template_id, "build_id": build_id, "body": body}))
                return {}

            def get_template(self, template_id):
                calls.append(("get_template", template_id))
                return {"templateID": template_id, "buildStatus": "building"}

            def get_build_status(self, template_id, build_id, params):
                raise AssertionError("get_build_status should not be called for background builds")

        client = GatewayClient(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        built = client.build_template_in_background(
            Template().from_image("docker.io/library/node:20"),
            "demo:v2",
        )

        self.assertEqual(built["template_id"], "tpl-bg")
        self.assertTrue(str(built["build_id"]).startswith("build-"))
        self.assertEqual([name for name, _ in calls], ["init", "create_template", "create_build"])

    def test_build_forwards_high_level_options_and_dedupes_tags(self) -> None:
        calls: list[tuple[str, object]] = []

        class MockBuildService:
            def __init__(self, *args, **kwargs) -> None:
                calls.append(("init", kwargs))

            def create_template(self, body):
                calls.append(("create_template", body))
                return {"templateID": "tpl-options"}

            def create_build(self, template_id, build_id, body):
                calls.append(("create_build", {"template_id": template_id, "build_id": build_id, "body": body}))
                return {}

            def get_build_status(self, template_id, build_id, params):
                calls.append(("get_build_status", params))
                return {"buildID": build_id, "templateID": template_id, "status": "ready", "logEntries": []}

            def get_template(self, template_id):
                return {"templateID": template_id, "buildStatus": "ready"}

            def get_build(self, template_id, build_id):
                return {"templateID": template_id, "buildID": build_id, "status": "ready"}

        client = GatewayClient(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        built = client.build_template(
            Template().from_image("docker.io/library/node:20"),
            "demo:v1",
            tags=["v1", "latest"],
            base_template_id="tpl-base-1",
            envs={"NODE_ENV": "production"},
            volume_mounts=[{"name": "workspace", "path": "/agent-workspace", "storageType": "nfs", "nfsHostPath": "/mnt/prod-sandbox-nfs-filesystem01"}],
            workdir="/agent-workspace",
            cpu_count=2,
            memory_mb=1024,
            poll_interval=0.0,
        )

        self.assertEqual(built["template_id"], "tpl-options")
        self.assertEqual(built["tags"], ["v1", "latest"])
        self.assertEqual(calls[1][1], {
            "name": "demo",
            "tags": ["v1", "latest"],
            "cpuCount": 2,
            "memoryMB": 1024,
            "extensions": {
                "baseTemplateID": "tpl-base-1",
                "envs": {"NODE_ENV": "production"},
                "volumeMounts": [{"name": "workspace", "path": "/agent-workspace", "storageType": "nfs", "nfsHostPath": "/mnt/prod-sandbox-nfs-filesystem01"}],
                "workdir": "/agent-workspace",
            },
        })
        self.assertEqual(calls[3][1].logs_offset, 0)
        self.assertEqual(calls[3][1].limit, 100)

    def test_template_management_helpers_use_e2b_style_shapes(self) -> None:
        calls: list[tuple[str, object]] = []

        class MockBuildService:
            def __init__(self, *args, **kwargs) -> None:
                calls.append(("init", kwargs))

            def resolve_template_ref(self, ref):
                calls.append(("resolve_template_ref", ref))
                return {"templateID": "tpl-1"}

            def get_template(self, template_id, params=None):
                calls.append(("get_template", {"template_id": template_id, "params": params}))
                return {"templateID": template_id, "buildStatus": "ready"}

            def get_build_status(self, template_id, build_id, params):
                calls.append(("get_build_status", {"template_id": template_id, "build_id": build_id, "params": params}))
                return {"templateID": template_id, "buildID": build_id, "status": "ready"}

        client = GatewayClient(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        self.assertTrue(client.template_exists("demo"))
        status = client.get_template_build_status({"template_id": "tpl-1", "build_id": "build-1"})

        self.assertEqual(status["status"], "ready")

    def test_template_management_helpers_handle_not_found_and_forward_options(self) -> None:
        calls: list[tuple[str, object]] = []

        class MockBuildService:
            def __init__(self, *args, **kwargs) -> None:
                calls.append(("init", kwargs))

            def resolve_template_ref(self, ref):
                calls.append(("resolve_template_ref", ref))
                if ref == "missing":
                    raise NotFoundError("not found", status_code=404)
                if ref == "broken":
                    raise APIError("boom", status_code=500)
                return {"templateID": "tpl-1"}

            def get_template(self, template_id, params=None):
                calls.append(("get_template", {"template_id": template_id, "params": params}))
                return {"templateID": template_id, "buildStatus": "ready"}

            def get_build_status(self, template_id, build_id, params):
                calls.append(("get_build_status", {"template_id": template_id, "build_id": build_id, "params": params}))
                return {"templateID": template_id, "buildID": build_id, "status": "ready"}

        client = GatewayClient(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()

        self.assertFalse(client.template_exists("missing"))
        with self.assertRaises(APIError):
            client.template_exists("broken")
        with self.assertRaises(ValidationError):
            client.get_template_build_status({"template_id": " ", "build_id": "build-1"})

        status = client.get_template_build_status(
            {"template_id": "tpl-1", "build_id": "build-1"},
            logs_offset=0,
            limit=100,
            level="info",
        )

        self.assertEqual(status["status"], "ready")
        self.assertEqual(calls[-1][1]["template_id"], "tpl-1")
        self.assertEqual(calls[-1][1]["build_id"], "build-1")
        self.assertEqual(calls[-1][1]["params"].logs_offset, 0)
        self.assertEqual(calls[-1][1]["params"].limit, 100)
        self.assertEqual(calls[-1][1]["params"].level, "info")

    def test_template_list_get_delete_use_shared_client_shape(self) -> None:
        calls: list[tuple[str, object]] = []

        class MockBuildService:
            def __init__(self, *args, **kwargs) -> None:
                calls.append(("init", kwargs))

            def list_templates(self, params=None):
                calls.append(("list_templates", params))
                return [{"templateID": "tpl-1"}]

            def resolve_template_ref(self, ref):
                calls.append(("resolve_template_ref", ref))
                return {"templateID": "tpl-1"}

            def get_template(self, template_id, params=None):
                calls.append(("get_template", {"template_id": template_id, "params": params}))
                return {"templateID": template_id}

            def delete_template(self, template_id):
                calls.append(("delete_template", template_id))

        client = GatewayClient(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        listed = client.list_templates({"visibility": "team"})
        detail = client.get_template("demo")
        client.delete_template("demo")

        self.assertEqual(listed[0]["templateID"], "tpl-1")
        self.assertEqual(detail["templateID"], "tpl-1")
        self.assertEqual(calls[1][0], "list_templates")
        self.assertEqual(calls[1][1].visibility, "team")
        self.assertIn(("resolve_template_ref", "demo"), calls)
        self.assertIn(("get_template", {"template_id": "tpl-1", "params": None}), calls)
        self.assertIn(("delete_template", "tpl-1"), calls)

    def test_template_list_get_delete_forward_facade_options(self) -> None:
        calls: list[tuple[str, object]] = []

        class MockBuildService:
            def __init__(self, *args, **kwargs) -> None:
                calls.append(("init", kwargs))

            def list_templates(self, params=None):
                calls.append(("list_templates", params))
                return [{"templateID": "tpl-direct"}]

            def resolve_template_ref(self, ref):
                calls.append(("resolve_template_ref", ref))
                return {"templateID": "tpl-delete"}

            def get_template(self, template_id, params=None):
                calls.append(("get_template", {"template_id": template_id, "params": params}))
                return {"templateID": template_id}

            def delete_template(self, template_id):
                calls.append(("delete_template", template_id))

        client = GatewayClient(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        listed = client.list_templates({
            "visibility": "team",
            "limit": 20,
            "offset": 40,
        })
        detail = client.get_template("tpl-direct", {"limit": 10, "next_token": "build-1"})
        client.delete_template("demo")

        self.assertEqual(listed[0]["templateID"], "tpl-direct")
        self.assertEqual(detail["templateID"], "tpl-direct")
        self.assertEqual(calls[1][1].visibility, "team")
        self.assertEqual(calls[1][1].limit, 20)
        self.assertEqual(calls[1][1].offset, 40)
        self.assertNotIn(("resolve_template_ref", "tpl-direct"), calls)
        self.assertIn(("resolve_template_ref", "demo"), calls)
        self.assertIn(("delete_template", "tpl-delete"), calls)
        self.assertEqual(detail["templateID"], "tpl-direct")
        self.assertEqual(calls[2][1]["template_id"], "tpl-direct")
        self.assertEqual(calls[2][1]["params"].limit, 10)
        self.assertEqual(calls[2][1]["params"].next_token, "build-1")

    def test_template_helpers_compile_to_expected_steps(self) -> None:
        request = (
            Template()
            .apt_install(["git", "curl"], no_install_recommends=True)
            .git_clone(
                "https://github.com/acme/repo.git",
                "/app/repo",
                branch="main",
                depth=1,
                user="root",
            )
            .make_dir(["/app/logs", "/app/cache"], mode=0o755, user="root")
            .make_symlink("/usr/bin/python3", "/usr/bin/python", force=True)
            .npm_install("tsx", g=True)
            .pip_install("numpy", g=False)
            .bun_install("prettier", dev=True)
            .set_workdir("/app/repo")
            .set_user("root")
            .request()
        )

        self.assertEqual(len(request["steps"]), 10)
        self.assertIn("apt-get", request["steps"][0]["args"][0])
        self.assertIn("--no-install-recommends", request["steps"][0]["args"][0])
        self.assertIn("git", request["steps"][1]["args"][0])
        self.assertIn("--branch", request["steps"][1]["args"][0])
        self.assertIn("mkdir", request["steps"][2]["args"][0])
        self.assertIn("mkdir", request["steps"][3]["args"][0])
        self.assertIn("ln", request["steps"][4]["args"][0])
        self.assertIn("npm", request["steps"][5]["args"][0])
        self.assertIn("pip", request["steps"][6]["args"][0])
        self.assertIn("bun", request["steps"][7]["args"][0])
        self.assertEqual(request["steps"][8], {"type": "WORKDIR", "args": ["/app/repo"]})
        self.assertEqual(request["steps"][9], {"type": "USER", "args": ["root"]})

    def test_template_helpers_support_skip_cache_copy_items_remove_and_rename(self) -> None:
        request = (
            Template()
            .skip_cache()
            .copy_items([{"src": "package.json", "dest": "/app/", "files_hash": "a" * 64, "user": "app"}])
            .remove("/tmp/cache", recursive=True, force=True, user="root")
            .rename("/tmp/old.txt", "/tmp/new.txt", user="root")
            .request()
        )

        self.assertEqual(len(request["steps"]), 4)
        self.assertEqual(request["steps"][0]["type"], "COPY")
        self.assertTrue(request["steps"][0]["force"])
        self.assertIn("chown", request["steps"][1]["args"][0])
        self.assertIn("app", request["steps"][1]["args"][0])
        self.assertTrue(request["steps"][1]["force"])
        self.assertIn("rm", request["steps"][2]["args"][0])
        self.assertTrue(request["steps"][2]["force"])
        self.assertIn("mv", request["steps"][3]["args"][0])
        self.assertTrue(request["steps"][3]["force"])

    def test_template_supports_run_cmd_user_and_copy_tar_options(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "hello.txt"
            link = root / "hello-link.txt"
            source.write_text("hello copy\n", encoding="utf-8")
            link.symlink_to(source)

            run_request = Template().run_cmd("apt-get install vim", user="root").request()
            default_request = json.loads(Template.to_json(
                Template().from_base_image().copy(str(link), "/app/"),
            ))
            mode_request = json.loads(Template.to_json(
                Template().from_base_image().copy(str(link), "/app/", mode=0o600),
            ))
            resolved_request = json.loads(Template.to_json(
                Template().from_base_image().copy(str(link), "/app/", resolve_symlinks=True),
            ))

        self.assertIn("su -s /bin/sh", run_request["steps"][0]["args"][0])
        self.assertNotEqual(default_request["steps"][0]["filesHash"], mode_request["steps"][0]["filesHash"])
        self.assertNotEqual(default_request["steps"][0]["filesHash"], resolved_request["steps"][0]["filesHash"])

    def test_template_image_helpers_and_serialization(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "hello.txt"
            source.write_text("hello copy\n", encoding="utf-8")

            request = json.loads(Template.to_json(
                Template()
                .from_node_image("24")
                .copy(str(source), "/app/")
                .set_envs({"NODE_ENV": "production"})
                .set_start_cmd("node server.js", wait_for_port(3000)),
            ))
            dockerfile = Template.to_dockerfile(
                Template()
                .from_python_image("3.12")
                .run_cmd("pip install numpy")
                .set_workdir("/app")
                .set_user("root"),
            )

        self.assertEqual(request["fromImage"], "node:24")
        self.assertRegex(request["steps"][0]["filesHash"], r"^[a-f0-9]{64}$")
        self.assertEqual(request["startCmd"], "node server.js")
        self.assertIn("FROM python:3.12", dockerfile)
        self.assertIn("RUN pip install numpy", dockerfile)
        self.assertIn("WORKDIR /app", dockerfile)
        self.assertIn("USER root", dockerfile)

        registry_request = Template().from_image("example.com/acme/app:latest", {"username": "robot", "password": "secret"}).request()
        self.assertEqual(registry_request["fromImageRegistry"]["type"], "registry")

        aws_request = Template().from_aws_registry("123.dkr.ecr.us-west-2.amazonaws.com/app:latest", {
            "accessKeyId": "AKIA",
            "secretAccessKey": "secret",
            "region": "us-west-2",
        }).request()
        self.assertEqual(aws_request["fromImageRegistry"]["type"], "aws")

        gcp_request = Template().from_gcp_registry("gcr.io/acme/app:latest", {
            "serviceAccountJSON": {"project_id": "acme"},
        }).request()
        self.assertEqual(gcp_request["fromImageRegistry"]["type"], "gcp")

    def test_template_parses_dockerfiles_from_inline_content_and_file_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "package.json"
            dockerfile = root / "Dockerfile"
            source.write_text('{"name":"demo"}\n', encoding="utf-8")
            dockerfile.write_text("FROM node:20\nCOPY package.json /app/\nCMD [\"node\", \"server.js\"]\n", encoding="utf-8")

            inline_request = (
                Template()
                .from_dockerfile("\n".join([
                    "FROM python:3.12",
                    "ENV APP_ENV=prod LOG_LEVEL=debug",
                    "RUN pip install numpy",
                    "WORKDIR /app",
                    "USER root",
                    "CMD [\"python\", \"app.py\"]",
                ]))
                .request()
            )
            file_request = json.loads(Template.to_json(Template().from_dockerfile(str(dockerfile))))

        self.assertEqual(inline_request["fromImage"], "python:3.12")
        self.assertEqual(inline_request["steps"][0], {"type": "ENV", "args": ["APP_ENV", "prod"]})
        self.assertEqual(inline_request["steps"][1], {"type": "ENV", "args": ["LOG_LEVEL", "debug"]})
        self.assertIn("pip install numpy", inline_request["steps"][2]["args"][0])
        self.assertEqual(inline_request["steps"][3], {"type": "WORKDIR", "args": ["/app"]})
        self.assertEqual(inline_request["steps"][4], {"type": "USER", "args": ["root"]})
        self.assertEqual(inline_request["startCmd"], "'python' 'app.py'")
        self.assertEqual(file_request["fromImage"], "node:20")
        self.assertEqual(file_request["steps"][0]["type"], "COPY")
        self.assertRegex(file_request["steps"][0]["filesHash"], r"^[a-f0-9]{64}$")
        self.assertEqual(file_request["startCmd"], "'node' 'server.js'")

    def test_template_rejects_unsupported_dockerfile_instructions(self) -> None:
        with self.assertRaisesRegex(Exception, "unsupported Dockerfile instruction: ENTRYPOINT"):
            Template().from_dockerfile("FROM node:20\nENTRYPOINT [\"node\"]\n")

    def test_template_build_auto_uploads_local_copy_sources(self) -> None:
        calls: list[tuple[str, object]] = []
        uploads: list[tuple[str, bytes, str | None]] = []

        class MockUploadResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getcode(self):
                return self.status

        class MockBuildService:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def create_template(self, body):
                return {"templateID": "tpl-copy"}

            def get_build_file(self, template_id, files_hash):
                calls.append(("get_build_file", {"template_id": template_id, "files_hash": files_hash}))
                return {"present": False, "url": f"https://upload.example/{files_hash}", "maxContextBytes": 104857600}

            def create_build(self, template_id, build_id, body):
                calls.append(("create_build", body))
                return {}

            def get_build_status(self, template_id, build_id, params):
                return {"buildID": build_id, "templateID": template_id, "status": "ready", "logEntries": []}

            def get_template(self, template_id):
                return {"templateID": template_id, "buildStatus": "ready"}

            def get_build(self, template_id, build_id):
                return {"templateID": template_id, "buildID": build_id, "status": "ready"}

        def fake_urlopen(request, timeout=30.0):
            uploads.append((request.full_url, request.data, request.headers.get("X-goog-content-length-range")))
            return MockUploadResponse()

        original_service = template_module.BuildService
        original_urlopen = template_module.urlopen
        template_module.BuildService = MockBuildService
        template_module.urlopen = fake_urlopen
        try:
            with TemporaryDirectory() as tmp:
                source = Path(tmp) / "hello.txt"
                source.write_text("hello copy\n", encoding="utf-8")
                client = GatewayClient(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
                client.build = MockBuildService()
                client.build_template(
                    Template().from_image("docker.io/library/alpine:3.20").copy(str(source), "/app/", user="app"),
                    "demo:auto-copy",
                    poll_interval=0.0,
                )
        finally:
            template_module.BuildService = original_service
            template_module.urlopen = original_urlopen

        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0][0].startswith("https://upload.example/"))
        self.assertEqual(uploads[0][1][:2], b"\x1f\x8b")
        self.assertEqual(uploads[0][2], "0,104857600")
        create_build_body = calls[1][1]
        self.assertRegex(create_build_body["steps"][0]["filesHash"], r"^[a-f0-9]{64}$")
        self.assertEqual(create_build_body["steps"][0]["args"][0], "hello.txt")
        self.assertEqual(create_build_body["steps"][1]["type"], "RUN")
        self.assertIn("chown", create_build_body["steps"][1]["args"][0])
        self.assertIn("app", create_build_body["steps"][1]["args"][0])

    def test_template_class_helpers_use_env_first_client(self) -> None:
        calls: list[tuple[str, object]] = []

        class MockClient:
            def __init__(self, **kwargs) -> None:
                calls.append(("init", kwargs))

            def build_template(self, template, name, **options):
                calls.append(("build_template", {"template": template, "name": name, "options": options}))
                return {"template_id": "tpl-1"}

            def build_template_in_background(self, template, name, **options):
                calls.append(("build_template_in_background", {"template": template, "name": name, "options": options}))
                return {"template_id": "tpl-bg"}

            def list_templates(self, params=None):
                calls.append(("list_templates", params))
                return [{"templateID": "tpl-1"}]

            def get_template(self, ref, params=None):
                calls.append(("get_template", {"ref": ref, "params": params}))
                return {"templateID": ref}

            def delete_template(self, ref):
                calls.append(("delete_template", ref))

            def assign_template_tags(self, target_name, tags):
                calls.append(("assign_template_tags", {"target_name": target_name, "tags": tags}))
                return {"build_id": "build-1", "tags": ["v1", "stable"]}

            def get_template_tags(self, template_id):
                calls.append(("get_template_tags", template_id))
                return [{"build_id": "build-1", "created_at": "2026-01-01T00:01:00Z", "tag": "stable"}]

            def remove_template_tags(self, name, tags):
                calls.append(("remove_template_tags", {"name": name, "tags": tags}))

            def template_exists(self, ref):
                calls.append(("template_exists", ref))
                return True

            def get_template_build_status(self, data, **options):
                calls.append(("get_template_build_status", {"data": data, "options": options}))
                return {"status": "ready"}

        original_client = client_module.GatewayClient
        client_module.GatewayClient = MockClient
        original_template_client = template_module.GatewayClient if hasattr(template_module, "GatewayClient") else None
        template_module.GatewayClient = MockClient
        try:
            template = Template().from_image("docker.io/library/alpine:3.20")
            self.assertEqual(
                Template.build(template, "demo:v1")["template_id"],
                "tpl-1",
            )
            self.assertEqual(Template.build_in_background(template, "demo:v1")["template_id"], "tpl-bg")
            self.assertEqual(Template.list(limit=10), [{"templateID": "tpl-1"}])
            self.assertEqual(Template.get("tpl-1", params={"limit": 5})["templateID"], "tpl-1")
            Template.delete("tpl-1")
            self.assertEqual(
                Template.assign_tags("demo:v1", ["stable", "prod"]),
                {"build_id": "build-1", "tags": ["v1", "stable"]},
            )
            self.assertEqual(
                Template.get_tags("demo"),
                [{"build_id": "build-1", "created_at": "2026-01-01T00:01:00Z", "tag": "stable"}],
            )
            Template.remove_tags("demo", "stable")
            self.assertTrue(Template.exists("tpl-1"))
            self.assertEqual(
                Template.get_build_status({"template_id": "tpl-1", "build_id": "build-1"}, limit=10)["status"],
                "ready",
            )
        finally:
            client_module.GatewayClient = original_client
            if original_template_client is not None:
                template_module.GatewayClient = original_template_client

        self.assertEqual(calls[0], ("init", {}))
        self.assertEqual(calls[1][0], "build_template")
        self.assertIn(("list_templates", {"limit": 10}), calls)
        self.assertIn(("get_template", {"ref": "tpl-1", "params": {"limit": 5}}), calls)
        self.assertIn(("assign_template_tags", {"target_name": "demo:v1", "tags": ["stable", "prod"]}), calls)
        self.assertIn(("get_template_tags", "demo"), calls)
        self.assertIn(("remove_template_tags", {"name": "demo", "tags": "stable"}), calls)
        self.assertGreaterEqual(calls.count(("init", {})), 1)
        self.assertEqual(calls[-1], ("get_template_build_status", {"data": {"template_id": "tpl-1", "build_id": "build-1"}, "options": {"limit": 10}}))


if __name__ == "__main__":
    unittest.main()
