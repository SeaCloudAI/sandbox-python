from __future__ import annotations

import json
import unittest
from urllib.error import HTTPError

from sandbox import Client
from sandbox.cmd import CmdRequestOptions, CommandService, DownloadRequest, UploadBytesRequest
from sandbox.control import SandboxLogsParams
from sandbox.core import APIError, NotFoundError, RequestTimeoutError, ValidationError


class FakeResponse:
    def __init__(self, status: int, body: str, reason: str = "OK", raw_body: bytes | None = None) -> None:
        self.status = status
        self._body = raw_body if raw_body is not None else body.encode("utf-8")
        self.reason = reason

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            body = self._body
            self._body = b""
            return body
        body = self._body[:size]
        self._body = self._body[size:]
        return body

    def close(self) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class MockClient(Client):
    def __init__(self, handler) -> None:
        super().__init__(base_url="https://hermes-gateway.sandbox.cloud.vtrix.ai", api_key="unit-auth-value")
        self._handler = handler
        self.build.open = handler

    def open(self, request):
        return self._handler(request)


class MockCommandService(CommandService):
    def __init__(self, handler) -> None:
        super().__init__(base_url="https://hermes-gateway.sandbox.cloud.vtrix.ai", access_token="unit-runtime-auth")
        self._handler = handler

    def _open_request(self, method, path, **kwargs):
        return self._handler(method, path, kwargs)


class ClientUnitTest(unittest.TestCase):
    def test_system_endpoints(self) -> None:
        client = MockClient(lambda request: FakeResponse(200, "metric 1\n"))
        self.assertEqual(client.metrics(), "metric 1\n")

        client = MockClient(lambda request: FakeResponse(200, json.dumps({"message": "shutdown initiated"})))
        self.assertEqual(client.shutdown()["message"], "shutdown initiated")

    def test_sandbox_request_encoding(self) -> None:
        def handler(request):
            if request.full_url.endswith("/api/v1/sandboxes"):
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.get_header("Content-type"), "application/json")
                self.assertEqual(json.loads(request.data.decode("utf-8")), {"templateID": "tpl", "waitReady": True})
                return FakeResponse(201, json.dumps({
                    "sandboxID": "sb-1",
                    "envdUrl": "https://hermes-gateway.sandbox.cloud.vtrix.ai",
                    "envdAccessToken": "unit-runtime-auth",
                }))
            self.fail("unexpected request")

        client = MockClient(handler)
        response = client.create_sandbox({"templateID": "tpl", "waitReady": True})
        self.assertEqual(response["sandboxID"], "sb-1")
        self.assertEqual(response.runtime.base_url, "https://hermes-gateway.sandbox.cloud.vtrix.ai")

    def test_build_namespace_reuses_gateway_configuration(self) -> None:
        def handler(request):
            if request.full_url.endswith("/api/v1/templates"):
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(
                    json.loads(request.data.decode("utf-8")),
                    {"name": "demo", "image": "docker.io/library/alpine:3.20"},
                )
                return FakeResponse(202, json.dumps({
                    "templateID": "tpl-1",
                    "buildID": "build-1",
                    "public": False,
                    "names": ["demo"],
                    "tags": [],
                    "aliases": [],
                }))
            self.fail("unexpected request")

        client = MockClient(handler)
        response = client.build.create_template({"name": "demo", "image": "docker.io/library/alpine:3.20"})
        self.assertEqual(response["templateID"], "tpl-1")

    def test_api_error_accepts_string_detail(self) -> None:
        client = MockClient(lambda request: FakeResponse(404, json.dumps({"error": "not found"}), reason="Not Found"))

        with self.assertRaises(NotFoundError) as raised:
            client.get_sandbox("sb-1")

        self.assertEqual(str(raised.exception), "not found")
        self.assertEqual(raised.exception.status_code, 404)

    def test_list_and_logs_params_encoding(self) -> None:
        seen = []

        def handler(request):
            seen.append(request.full_url)
            return FakeResponse(200, json.dumps([] if "/sandboxes?" in request.full_url else {"logs": []}))

        client = MockClient(handler)
        client.list_sandboxes(
            params=type("P", (), {
                "metadata": {"app": "prod", "team": "core"},
                "state": ["running", "paused"],
                "limit": 10,
                "next_token": "MQ",
            })(),
        )
        client.get_sandbox_logs(
            "sb-1",
            SandboxLogsParams(cursor=0, limit=10, direction="forward", level="info", search="health"),
        )
        self.assertIn("metadata=app%3Dprod%26team%3Dcore", seen[0])
        self.assertIn("state=running", seen[0])
        self.assertIn("nextToken=MQ", seen[0])
        self.assertIn("direction=forward", seen[1])
        self.assertIn("search=health", seen[1])

    def test_list_returns_bound_handles(self) -> None:
        seen = []

        def handler(request):
            seen.append(request.full_url)
            if request.full_url.endswith("/api/v1/sandboxes"):
                return FakeResponse(200, json.dumps([{
                    "sandboxID": "sb-1",
                    "clientID": "u1",
                    "envdVersion": "v1",
                    "status": "running",
                }]))
            if request.full_url.endswith("/logs"):
                return FakeResponse(200, json.dumps({"logs": []}))
            return FakeResponse(200, json.dumps({
                "sandboxID": "sb-1",
                "envdUrl": "https://hermes-gateway.sandbox.cloud.vtrix.ai",
                "envdAccessToken": "unit-runtime-auth",
            }))

        client = MockClient(handler)
        listed = client.list_sandboxes()
        self.assertEqual(listed[0]["sandboxID"], "sb-1")
        detail = listed[0].reload()
        listed[0].logs()
        self.assertEqual(detail.runtime.base_url, "https://hermes-gateway.sandbox.cloud.vtrix.ai")
        self.assertTrue(seen[1].endswith("/api/v1/sandboxes/sb-1"))
        self.assertTrue(seen[2].endswith("/api/v1/sandboxes/sb-1/logs"))

    def test_lifecycle_endpoints(self) -> None:
        calls = []

        def handler(request):
            calls.append((request.get_method(), request.full_url, request.data))
            if request.full_url.endswith("/heartbeat"):
                return FakeResponse(200, json.dumps({
                    "code": 0,
                    "message": "success",
                    "data": {"received": True, "status": "healthy"},
                    "request_id": "req-1",
                }))
            if request.full_url.endswith("/connect"):
                return FakeResponse(201, json.dumps({
                    "sandboxID": "sb-1",
                    "envdUrl": "https://hermes-gateway.sandbox.cloud.vtrix.ai",
                    "envdAccessToken": "unit-runtime-auth",
                }))
            if request.get_method() == "DELETE" or request.full_url.endswith("/pause") or request.full_url.endswith("/timeout") or request.full_url.endswith("/refreshes"):
                return FakeResponse(204, "")
            return FakeResponse(200, json.dumps({
                "sandboxID": "sb-1",
                "envdUrl": "https://hermes-gateway.sandbox.cloud.vtrix.ai",
                "envdAccessToken": "unit-runtime-auth",
                "logs": [],
            }))

        client = MockClient(handler)
        self.assertEqual(client.get_sandbox("sb-1")["sandboxID"], "sb-1")
        self.assertTrue(client.send_heartbeat("sb-1", {"status": "healthy"})["received"])
        client.set_sandbox_timeout("sb-1", {"timeout": 1200})
        client.refresh_sandbox("sb-1", {"duration": 60})
        client.refresh_sandbox("sb-1")
        client.pause_sandbox("sb-1")
        self.assertEqual(client.connect_sandbox("sb-1", {"timeout": 1200}).status_code, 201)
        client.delete_sandbox("sb-1")

        self.assertEqual(calls[0][1], "https://hermes-gateway.sandbox.cloud.vtrix.ai/api/v1/sandboxes/sb-1")
        self.assertEqual(calls[-1][0], "DELETE")

    def test_bound_sandbox_helpers_reuse_original_client(self) -> None:
        seen: list[str] = []

        def handler(request):
            seen.append(request.full_url)
            if request.full_url.endswith("/api/v1/sandboxes"):
                return FakeResponse(201, json.dumps({
                    "sandboxID": "sb-1",
                    "envdUrl": "https://hermes-gateway.sandbox.cloud.vtrix.ai",
                    "envdAccessToken": "unit-runtime-auth",
                }))
            if request.full_url.endswith("/logs"):
                return FakeResponse(200, json.dumps({"logs": []}))
            return FakeResponse(200, json.dumps({
                "sandboxID": "sb-1",
                "envdUrl": "https://hermes-gateway.sandbox.cloud.vtrix.ai",
                "envdAccessToken": "unit-runtime-auth",
            }))

        client = MockClient(handler)
        sandbox = client.create_sandbox({"templateID": "tpl"})
        detail = sandbox.reload()
        sandbox.logs()

        self.assertEqual(detail["sandboxID"], "sb-1")
        self.assertTrue(seen[1].endswith("/api/v1/sandboxes/sb-1"))
        self.assertTrue(seen[2].endswith("/api/v1/sandboxes/sb-1/logs"))

    def test_validations(self) -> None:
        client = MockClient(lambda request: FakeResponse(200, "{}"))

        with self.assertRaises(ValidationError):
            client.get_sandbox_logs("sb", SandboxLogsParams(limit=1001))
        with self.assertRaises(ValidationError):
            client.connect_sandbox("sb", {"timeout": -1})
        with self.assertRaises(ValidationError):
            client.set_sandbox_timeout("sb", {"timeout": 86401})
        with self.assertRaises(ValidationError):
            client.refresh_sandbox("sb", {"duration": 3601})
        with self.assertRaises(ValidationError):
            client.send_heartbeat("sb", {"status": "bad"})

    def test_api_error_decoding(self) -> None:
        def handler(request):
            raise HTTPError(
                request.full_url,
                404,
                "Not Found",
                hdrs=None,
                fp=FakeResponse(404, json.dumps({"code": 404, "message": "Not found"}), reason="Not Found"),
            )

        client = MockClient(handler)
        with self.assertRaises(NotFoundError) as ctx:
            client.get_sandbox("sb-1")
        self.assertEqual(ctx.exception.kind, "not_found")
        self.assertFalse(ctx.exception.retryable)

    def test_cmd_list_dir_headers(self) -> None:
        def handler(method, path, kwargs):
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/filesystem.Filesystem/ListDir")
            headers = kwargs["headers"]
            self.assertEqual(headers["Connect-Protocol-Version"], "1")
            self.assertEqual(headers["X-Access-Token"], "unit-runtime-auth")
            self.assertEqual(headers["Authorization"], "Basic c2FuZGJveDo=")
            self.assertEqual(kwargs["body"], {"path": "/tmp"})
            return FakeResponse(200, json.dumps({"entries": []}))

        cmd = MockCommandService(handler)
        response = cmd.list_dir({"path": "/tmp"}, CmdRequestOptions(username="sandbox"))
        self.assertEqual(response["entries"], [])

    def test_cmd_download_uses_query_and_range(self) -> None:
        def handler(method, path, kwargs):
            self.assertEqual(method, "GET")
            self.assertIn("path=~%2Fhello.txt", path)
            self.assertIn("username=sandbox", path)
            self.assertEqual(kwargs["headers"]["Range"], "bytes=0-3")
            return FakeResponse(206, "hell")

        cmd = MockCommandService(handler)
        response = cmd.download(
            DownloadRequest(path="~/hello.txt"),
            CmdRequestOptions(username="sandbox", range="bytes=0-3"),
        )
        with response:
            self.assertEqual(response.read().decode("utf-8"), "hell")

    def test_runtime_from_sandbox_uses_envd_fields(self) -> None:
        client = MockClient(lambda request: FakeResponse(200, "{}"))
        runtime = client.runtime_from_sandbox({
            "envdUrl": "https://hermes-gateway.sandbox.cloud.vtrix.ai",
            "envdAccessToken": "unit-runtime-auth",
        })
        self.assertEqual(runtime.base_url, "https://hermes-gateway.sandbox.cloud.vtrix.ai")
        self.assertEqual(runtime.access_token, "unit-runtime-auth")

    def test_transport_timeout_raises_typed_error(self) -> None:
        client = MockClient(lambda request: (_ for _ in ()).throw(TimeoutError("timed out")))
        with self.assertRaises(RequestTimeoutError):
            client.metrics()

    def test_cmd_stream_parsing_and_stream_input(self) -> None:
        stream_bytes = connect_frame({"event": {"start": {"pid": 1234, "cmdId": "cmd-1"}}}) + connect_frame({
            "event": {"data": {"stdout": "aGVsbG8K"}},
        })

        def handler(method, path, kwargs):
            if path == "/process.Process/Start":
                self.assertEqual(kwargs["headers"]["Content-Type"], "application/connect+json")
                return FakeResponse(200, "", raw_body=stream_bytes)
            if path == "/process.Process/StreamInput":
                frames = decode_frames(kwargs["data"])
                self.assertEqual(len(frames), 2)
                self.assertIn(b'"pid": 42', frames[0]["payload"])
                self.assertIn(b'"stdin": "aGVsbG8="', frames[1]["payload"])
                return FakeResponse(200, "", raw_body=connect_frame({}))
            self.fail(f"unexpected path {path}")

        cmd = MockCommandService(handler)
        stream = cmd.start({"process": {"cmd": "echo", "args": ["hello"]}})
        first = stream.next()
        second = stream.next()
        stream.close()
        self.assertEqual(first["event"]["start"]["cmdId"], "cmd-1")
        self.assertEqual(second["event"]["data"]["stdout"], "aGVsbG8K")

        frame = cmd.stream_input([
            {"start": {"process": {"pid": 42}}},
            {"data": {"input": {"stdin": "aGVsbG8="}}},
        ])
        self.assertIsNotNone(frame)

    def test_cmd_proxy_passthrough_and_write_file_validation(self) -> None:
        def handler(method, path, kwargs):
            self.assertEqual(path, "/proxy/8080/health")
            return FakeResponse(502, "upstream failed")

        cmd = MockCommandService(handler)
        response = cmd.proxy(type("ProxyRequest", (), {"port": 8080, "method": "GET", "path": "/health", "body": None, "headers": {}})())
        with response:
            self.assertEqual(response.status, 502)
            self.assertEqual(response.read().decode("utf-8"), "upstream failed")

        with self.assertRaises(ValidationError):
            cmd.write_file(UploadBytesRequest(path="", data=b""))

    def test_cmd_base_url_prefix_is_preserved(self) -> None:
        cmd = CommandService(base_url="https://hermes-gateway.sandbox.cloud.vtrix.ai/sandbox/sb-1", access_token="unit-runtime-auth")
        self.assertEqual(cmd._build_url("/run"), "https://hermes-gateway.sandbox.cloud.vtrix.ai/sandbox/sb-1/run")


def connect_frame(payload: dict[str, object]) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    return bytes([0]) + len(data).to_bytes(4, "big") + data


def decode_frames(data: bytes) -> list[dict[str, bytes | int]]:
    frames: list[dict[str, bytes | int]] = []
    offset = 0
    while offset < len(data):
        size = int.from_bytes(data[offset + 1:offset + 5], "big")
        payload = data[offset + 5:offset + 5 + size]
        frames.append({"flags": data[offset], "payload": payload})
        offset += 5 + size
    return frames
