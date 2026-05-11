from __future__ import annotations

import json
import os
import socket
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .._version import SDK_VERSION
from .exceptions import APIError, ConfigurationError, RequestTimeoutError, create_api_error

DEFAULT_BASE_URL = "https://sandbox-gateway.cloud.seaart.ai"


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
        request_timeout: float | None = None,
    ) -> None:
        normalized_base_url = _resolve_base_url(base_url=base_url, domain=domain)
        normalized_api_key = (api_key or os.getenv("E2B_API_KEY") or "").strip()
        normalized_project_id = (project_id or os.getenv("SEACLOUD_PROJECT_ID") or "").strip()
        resolved_timeout = timeout if request_timeout is None else request_timeout

        if not normalized_base_url:
            raise ConfigurationError("base_url is required")
        if not normalized_api_key:
            raise ConfigurationError("api_key is required")

        self.base_url = normalized_base_url
        self.timeout = resolved_timeout
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

    def build_headers(self, extra_headers: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(self._default_headers)
        if extra_headers:
            headers.update(extra_headers)
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

    def open(self, request: Request):
        return urlopen(request, timeout=self.timeout)

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
    ) -> Any:
        response = self._request_response(
            method,
            path,
            headers=headers,
            body=body,
            expected_statuses=expected_statuses,
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
    ) -> str:
        response = self._request_response(
            method,
            path,
            headers=headers,
            expected_statuses=expected_statuses,
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
    ) -> None:
        response = self._request_response(
            method,
            path,
            headers=headers,
            body=body,
            expected_statuses=expected_statuses,
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
    ):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        if payload is None and method.upper() in {"POST", "PUT", "PATCH"}:
            payload = b""
        request = self.build_request(method, path, headers=headers, data=payload)
        try:
            response = self.open(request)
        except HTTPError as exc:
            raise self._decode_api_error(exc) from exc
        except TimeoutError as exc:
            raise RequestTimeoutError(self.timeout, cause=exc) from exc
        except socket.timeout as exc:
            raise RequestTimeoutError(self.timeout, cause=exc) from exc

        status_code = getattr(response, "status", response.getcode())
        if status_code not in expected_statuses:
            try:
                raise self._decode_api_error(response)
            finally:
                response.close()
        return response

    def _decode_api_error(self, response) -> APIError:
        body = response.read().decode("utf-8")
        parsed: dict[str, Any] | None = None
        if body:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None

        detail = parsed.get("error") if parsed else None
        message = parsed.get("message") if parsed else getattr(response, "reason", "request failed")
        return create_api_error(
            message or "request failed",
            status_code=getattr(response, "status", response.getcode()),
            code=parsed.get("code") if parsed else None,
            request_id=parsed.get("request_id") if parsed else None,
            detail=detail,
            body=body,
        )


def _resolve_base_url(*, base_url: str | None, domain: str | None) -> str:
    normalized = (base_url or "").strip()
    if normalized:
        return normalized.rstrip("/")

    explicit_domain = (domain or "").strip()
    if explicit_domain:
        return _normalize_domain(explicit_domain)

    env_domain = os.getenv("E2B_DOMAIN", "").strip()
    if env_domain:
        return _normalize_domain(env_domain)

    return DEFAULT_BASE_URL


def _normalize_domain(value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        return value.rstrip("/")
    return f"https://{value}".rstrip("/")
