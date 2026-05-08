from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sandbox.template as template_module
from sandbox import Client, LogEntry, Sandbox, Template, wait_for_port
from sandbox.core import APIError, NotFoundError, ServerError, ValidationError


class FacadeSandboxTest(unittest.TestCase):
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
        self.assertEqual(created.sandboxDomain, "runtime.cloud.seaart.ai")
        self.assertTrue(created.is_running())
        self.assertEqual(created.get_metrics()["cpu"], 1)
        self.assertEqual(created.commands.exec("echo hi")["exitCode"], 0)
        self.assertEqual(mock_client.connect_calls[0], ("sb-1", {"timeout": 120}))
        self.assertEqual(len(listed), 1)
        self.assertFalse(listed[0].is_running())

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

        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(result["stdout"], "ok\n")
        self.assertEqual(mock_client.runtime.calls[0], {
            "cmd": "git",
            "args": ["clone", "--branch", "main", "--depth", "1", "https://github.com/acme/repo.git", "/workspace/repo"],
            "cwd": None,
            "env": None,
            "timeout": None,
            "stdin": None,
        })
        self.assertEqual(created.files.write("/tmp/hello.txt", "hello"), {
            "path": "/tmp/hello.txt",
            "bytesWritten": 5,
            "bytes_written": 5,
        })
        self.assertEqual(created.files.write_files([
            {"path": "/tmp/a.txt", "content": "a"},
            {"path": "/tmp/b.txt", "content": "bb"},
        ]), [
            {"path": "/tmp/a.txt", "bytesWritten": 1, "bytes_written": 1},
            {"path": "/tmp/b.txt", "bytesWritten": 2, "bytes_written": 2},
        ])

    def test_filesystem_pty_proxy_and_extra_git_helpers(self) -> None:
        class MockStream:
            def __init__(self, frames) -> None:
                self.frames = list(frames)

            def next(self):
                return self.frames.pop(0) if self.frames else None

        class MockRuntime:
            def __init__(self) -> None:
                self.calls = []

            def stat(self, body, options=None):
                self.calls.append(("stat", body))
                if body["path"] == "/tmp/missing":
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

            def watch_dir(self, body, options=None):
                self.calls.append(("watch_dir", body))
                return "watch-stream"

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
        self.assertEqual(created.files.list("/tmp", depth=1)[0]["path"], "/tmp/a.txt")
        self.assertTrue(created.files.make_dir("/tmp/new"))
        created.files.remove("/tmp/old")
        self.assertEqual(created.files.rename("/tmp/a.txt", "/tmp/b.txt")["path"], "/tmp/b.txt")
        self.assertEqual(created.files.watch_dir("/tmp", recursive=True), "watch-stream")

        pty_handle = created.pty.create("bash", size={"cols": 90, "rows": 30})
        connected = created.pty.connect(77)
        created.pty.resize(77, {"cols": 100, "rows": 40})
        self.assertEqual(pty_handle.pid, 77)
        self.assertEqual(connected.pid, 77)
        self.assertTrue(created.pty.kill(77))
        self.assertFalse(created.pty.kill(404))
        self.assertFalse(created.pty.kill(405))

        created.git.pull("/workspace/repo", envs={"A": "1"}, timeout=5)
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
            "timeout": 5,
            "stdin": None,
        }))
        self.assertEqual(mock_client.runtime.calls[-3], ("run", {
            "cmd": "git",
            "args": ["checkout", "main"],
            "cwd": "/workspace/repo",
            "env": None,
            "timeout": None,
            "stdin": None,
        }))
        self.assertEqual(mock_client.runtime.calls[-2], ("run", {
            "cmd": "git",
            "args": ["status"],
            "cwd": "/workspace/repo",
            "env": None,
            "timeout": None,
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
                    {"event": {"end": {"exited": True, "status": "exited", "error": None}}},
                ])

            def send_input(self, body, options=None):
                self.calls.append(("send_input", body))

            def get_result(self, body, options=None):
                if body["cmdId"] == "cmd-bg":
                    return {"exitCode": 0, "stdout": "ping\n", "stderr": ""}
                return {"exitCode": 0, "stdout": "", "stderr": ""}

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

        pty_handle = created.pty.create("bash")
        pty_handle.send_stdin("ls\n")
        pty_waited = pty_handle.wait()

        self.assertEqual(waited["stdout"], "ping\n")
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
        client = Client(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        built = client.build_template(
            Template().from_image("docker.io/library/node:20").run_cmd("npm install").set_start_cmd("npm start", wait_for_port(3000)),
            "demo:v1",
            base_template_id="tpl-base-1",
            poll_interval=0.0,
            on_build_logs=lambda entry: logs.append(str(entry)),
        )

        self.assertEqual(built["templateID"], "tpl-1")
        self.assertEqual(built["status"], "ready")
        self.assertEqual(calls[1][1], {
            "name": "demo",
            "tags": ["v1"],
            "extensions": {"seacloud": {"baseTemplateID": "tpl-base-1"}},
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

        client = Client(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        built = client.build_template_in_background(
            Template().from_image("docker.io/library/node:20"),
            "demo:v2",
        )

        self.assertEqual(built["templateID"], "tpl-bg")
        self.assertEqual(built["status"], "building")
        self.assertEqual([name for name, _ in calls], ["init", "create_template", "create_build", "get_template"])

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

        client = Client(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        built = client.build_template(
            Template().from_image("docker.io/library/node:20"),
            "demo:v1",
            tags=["v1", "latest"],
            base_template_id="tpl-base-1",
            cpu_count=2,
            memory_mb=1024,
            poll_interval=0.0,
        )

        self.assertEqual(built["templateID"], "tpl-options")
        self.assertEqual(built["tags"], ["v1", "latest"])
        self.assertEqual(calls[1][1], {
            "name": "demo",
            "tags": ["v1", "latest"],
            "cpuCount": 2,
            "memoryMB": 1024,
            "extensions": {"seacloud": {"baseTemplateID": "tpl-base-1"}},
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

        client = Client(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        self.assertTrue(client.template_exists("demo"))
        status = client.get_template_build_status({"templateId": "tpl-1", "buildId": "build-1"})

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

        client = Client(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()

        self.assertFalse(client.template_exists("missing"))
        with self.assertRaises(APIError):
            client.template_exists("broken")
        with self.assertRaises(ValidationError):
            client.get_template_build_status({"templateID": " ", "buildID": "build-1"})

        status = client.get_template_build_status(
            {"templateID": "tpl-1", "buildID": "build-1"},
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

        client = Client(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
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

        client = Client(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        client.build = MockBuildService()
        listed = client.list_templates({
            "visibility": "team",
            "teamID": "team-1",
            "limit": 20,
            "offset": 40,
        })
        detail = client.get_template("tpl-direct", {"limit": 10, "nextToken": "build-1"})
        client.delete_template("demo")

        self.assertEqual(listed[0]["templateID"], "tpl-direct")
        self.assertEqual(detail["templateID"], "tpl-direct")
        self.assertEqual(calls[1][1].visibility, "team")
        self.assertEqual(calls[1][1].team_id, "team-1")
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
            .copy_items([{"src": "package.json", "dest": "/app/", "files_hash": "a" * 64}])
            .remove("/tmp/cache", recursive=True, force=True, user="root")
            .rename("/tmp/old.txt", "/tmp/new.txt", user="root")
            .request()
        )

        self.assertEqual(len(request["steps"]), 3)
        self.assertEqual(request["steps"][0]["type"], "COPY")
        self.assertTrue(request["steps"][0]["force"])
        self.assertIn("rm", request["steps"][1]["args"][0])
        self.assertTrue(request["steps"][1]["force"])
        self.assertIn("mv", request["steps"][2]["args"][0])
        self.assertTrue(request["steps"][2]["force"])

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
        uploads: list[tuple[str, bytes]] = []

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
                return {"present": False, "url": f"https://upload.example/{files_hash}"}

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
            uploads.append((request.full_url, request.data))
            return MockUploadResponse()

        original_service = template_module.BuildService
        original_urlopen = template_module.urlopen
        template_module.BuildService = MockBuildService
        template_module.urlopen = fake_urlopen
        try:
            with TemporaryDirectory() as tmp:
                source = Path(tmp) / "hello.txt"
                source.write_text("hello copy\n", encoding="utf-8")
                client = Client(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
                client.build = MockBuildService()
                client.build_template(
                    Template().from_image("docker.io/library/alpine:3.20").copy(str(source), "/app/"),
                    "demo:auto-copy",
                    poll_interval=0.0,
                )
        finally:
            template_module.BuildService = original_service
            template_module.urlopen = original_urlopen

        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0][0].startswith("https://upload.example/"))
        self.assertEqual(uploads[0][1][:2], b"\x1f\x8b")
        create_build_body = calls[1][1]
        self.assertRegex(create_build_body["steps"][0]["filesHash"], r"^[a-f0-9]{64}$")
        self.assertEqual(create_build_body["steps"][0]["args"][0], "hello.txt")


if __name__ == "__main__":
    unittest.main()
