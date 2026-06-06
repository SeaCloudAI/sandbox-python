from __future__ import annotations

import json
import os
import ssl
import unittest
from typing import Any
from urllib.error import HTTPError, URLError

from sandbox import Template
from sandbox._client import GatewayClient
from sandbox.cmd import CmdRequestOptions, CommandService, DownloadRequest, UploadBytesRequest
import sandbox.cmd.service as cmd_service_module
from sandbox.control import ListSandboxesParams, SandboxLogsParams, SandboxMetricsParams
from sandbox.core import APIError, NotFoundError, RequestTimeoutError, ValidationError
from sandbox.core import RateLimitError


class FakeResponse:
    def __init__(
        self,
        status: int,
        body: str,
        reason: str = "OK",
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._body = raw_body if raw_body is not None else body.encode("utf-8")
        self.reason = reason
        self.headers = headers or {}

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


class MockGatewayClient(GatewayClient):
    def __init__(self, handler) -> None:
        super().__init__(
            base_url="https://sandbox-gateway.cloud.seaart.ai/api/v1",
            api_key="unit-auth-value",
            project_id="project-1",
        )
        self._handler = handler
        self.build.open = handler

    def open(self, request):
        return self._handler(request)


class MockCommandService(CommandService):
    def __init__(self, handler) -> None:
        super().__init__(base_url="https://sandbox-gateway.cloud.seaart.ai/api/v1", access_token="unit-runtime-auth")
        self._handler = handler

    def _open_request(self, method, path, **kwargs):
        return self._handler(method, path, kwargs)


class GatewayClientUnitTest(unittest.TestCase):
    def test_system_endpoints(self) -> None:
        client = MockGatewayClient(lambda request: FakeResponse(200, "metric 1\n"))
        self.assertEqual(client.metrics(), "metric 1\n")

        client = MockGatewayClient(lambda request: FakeResponse(200, json.dumps({"message": "shutdown initiated"})))
        self.assertEqual(client.shutdown()["message"], "shutdown initiated")

        def observability_handler(request):
            self.assertEqual(request.full_url, "https://sandbox-gateway.cloud.seaart.ai/api/v1/observability/summary")
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("X-project-id"), "project-1")
            return FakeResponse(200, json.dumps({
                "status": "ok",
                "projectID": "project-1",
                "userID": "user-1",
                "usage": {
                    "sandboxes": {
                        "resource": "sandboxes",
                        "user": {"limits": {"held": {"limit": 20, "used": 1, "remaining": 19, "enforced": True}}},
                    },
                    "templates": {
                        "resource": "templates",
                        "user": {"limits": {"concurrentBuilds": {"limit": 3, "used": 0, "remaining": 3, "enforced": True}}},
                    },
                },
                "availability": {
                    "sandboxes": {"status": "available"},
                    "templates": {"status": "available"},
                },
                "checks": [{
                    "status": "exhausted",
                    "scope": "user",
                    "resource": "templates",
                    "metric": "concurrentBuilds",
                    "used": 3,
                    "limit": 3,
                    "remaining": 0,
                    "message": "User concurrent build quota is exhausted.",
                    "usageEndpoint": "/api/v1/usage/template-limits",
                }],
                "actions": [{
                    "status": "limit_reached",
                    "scope": "user",
                    "resource": "templates",
                    "message": "User concurrent build quota is exhausted. Review current usage before retrying.",
                    "endpoint": "/api/v1/usage/template-limits",
                }],
                "endpoints": {
                    "sandboxUsage": "/api/v1/usage/limits",
                    "templateUsage": "/api/v1/usage/template-limits",
                    "sandboxDetail": "/api/v1/sandboxes/{sandboxID}",
                    "sandboxMetrics": "/api/v1/sandboxes/{sandboxID}/metrics",
                    "sandboxLogs": "/api/v1/sandboxes/{sandboxID}/logs",
                    "buildStatus": "/api/v1/templates/{templateID}/builds/{buildID}/status",
                    "buildLogs": "/api/v1/templates/{templateID}/builds/{buildID}/logs",
                },
            }))

        client = MockGatewayClient(observability_handler)
        summary = client.get_observability_summary()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["projectID"], "project-1")
        self.assertEqual(summary["usage"]["templates"]["user"]["limits"]["concurrentBuilds"]["remaining"], 3)
        self.assertEqual(summary["checks"][0]["metric"], "concurrentBuilds")
        self.assertEqual(summary["actions"][0]["status"], "limit_reached")
        self.assertEqual(summary["endpoints"]["buildStatus"], "/api/v1/templates/{templateID}/builds/{buildID}/status")

    def test_rate_limit_errors_expose_public_diagnostics(self) -> None:
        def handler(request):
            return FakeResponse(429, json.dumps({
                "code": 429,
                "message": "sandbox limit exceeded",
                "requestID": "req-camel",
                "details": {
                    "reason": "usage_limit",
                    "scope": "project",
                    "resource": "sandboxes",
                    "metric": "dailyCreates",
                    "used": 101,
                    "limit": 100,
                    "remaining": 0,
                    "usageEndpoint": "/api/v1/usage/limits",
                },
            }), reason="Too Many Requests")

        client = MockGatewayClient(handler)
        with self.assertRaises(RateLimitError) as ctx:
            client.create_sandbox({"templateID": "tpl"})
        self.assertEqual(ctx.exception.request_id, "req-camel")
        self.assertEqual(ctx.exception.details["scope"], "project")
        self.assertEqual(ctx.exception.details["metric"], "dailyCreates")
        self.assertEqual(ctx.exception.details["usageEndpoint"], "/api/v1/usage/limits")
        self.assertEqual(ctx.exception.usage_limit["scope"], "project")
        self.assertEqual(ctx.exception.usage_limit["metric"], "dailyCreates")

    def test_sandbox_request_encoding(self) -> None:
        def handler(request):
            if request.full_url.endswith("/api/v1/sandboxes"):
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.get_header("Content-type"), "application/json")
                self.assertEqual(request.get_header("X-project-id"), "project-1")
                self.assertEqual(json.loads(request.data.decode("utf-8")), {
                    "templateID": "tpl",
                    "waitReady": True,
                    "network": {
                        "allowInternetAccess": False,
                        "allowOut": ["1.1.1.1"],
                    },
                })
                return FakeResponse(201, json.dumps({
                    "sandboxID": "sb-1",
                    "envdUrl": "https://sandbox-gateway.cloud.seaart.ai",
                    "envdAccessToken": "unit-runtime-auth",
                    "network": {
                        "allowInternetAccess": False,
                        "allowOut": ["1.1.1.1/32"],
                    },
                    "timeline": [
                        {"phase": "created", "status": "completed", "timestamp": "2026-01-01T00:00:00Z"},
                    ],
                    "diagnostic": {
                        "reason": "startup_pending",
                        "message": "Sandbox startup is pending.",
                    },
                }))
            self.fail("unexpected request")

        client = MockGatewayClient(handler)
        response = client.create_sandbox({
            "templateID": "tpl",
            "waitReady": True,
            "network": {
                "allowInternetAccess": False,
                "allowOut": ["1.1.1.1"],
            },
        })
        self.assertEqual(response["sandboxID"], "sb-1")
        self.assertEqual(response["network"]["allowOut"], ["1.1.1.1/32"])
        self.assertEqual(response["timeline"][0]["phase"], "created")
        self.assertEqual(response["diagnostic"]["reason"], "startup_pending")
        self.assertEqual(response.runtime.base_url, "https://sandbox-gateway.cloud.seaart.ai")

    def test_create_sandbox_defaults_template_when_omitted(self) -> None:
        def handler(request):
            self.assertEqual(request.full_url, "https://sandbox-gateway.cloud.seaart.ai/api/v1/sandboxes")
            self.assertEqual(json.loads(request.data.decode("utf-8")), {
                "waitReady": False,
                "autoResume": True,
                "allowInternetAccess": False,
                "volumeMounts": [{"name": "cache", "path": "/cache"}],
            })
            return FakeResponse(201, json.dumps({"sandboxID": "sb-2", "templateID": "base"}))

        client = MockGatewayClient(handler)
        response = client.create_sandbox({
            "waitReady": False,
            "autoResume": True,
            "allowInternetAccess": False,
            "volumeMounts": [{"name": "cache", "path": "/cache"}],
        })
        self.assertEqual(response["sandboxID"], "sb-2")

    def test_lifecycle_volumes_and_teams_requests(self) -> None:
        calls: list[tuple[str, str]] = []

        def handler(request):
            calls.append((request.get_method(), request.full_url))
            self.assertEqual(request.get_header("X-namespace-id"), "ns-1")
            self.assertEqual(request.get_header("X-user-id"), "user-1")
            if request.full_url.endswith("/api/v1/events/sandboxes?limit=5&orderAsc=true&types=sandbox.lifecycle.created"):
                return FakeResponse(200, json.dumps([{
                    "version": "v1",
                    "id": "evt-1",
                    "type": "sandbox.lifecycle.created",
                    "sandboxId": "sb-1",
                    "sandboxTeamId": "project-1",
                    "timestamp": "2026-06-04T09:00:00Z",
                }]))
            if request.full_url.endswith("/api/v1/events/webhooks") and request.get_method() == "POST":
                body = json.loads(request.data.decode("utf-8"))
                self.assertEqual(body["retryPolicy"]["maxAttempts"], 5)
                return FakeResponse(201, json.dumps({
                    "id": "wh-1",
                    "teamId": "project-1",
                    "name": "lifecycle",
                    "createdAt": "2026-06-04T09:00:00Z",
                    "enabled": True,
                    "url": "https://example.com/hook",
                    "events": ["sandbox.lifecycle.created"],
                    "retryPolicy": {"maxAttempts": 5, "delaySeconds": [1, 5], "deadLetterEnabled": True},
                    "deadLetterUrl": "https://example.com/dlq",
                }))
            if request.full_url.endswith("/api/v1/events/webhook-deliveries?webhookID=wh-1"):
                return FakeResponse(200, json.dumps([{
                    "id": "del-1",
                    "eventId": "evt-1",
                    "webhookId": "wh-1",
                    "namespaceId": "ns-1",
                    "teamId": "project-1",
                    "url": "https://example.com/hook",
                    "status": "succeeded",
                    "attempts": 1,
                    "createdAt": "2026-06-04T09:00:00Z",
                }]))
            if request.full_url.endswith("/api/v1/events/webhook-deliveries/del-1/replay"):
                return FakeResponse(202, json.dumps({
                    "id": "del-2",
                    "eventId": "evt-1",
                    "webhookId": "wh-1",
                    "namespaceId": "ns-1",
                    "teamId": "project-1",
                    "url": "https://example.com/hook",
                    "status": "pending",
                    "attempts": 0,
                    "createdAt": "2026-06-04T09:00:01Z",
                }))
            if request.full_url.endswith("/api/v1/volumes") and request.get_method() == "POST":
                return FakeResponse(201, json.dumps({"volumeID": "vol-1", "name": "cache", "token": "token-1"}))
            if request.full_url.endswith("/api/v1/volumes") and request.get_method() == "GET":
                return FakeResponse(200, json.dumps([{"volumeID": "vol-1", "name": "cache"}]))
            if request.full_url.endswith("/api/v1/teams"):
                return FakeResponse(200, json.dumps([{"teamID": "project-1", "name": "project-1", "apiKey": "key-1", "isDefault": True}]))
            if request.full_url.endswith("/api/v1/teams/project-1/metrics/max?metric=concurrent_sandboxes"):
                return FakeResponse(200, json.dumps({"timestamp": "2026-06-04T09:00:00Z", "timestampUnix": 1780563600, "value": 3}))
            self.fail(f"unexpected request {request.get_method()} {request.full_url}")

        class EventGatewayClient(GatewayClient):
            def __init__(self) -> None:
                super().__init__(
                    base_url="https://sandbox-gateway.cloud.seaart.ai/api/v1",
                    api_key="unit-auth-value",
                    namespace_id="ns-1",
                    user_id="user-1",
                    project_id="project-1",
                )

            def open(self, request):
                return handler(request)

        client = EventGatewayClient()
        self.assertEqual(client.list_sandbox_events({"limit": 5, "orderAsc": True, "types": ["sandbox.lifecycle.created"]})[0]["id"], "evt-1")
        webhook = client.create_webhook({
            "name": "lifecycle",
            "url": "https://example.com/hook",
            "events": ["sandbox.lifecycle.created"],
            "signatureSecret": "secret",
            "retryPolicy": {"maxAttempts": 5, "delaySeconds": [1, 5], "deadLetterEnabled": True},
            "deadLetterUrl": "https://example.com/dlq",
        })
        self.assertEqual(webhook["retryPolicy"]["maxAttempts"], 5)
        self.assertEqual(client.list_webhook_deliveries({"webhookID": "wh-1"})[0]["status"], "succeeded")
        self.assertEqual(client.replay_webhook_delivery("del-1")["status"], "pending")
        self.assertEqual(client.create_volume({"name": "cache"})["token"], "token-1")
        self.assertEqual(client.list_volumes()[0]["volumeID"], "vol-1")
        self.assertEqual(client.list_teams()[0]["teamID"], "project-1")
        self.assertEqual(client.get_team_metrics_max("project-1", {"metric": "concurrent_sandboxes"})["value"], 3)
        self.assertEqual(len(calls), 8)

    def test_gateway_client_falls_back_to_seacloud_api_key(self) -> None:
        previous = os.environ.get("SEACLOUD_API_KEY")
        os.environ["SEACLOUD_API_KEY"] = "unit-auth-value"
        try:
            seen_headers = {}

            class SeaCloudGatewayEnvClient(GatewayClient):
                def open(self, request):
                    seen_headers.update({key.lower(): value for key, value in request.header_items()})
                    return FakeResponse(200, "[]")

            response = SeaCloudGatewayEnvClient(
                base_url="https://sandbox-gateway.cloud.seaart.ai/api/v1",
            ).list_sandboxes()
            self.assertEqual(response, [])
            self.assertEqual(seen_headers["authorization"], "Bearer unit-auth-value")
            self.assertEqual(seen_headers["x-api-key"], "unit-auth-value")
        finally:
            if previous is None:
                del os.environ["SEACLOUD_API_KEY"]
            else:
                os.environ["SEACLOUD_API_KEY"] = previous

    def test_gateway_client_falls_back_to_seacloud_base_url(self) -> None:
        previous_api_key = os.environ.get("SEACLOUD_API_KEY")
        previous_base_url = os.environ.get("SEACLOUD_BASE_URL")
        os.environ["SEACLOUD_API_KEY"] = "unit-auth-value"
        os.environ["SEACLOUD_BASE_URL"] = "seacloud.example.test/api/v1"
        try:
            seen_headers = {}
            seen_urls = []

            class SeaCloudPreferredGatewayEnvClient(GatewayClient):
                def open(self, request):
                    seen_urls.append(request.full_url)
                    seen_headers.update({key.lower(): value for key, value in request.header_items()})
                    return FakeResponse(200, "[]")

            response = SeaCloudPreferredGatewayEnvClient().list_sandboxes()
            self.assertEqual(response, [])
            self.assertTrue(seen_urls[0].startswith("https://seacloud.example.test/"))
            self.assertEqual(seen_headers["authorization"], "Bearer unit-auth-value")
            self.assertEqual(seen_headers["x-api-key"], "unit-auth-value")
        finally:
            if previous_api_key is None:
                del os.environ["SEACLOUD_API_KEY"]
            else:
                os.environ["SEACLOUD_API_KEY"] = previous_api_key
            if previous_base_url is None:
                del os.environ["SEACLOUD_BASE_URL"]
            else:
                os.environ["SEACLOUD_BASE_URL"] = previous_base_url

    def test_gateway_client_base_url_controls_gateway_api_root_path(self) -> None:
        seen_urls = []
        seen_headers = {}

        class SeaCloudPrefixedGatewayClient(GatewayClient):
            def open(self, request):
                seen_urls.append(request.full_url)
                seen_headers.update({key.lower(): value for key, value in request.header_items()})
                return FakeResponse(200, "[]")

        response = SeaCloudPrefixedGatewayClient(
            base_url="https://seacloud-sandbox-service.dev.seaart.dev/api/v1/sandbox",
            api_key="unit-auth-value",
        ).list_sandboxes()

        self.assertEqual(response, [])
        self.assertEqual(seen_urls[0], "https://seacloud-sandbox-service.dev.seaart.dev/api/v1/sandbox/sandboxes")
        self.assertEqual(seen_headers["authorization"], "Bearer unit-auth-value")
        self.assertEqual(seen_headers["x-api-key"], "unit-auth-value")

    def test_build_namespace_reuses_gateway_configuration(self) -> None:
        def handler(request):
            if request.full_url.endswith("/api/v1/templates"):
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.get_header("X-project-id"), "project-1")
                self.assertEqual(
                    json.loads(request.data.decode("utf-8")),
                    {"name": "demo", "cpuCount": 2, "memoryMB": 1024},
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

        client = MockGatewayClient(handler)
        response = client.build.create_template({"name": "demo", "cpuCount": 2, "memoryMB": 1024})
        self.assertEqual(response["templateID"], "tpl-1")

    def test_api_error_accepts_string_detail(self) -> None:
        client = MockGatewayClient(lambda request: FakeResponse(404, json.dumps({"error": "not found"}), reason="Not Found"))

        with self.assertRaises(NotFoundError) as raised:
            client.get_sandbox("sb-1")

        self.assertEqual(str(raised.exception), "not found")
        self.assertEqual(raised.exception.status_code, 404)

    def test_list_and_logs_params_encoding(self) -> None:
        seen = []

        def handler(request):
            seen.append(request.full_url)
            if "/sandboxes?" in request.full_url:
                return FakeResponse(200, json.dumps([]), headers={"X-Next-Token": "Mg", "X-Has-Next": "true"})
            return FakeResponse(200, json.dumps({
                "logs": [],
                "hasMore": False,
                "diagnostic": {
                    "reason": "filters_applied",
                    "message": "No sandbox logs matched the current filters. Try removing search or level filters.",
                },
            }))

        client = MockGatewayClient(handler)
        page = client.list_sandboxes_page(
            params=type("P", (), {
                "metadata": {"app": "prod", "team": "core"},
                "state": ["running", "paused"],
                "limit": 10,
                "next_token": "MQ",
            })(),
        )
        logs = client.get_sandbox_logs(
            "sb-1",
            {"cursor": 0, "limit": 10, "direction": "forward", "level": "info", "search": "health"},
        )
        self.assertEqual(logs["diagnostic"]["reason"], "filters_applied")
        self.assertIn("metadata=app%3Dprod%26team%3Dcore", seen[0])
        self.assertIn("state=running", seen[0])
        self.assertIn("nextToken=MQ", seen[0])
        self.assertEqual(page.next_token, "Mg")
        self.assertTrue(page.has_next)
        self.assertIn("direction=forward", seen[1])
        self.assertIn("search=health", seen[1])

    def test_logger_receives_sanitized_request_lifecycle_events(self) -> None:
        events: list[dict[str, Any]] = []

        class LoggingGatewayClient(GatewayClient):
            def open(client_self, request, *, request_timeout_ms=None):
                self.assertIn("nextToken=secret-page", request.full_url)
                self.assertTrue(request.get_header("X-request-id") or request.get_header("X-Request-ID"))
                return FakeResponse(200, "[]")

        client = LoggingGatewayClient(
            base_url="https://sandbox-gateway.cloud.seaart.ai/api/v1",
            api_key="unit-auth-value",
            logger=lambda event: events.append(dict(event)),
        )
        self.assertEqual(client.list_sandboxes(ListSandboxesParams(next_token="secret-page")), [])

        self.assertEqual(events[0]["type"], "request")
        self.assertEqual(events[0]["method"], "GET")
        self.assertEqual(events[0]["path"], "/api/v1/sandboxes?nextToken=%3Credacted%3E")
        self.assertTrue(events[0]["request_id"])
        self.assertEqual(events[1]["type"], "response")
        self.assertEqual(events[1]["request_id"], events[0]["request_id"])
        self.assertFalse(any("unit-auth-value" in json.dumps(event) for event in events))

    def test_diagnostic_logger_failures_do_not_affect_requests(self) -> None:
        class LoggingGatewayClient(GatewayClient):
            def open(client_self, request, *, request_timeout_ms=None):
                return FakeResponse(200, "[]")

        client = LoggingGatewayClient(
            base_url="https://sandbox-gateway.cloud.seaart.ai/api/v1",
            api_key="unit-auth-value",
            logger=lambda event: (_ for _ in ()).throw(RuntimeError("logger failed")),
        )

        self.assertEqual(client.list_sandboxes(), [])

    def test_diagnostic_network_errors_redact_embedded_urls(self) -> None:
        events: list[dict[str, Any]] = []

        class FailingGatewayClient(GatewayClient):
            def open(client_self, request, *, request_timeout_ms=None):
                raise RuntimeError(
                    "Get https://sandbox-gateway.cloud.seaart.ai/api/v1/sandboxes?signature=secret-token failed",
                )

        client = FailingGatewayClient(
            base_url="https://sandbox-gateway.cloud.seaart.ai/api/v1",
            api_key="unit-auth-value",
            logger=lambda event: events.append(dict(event)),
        )

        with self.assertRaisesRegex(RuntimeError, "secret-token"):
            client.list_sandboxes()

        error_event = next(event for event in events if event["type"] == "error")
        self.assertNotIn("secret-token", error_event["error"])
        self.assertIn("signature=%3Credacted%3E", error_event["error"])

    def test_cmd_logger_redacts_signed_query_parameters(self) -> None:
        events: list[dict[str, Any]] = []
        original_urlopen = cmd_service_module.urlopen

        def fake_urlopen(request, timeout):
            self.assertIn("signature=signed-secret", request.full_url)
            self.assertTrue(request.get_header("X-request-id") or request.get_header("X-Request-ID"))
            return FakeResponse(200, "hell")

        cmd_service_module.urlopen = fake_urlopen
        try:
            service = CommandService(
                base_url="https://sandbox-gateway.cloud.seaart.ai/api/v1",
                access_token="unit-runtime-auth",
                logger=lambda event: events.append(dict(event)),
            )
            with service.download(
                DownloadRequest(path="~/hello.txt"),
                CmdRequestOptions(signature="signed-secret", signature_expiration=3600),
            ) as response:
                self.assertEqual(response.read().decode("utf-8"), "hell")
        finally:
            cmd_service_module.urlopen = original_urlopen

        self.assertEqual(events[0]["type"], "request")
        self.assertNotIn("signed-secret", events[0]["path"])
        self.assertIn("signature=%3Credacted%3E", events[0]["path"])
        self.assertEqual(events[1]["type"], "response")
        self.assertEqual(events[1]["request_id"], events[0]["request_id"])

    def test_list_returns_bound_handles(self) -> None:
        seen = []

        def handler(request):
            seen.append(request.full_url)
            if request.full_url.endswith("/api/v1/sandboxes"):
                return FakeResponse(200, json.dumps([{
                    "sandboxID": "sb-1",
                    "clientID": "u1",
                    "status": "running",
                }]))
            if request.full_url.endswith("/logs"):
                return FakeResponse(200, json.dumps({"logs": []}))
            return FakeResponse(200, json.dumps({
                "sandboxID": "sb-1",
                "envdUrl": "https://sandbox-gateway.cloud.seaart.ai",
                "envdAccessToken": "unit-runtime-auth",
            }))

        client = MockGatewayClient(handler)
        listed = client.list_sandboxes()
        self.assertEqual(listed[0]["sandboxID"], "sb-1")
        detail = listed[0].reload()
        listed[0].logs()
        self.assertEqual(detail.runtime.base_url, "https://sandbox-gateway.cloud.seaart.ai")
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
                    "envdUrl": "https://sandbox-gateway.cloud.seaart.ai",
                    "envdAccessToken": "unit-runtime-auth",
                }))
            if request.get_method() == "DELETE" or request.full_url.endswith("/pause") or request.full_url.endswith("/timeout") or request.full_url.endswith("/refreshes"):
                return FakeResponse(204, "")
            return FakeResponse(200, json.dumps({
                "sandboxID": "sb-1",
                "envdUrl": "https://sandbox-gateway.cloud.seaart.ai",
                "envdAccessToken": "unit-runtime-auth",
                "logs": [],
            }))

        client = MockGatewayClient(handler)
        self.assertEqual(client.get_sandbox("sb-1")["sandboxID"], "sb-1")
        self.assertTrue(client.send_heartbeat("sb-1", {"status": "healthy"})["received"])
        client.set_sandbox_timeout("sb-1", {"timeout": 1200})
        client.refresh_sandbox("sb-1", {"duration": 60})
        client.refresh_sandbox("sb-1")
        client.pause_sandbox("sb-1")
        self.assertEqual(client.connect_sandbox("sb-1", {"timeout": 1200}).status_code, 201)
        client.delete_sandbox("sb-1")

        self.assertEqual(calls[0][1], "https://sandbox-gateway.cloud.seaart.ai/api/v1/sandboxes/sb-1")
        self.assertEqual(calls[-1][0], "DELETE")

        pause_call = next(call for call in calls if call[1].endswith("/pause"))
        self.assertEqual(pause_call[2], b"")

        refresh_without_body = next(
            call for call in calls if call[1].endswith("/refreshes") and call[2] == b""
        )
        self.assertEqual(refresh_without_body[0], "POST")

    def test_sandbox_metrics_endpoints(self) -> None:
        seen: list[str] = []

        def handler(request):
            seen.append(request.full_url)
            if request.full_url.endswith("/api/v1/sandboxes/sb-1/metrics"):
                return FakeResponse(200, json.dumps({
                    "sandboxID": "sb-1",
                    "collectedAt": "2026-05-20T00:00:00Z",
                    "load1": 0.4,
                    "cpuUserRate": 0.2,
                    "memoryAvailableBytes": 1073741824,
                    "memoryUsagePercent": 50,
                    "diskReadBytesPerSecond": 4096,
                    "networkRecvBytesPerSecond": 100,
                    "taskCurrent": 3,
                }))
            if "/api/v1/sandboxes/metrics?" in request.full_url:
                self.assertIn("sandbox_ids=sb-1%2Csb-2", request.full_url)
                self.assertIn("limit=2", request.full_url)
                return FakeResponse(200, json.dumps({
                    "collectedAt": "2026-05-20T00:00:00Z",
                    "items": [{
                        "sandboxID": "sb-1",
                        "collectedAt": "2026-05-20T00:00:00Z",
                        "load1": 0.1,
                        "memoryUsagePercent": 10,
                        "networkRecvBytesPerSecond": 1,
                    }],
                    "sandboxes": {},
                }))
            self.fail(f"unexpected request: {request.full_url}")

        client = MockGatewayClient(handler)
        single = client.get_sandbox_metrics("sb-1")
        batch = client.list_sandbox_metrics({"sandboxIDs": ["sb-1", " ", "sb-2"], "limit": 2})

        self.assertEqual(single["load1"], 0.4)
        self.assertEqual(single["memoryUsagePercent"], 50)
        self.assertEqual(single["diskReadBytesPerSecond"], 4096)
        self.assertEqual(single["networkRecvBytesPerSecond"], 100)
        self.assertEqual(single["taskCurrent"], 3)
        self.assertEqual(batch["items"][0]["sandboxID"], "sb-1")
        self.assertEqual(len(seen), 2)

    def test_admin_control_endpoints(self) -> None:
        calls: list[tuple[str, str, dict[str, Any] | None]] = []

        def handler(request):
            body = json.loads(request.data.decode("utf-8")) if request.data else None
            calls.append((request.get_method(), request.full_url, body))
            if request.full_url.endswith("/admin/pool/status"):
                return FakeResponse(200, json.dumps({
                    "code": 0,
                    "data": {"total": 10, "warm": 2, "active": 3, "creating": 1, "stopped": 1, "deleting": 1, "deleted": 2, "utilization": 0.5},
                    "request_id": "req-pool",
                }))
            if request.full_url.endswith("/admin/rolling/start"):
                return FakeResponse(200, json.dumps({
                    "code": 0,
                    "data": {"phase": "running", "progress": 0.25, "warm_total": 4, "warm_updated": 1, "duration": "10s"},
                    "request_id": "req-start",
                }))
            if request.full_url.endswith("/admin/rolling/status"):
                return FakeResponse(200, json.dumps({
                    "code": 0,
                    "data": {"phase": "running", "progress": 0.5, "warm_total": 4, "warm_updated": 2, "duration": "20s"},
                    "request_id": "req-status",
                }))
            if request.full_url.endswith("/admin/rolling/cancel"):
                return FakeResponse(200, json.dumps({
                    "code": 0,
                    "data": {"phase": "cancelled", "progress": 0.5, "warm_total": 4, "warm_updated": 2, "duration": "21s"},
                    "request_id": "req-cancel",
                }))
            self.fail("unexpected request")

        client = MockGatewayClient(handler)
        self.assertEqual(client.get_pool_status()["request_id"], "req-pool")
        self.assertEqual(client.start_rolling_update({"templateId": "tpl-1"})["request_id"], "req-start")
        self.assertEqual(client.get_rolling_update_status()["request_id"], "req-status")
        self.assertEqual(client.cancel_rolling_update()["request_id"], "req-cancel")
        self.assertEqual(calls[1][2], {"templateId": "tpl-1"})
        with self.assertRaises(ValidationError):
            client.start_rolling_update({"templateId": " "})

    def test_bound_sandbox_helpers_reuse_original_client(self) -> None:
        seen: list[str] = []

        def handler(request):
            seen.append(request.full_url)
            if request.full_url.endswith("/api/v1/sandboxes"):
                return FakeResponse(201, json.dumps({
                    "sandboxID": "sb-1",
                    "envdUrl": "https://sandbox-gateway.cloud.seaart.ai",
                    "envdAccessToken": "unit-runtime-auth",
                }))
            if request.full_url.endswith("/logs"):
                return FakeResponse(200, json.dumps({"logs": []}))
            if request.full_url.endswith("/metrics"):
                return FakeResponse(200, json.dumps({
                    "sandboxID": "sb-1",
                    "collectedAt": "2026-05-20T00:00:00Z",
                    "load1": 0.1,
                    "memoryUsagePercent": 10,
                    "networkRecvBytesPerSecond": 1,
                }))
            return FakeResponse(200, json.dumps({
                "sandboxID": "sb-1",
                "envdUrl": "https://sandbox-gateway.cloud.seaart.ai",
                "envdAccessToken": "unit-runtime-auth",
            }))

        client = MockGatewayClient(handler)
        sandbox = client.create_sandbox({"templateID": "tpl"})
        detail = sandbox.reload()
        sandbox.logs()
        metrics = sandbox.metrics()

        self.assertEqual(detail["sandboxID"], "sb-1")
        self.assertEqual(metrics["sandboxID"], "sb-1")
        self.assertTrue(seen[1].endswith("/api/v1/sandboxes/sb-1"))
        self.assertTrue(seen[2].endswith("/api/v1/sandboxes/sb-1/logs"))
        self.assertTrue(seen[3].endswith("/api/v1/sandboxes/sb-1/metrics"))

    def test_high_level_client_helpers_reuse_stored_gateway_config(self) -> None:
        calls: list[tuple[str, str, dict[str, Any] | None]] = []

        def handler(request):
            body = json.loads(request.data.decode("utf-8")) if request.data else None
            calls.append((request.get_method(), request.full_url, body))
            if request.full_url.endswith("/api/v1/sandboxes"):
                return FakeResponse(201, json.dumps({
                    "sandboxID": "sb-high",
                    "envdUrl": "https://sandbox-gateway.cloud.seaart.ai",
                    "envdAccessToken": "unit-runtime-auth",
                    "status": "running",
                }))
            return FakeResponse(200, json.dumps({
                "sandboxID": "sb-high",
                "envdUrl": "https://sandbox-gateway.cloud.seaart.ai",
                "envdAccessToken": "unit-runtime-auth",
                "status": "running",
        }))

        client = MockGatewayClient(handler)
        sandbox = client.create("tpl", waitReady=True, network={
            "allowInternetAccess": False,
            "allowOut": ["1.1.1.1"],
        })
        info = sandbox.get_info()

        self.assertEqual(sandbox.sandbox_id, "sb-high")
        self.assertEqual(info["sandbox_id"], "sb-high")
        self.assertEqual(calls[0][2], {
            "templateID": "tpl",
            "waitReady": True,
            "network": {
                "allowInternetAccess": False,
                "allowOut": ["1.1.1.1"],
            },
        })
        self.assertEqual(calls[1][1], "https://sandbox-gateway.cloud.seaart.ai/api/v1/sandboxes/sb-high")

    def test_build_template_helper_reuses_build_service(self) -> None:
        calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []

        def handler(request):
            body = json.loads(request.data.decode("utf-8")) if request.data else None
            calls.append((request.get_method(), request.full_url, body, request.get_header("X-project-id")))
            if request.full_url.endswith("/api/v1/templates"):
                return FakeResponse(202, json.dumps({
                    "templateID": "tpl-1",
                    "buildID": "build-1",
                    "public": False,
                    "names": ["demo"],
                    "tags": [],
                    "aliases": [],
                }))
            if "/status" in request.full_url:
                return FakeResponse(200, json.dumps({"status": "ready", "logEntries": []}))
            if "/builds/" in request.full_url and request.get_method() == "POST":
                return FakeResponse(202, json.dumps({"buildID": "build-1", "status": "building"}))
            if "/builds/" in request.full_url:
                return FakeResponse(200, json.dumps({"buildID": "build-1", "status": "ready"}))
            return FakeResponse(200, json.dumps({
                "templateID": "tpl-1",
                "names": ["demo"],
                "tags": ["v1"],
                "aliases": [],
                "public": False,
            }))

        client = MockGatewayClient(handler)
        built = client.build_template(Template().from_base_image().run_cmd("echo hello"), "demo:v1", cpu_count=2)

        self.assertEqual(built["template_id"], "tpl-1")
        self.assertEqual(calls[0][1], "https://sandbox-gateway.cloud.seaart.ai/api/v1/templates")
        self.assertEqual(calls[0][2], {"name": "demo", "tags": ["v1"], "cpuCount": 2})
        self.assertEqual(calls[0][3], "project-1")

    def test_validations(self) -> None:
        client = MockGatewayClient(lambda request: FakeResponse(200, "{}"))

        with self.assertRaises(ValidationError):
            client.get_sandbox_logs("sb", SandboxLogsParams(limit=1001))
        with self.assertRaises(ValidationError):
            client.connect_sandbox("sb", {"timeout": -1})
        with self.assertRaises(ValidationError):
            client.set_sandbox_timeout("sb", {"timeout": 86_401})
        with self.assertRaises(ValidationError):
            client.refresh_sandbox("sb", {"duration": 3601})
        with self.assertRaises(ValidationError):
            client.send_heartbeat("sb", {"status": "bad"})

    def test_boundary_values_are_accepted(self) -> None:
        calls = []

        def handler(request):
            calls.append((request.get_method(), request.full_url, request.data))
            if request.full_url.endswith("/connect"):
                return FakeResponse(200, json.dumps({"sandboxID": "sb"}))
            if request.full_url.endswith("/heartbeat"):
                return FakeResponse(200, json.dumps({
                    "code": 0,
                    "message": "success",
                    "data": {"received": True, "status": "healthy"},
                    "request_id": "req-boundary",
                }))
            if "/logs" in request.full_url:
                return FakeResponse(200, json.dumps({"logs": []}))
            return FakeResponse(204, "")

        client = MockGatewayClient(handler)
        client.get_sandbox_logs("sb", SandboxLogsParams(cursor=0, limit=1000, direction="backward", search="x" * 256))
        client.connect_sandbox("sb", {"timeout": 0})
        client.set_sandbox_timeout("sb", {"timeout": 86_400})
        client.refresh_sandbox("sb", {"duration": 0})
        client.refresh_sandbox("sb", {"duration": 3600})
        client.send_heartbeat("sb", {"status": "healthy"})

        self.assertEqual(len(calls), 6)

    def test_empty_sandbox_ids_are_rejected(self) -> None:
        client = MockGatewayClient(lambda request: FakeResponse(200, "{}"))

        with self.assertRaises(ValidationError):
            client.get_sandbox(" ")
        with self.assertRaises(ValidationError):
            client.pause_sandbox(" ")
        with self.assertRaises(ValidationError):
            client.connect_sandbox(" ", {"timeout": 1})
        with self.assertRaises(ValidationError):
            client.set_sandbox_timeout(" ", {"timeout": 1})
        with self.assertRaises(ValidationError):
            client.refresh_sandbox(" ", {"duration": 1})
        with self.assertRaises(ValidationError):
            client.send_heartbeat(" ", {"status": "healthy"})

    def test_api_error_decoding(self) -> None:
        def handler(request):
            raise HTTPError(
                request.full_url,
                404,
                "Not Found",
                hdrs=None,
                fp=FakeResponse(404, json.dumps({"code": 404, "message": "Not found"}), reason="Not Found"),
            )

        client = MockGatewayClient(handler)
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

    def test_cmd_envs_configure_and_ports(self) -> None:
        calls: list[tuple[str, str, dict[str, Any]]] = []

        def handler(method, path, kwargs):
            calls.append((method, path, kwargs))
            if path == "/envs":
                return FakeResponse(200, json.dumps({"NODE_ENV": "production"}))
            if path == "/configure":
                return FakeResponse(204, "")
            if path == "/ports":
                return FakeResponse(200, json.dumps([{"port": 3000, "protocol": "tcp", "address": "127.0.0.1"}]))
            self.fail(f"unexpected path {path}")

        cmd = MockCommandService(handler)
        self.assertEqual(cmd.envs(), {"NODE_ENV": "production"})
        cmd.configure({"envs": {"A": "1"}})
        self.assertEqual(cmd.ports(), [{"port": 3000, "protocol": "tcp", "address": "127.0.0.1"}])
        self.assertEqual(calls[1][2]["body"], {"envs": {"A": "1"}})

    def test_cmd_watcher_and_file_helpers(self) -> None:
        def handler(method, path, kwargs):
            if path == "/filesystem.Filesystem/CreateWatcher":
                self.assertEqual(kwargs["body"], {"path": "/tmp", "recursive": True})
                return FakeResponse(200, json.dumps({"watcherId": "watch-1"}))
            if path == "/filesystem.Filesystem/GetWatcherEvents":
                self.assertEqual(kwargs["body"], {"watcherId": "watch-1", "limit": 10})
                return FakeResponse(200, json.dumps({"events": [{"name": "a.txt", "type": "EVENT_TYPE_WRITE"}]}))
            if path == "/filesystem.Filesystem/RemoveWatcher":
                self.assertEqual(kwargs["body"], {"watcherId": "watch-1"})
                return FakeResponse(200, json.dumps({}))
            if path.startswith("/files?"):
                self.assertIn("path=%2Ftmp", path)
                self.assertIn("multipart/form-data", kwargs["headers"]["Content-Type"])
                self.assertIn(b'filename="hello.txt"', kwargs["data"])
                return FakeResponse(200, json.dumps([{"path": "/tmp/hello.txt", "name": "hello.txt", "type": "file"}]))
            if path == "/files/compose":
                self.assertEqual(kwargs["body"], {"source_paths": ["/tmp/a.txt", "/tmp/b.txt"], "destination": "/tmp/out.txt"})
                return FakeResponse(200, json.dumps({"path": "/tmp/out.txt", "name": "out.txt", "type": "file"}))
            self.fail(f"unexpected path {path}")

        cmd = MockCommandService(handler)
        watcher = cmd.create_watcher({"path": "/tmp", "recursive": True})
        events = cmd.get_watcher_events({"watcherId": watcher["watcherId"], "limit": 10})
        cmd.remove_watcher({"watcherId": watcher["watcherId"]})
        uploaded = cmd.upload_multipart(type("Req", (), {
            "path": "/tmp",
            "parts": [type("Part", (), {
                "data": b"hello",
                "field_name": "file",
                "file_name": "hello.txt",
                "content_type": "text/plain",
            })()],
        })())
        composed = cmd.compose_files({"source_paths": ["/tmp/a.txt", "/tmp/b.txt"], "destination": "/tmp/out.txt"})

        self.assertEqual(watcher["watcherId"], "watch-1")
        self.assertEqual(events["events"], [{"name": "a.txt", "type": "EVENT_TYPE_WRITE"}])
        self.assertEqual(uploaded, [{"path": "/tmp/hello.txt", "name": "hello.txt", "type": "file"}])
        self.assertEqual(composed["path"], "/tmp/out.txt")
        with self.assertRaises(ValidationError):
            cmd.get_watcher_events({"watcherId": " "})
        with self.assertRaises(ValidationError):
            cmd.remove_watcher({"watcherId": " "})
        with self.assertRaises(ValidationError):
            cmd.upload_multipart(type("Req", (), {"path": "/tmp", "parts": []})())

    def test_cmd_files_content_upload_bytes_upload_json_and_edit(self) -> None:
        def handler(method, path, kwargs):
            if path == "/files/content?path=%2Ftmp%2Fa.txt&max_tokens=32":
                return FakeResponse(200, json.dumps({"type": "text", "content": "hello", "truncated": False}))
            if path == "/files?path=%2Ftmp%2Fa.txt":
                self.assertEqual(kwargs["headers"]["Content-Encoding"], "gzip")
                self.assertEqual(kwargs["data"][:2], b"\x1f\x8b")
                return FakeResponse(200, json.dumps([{"path": "/tmp/a.txt", "name": "a.txt", "type": "file"}]))
            if path == "/files":
                self.assertEqual(kwargs["body"], {"path": "/tmp/b.txt", "content": "hello"})
                return FakeResponse(200, json.dumps([{"path": "/tmp/b.txt", "name": "b.txt", "type": "file"}]))
            if path == "/filesystem.Filesystem/Edit":
                self.assertEqual(kwargs["body"], {"path": "/tmp/a.txt", "oldText": "a", "newText": "b"})
                return FakeResponse(200, json.dumps({"message": "ok"}))
            self.fail(f"unexpected path {path}")

        cmd = MockCommandService(handler)
        self.assertEqual(
            cmd.files_content(type("Req", (), {"path": "/tmp/a.txt", "max_tokens": 32})()),
            {"type": "text", "content": "hello", "truncated": False},
        )
        self.assertEqual(
            cmd.upload_bytes(UploadBytesRequest(path="/tmp/a.txt", data=b"hello", gzip_compress=True)),
            [{"path": "/tmp/a.txt", "name": "a.txt", "type": "file"}],
        )
        self.assertEqual(
            cmd.upload_json({"path": "/tmp/b.txt", "content": "hello"}),
            [{"path": "/tmp/b.txt", "name": "b.txt", "type": "file"}],
        )
        self.assertEqual(
            cmd.edit({"path": "/tmp/a.txt", "oldText": "a", "newText": "b"}),
            {"message": "ok"},
        )

    def test_cmd_invalid_process_and_path_inputs(self) -> None:
        cmd = MockCommandService(lambda method, path, kwargs: FakeResponse(200, "{}"))

        with self.assertRaises(ValidationError):
            cmd.create_watcher({"path": " "})
        with self.assertRaises(ValidationError):
            cmd.files_content(type("Req", (), {"path": " ", "max_tokens": None})())
        with self.assertRaises(ValidationError):
            cmd.upload_json({"path": " "})
        with self.assertRaises(ValidationError):
            cmd.edit({"path": " ", "oldText": "a", "newText": "b"})
        with self.assertRaises(ValidationError):
            cmd.stream_input([])
        with self.assertRaises(ValidationError):
            cmd.send_input({"process": {}, "input": {"stdin": ""}})
        with self.assertRaises(ValidationError):
            cmd.send_input({"process": {"pid": 1, "tag": "x"}, "input": {"stdin": "x"}})
        with self.assertRaises(ValidationError):
            cmd.send_signal({"process": {}, "signal": "SIGNAL_SIGKILL"})
        with self.assertRaises(ValidationError):
            cmd.close_stdin({"process": {}})
        with self.assertRaises(ValidationError):
            cmd.get_result({"cmdId": " "})

    def test_runtime_from_sandbox_uses_envd_fields(self) -> None:
        client = MockGatewayClient(lambda request: FakeResponse(200, "{}"))
        runtime = client.runtime_from_sandbox({
            "envdUrl": "https://sandbox-gateway.cloud.seaart.ai",
            "envdAccessToken": "unit-runtime-auth",
        })
        self.assertEqual(runtime.base_url, "https://sandbox-gateway.cloud.seaart.ai")
        self.assertEqual(runtime.access_token, "unit-runtime-auth")

    def test_transport_timeout_raises_typed_error(self) -> None:
        client = MockGatewayClient(lambda request: (_ for _ in ()).throw(TimeoutError("timed out")))
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

    def test_cmd_watch_dir_skips_keepalive_and_stops_on_end_frame(self) -> None:
        stream_bytes = empty_connect_frame() + connect_frame({
            "filesystem": {"type": "EVENT_TYPE_WRITE", "name": "a.txt"},
        }) + empty_connect_frame(0x02)

        def handler(method, path, kwargs):
            if path == "/filesystem.Filesystem/WatchDir":
                self.assertEqual(kwargs["headers"]["Content-Type"], "application/connect+json")
                return FakeResponse(200, "", raw_body=stream_bytes)
            self.fail(f"unexpected path {path}")

        cmd = MockCommandService(handler)
        stream = cmd.watch_dir({"path": "/tmp", "recursive": True})
        first = stream.next()
        second = stream.next()
        stream.close()
        self.assertEqual(first, {"filesystem": {"type": "EVENT_TYPE_WRITE", "name": "a.txt"}})
        self.assertIsNone(second)

    def test_cmd_connect_retries_transient_ssl_eof_once(self) -> None:
        calls = 0
        original_urlopen = cmd_service_module.urlopen

        def fake_urlopen(request, timeout=30.0):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol"))
            return FakeResponse(200, "", raw_body=connect_frame({
                "event": {"start": {"pid": 77, "cmdId": "cmd-77"}},
            }))

        cmd_service_module.urlopen = fake_urlopen
        try:
            cmd = CommandService(base_url="https://sandbox-runtime.cloud.seaart.ai", access_token="unit-runtime-auth")
            stream = cmd.connect({"process": {"pid": 77}})
            first = stream.next()
            stream.close()
        finally:
            cmd_service_module.urlopen = original_urlopen

        self.assertEqual(calls, 2)
        self.assertEqual(first["event"]["start"]["pid"], 77)

    def test_cmd_stream_input_returns_raw_end_frame(self) -> None:
        def handler(method, path, kwargs):
            if path == "/process.Process/StreamInput":
                return FakeResponse(200, "", raw_body=empty_connect_frame(0x02))
            self.fail(f"unexpected path {path}")

        cmd = MockCommandService(handler)
        frame = cmd.stream_input([{"keepalive": {}}])
        self.assertIsNotNone(frame)
        self.assertEqual(frame.flags, 0x02)
        self.assertEqual(frame.payload, b"")

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
        cmd = CommandService(base_url="https://sandbox-gateway.cloud.seaart.ai/sandbox/sb-1", access_token="unit-runtime-auth")
        self.assertEqual(cmd._build_url("/run"), "https://sandbox-gateway.cloud.seaart.ai/sandbox/sb-1/run")


def connect_frame(payload: dict[str, object]) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    return bytes([0]) + len(data).to_bytes(4, "big") + data


def empty_connect_frame(flags: int = 0) -> bytes:
    return bytes([flags]) + (0).to_bytes(4, "big")


def decode_frames(data: bytes) -> list[dict[str, bytes | int]]:
    frames: list[dict[str, bytes | int]] = []
    offset = 0
    while offset < len(data):
        size = int.from_bytes(data[offset + 1:offset + 5], "big")
        payload = data[offset + 5:offset + 5 + size]
        frames.append({"flags": data[offset], "payload": payload})
        offset += 5 + size
    return frames
