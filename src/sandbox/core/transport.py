from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .._version import SDK_VERSION
from .exceptions import APIError, ConfigurationError, RequestTimeoutError, create_api_error

SDKLogger = Callable[[Mapping[str, Any]], None]


class BaseTransport:
    """Shared HTTP transport for the Sandbox SDK."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        domain: str | None = None,
        project_id: str | None = None,
        timeout: float = 30.0,
        request_timeout_ms: int | None = None,
        debug: bool = False,
        logger: SDKLogger | None = None,
    ) -> None:
        normalized_base_url = _resolve_base_url(base_url=base_url, domain=domain)
        normalized_api_key = (api_key or os.getenv("SEACLOUD_API_KEY") or "").strip()
        normalized_project_id = (project_id or "").strip()
        resolved_timeout = timeout if request_timeout_ms is None else request_timeout_ms / 1000

        if not normalized_base_url:
            raise ConfigurationError("base_url is required")
        if not normalized_api_key:
            raise ConfigurationError("api_key is required")

        self.base_url = normalized_base_url
        self.timeout = resolved_timeout
        self.timeout_ms = int(round(resolved_timeout * 1000))
        self.debug = debug
        self.logger = logger
        self._default_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {normalized_api_key}",
            "User-Agent": f"seacloudai-sandbox-python/{SDK_VERSION}",
            "X-API-Key": normalized_api_key,
        }
        if normalized_project_id:
            self._default_headers["X-Project-ID"] = normalized_project_id

    def build_url(self, path: str) -> str:
        normalized_path = path.strip() or "/"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        return urljoin(f"{self.base_url}/", normalized_path.lstrip("/"))

    def api_path(self, path: str) -> str:
        suffix = path.strip()
        return suffix if suffix.startswith("/") else f"/{suffix}"

    def build_headers(self, extra_headers: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(self._default_headers)
        if extra_headers:
            headers.update(extra_headers)
        if not _get_header(headers, "X-Request-ID"):
            headers["X-Request-ID"] = str(uuid.uuid4())
        return headers

    def build_request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        data: bytes | None = None,
    ) -> Request:
        return Request(
            url=self.build_url(path),
            data=data,
            headers=self.build_headers(headers),
            method=method.upper(),
        )

    def open(self, request: Request, *, request_timeout_ms: int | None = None):
        timeout = self.timeout if request_timeout_ms is None else int(request_timeout_ms) / 1000
        return urlopen(request, timeout=timeout)

    def metrics(self) -> str:
        return self._request_text("GET", "/metrics")

    def shutdown(self) -> dict[str, Any]:
        return self._request_json("POST", "/shutdown")

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        request_timeout_ms: int | None = None,
    ) -> Any:
        response = self._request_response(
            method,
            path,
            headers=headers,
            body=body,
            expected_statuses=expected_statuses,
            request_timeout_ms=request_timeout_ms,
        )
        with response:
            payload = response.read()
        return json.loads(payload.decode("utf-8"))

    def _request_text(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        request_timeout_ms: int | None = None,
    ) -> str:
        response = self._request_response(
            method,
            path,
            headers=headers,
            expected_statuses=expected_statuses,
            request_timeout_ms=request_timeout_ms,
        )
        with response:
            return response.read().decode("utf-8")

    def _request_empty(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        expected_statuses: tuple[int, ...] = (204,),
        request_timeout_ms: int | None = None,
    ) -> None:
        response = self._request_response(
            method,
            path,
            headers=headers,
            body=body,
            expected_statuses=expected_statuses,
            request_timeout_ms=request_timeout_ms,
        )
        with response:
            response.read()

    def _request_response(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        request_timeout_ms: int | None = None,
    ):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        if payload is None and method.upper() in {"POST", "PUT", "PATCH"}:
            payload = b""
        request = self.build_request(method, path, headers=headers, data=payload)
        request_id = request.get_header("X-request-id") or request.get_header("X-Request-ID") or ""
        started = time.monotonic()
        event_base = {
            "method": method.upper(),
            "path": _sanitize_diagnostic_path(request.full_url),
            "request_id": request_id,
        }
        self._emit_diagnostic({"type": "request", **event_base})
        try:
            if request_timeout_ms is None:
                response = self.open(request)
            else:
                response = self.open(request, request_timeout_ms=request_timeout_ms)
        except HTTPError as exc:
            api_error = self._decode_api_error(exc)
            self._emit_api_error(event_base, api_error, started)
            raise api_error from exc
        except TimeoutError as exc:
            self._emit_diagnostic({
                "type": "error",
                **event_base,
                "duration_ms": _duration_ms(started),
                "error": f"request timed out after {self.timeout_ms if request_timeout_ms is None else int(request_timeout_ms)}ms",
                "error_kind": "timeout",
                "retryable": True,
            })
            raise RequestTimeoutError(self.timeout_ms if request_timeout_ms is None else int(request_timeout_ms), cause=exc) from exc
        except socket.timeout as exc:
            self._emit_diagnostic({
                "type": "error",
                **event_base,
                "duration_ms": _duration_ms(started),
                "error": f"request timed out after {self.timeout_ms if request_timeout_ms is None else int(request_timeout_ms)}ms",
                "error_kind": "timeout",
                "retryable": True,
            })
            raise RequestTimeoutError(self.timeout_ms if request_timeout_ms is None else int(request_timeout_ms), cause=exc) from exc
        except Exception as exc:
            self._emit_diagnostic({
                "type": "error",
                **event_base,
                "duration_ms": _duration_ms(started),
                "error": _sanitize_diagnostic_error(str(exc)),
            })
            raise

        status_code = getattr(response, "status", response.getcode())
        self._emit_diagnostic({
            "type": "response",
            **event_base,
            "status": status_code,
            "duration_ms": _duration_ms(started),
        })
        if status_code not in expected_statuses:
            try:
                api_error = self._decode_api_error(response)
                self._emit_api_error(event_base, api_error, started)
                raise api_error
            finally:
                response.close()
        return response

    def _emit_api_error(self, event_base: Mapping[str, Any], error: APIError, started: float) -> None:
        self._emit_diagnostic({
            "type": "error",
            **event_base,
            "request_id": error.request_id or event_base.get("request_id", ""),
            "status": error.status_code,
            "duration_ms": _duration_ms(started),
            "error": _sanitize_diagnostic_error(str(error)),
            "error_kind": error.kind,
            "retryable": error.retryable,
        })

    def _emit_diagnostic(self, event: Mapping[str, Any]) -> None:
        if self.logger is not None:
            try:
                self.logger(event)
            except Exception:
                # Diagnostics must never change request behavior.
                pass
            return
        if self.debug:
            parts = [f"{key}={value}" for key, value in event.items() if value is not None and value != ""]
            print(f"[seacloudai-sandbox] {' '.join(parts)}", file=sys.stderr)

    def _decode_api_error(self, response) -> APIError:
        body = response.read().decode("utf-8")
        parsed: dict[str, Any] | None = None
        if body:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None

        detail = parsed.get("error") if parsed else None
        details = parsed.get("details") if parsed else None
        message = parsed.get("message") if parsed else getattr(response, "reason", "request failed")
        return create_api_error(
            message or "request failed",
            status_code=getattr(response, "status", response.getcode()),
            code=parsed.get("code") if parsed else None,
            request_id=(parsed.get("requestID") or parsed.get("request_id")) if parsed else None,
            detail=detail,
            details=details,
            body=body,
        )


def _resolve_base_url(*, base_url: str | None, domain: str | None) -> str:
    normalized = (base_url or "").strip()
    if normalized:
        return normalized.rstrip("/")

    explicit_domain = (domain or "").strip()
    if explicit_domain:
        return _normalize_domain(explicit_domain)

    env_domain = os.getenv("SEACLOUD_BASE_URL", "").strip()
    if env_domain:
        return _normalize_domain(env_domain)

    return ""


def _normalize_domain(value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        return value.rstrip("/")
    return f"https://{value}".rstrip("/")


def _get_header(headers: Mapping[str, str], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return str(value).strip()
    return ""


def _duration_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))


def _sanitize_diagnostic_path(value: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parsed = urlsplit(value)
    pairs = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        if _is_sensitive_query_key(key):
            pairs.append((key, "<redacted>"))
        else:
            pairs.append((key, item_value))
    query = urlencode(pairs)
    return urlunsplit(("", "", parsed.path or "/", query, ""))


def _sanitize_diagnostic_error(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            return _sanitize_diagnostic_path(match.group(0))
        except Exception:
            return "<redacted-url>"

    return re.sub(r"https?://[^\s\"'<>]+", replace, value)


def _is_sensitive_query_key(key: str) -> bool:
    normalized = key.lower()
    return "token" in normalized or "signature" in normalized or normalized == "api_key"
