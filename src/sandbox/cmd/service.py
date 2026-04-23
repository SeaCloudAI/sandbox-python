from __future__ import annotations

import base64
import gzip
import json
import socket
import uuid
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .._version import SDK_VERSION
from ..core.exceptions import (
    APIError,
    ConfigurationError,
    RequestTimeoutError,
    ValidationError,
    create_api_error,
)
from .models import (
    CmdRequestOptions,
    ConnectFrame,
    ConnectStream,
    DownloadRequest,
    FileRequest,
    FilesContentRequest,
    FilesystemWatchStream,
    ProcessStream,
    ProxyRequest,
    UploadBytesRequest,
    UploadMultipartRequest,
)


class CommandService:
    def __init__(self, base_url: str, access_token: str = "", *, timeout: float = 30.0) -> None:
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            raise ConfigurationError("base_url is required")
        self.base_url = normalized_base_url
        self.access_token = access_token.strip()
        self.timeout = timeout

    def metrics(self) -> dict[str, Any]:
        return self._request_json("GET", "/metrics")

    def envs(self) -> dict[str, str]:
        return self._request_json("GET", "/envs")

    def configure(self, body: Mapping[str, Any] | None = None) -> None:
        self._request_empty(
            "POST",
            "/configure",
            body=body or {},
            headers={"Content-Type": "application/json"},
            expected_statuses=(204,),
        )

    def ports(self) -> list[dict[str, Any]]:
        return self._request_json("GET", "/ports")

    def proxy(self, request: ProxyRequest):
        if request.port <= 0:
            raise ValidationError("port must be a positive integer")
        path = f"/proxy/{request.port}/"
        suffix = request.path.strip().lstrip("/")
        if suffix:
            path += suffix
        return self._open_request(
            request.method or "GET",
            path,
            headers=request.headers,
            data=request.body,
            expected_statuses=None,
        )

    def download(self, request: DownloadRequest, options: CmdRequestOptions | None = None):
        query = self._file_query(request.path, options)
        headers = dict((options.headers if options else {}) or {})
        if options and options.range.strip():
            headers["Range"] = options.range.strip()
        return self._open_request(
            "GET",
            f"/files?{query}",
            headers=headers,
            expected_statuses=(200, 206),
            timeout=self._timeout_from_options(options),
        )

    def files_content(
        self,
        request: FilesContentRequest,
        options: CmdRequestOptions | None = None,
    ) -> dict[str, Any]:
        query = self._file_query(request.path, options)
        if request.max_tokens is not None:
            query += ("&" if query else "") + urlencode({"max_tokens": str(request.max_tokens)})
        return self._request_json("GET", f"/files/content?{query}", timeout=self._timeout_from_options(options))

    def upload_bytes(
        self,
        request: UploadBytesRequest,
        options: CmdRequestOptions | None = None,
    ) -> list[dict[str, Any]]:
        query = self._file_query(request.path, options)
        payload = gzip.compress(request.data) if request.gzip_compress else request.data
        headers = dict((options.headers if options else {}) or {})
        headers["Content-Type"] = "application/octet-stream"
        if request.gzip_compress:
            headers["Content-Encoding"] = "gzip"
        return self._request_json(
            "POST",
            f"/files?{query}",
            headers=headers,
            raw_body=payload,
            timeout=self._timeout_from_options(options),
        )

    def upload_json(
        self,
        entry: Mapping[str, Any],
        options: CmdRequestOptions | None = None,
    ) -> list[dict[str, Any]]:
        self._require_path(str(entry.get("path", "")))
        query = self._query_from_options(options)
        path = "/files" + (f"?{query}" if query else "")
        return self._request_json(
            "POST",
            path,
            headers={"Content-Type": "application/json"},
            body=entry,
            timeout=self._timeout_from_options(options),
        )

    def upload_multipart(
        self,
        request: UploadMultipartRequest,
        options: CmdRequestOptions | None = None,
    ) -> list[dict[str, Any]]:
        if not request.parts:
            raise ValidationError("multipart upload requires at least one part")
        query = self._file_query(request.path, options) if request.path.strip() else self._query_from_options(options)
        body, content_type = self._encode_multipart(request.parts)
        return self._request_json(
            "POST",
            "/files" + (f"?{query}" if query else ""),
            headers={"Content-Type": content_type},
            raw_body=body,
            timeout=self._timeout_from_options(options),
        )

    def write_batch(
        self,
        body: Mapping[str, Any],
        options: CmdRequestOptions | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/files/batch",
            headers=self._json_headers(options),
            body=body,
            timeout=self._timeout_from_options(options),
        )

    def compose_files(
        self,
        body: Mapping[str, Any],
        options: CmdRequestOptions | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/files/compose",
            headers=self._json_headers(options),
            body=body,
            timeout=self._timeout_from_options(options),
        )

    def list_dir(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> dict[str, Any]:
        self._require_path(str(body.get("path", "")))
        return self._connect_json("/filesystem.Filesystem/ListDir", body, options)

    def stat(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> dict[str, Any]:
        self._require_path(str(body.get("path", "")))
        return self._connect_json("/filesystem.Filesystem/Stat", body, options)

    def make_dir(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> dict[str, Any]:
        self._require_path(str(body.get("path", "")))
        return self._connect_json("/filesystem.Filesystem/MakeDir", body, options)

    def remove(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> None:
        self._require_path(str(body.get("path", "")))
        self._connect_empty("/filesystem.Filesystem/Remove", body, options)

    def move(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> dict[str, Any]:
        self._require_path(str(body.get("source", "")))
        self._require_path(str(body.get("destination", "")))
        return self._connect_json("/filesystem.Filesystem/Move", body, options)

    def edit(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> dict[str, Any]:
        self._require_path(str(body.get("path", "")))
        return self._connect_json("/filesystem.Filesystem/Edit", body, options)

    def watch_dir(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> FilesystemWatchStream:
        self._require_path(str(body.get("path", "")))
        response = self._open_request(
            "POST",
            "/filesystem.Filesystem/WatchDir",
            headers=self._connect_headers(options, connect_content_type=True),
            body=body,
            expected_statuses=(200,),
            timeout=self._timeout_from_options(options),
        )
        return FilesystemWatchStream(response)

    def create_watcher(
        self,
        body: Mapping[str, Any],
        options: CmdRequestOptions | None = None,
    ) -> dict[str, Any]:
        self._require_path(str(body.get("path", "")))
        return self._connect_json("/filesystem.Filesystem/CreateWatcher", body, options)

    def get_watcher_events(
        self,
        body: Mapping[str, Any],
        options: CmdRequestOptions | None = None,
    ) -> dict[str, Any]:
        if not str(body.get("watcherId", "")).strip():
            raise ValidationError("watcherId is required")
        return self._connect_json("/filesystem.Filesystem/GetWatcherEvents", body, options)

    def remove_watcher(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> None:
        if not str(body.get("watcherId", "")).strip():
            raise ValidationError("watcherId is required")
        self._connect_empty("/filesystem.Filesystem/RemoveWatcher", body, options)

    def start(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> ProcessStream:
        process = body.get("process") or {}
        if not str(process.get("cmd", "")).strip():
            raise ValidationError("cmd is required")
        response = self._open_request(
            "POST",
            "/process.Process/Start",
            headers=self._connect_headers(options, connect_content_type=True),
            body=body,
            expected_statuses=(200,),
            timeout=self._timeout_from_options(options),
        )
        return ProcessStream(response)

    def connect(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> ProcessStream:
        self._validate_selector(body.get("process") or {})
        response = self._open_request(
            "POST",
            "/process.Process/Connect",
            headers=self._connect_headers(options, connect_content_type=True),
            body=body,
            expected_statuses=(200,),
            timeout=self._timeout_from_options(options),
        )
        return ProcessStream(response)

    def list_processes(self, options: CmdRequestOptions | None = None) -> dict[str, Any]:
        return self._connect_json("/process.Process/List", {}, options)

    def send_input(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> None:
        self._validate_selector(body.get("process") or {})
        self._validate_input(body.get("input") or {})
        self._connect_empty("/process.Process/SendInput", body, options)

    def send_signal(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> None:
        self._validate_selector(body.get("process") or {})
        self._connect_empty("/process.Process/SendSignal", body, options)

    def close_stdin(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> None:
        self._validate_selector(body.get("process") or {})
        self._connect_empty("/process.Process/CloseStdin", body, options)

    def update(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> None:
        self._validate_selector(body.get("process") or {})
        if body.get("pty") is None:
            raise ValidationError("pty is required")
        self._connect_empty("/process.Process/Update", body, options)

    def stream_input(
        self,
        frames: list[Mapping[str, Any]],
        options: CmdRequestOptions | None = None,
    ) -> ConnectFrame | None:
        if not frames:
            raise ValidationError("stream input requires at least one frame")
        payload = self._encode_connect_frames(frames)
        response = self._open_request(
            "POST",
            "/process.Process/StreamInput",
            headers=self._connect_headers(options, connect_content_type=True),
            data=payload,
            expected_statuses=(200,),
            timeout=self._timeout_from_options(options),
        )
        stream = ConnectStream(response)
        try:
            return stream.next_frame()
        finally:
            stream.close()

    def get_result(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> dict[str, Any]:
        if not str(body.get("cmdId", "")).strip():
            raise ValidationError("cmdId is required")
        return self._connect_json("/process.Process/GetResult", body, options)

    def run(self, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> dict[str, Any]:
        if not str(body.get("cmd", "")).strip():
            raise ValidationError("cmd is required")
        return self._request_json(
            "POST",
            "/run",
            headers=self._basic_headers(options, include_content_type=True),
            body=body,
            timeout=self._timeout_from_options(options),
        )

    def read_file(self, request: FileRequest, options: CmdRequestOptions | None = None):
        query = self._file_query(request.path, options)
        return self._open_request(
            "GET",
            f"/file?{query}",
            expected_statuses=(200,),
            timeout=self._timeout_from_options(options),
        )

    def write_file(self, request: UploadBytesRequest, options: CmdRequestOptions | None = None) -> None:
        query = self._file_query(request.path, options)
        payload = gzip.compress(request.data) if request.gzip_compress else request.data
        headers = dict((options.headers if options else {}) or {})
        headers["Content-Type"] = "application/octet-stream"
        if request.gzip_compress:
            headers["Content-Encoding"] = "gzip"
        self._request_empty(
            "POST",
            f"/file?{query}",
            headers=headers,
            raw_body=payload,
            expected_statuses=(204,),
            timeout=self._timeout_from_options(options),
        )

    def _build_url(self, path: str) -> str:
        normalized_path = path.strip() or "/"
        query = ""
        if "?" in normalized_path:
            normalized_path, query = normalized_path.split("?", 1)
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        parsed = urlsplit(self.base_url)
        base_path = parsed.path.rstrip("/")
        req_path = normalized_path.lstrip("/")
        if base_path:
            path_part = f"{base_path}/{req_path}" if req_path else base_path
        else:
            path_part = f"/{req_path}" if req_path else "/"
        return urlunsplit((parsed.scheme, parsed.netloc, path_part, query, ""))

    def _build_headers(self, extra_headers: Mapping[str, str] | None = None, *, accept: str = "application/json") -> dict[str, str]:
        headers = {"User-Agent": f"seacloudai-sandbox-python-cmd/{SDK_VERSION}"}
        if accept:
            headers["Accept"] = accept
        if self.access_token:
            headers["X-Access-Token"] = self.access_token
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _basic_headers(self, options: CmdRequestOptions | None = None, *, include_content_type: bool = False) -> dict[str, str]:
        headers = self._build_headers(options.headers if options else None)
        if include_content_type:
            headers["Content-Type"] = "application/json"
        if options and options.username.strip() and "Authorization" not in headers:
            token = base64.b64encode(f"{options.username.strip()}:".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _connect_headers(self, options: CmdRequestOptions | None = None, *, connect_content_type: bool = False) -> dict[str, str]:
        headers = self._basic_headers(options)
        headers["Connect-Protocol-Version"] = "1"
        headers["Content-Type"] = "application/connect+json" if connect_content_type else "application/json"
        return headers

    def _json_headers(self, options: CmdRequestOptions | None = None) -> dict[str, str]:
        headers = self._build_headers(options.headers if options else None)
        headers["Content-Type"] = "application/json"
        return headers

    def _query_from_options(self, options: CmdRequestOptions | None) -> str:
        if not options:
            return ""
        params: dict[str, str] = {}
        if options.username.strip():
            params["username"] = options.username.strip()
        if options.signature.strip():
            params["signature"] = options.signature.strip()
        if options.signature_expiration is not None:
            params["signature_expiration"] = str(options.signature_expiration)
        return urlencode(params)

    def _file_query(self, path: str, options: CmdRequestOptions | None) -> str:
        self._require_path(path)
        base = urlencode({"path": path})
        extra = self._query_from_options(options)
        return f"{base}&{extra}" if extra else base

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        raw_body: bytes | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        timeout: float | None = None,
    ) -> Any:
        response = self._open_request(
            method,
            path,
            headers=headers,
            body=body,
            data=raw_body,
            expected_statuses=expected_statuses,
            timeout=timeout,
        )
        with response:
            return json.loads(response.read().decode("utf-8"))

    def _request_empty(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        raw_body: bytes | None = None,
        expected_statuses: tuple[int, ...] = (204,),
        timeout: float | None = None,
    ) -> None:
        response = self._open_request(
            method,
            path,
            headers=headers,
            body=body,
            data=raw_body,
            expected_statuses=expected_statuses,
            timeout=timeout,
        )
        with response:
            response.read()

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected_statuses: tuple[int, ...],
        timeout: float | None = None,
    ):
        return self._open_request(method, path, expected_statuses=expected_statuses, timeout=timeout)

    def _open_request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        data: bytes | None = None,
        expected_statuses: tuple[int, ...] | None = (200,),
        timeout: float | None = None,
    ):
        payload = data if data is not None else (None if body is None else json.dumps(body).encode("utf-8"))
        request = Request(
            url=self._build_url(path),
            data=payload,
            headers=self._build_headers(headers),
            method=method.upper(),
        )
        request_timeout = self.timeout if timeout is None else timeout
        try:
            response = urlopen(request, timeout=request_timeout)
        except HTTPError as exc:
            if expected_statuses is None:
                return exc
            raise self._decode_api_error(exc) from exc
        except TimeoutError as exc:
            raise RequestTimeoutError(request_timeout, cause=exc) from exc
        except socket.timeout as exc:
            raise RequestTimeoutError(request_timeout, cause=exc) from exc

        status_code = getattr(response, "status", response.getcode())
        if expected_statuses is not None and status_code not in expected_statuses:
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
        return create_api_error(
            (parsed or {}).get("message") or getattr(response, "reason", "request failed"),
            status_code=getattr(response, "status", response.getcode()),
            code=(parsed or {}).get("code"),
            request_id=(parsed or {}).get("request_id"),
            detail=(parsed or {}).get("error"),
            body=body,
        )

    def _connect_json(self, path: str, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> dict[str, Any]:
        return self._request_json(
            "POST",
            path,
            headers=self._connect_headers(options),
            body=body,
            timeout=self._timeout_from_options(options),
        )

    def _connect_empty(self, path: str, body: Mapping[str, Any], options: CmdRequestOptions | None = None) -> None:
        self._request_empty(
            "POST",
            path,
            headers=self._connect_headers(options),
            body=body,
            expected_statuses=(200,),
            timeout=self._timeout_from_options(options),
        )

    def _timeout_from_options(self, options: CmdRequestOptions | None) -> float | None:
        if options is None or options.timeout is None:
            return None
        return options.timeout

    def _validate_selector(self, selector: Mapping[str, Any]) -> None:
        has_pid = selector.get("pid") is not None
        has_tag = bool(str(selector.get("tag", "")).strip())
        if not has_pid and not has_tag:
            raise ValidationError("process selector requires pid or tag")
        if has_pid and has_tag:
            raise ValidationError("process selector requires exactly one of pid or tag")

    def _validate_input(self, input_body: Mapping[str, Any]) -> None:
        if not str(input_body.get("stdin", "")).strip() and not str(input_body.get("pty", "")).strip():
            raise ValidationError("process input requires stdin or pty")

    def _require_path(self, path: str) -> None:
        if not path.strip():
            raise ValidationError("path is required")

    def _encode_connect_frames(self, frames: list[Mapping[str, Any]]) -> bytes:
        data = bytearray()
        for frame in frames:
            payload = json.dumps(frame).encode("utf-8")
            data.append(0)
            data.extend(len(payload).to_bytes(4, "big"))
            data.extend(payload)
        return bytes(data)

    def _encode_multipart(self, parts: list[Any]) -> tuple[bytes, str]:
        boundary = f"----seacloudai-sandbox-{uuid.uuid4().hex}"
        body = bytearray()
        for part in parts:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            disposition = f'form-data; name="{part.field_name or "file"}"'
            if part.file_name:
                disposition += f'; filename="{part.file_name}"'
            body.extend(f"Content-Disposition: {disposition}\r\n".encode("utf-8"))
            if part.content_type:
                body.extend(f"Content-Type: {part.content_type}\r\n".encode("utf-8"))
            body.extend(b"\r\n")
            body.extend(part.data)
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        return bytes(body), f"multipart/form-data; boundary={boundary}"
