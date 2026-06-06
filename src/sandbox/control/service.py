from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from ..core.transport import BaseTransport
from ..core.exceptions import ValidationError
from .models import (
    ConnectSandboxResponse,
    ListSandboxesParams,
    SandboxesPage,
    SandboxEventsParams,
    SandboxLogsParams,
    SandboxMetricsParams,
    TeamMetricsMaxParams,
    TeamMetricsParams,
    WebhookDeliveriesParams,
)


class ControlService(BaseTransport):
    def create_sandbox(self, body: Mapping[str, Any] | None = None, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        body = dict(body or {})
        _reject_unsupported_create_fields(body)
        return self._request_json(
            "POST",
            self.api_path("/sandboxes"),
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(201,),
            request_timeout_ms=request_timeout_ms,
        )

    def list_sandbox_events(
        self,
        params: SandboxEventsParams | Mapping[str, Any] | None = None,
        *,
        request_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        path = self._with_query(self.api_path("/events/sandboxes"), self._encode_events_params(params))
        return self._request_json("GET", path, request_timeout_ms=request_timeout_ms)

    def list_sandbox_events_by_sandbox(
        self,
        sandbox_id: str,
        params: SandboxEventsParams | Mapping[str, Any] | None = None,
        *,
        request_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        self._require_sandbox_id(sandbox_id)
        path = self._with_query(
            self.api_path(f"/events/sandboxes/{quote(sandbox_id, safe='')}"),
            self._encode_events_params(params),
        )
        return self._request_json("GET", path, request_timeout_ms=request_timeout_ms)

    def create_webhook(
        self,
        body: Mapping[str, Any],
        *,
        request_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            self.api_path("/events/webhooks"),
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(201,),
            request_timeout_ms=request_timeout_ms,
        )

    def list_webhooks(self, *, request_timeout_ms: int | None = None) -> list[dict[str, Any]]:
        return self._request_json("GET", self.api_path("/events/webhooks"), request_timeout_ms=request_timeout_ms)

    def get_webhook(self, webhook_id: str, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        self._require_webhook_id(webhook_id)
        return self._request_json(
            "GET",
            self.api_path(f"/events/webhooks/{quote(webhook_id, safe='')}"),
            request_timeout_ms=request_timeout_ms,
        )

    def update_webhook(
        self,
        webhook_id: str,
        body: Mapping[str, Any],
        *,
        request_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self._require_webhook_id(webhook_id)
        return self._request_json(
            "PATCH",
            self.api_path(f"/events/webhooks/{quote(webhook_id, safe='')}"),
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            request_timeout_ms=request_timeout_ms,
        )

    def delete_webhook(self, webhook_id: str, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        self._require_webhook_id(webhook_id)
        return self._request_json(
            "DELETE",
            self.api_path(f"/events/webhooks/{quote(webhook_id, safe='')}"),
            expected_statuses=(200,),
            request_timeout_ms=request_timeout_ms,
        )

    def list_webhook_deliveries(
        self,
        params: WebhookDeliveriesParams | Mapping[str, Any] | None = None,
        *,
        request_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        path = self._with_query(self.api_path("/events/webhook-deliveries"), self._encode_delivery_params(params))
        return self._request_json("GET", path, request_timeout_ms=request_timeout_ms)

    def list_webhook_deliveries_by_webhook(
        self,
        webhook_id: str,
        params: WebhookDeliveriesParams | Mapping[str, Any] | None = None,
        *,
        request_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        self._require_webhook_id(webhook_id)
        path = self._with_query(
            self.api_path(f"/events/webhooks/{quote(webhook_id, safe='')}/deliveries"),
            self._encode_delivery_params(params),
        )
        return self._request_json("GET", path, request_timeout_ms=request_timeout_ms)

    def replay_webhook_delivery(self, delivery_id: str, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        self._require_delivery_id(delivery_id)
        return self._request_json(
            "POST",
            self.api_path(f"/events/webhook-deliveries/{quote(delivery_id, safe='')}/replay"),
            expected_statuses=(202,),
            request_timeout_ms=request_timeout_ms,
        )

    def list_volumes(self, *, request_timeout_ms: int | None = None) -> list[dict[str, Any]]:
        return self._request_json("GET", self.api_path("/volumes"), request_timeout_ms=request_timeout_ms)

    def create_volume(self, body: Mapping[str, Any], *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        return self._request_json(
            "POST",
            self.api_path("/volumes"),
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(201,),
            request_timeout_ms=request_timeout_ms,
        )

    def get_volume(self, volume_id: str, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        self._require_volume_id(volume_id)
        return self._request_json("GET", self.api_path(f"/volumes/{quote(volume_id, safe='')}"), request_timeout_ms=request_timeout_ms)

    def delete_volume(self, volume_id: str, *, request_timeout_ms: int | None = None) -> None:
        self._require_volume_id(volume_id)
        self._request_empty(
            "DELETE",
            self.api_path(f"/volumes/{quote(volume_id, safe='')}"),
            expected_statuses=(204,),
            request_timeout_ms=request_timeout_ms,
        )

    def list_teams(self, *, request_timeout_ms: int | None = None) -> list[dict[str, Any]]:
        return self._request_json("GET", self.api_path("/teams"), request_timeout_ms=request_timeout_ms)

    def get_team_metrics(
        self,
        team_id: str,
        params: TeamMetricsParams | Mapping[str, Any] | None = None,
        *,
        request_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        self._require_team_id(team_id)
        path = self._with_query(self.api_path(f"/teams/{quote(team_id, safe='')}/metrics"), self._encode_team_metrics_params(params))
        return self._request_json("GET", path, request_timeout_ms=request_timeout_ms)

    def get_team_metrics_max(
        self,
        team_id: str,
        params: TeamMetricsMaxParams | Mapping[str, Any],
        *,
        request_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self._require_team_id(team_id)
        path = self._with_query(self.api_path(f"/teams/{quote(team_id, safe='')}/metrics/max"), self._encode_team_metrics_max_params(params))
        return self._request_json("GET", path, request_timeout_ms=request_timeout_ms)

    def list_sandboxes(
        self,
        params: ListSandboxesParams | Mapping[str, Any] | None = None,
        request_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.list_sandboxes_page(params, request_timeout_ms=request_timeout_ms).items

    def list_sandboxes_page(
        self,
        params: ListSandboxesParams | Mapping[str, Any] | None = None,
        request_timeout_ms: int | None = None,
    ) -> SandboxesPage:
        path = self._with_query(self.api_path("/sandboxes"), self._encode_list_params(params))
        response = self._request_response("GET", path, request_timeout_ms=request_timeout_ms)
        with response:
            items = json.loads(response.read().decode("utf-8"))
            headers = getattr(response, "headers", {})
            next_token = headers.get("X-Next-Token", "")
            has_next = headers.get("X-Has-Next", "").lower() == "true"
        return SandboxesPage(items=items, next_token=next_token, has_next=has_next)

    def get_sandbox(self, sandbox_id: str, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        self._require_sandbox_id(sandbox_id)
        return self._request_json("GET", self.api_path(f"/sandboxes/{quote(sandbox_id, safe='')}"), request_timeout_ms=request_timeout_ms)

    def get_sandbox_metrics(self, sandbox_id: str, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        self._require_sandbox_id(sandbox_id)
        return self._request_json(
            "GET",
            self.api_path(f"/sandboxes/{quote(sandbox_id, safe='')}/metrics"),
            request_timeout_ms=request_timeout_ms,
        )

    def list_sandbox_metrics(
        self,
        params: SandboxMetricsParams | Mapping[str, Any] | None = None,
        request_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        path = self._with_query(self.api_path("/sandboxes/metrics"), self._encode_metrics_params(params))
        return self._request_json("GET", path, request_timeout_ms=request_timeout_ms)

    def get_observability_summary(self, *, request_timeout_ms: int | None = None) -> dict[str, Any]:
        return self._request_json(
            "GET",
            self.api_path(f"/observability/summary"),
            request_timeout_ms=request_timeout_ms,
        )

    def delete_sandbox(self, sandbox_id: str, *, request_timeout_ms: int | None = None) -> None:
        self._require_sandbox_id(sandbox_id)
        self._request_empty(
            "DELETE",
            self.api_path(f"/sandboxes/{quote(sandbox_id, safe='')}"),
            expected_statuses=(204,),
            request_timeout_ms=request_timeout_ms,
        )

    def get_sandbox_logs(
        self,
        sandbox_id: str,
        params: SandboxLogsParams | Mapping[str, Any] | None = None,
        request_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self._require_sandbox_id(sandbox_id)
        self._validate_logs_params(params)
        path = self._with_query(
            self.api_path(f"/sandboxes/{quote(sandbox_id, safe='')}/logs"),
            self._encode_logs_params(params),
        )
        return self._request_json("GET", path, request_timeout_ms=request_timeout_ms)

    def pause_sandbox(self, sandbox_id: str, *, request_timeout_ms: int | None = None) -> None:
        self._require_sandbox_id(sandbox_id)
        self._request_empty(
            "POST",
            self.api_path(f"/sandboxes/{quote(sandbox_id, safe='')}/pause"),
            expected_statuses=(204,),
            request_timeout_ms=request_timeout_ms,
        )

    def connect_sandbox(
        self,
        sandbox_id: str,
        body: Mapping[str, Any],
        *,
        request_timeout_ms: int | None = None,
    ) -> ConnectSandboxResponse:
        self._require_sandbox_id(sandbox_id)
        self._validate_timeout_seconds(body.get("timeout"), "connect timeout")
        response = self._request_response(
            "POST",
            self.api_path(f"/sandboxes/{quote(sandbox_id, safe='')}/connect"),
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(200, 201),
            request_timeout_ms=request_timeout_ms,
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
        *,
        request_timeout_ms: int | None = None,
    ) -> None:
        self._require_sandbox_id(sandbox_id)
        self._validate_timeout_seconds(body.get("timeout"), "timeout")
        self._request_empty(
            "POST",
            self.api_path(f"/sandboxes/{quote(sandbox_id, safe='')}/timeout"),
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(204,),
            request_timeout_ms=request_timeout_ms,
        )

    def refresh_sandbox(
        self,
        sandbox_id: str,
        body: Mapping[str, Any] | None = None,
        *,
        request_timeout_ms: int | None = None,
    ) -> None:
        self._require_sandbox_id(sandbox_id)
        self._validate_refresh_duration(None if body is None else body.get("duration"))
        self._request_empty(
            "POST",
            self.api_path(f"/sandboxes/{quote(sandbox_id, safe='')}/refreshes"),
            headers=None if body is None else self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(204,),
            request_timeout_ms=request_timeout_ms,
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
            self.api_path(f"/sandboxes/{quote(sandbox_id, safe='')}/heartbeat"),
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

    def _require_webhook_id(self, webhook_id: str) -> None:
        if not webhook_id.strip():
            raise ValidationError("webhook_id is required")

    def _require_delivery_id(self, delivery_id: str) -> None:
        if not delivery_id.strip():
            raise ValidationError("delivery_id is required")

    def _require_volume_id(self, volume_id: str) -> None:
        if not volume_id.strip():
            raise ValidationError("volume_id is required")

    def _require_team_id(self, team_id: str) -> None:
        if not team_id.strip():
            raise ValidationError("team_id is required")

    def _validate_timeout_seconds(self, timeout: Any, field: str) -> None:
        if not isinstance(timeout, int) or timeout < 0 or timeout > 86_400:
            raise ValidationError(f"{field} must be an integer between 0 and 86400")

    def _validate_refresh_duration(self, duration: Any) -> None:
        if duration is None:
            return
        if not isinstance(duration, int) or duration < 0 or duration > 3600:
            raise ValidationError("refresh duration must be an integer between 0 and 3600")

    def _validate_heartbeat_status(self, status: str) -> None:
        if status.strip() not in {"starting", "healthy", "error"}:
            raise ValidationError("heartbeat status must be one of starting, healthy, error")

    def _validate_logs_params(self, params: SandboxLogsParams | Mapping[str, Any] | None) -> None:
        if params is None:
            return
        cursor = _param_get(params, "cursor")
        limit = _param_get(params, "limit")
        direction = _param_get(params, "direction")
        search = _param_get(params, "search")
        if cursor is not None and (not isinstance(cursor, int) or cursor < 0):
            raise ValidationError("logs cursor must be a non-negative integer")
        if limit is not None and (not isinstance(limit, int) or limit < 0 or limit > 1000):
            raise ValidationError("logs limit must be an integer between 0 and 1000")
        if direction and direction not in {"forward", "backward"}:
            raise ValidationError('logs direction must be "forward" or "backward"')
        if search is not None and len(search) > 256:
            raise ValidationError("logs search must be at most 256 characters")

    def _with_query(self, path: str, params: Mapping[str, Any]) -> str:
        if not params:
            return path
        return f"{path}?{urlencode(params, doseq=True)}"

    def _encode_list_params(self, params: ListSandboxesParams | Mapping[str, Any] | None) -> dict[str, Any]:
        if params is None:
            return {}
        query: dict[str, Any] = {}
        metadata = _param_get(params, "metadata")
        state = _param_get(params, "state")
        limit = _param_get(params, "limit")
        next_token = _param_get(params, "next_token", "nextToken")
        if metadata:
            query["metadata"] = urlencode(metadata)
        if state:
            query["state"] = [item.strip() for item in state if item.strip()]
        if limit is not None:
            query["limit"] = str(limit)
        if next_token:
            query["nextToken"] = next_token
        return query

    def _encode_metrics_params(self, params: SandboxMetricsParams | Mapping[str, Any] | None) -> dict[str, str]:
        if params is None:
            return {}
        query: dict[str, str] = {}
        sandbox_ids = _param_get(params, "sandbox_ids", "sandboxIDs") or []
        ids = [item.strip() for item in sandbox_ids if item.strip()]
        if ids:
            query["sandbox_ids"] = ",".join(ids)
        limit = _param_get(params, "limit")
        if limit is not None:
            query["limit"] = str(limit)
        return query

    def _encode_events_params(self, params: SandboxEventsParams | Mapping[str, Any] | None) -> dict[str, Any]:
        if params is None:
            return {}
        query: dict[str, Any] = {}
        offset = _param_get(params, "offset")
        limit = _param_get(params, "limit")
        order_asc = _param_get(params, "order_asc", "orderAsc")
        types = _param_get(params, "types") or []
        if offset is not None:
            query["offset"] = str(offset)
        if limit is not None:
            query["limit"] = str(limit)
        if order_asc is not None:
            query["orderAsc"] = "true" if bool(order_asc) else "false"
        event_types = [str(item).strip() for item in types if str(item).strip()]
        if event_types:
            query["types"] = event_types
        return query

    def _encode_delivery_params(self, params: WebhookDeliveriesParams | Mapping[str, Any] | None) -> dict[str, Any]:
        if params is None:
            return {}
        query: dict[str, Any] = {}
        offset = _param_get(params, "offset")
        limit = _param_get(params, "limit")
        order_asc = _param_get(params, "order_asc", "orderAsc")
        webhook_id = _param_get(params, "webhook_id", "webhookID")
        event_id = _param_get(params, "event_id", "eventID")
        status = _param_get(params, "status")
        if offset is not None:
            query["offset"] = str(offset)
        if limit is not None:
            query["limit"] = str(limit)
        if order_asc is not None:
            query["orderAsc"] = "true" if bool(order_asc) else "false"
        if webhook_id:
            query["webhookID"] = str(webhook_id).strip()
        if event_id:
            query["eventID"] = str(event_id).strip()
        if status:
            query["status"] = str(status).strip()
        return query

    def _encode_team_metrics_params(self, params: TeamMetricsParams | Mapping[str, Any] | None) -> dict[str, str]:
        if params is None:
            return {}
        query: dict[str, str] = {}
        start = _param_get(params, "start")
        end = _param_get(params, "end")
        if start is not None:
            query["start"] = str(start)
        if end is not None:
            query["end"] = str(end)
        return query

    def _encode_team_metrics_max_params(self, params: TeamMetricsMaxParams | Mapping[str, Any]) -> dict[str, str]:
        query = self._encode_team_metrics_params(params)
        metric = _param_get(params, "metric")
        if metric:
            query["metric"] = str(metric).strip()
        return query

    def _encode_logs_params(self, params: SandboxLogsParams | Mapping[str, Any] | None) -> dict[str, str]:
        if params is None:
            return {}
        query: dict[str, str] = {}
        cursor = _param_get(params, "cursor")
        limit = _param_get(params, "limit")
        direction = _param_get(params, "direction")
        level = _param_get(params, "level")
        search = _param_get(params, "search")
        if cursor is not None:
            query["cursor"] = str(cursor)
        if limit is not None:
            query["limit"] = str(limit)
        if direction:
            query["direction"] = direction
        if level:
            query["level"] = level
        if search:
            query["search"] = search
        return query


def _reject_unsupported_create_fields(body: Mapping[str, Any]) -> None:
    for key in ("secure", "mcp", "volume_mounts"):
        if key in body:
            raise ValidationError(f"{key} is not supported")


def _param_get(params: Any, *names: str) -> Any:
    if isinstance(params, Mapping):
        for name in names:
            if name in params:
                return params[name]
        return None
    for name in names:
        if hasattr(params, name):
            return getattr(params, name)
    return None
