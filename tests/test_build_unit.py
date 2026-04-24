from __future__ import annotations

import json
import unittest
from urllib.error import HTTPError

from sandbox.build import (
    BuildLogsParams,
    BuildStatusParams,
    BuildService,
    GetTemplateParams,
    ListTemplatesParams,
)
from sandbox.core import APIError, ValidationError


class FakeResponse:
    def __init__(self, status: int, body: str, reason: str = "OK") -> None:
        self.status = status
        self._body = body.encode("utf-8")
        self.reason = reason

    def read(self) -> bytes:
        body = self._body
        self._body = b""
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


class MockBuildService(BuildService):
    def __init__(self, handler) -> None:
        super().__init__(base_url="https://sandbox-gateway.cloud.seaart.ai", api_key="unit-auth-value")
        self._handler = handler

    def open(self, request):
        return self._handler(request)


class BuildServiceUnitTest(unittest.TestCase):
    def test_system_and_direct_build(self) -> None:
        service = MockBuildService(lambda request: FakeResponse(200, "metric 1\n"))
        self.assertEqual(service.metrics(), "metric 1\n")

        def direct_handler(request):
            self.assertEqual(request.full_url, "https://sandbox-gateway.cloud.seaart.ai/build")
            self.assertIsNone(request.get_header("X-Namespace-ID"))
            self.assertEqual(request.get_header("Content-type"), "application/json")
            self.assertEqual(json.loads(request.data.decode("utf-8")), {
                "project": "proj",
                "image": "app",
                "tag": "v1",
                "dockerfile": "FROM alpine:3.20",
            })
            return FakeResponse(202, json.dumps({
                "templateID": "tpl-1",
                "buildID": "build-1",
                "imageFullName": "example-image:v1",
            }))

        service = MockBuildService(direct_handler)
        response = service.direct_build({
            "project": "proj",
            "image": "app",
            "tag": "v1",
            "dockerfile": "FROM alpine:3.20",
        })
        self.assertEqual(response["templateID"], "tpl-1")

    def test_template_endpoints(self) -> None:
        calls = []

        def handler(request):
            calls.append((request.get_method(), request.full_url))
            if request.full_url.endswith("/api/v1/templates") and request.get_method() == "POST":
                self.assertEqual(json.loads(request.data.decode("utf-8")), {
                    "name": "demo",
                    "visibility": "personal",
                    "baseTemplateID": "tpl-base-1",
                    "image": "docker.io/library/alpine:3.20",
                    "cpuCount": 2,
                    "envs": {"APP_ENV": "test"},
                })
                return FakeResponse(202, json.dumps({
                    "templateID": "tpl-1",
                    "buildID": "build-1",
                    "public": False,
                    "names": ["user/demo"],
                    "tags": ["v1"],
                    "aliases": ["demo"],
                }))
            if "/api/v1/templates?" in request.full_url:
                return FakeResponse(200, json.dumps([]))
            if request.full_url.endswith("/api/v1/templates/aliases/tpl-1"):
                return FakeResponse(200, json.dumps({"templateID": "tpl-1", "public": False}))
            if "/api/v1/templates/tpl-1?limit=10&nextToken=build-1" in request.full_url:
                return FakeResponse(200, json.dumps({
                    "templateID": "tpl-1",
                    "buildID": "build-2",
                    "buildStatus": "ready",
                    "public": False,
                    "names": ["user/demo"],
                    "aliases": ["demo"],
                    "tags": ["v1"],
                    "name": "demo",
                    "visibility": "personal",
                    "baseTemplateID": "tpl-base-1",
                    "image": "example-image:v1",
                    "imageSource": "dockerfile",
                    "envdVersion": "sandbox-builder-v1",
                    "cpuCount": 2,
                    "memoryMB": 1024,
                    "diskSizeMB": 5120,
                    "createdBy": {"id": "user", "email": "test-user"},
                    "createdByID": "user",
                    "projectID": "proj-1",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:01:00Z",
                    "lastSpawnedAt": "2026-01-01T00:02:00Z",
                    "spawnCount": 3,
                    "buildCount": 4,
                    "storageType": "ephemeral",
                    "ttlSeconds": 300,
                    "port": 9000,
                    "startCmd": "npm start",
                    "readyCmd": "test-ready-command",
                    "builds": [{
                        "buildID": "build-2",
                        "templateID": "tpl-1",
                        "status": "ready",
                        "image": "example-image:v1",
                        "errorMessage": "",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-01-01T00:02:00Z",
                        "finishedAt": "2026-01-01T00:02:00Z",
                    }],
                    "nextToken": "build-next",
                }))
            if request.full_url.endswith("/api/v1/templates/tpl-1") and request.get_method() == "PATCH":
                return FakeResponse(200, json.dumps({"names": ["user/demo-2"]}))
            if request.full_url.endswith("/api/v1/templates/tpl-1") and request.get_method() == "DELETE":
                return FakeResponse(204, "")
            self.fail(f"unexpected request: {request.get_method()} {request.full_url}")

        service = MockBuildService(handler)
        created = service.create_template({
            "name": "demo",
            "visibility": "personal",
            "baseTemplateID": "tpl-base-1",
            "image": "docker.io/library/alpine:3.20",
            "cpuCount": 2,
            "envs": {"APP_ENV": "test"},
        })
        listed = service.list_templates(ListTemplatesParams(
            visibility="team",
            team_id="team-1",
            limit=20,
            offset=40,
        ))
        aliased = service.get_template_by_alias("tpl-1")
        detail = service.get_template(
            "tpl-1",
            GetTemplateParams(limit=10, next_token="build-1"),
        )
        updated = service.update_template("tpl-1", {"name": "demo-2"})
        service.delete_template("tpl-1")

        self.assertEqual(created["templateID"], "tpl-1")
        self.assertEqual(listed, [])
        self.assertEqual(aliased["templateID"], "tpl-1")
        self.assertEqual(detail["templateID"], "tpl-1")
        self.assertEqual(detail["baseTemplateID"], "tpl-base-1")
        self.assertEqual(detail["imageSource"], "dockerfile")
        self.assertEqual(detail["createdBy"]["email"], "test-user")
        self.assertEqual(detail["builds"][0]["status"], "ready")
        self.assertEqual(detail["nextToken"], "build-next")
        self.assertEqual(updated["names"], ["user/demo-2"])
        self.assertEqual(calls[-1][0], "DELETE")

    def test_build_endpoints(self) -> None:
        def handler(request):
            if request.full_url.endswith("/api/v1/templates/tpl-1/builds") and request.get_method() == "POST":
                payload = json.loads(request.data.decode("utf-8")) if request.data else None
                if payload is None:
                    return FakeResponse(202, json.dumps({
                        "buildID": "build-1",
                        "templateID": "tpl-1",
                        "status": "uploaded",
                        "image": "example-image:v1",
                        "errorMessage": "",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-01-01T00:00:01Z",
                        "finishedAt": "2026-01-01T00:00:02Z",
                    }))
                if payload == {
                    "buildID": "build-abc",
                    "fromTemplate": "base",
                }:
                    return FakeResponse(202, "{}")
                self.assertEqual(payload, {
                    "fromImage": "docker.io/library/node:20",
                    "filesHash": "a" * 64,
                    "steps": [{"type": "files", "filesHash": "a" * 64}],
                    "startCmd": "npm start",
                    "readyCmd": "test-ready-command",
                })
                return FakeResponse(202, "{}")
            if request.full_url.endswith("/api/v1/templates/tpl-1/files/" + "a" * 64):
                return FakeResponse(200, json.dumps({"present": False, "url": "https://sandbox-gateway.cloud.seaart.ai"}))
            if request.full_url.endswith("/api/v1/templates/tpl-1/files/" + "b" * 64):
                return FakeResponse(200, json.dumps({"present": True}))
            if request.full_url.endswith("/rollback"):
                return FakeResponse(200, json.dumps({"templateID": "tpl-1"}))
            if request.full_url.endswith("/api/v1/templates/tpl-1/builds"):
                return FakeResponse(200, json.dumps({"builds": [], "total": 0}))
            if request.full_url.endswith("/api/v1/templates/tpl-1/builds/build-1"):
                return FakeResponse(200, json.dumps({"buildID": "build-1", "templateID": "tpl-1", "status": "ready"}))
            if request.full_url.endswith("/status?logsOffset=5&limit=10"):
                return FakeResponse(200, json.dumps({
                    "buildID": "build-1",
                    "templateID": "tpl-1",
                    "status": "building",
                    "logs": [{
                        "timestamp": "2026-01-01T00:00:00Z",
                        "level": "info",
                        "step": "build",
                        "message": "building image",
                    }],
                    "reason": None,
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:00:01Z",
                }))
            if request.full_url.endswith("/status") and "logsOffset" not in request.full_url:
                return FakeResponse(200, json.dumps({
                    "buildID": "build-1",
                    "templateID": "tpl-1",
                    "status": "building",
                    "logs": ["raw-line"],
                    "logEntries": [{
                        "timestamp": "2026-01-01T00:00:00Z",
                        "level": "info",
                        "step": "build",
                        "message": "structured log",
                    }],
                    "reason": "queued",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:00:01Z",
                }))
            if "source=persistent" in request.full_url and request.full_url.endswith("/logs?cursor=0&limit=10&direction=forward&source=persistent"):
                return FakeResponse(200, json.dumps({"logs": []}))
            self.fail(f"unexpected request: {request.get_method()} {request.full_url}")

        service = MockBuildService(handler)
        response = service.create_build("tpl-1", {
            "buildID": "build-abc",
            "fromTemplate": "base",
        })
        self.assertTrue(response["empty"])

        native = service.create_build("tpl-1")
        self.assertFalse(native["empty"])
        self.assertEqual(native["buildID"], "build-1")

        encoded = service.create_build("tpl-1", {
            "fromImage": "docker.io/library/node:20",
            "filesHash": "a" * 64,
            "steps": [{"type": "files", "filesHash": "a" * 64}],
            "startCmd": "npm start",
            "readyCmd": "test-ready-command",
        })
        self.assertTrue(encoded["empty"])

        file_resp = service.get_build_file("tpl-1", "a" * 64)
        self.assertFalse(file_resp["present"])
        existing = service.get_build_file("tpl-1", "b" * 64)
        self.assertTrue(existing["present"])

        rollback = service.rollback_template("tpl-1", {"buildID": "build-1"})
        self.assertEqual(rollback["templateID"], "tpl-1")

        history = service.list_builds("tpl-1")
        build = service.get_build("tpl-1", "build-1")
        status = service.get_build_status("tpl-1", "build-1", BuildStatusParams(logs_offset=5, limit=10))
        logs = service.get_build_logs("tpl-1", "build-1", BuildLogsParams(cursor=0, limit=10, direction="forward", source="persistent"))

        self.assertEqual(history["total"], 0)
        self.assertEqual(build["buildID"], "build-1")
        self.assertEqual(status["logEntries"][0]["message"], "building image")
        self.assertEqual(logs["logs"], [])
        status_with_entries = service.get_build_status("tpl-1", "build-1")
        self.assertEqual(status_with_entries["logs"], ["raw-line"])
        self.assertEqual(status_with_entries["logEntries"][0]["message"], "structured log")

    def test_validations_and_errors(self) -> None:
        service = MockBuildService(lambda request: FakeResponse(200, "{}"))

        with self.assertRaises(ValidationError):
            service.create_build("tpl-1", {"fromImageRegistry": "docker.io/node:20"})
        with self.assertRaises(ValidationError):
            service.create_build("tpl-1", {
                "steps": [{"type": "files", "filesHash": "a" * 64, "args": ["x"]}],
            })
        with self.assertRaises(ValidationError):
            service.create_build("tpl-1", {"buildID": "Build-Uppercase"})
        with self.assertRaises(ValidationError):
            service.create_build("tpl-1", {"filesHash": "bad"})
        with self.assertRaises(ValidationError):
            service.create_build("tpl-1", {
                "filesHash": "a" * 64,
                "steps": [{"type": "files", "filesHash": "b" * 64}],
            })
        with self.assertRaises(ValidationError):
            service.list_templates(ListTemplatesParams(limit=101))
        with self.assertRaises(ValidationError):
            service.list_templates(ListTemplatesParams(offset=-1))
        with self.assertRaises(ValidationError):
            service.get_template("tpl-1", GetTemplateParams(limit=101))
        with self.assertRaises(ValidationError):
            service.get_template_by_alias(" ")
        with self.assertRaisesRegex(ValidationError, "official templates are not supported by the public SDK"):
            service.create_template({
                "name": "official-template",
                "visibility": "official",
                "image": "docker.io/library/alpine:3.20",
            })
        with self.assertRaisesRegex(ValidationError, "official templates are not supported by the public SDK"):
            service.update_template("tpl-1", {"visibility": "official"})
        with self.assertRaisesRegex(ValidationError, "template field type is not supported by the public SDK"):
            service.create_template({
                "name": "demo",
                "image": "docker.io/library/alpine:3.20",
                "type": "base",
            })
        with self.assertRaises(ValidationError):
            service.get_build_status("tpl-1", "build-1", BuildStatusParams(limit=101))
        with self.assertRaises(ValidationError):
            service.get_build_logs("tpl-1", "build-1", BuildLogsParams(source="invalid"))
        with self.assertRaises(ValidationError):
            service.get_build_file("tpl-1", "bad")

        def error_handler(request):
            raise HTTPError(
                request.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=FakeResponse(400, json.dumps({
                    "code": 400,
                    "message": "validation failed",
                    "error": {"code": "INVALID_HASH", "details": "hash must be sha256"},
                    "request_id": "req-build-1",
                }), reason="Bad Request"),
            )

        error_client = MockBuildService(error_handler)
        with self.assertRaises(APIError) as ctx:
            error_client.get_build_file("tpl-1", "a" * 64)
        self.assertEqual(ctx.exception.request_id, "req-build-1")
