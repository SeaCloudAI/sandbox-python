from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from ..core.transport import BaseTransport
from ..core.exceptions import ValidationError
from .models import ConnectSandboxResponse, ListSandboxesParams, SandboxLogsParams


class ControlService(BaseTransport):
    def create_sandbox(self, body: Mapping[str, Any]) -> dict[str, Any]:
        if not str(body.get("templateID", "")).strip():
            raise ValidationError("templateID is required")
        return self._request_json(
            "POST",
            "/api/v1/sandboxes",
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(201,),
        )

    def list_sandboxes(
        self,
        params: ListSandboxesParams | None = None,
    ) -> list[dict[str, Any]]:
        path = self._with_query("/api/v1/sandboxes", self._encode_list_params(params))
        return self._request_json("GET", path)

    def get_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        self._require_sandbox_id(sandbox_id)
        return self._request_json("GET", f"/api/v1/sandboxes/{quote(sandbox_id, safe='')}")

    def delete_sandbox(self, sandbox_id: str) -> None:
        self._require_sandbox_id(sandbox_id)
        self._request_empty(
            "DELETE",
            f"/api/v1/sandboxes/{quote(sandbox_id, safe='')}",
            expected_statuses=(204,),
        )

    def get_sandbox_logs(
        self,
        sandbox_id: str,
        params: SandboxLogsParams | None = None,
    ) -> dict[str, Any]:
        self._require_sandbox_id(sandbox_id)
        self._validate_logs_params(params)
        path = self._with_query(
            f"/api/v1/sandboxes/{quote(sandbox_id, safe='')}/logs",
            self._encode_logs_params(params),
        )
        return self._request_json("GET", path)

    def pause_sandbox(self, sandbox_id: str) -> None:
        self._require_sandbox_id(sandbox_id)
        self._request_empty(
            "POST",
            f"/api/v1/sandboxes/{quote(sandbox_id, safe='')}/pause",
            expected_statuses=(204,),
        )

    def connect_sandbox(
        self,
        sandbox_id: str,
        body: Mapping[str, Any],
    ) -> ConnectSandboxResponse:
        self._require_sandbox_id(sandbox_id)
        self._validate_timeout(body.get("timeout"), "connect timeout")
        response = self._request_response(
            "POST",
            f"/api/v1/sandboxes/{quote(sandbox_id, safe='')}/connect",
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(200, 201),
        )
        with response:
            return ConnectSandboxResponse(
                status_code=response.status,
                sandbox=json.loads(response.read().decode("utf-8")),
            )

    def set_sandbox_timeout(
        self,
        sandbox_id: str,
        body: Mapping[str, Any],
    ) -> None:
        self._require_sandbox_id(sandbox_id)
        self._validate_timeout(body.get("timeout"), "timeout")
        self._request_empty(
            "POST",
            f"/api/v1/sandboxes/{quote(sandbox_id, safe='')}/timeout",
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(204,),
        )

    def refresh_sandbox(
        self,
        sandbox_id: str,
        body: Mapping[str, Any] | None = None,
    ) -> None:
        self._require_sandbox_id(sandbox_id)
        self._validate_refresh_duration(None if body is None else body.get("duration"))
        self._request_empty(
            "POST",
            f"/api/v1/sandboxes/{quote(sandbox_id, safe='')}/refreshes",
            headers=None if body is None else self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(204,),
        )

    def send_heartbeat(
        self,
        sandbox_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._require_sandbox_id(sandbox_id)
        self._validate_heartbeat_status(str(body.get("status", "")))
        wrapped = self._request_json(
            "POST",
            f"/api/v1/sandboxes/{quote(sandbox_id, safe='')}/heartbeat",
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
        )
        result = dict(wrapped["data"])
        result["request_id"] = wrapped.get("request_id")
        return result

    def get_pool_status(self) -> dict[str, Any]:
        wrapped = self._request_json("GET", "/admin/pool/status")
        result = dict(wrapped["data"])
        result["request_id"] = wrapped.get("request_id")
        return result

    def start_rolling_update(
        self,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not str(body.get("templateId", "")).strip():
            raise ValidationError("templateId is required")
        wrapped = self._request_json(
            "POST",
            "/admin/rolling/start",
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
        )
        result = dict(wrapped["data"])
        result["request_id"] = wrapped.get("request_id")
        return result

    def get_rolling_update_status(self) -> dict[str, Any]:
        wrapped = self._request_json("GET", "/admin/rolling/status")
        result = dict(wrapped["data"])
        result["request_id"] = wrapped.get("request_id")
        return result

    def cancel_rolling_update(self) -> dict[str, Any]:
        wrapped = self._request_json("POST", "/admin/rolling/cancel")
        result = dict(wrapped["data"])
        result["request_id"] = wrapped.get("request_id")
        return result

    def _require_sandbox_id(self, sandbox_id: str) -> None:
        if not sandbox_id.strip():
            raise ValidationError("sandbox_id is required")

    def _validate_timeout(self, timeout: Any, field: str) -> None:
        if not isinstance(timeout, int) or timeout < 0 or timeout > 86400:
            raise ValidationError(f"{field} must be an integer between 0 and 86400")

    def _validate_refresh_duration(self, duration: Any) -> None:
        if duration is None:
            return
        if not isinstance(duration, int) or duration < 0 or duration > 3600:
            raise ValidationError("refresh duration must be an integer between 0 and 3600")

    def _validate_heartbeat_status(self, status: str) -> None:
        if status.strip() not in {"starting", "healthy", "error"}:
            raise ValidationError("heartbeat status must be one of starting, healthy, error")

    def _validate_logs_params(self, params: SandboxLogsParams | None) -> None:
        if params is None:
            return
        if params.cursor is not None and (not isinstance(params.cursor, int) or params.cursor < 0):
            raise ValidationError("logs cursor must be a non-negative integer")
        if params.limit is not None and (not isinstance(params.limit, int) or params.limit < 0 or params.limit > 1000):
            raise ValidationError("logs limit must be an integer between 0 and 1000")
        if params.direction and params.direction not in {"forward", "backward"}:
            raise ValidationError('logs direction must be "forward" or "backward"')
        if params.search is not None and len(params.search) > 256:
            raise ValidationError("logs search must be at most 256 characters")

    def _with_query(self, path: str, params: Mapping[str, Any]) -> str:
        if not params:
            return path
        return f"{path}?{urlencode(params, doseq=True)}"

    def _encode_list_params(self, params: ListSandboxesParams | None) -> dict[str, Any]:
        if params is None:
            return {}
        query: dict[str, Any] = {}
        if params.metadata:
            query["metadata"] = urlencode(params.metadata)
        if params.state:
            query["state"] = [item.strip() for item in params.state if item.strip()]
        if params.limit is not None:
            query["limit"] = str(params.limit)
        if params.next_token:
            query["nextToken"] = params.next_token
        return query

    def _encode_logs_params(self, params: SandboxLogsParams | None) -> dict[str, str]:
        if params is None:
            return {}
        query: dict[str, str] = {}
        if params.cursor is not None:
            query["cursor"] = str(params.cursor)
        if params.limit is not None:
            query["limit"] = str(params.limit)
        if params.direction:
            query["direction"] = params.direction
        if params.level:
            query["level"] = params.level
        if params.search:
            query["search"] = params.search
        return query
