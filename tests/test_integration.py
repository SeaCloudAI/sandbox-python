from __future__ import annotations

import os
import time
import unittest
import base64

from sandbox import Client
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

        cls.client = Client(base_url=base_url, api_key=api_key)
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

        workspace_id = f"python-sdk-test-{time.time_ns()}"
        created = self.client.create_sandbox({
            "templateID": self.template_id,
            "workspaceId": workspace_id,
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

            logs = self.client.get_sandbox_logs(sandbox_id, SandboxLogsParams(limit=10))
            self.assertIsInstance(logs["logs"], list)

            self.client.pause_sandbox(sandbox_id)

            connected = self.client.connect_sandbox(sandbox_id, {"timeout": 1200})
            self.assertIn(connected.status_code, (200, 201))
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

        cls.client = Client(base_url=base_url, api_key=api_key)
        cls.template_id = template_id

    def test_cmd_smoke(self) -> None:
        if not self.template_id:
            self.skipTest("SANDBOX_TEST_TEMPLATE_ID is not set")
        workspace_root = os.getenv("SANDBOX_TEST_SANDBOX_ROOT", "/root/workspace")

        workspace_id = f"python-cmd-sdk-test-{time.time_ns()}"
        created = self.client.create_sandbox({
            "templateID": self.template_id,
            "workspaceId": workspace_id,
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

            listing = cmd.list_dir({"path": workspace_root, "depth": 1})
            self.assertIsInstance(listing["entries"], list)

            stream = cmd.start({"process": {"cmd": "cat"}, "tag": "python-cmd-test"})
            try:
                start_frame = stream.next()
                self.assertTrue(start_frame["event"]["start"]["cmdId"])
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
            finally:
                stream.close()
        finally:
            try:
                self.client.delete_sandbox(sandbox_id)
            except APIError as exc:
                if exc.status_code != 404:
                    raise
