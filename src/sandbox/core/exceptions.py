from __future__ import annotations

class SandboxError(Exception):
    """Base exception for the Sandbox SDK."""


class TransportError(SandboxError):
    """Raised when the SDK cannot complete the HTTP request."""


class RequestTimeoutError(TransportError):
    """Raised when a request exceeds the configured timeout."""

    def __init__(self, timeout: float, *, cause: Exception | None = None) -> None:
        super().__init__(f"request timed out after {timeout:g}s")
        self.timeout = timeout
        self.__cause__ = cause


class ConfigurationError(SandboxError):
    """Raised when SDK configuration is invalid."""


class ValidationError(SandboxError):
    """Raised when request parameters are invalid."""


class APIError(SandboxError):
    """Raised when the API returns a non-success response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: int | None = None,
        request_id: str | None = None,
        detail: object | None = None,
        body: str = "",
        kind: str | None = None,
    ) -> None:
        super().__init__(_detail_message(detail) or message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.detail = detail
        self.body = body
        self.kind = kind or classify_api_error(status_code)

    @property
    def retryable(self) -> bool:
        return self.kind in {"rate_limit", "timeout", "server"}


class AuthenticationError(APIError):
    pass


class PermissionError(APIError):
    pass


class NotFoundError(APIError):
    pass


class ConflictError(APIError):
    pass


class RateLimitError(APIError):
    pass


class TimeoutAPIError(APIError):
    pass


class ServerError(APIError):
    pass


def create_api_error(
    message: str,
    *,
    status_code: int,
    code: int | None = None,
    request_id: str | None = None,
    detail: object | None = None,
    body: str = "",
) -> APIError:
    kind = classify_api_error(status_code)
    error_cls: type[APIError]
    if kind == "authentication":
        error_cls = AuthenticationError
    elif kind == "permission":
        error_cls = PermissionError
    elif kind == "not_found":
        error_cls = NotFoundError
    elif kind == "conflict":
        error_cls = ConflictError
    elif kind == "rate_limit":
        error_cls = RateLimitError
    elif kind == "timeout":
        error_cls = TimeoutAPIError
    elif kind == "server":
        error_cls = ServerError
    else:
        error_cls = APIError

    return error_cls(
        message,
        status_code=status_code,
        code=code,
        request_id=request_id,
        detail=detail,
        body=body,
        kind=kind,
    )


def classify_api_error(status_code: int) -> str:
    if status_code == 401:
        return "authentication"
    if status_code == 403:
        return "permission"
    if status_code == 404:
        return "not_found"
    if status_code == 408:
        return "timeout"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        return "rate_limit"
    if status_code >= 500:
        return "server"
    return "unknown"


def _detail_message(detail: object | None) -> str:
    # Runtime routes may return {"error": "not found"} instead of the standard error object.
    if isinstance(detail, dict):
        value = detail.get("details") or detail.get("message")
        return str(value) if value else ""
    if isinstance(detail, str):
        return detail
    return ""
