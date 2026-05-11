from __future__ import annotations

import os
import time
import unittest
import base64

from sandbox._client import GatewayClient
from sandbox.cmd import DownloadRequest, FileRequest, FilesContentRequest, UploadBytesRequest
from sandbox.control import SandboxLogsParams
from sandbox.core import APIError


def should_run_integration() -> bool:
    return os.getenv("SANDBOX_RUN_INTEGRATION") == "1"


@unittest.skipUnless(should_run_integration(), "set SANDBOX_RUN_INTEGRATION=1 to run integration tests")
class ControlPlaneIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base_url = os.getenv("SANDBOX_TEST_BASE_URL", "")
        api_key = os.getenv("SANDBOX_TEST_API_KEY", "")
        template_id = os.getenv("SANDBOX_TEST_TEMPLATE_ID", "")

        if not base_url or not api_key:
            raise unittest.SkipTest("integration test env is incomplete")

        cls.client = GatewayClient(base_url=base_url, api_key=api_key)
        cls.template_id = template_id

    def test_list_sandboxes(self) -> None:
        response = self.client.list_sandboxes(params=None)
        self.assertIsInstance(response, list)

    def test_pool_status(self) -> None:
        try:
            response = self.client.get_pool_status()
        except APIError as exc:
            if exc.status_code == 404:
                self.skipTest("admin pool status is not exposed by this gateway")
            raise
        self.assertGreaterEqual(response["total"], 0)

    def test_rolling_status(self) -> None:
        try:
            response = self.client.get_rolling_update_status()
        except APIError as exc:
            if exc.status_code == 404:
                self.skipTest("admin rolling status is not exposed by this gateway")
            raise
        self.assertTrue(response["phase"])

    def test_sandbox_lifecycle(self) -> None:
        if not self.template_id:
            self.skipTest("SANDBOX_TEST_TEMPLATE_ID is not set")

        created = self.client.create_sandbox({
            "templateID": self.template_id,
            "timeout": 1800,
            "waitReady": True,
        })

        sandbox_id = created["sandboxID"]
        self.assertTrue(sandbox_id)

        try:
            detail = self.client.get_sandbox(sandbox_id)
            self.assertEqual(detail["sandboxID"], sandbox_id)

            heartbeat = self.client.send_heartbeat(sandbox_id, {"status": "healthy"})
            self.assertTrue(heartbeat["received"])

            self.client.set_sandbox_timeout(sandbox_id, {"timeout": 1200})
            self.client.refresh_sandbox(sandbox_id, {"duration": 60})
            self.client.refresh_sandbox(sandbox_id)

            logs = self.client.get_sandbox_logs(sandbox_id, SandboxLogsParams(limit=10))
            self.assertIsInstance(logs["logs"], list)

            self.client.pause_sandbox(sandbox_id)

            connected = self.client.connect_sandbox(sandbox_id, {"timeout": 1200})
            self.assertIn(connected.status_code, (200, 201))
            runtime = self.client.runtime_from_sandbox(connected.sandbox)
            resumed = runtime.run({"cmd": "sh", "args": ["-lc", "echo resumed-python"]})
            self.assertEqual(resumed["exit_code"], 0)
            self.assertIn("resumed-python", resumed["stdout"])
        finally:
            try:
                self.client.delete_sandbox(sandbox_id)
            except APIError as exc:
                if exc.status_code != 404:
                    raise


@unittest.skipUnless(should_run_integration(), "set SANDBOX_RUN_INTEGRATION=1 to run integration tests")
class CmdIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base_url = os.getenv("SANDBOX_TEST_BASE_URL", "")
        api_key = os.getenv("SANDBOX_TEST_API_KEY", "")
        template_id = os.getenv("SANDBOX_TEST_TEMPLATE_ID", "")

        if not base_url or not api_key:
            raise unittest.SkipTest("integration test env is incomplete")

        cls.client = GatewayClient(base_url=base_url, api_key=api_key)
        cls.template_id = template_id

    def test_cmd_smoke(self) -> None:
        if not self.template_id:
            self.skipTest("SANDBOX_TEST_TEMPLATE_ID is not set")
        workspace_root = os.getenv("SANDBOX_TEST_SANDBOX_ROOT", "/root/workspace")

        created = self.client.create_sandbox({
            "templateID": self.template_id,
            "timeout": 1800,
            "waitReady": True,
        })

        sandbox_id = created["sandboxID"]
        self.assertTrue(sandbox_id)

        try:
            envd_url = created.get("envdUrl")
            if not envd_url:
                self.skipTest("sandbox did not return envdUrl")

            cmd = self.client.runtime_from_sandbox(created)

            file_path = workspace_root.rstrip("/") + "/python-cmd-sdk.txt"
            upload = cmd.upload_bytes(UploadBytesRequest(path=file_path, data=b"python-cmd"))
            self.assertIsInstance(upload, list)
            with cmd.download(DownloadRequest(path=file_path)) as response:
                self.assertEqual(response.read().decode("utf-8"), "python-cmd")

            content = cmd.files_content(FilesContentRequest(path=file_path))
            self.assertEqual(content["type"], "text")
            self.assertEqual(content["content"], "python-cmd")

            base_dir = workspace_root.rstrip("/") + f"/python-cmd-{time.time_ns()}"
            cmd.make_dir({"path": base_dir})
            json_path = base_dir + "/json.txt"
            gzip_path = base_dir + "/gzip.txt"
            moved_path = base_dir + "/moved.txt"
            batch_a_path = base_dir + "/batch-a.txt"
            batch_b_path = base_dir + "/batch-b.txt"
            composed_path = base_dir + "/joined.txt"

            cmd.upload_json({"path": json_path, "content": "alpha"})
            cmd.edit({"path": json_path, "oldText": "alpha", "newText": "beta"})
            cmd.upload_bytes(UploadBytesRequest(path=gzip_path, data=b"gzip-python", gzip_compress=True))
            cmd.move({"source": json_path, "destination": moved_path})
            batch = cmd.write_batch({
                "files": [
                    {"path": batch_a_path, "content": "A"},
                    {"path": batch_b_path, "content": "B"},
                ],
            })
            self.assertEqual(len(batch["files"]), 2)
            self.assertEqual(wait_for_downloaded_text(cmd, gzip_path), "gzip-python")
            cmd.compose_files({
                "source_paths": [moved_path, gzip_path],
                "destination": composed_path,
            })
            composed_text = wait_for_downloaded_text(cmd, composed_path)
            self.assertIn("beta", composed_text)
            self.assertIn("gzip-python", composed_text)

            listing = cmd.list_dir({"path": base_dir, "depth": 1})
            self.assertIsInstance(listing["entries"], list)
            self.assertTrue(any(entry["path"] == composed_path for entry in listing["entries"]))
            self.assertFalse(any(entry["path"] == gzip_path for entry in listing["entries"]))
            self.assertFalse(any(entry["path"] == moved_path for entry in listing["entries"]))
            cmd.remove({"path": composed_path})

            watch_root = "/tmp"
            watch_file_name = f"python-watch-{time.time_ns()}.txt"
            try:
                watcher = cmd.create_watcher({"path": watch_root})
            except APIError as exc:
                if is_watcher_unsupported(exc):
                    self.skipTest("watcher is not supported by this sandbox filesystem layout")
                raise
            try:
                cmd.upload_bytes(UploadBytesRequest(
                    path=watch_root + "/" + watch_file_name,
                    data=b"watch-python",
                ))
                events = wait_for_watcher_event(cmd, watcher["watcherId"], watch_file_name)
                self.assertTrue(any(event["name"] == watch_file_name for event in events))
            finally:
                cmd.remove_watcher({"watcherId": watcher["watcherId"]})

            stream = cmd.start({"process": {"cmd": "cat"}, "tag": "python-cmd-test"})
            try:
                start_frame = stream.next()
                self.assertTrue(start_frame["event"]["start"]["cmdId"])
                pid = start_frame["event"]["start"]["pid"]
                cmd_id = start_frame["event"]["start"]["cmdId"]
                process_list = cmd.list_processes()
                self.assertTrue(any(item["pid"] == pid for item in process_list.get("processes", [])))
                cmd.send_input({
                    "process": {"tag": "python-cmd-test"},
                    "input": {"stdin": "cGluZwo="},
                })
                cmd.close_stdin({"process": {"tag": "python-cmd-test"}})

                saw_output = False
                saw_end = False
                for _ in range(10):
                    frame = stream.next()
                    if frame is None:
                        break
                    data = frame["event"].get("data")
                    if data and data.get("stdout"):
                        output = base64.b64decode(data["stdout"]).decode("utf-8")
                        if "ping" in output:
                            saw_output = True
                    if frame["event"].get("end"):
                        saw_end = True
                        break

                self.assertTrue(saw_output)
                self.assertTrue(saw_end)
                result = cmd.get_result({"cmdId": cmd_id})
                self.assertEqual(result["exit_code"], 0)
                self.assertIn("ping", result["stdout"])
            finally:
                stream.close()
        finally:
            try:
                self.client.delete_sandbox(sandbox_id)
            except APIError as exc:
                if exc.status_code != 404:
                    raise

    def test_high_level_facade_smoke(self) -> None:
        if not self.template_id:
            self.skipTest("SANDBOX_TEST_TEMPLATE_ID is not set")
        workspace_root = os.getenv("SANDBOX_TEST_SANDBOX_ROOT", "/root/workspace")

        sandbox = self.client.create(
            self.template_id,
            timeout=1800,
            waitReady=True,
        )

        try:
            result = sandbox.commands.run("sh", args=["-lc", "echo facade-python"])
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("facade-python", result["stdout"])

            file_path = workspace_root.rstrip("/") + "/python-facade-sdk.txt"
            sandbox.files.write(file_path, "python-facade")
            self.assertEqual(sandbox.files.read(file_path), "python-facade")
            self.assertTrue(sandbox.files.exists(file_path))

            pty_handle = sandbox.pty.create(
                "sh",
                args=["-lc", 'printf "ready\\n"; IFS= read line; printf "got:%s\\n" "$line"'],
                size={"cols": 90, "rows": 30},
            )
            sandbox.pty.resize(pty_handle.pid, {"cols": 100, "rows": 40})
            pty_handle.send_stdin("ping\n")
            pty_result = pty_handle.wait()
            self.assertIn("ready", pty_result["pty"])
            self.assertIn("got:ping", pty_result["pty"])

            command_handle = sandbox.commands.run(
                "sh",
                args=["-lc", 'IFS= read line; printf "cmd:%s\\n" "$line"'],
                background=True,
            )
            connected_command = sandbox.commands.connect(command_handle.pid)
            connected_command.send_stdin("pong\n")
            connected_command_result = connected_command.wait()
            self.assertIn("cmd:pong", connected_command_result["stdout"])

            long_running_command = sandbox.commands.run(
                "sh",
                args=["-lc", "sleep 30"],
                background=True,
            )
            self.assertTrue(sandbox.commands.kill(long_running_command.pid))
            self.assertFalse(sandbox.commands.kill(long_running_command.pid))

            pty_source = sandbox.pty.create(
                "sh",
                args=["-lc", 'IFS= read line; printf "pty:%s\\n" "$line"'],
            )
            connected_pty = sandbox.pty.connect(pty_source.pid)
            connected_pty.send_stdin("echoed\n")
            connected_pty_result = connected_pty.wait()
            self.assertIn("pty:echoed", connected_pty_result["pty"])

            long_running_pty = sandbox.pty.create(
                "sh",
                args=["-lc", "sleep 30"],
            )
            self.assertTrue(sandbox.pty.kill(long_running_pty.pid))
            self.assertFalse(sandbox.pty.kill(long_running_pty.pid))
        finally:
            try:
                sandbox.delete()
            except APIError as exc:
                if exc.status_code != 404:
                    raise


def wait_for_watcher_event(cmd, watcher_id: str, file_name: str) -> list[dict[str, object]]:
    for _ in range(12):
        response = cmd.get_watcher_events({"watcherId": watcher_id, "limit": 20})
        events = response.get("events", [])
        if any(event.get("name") == file_name for event in events):
            return events
        time.sleep(0.5)
    return []


def wait_for_downloaded_text(cmd, path: str) -> str:
    for attempt in range(8):
        try:
            with cmd.download(DownloadRequest(path=path)) as response:
                return response.read().decode("utf-8")
        except APIError as exc:
            if exc.status_code != 404 or attempt == 7:
                raise
        time.sleep(0.3)
    raise AssertionError(f"timed out waiting for file {path}")


def is_watcher_unsupported(error: APIError) -> bool:
    message = str(error)
    return "network filesystem" in message or "outside allowed directory" in message
