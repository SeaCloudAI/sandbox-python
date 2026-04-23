from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, BinaryIO, Mapping


@dataclass
class CmdRequestOptions:
    username: str = ""
    signature: str = ""
    signature_expiration: int | None = None
    range: str = ""
    timeout: float | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class DownloadRequest:
    path: str


@dataclass
class FilesContentRequest:
    path: str
    max_tokens: int | None = None


@dataclass
class UploadBytesRequest:
    path: str
    data: bytes
    gzip_compress: bool = False


@dataclass
class MultipartFile:
    data: bytes
    field_name: str = "file"
    file_name: str = ""
    content_type: str = "application/octet-stream"


@dataclass
class UploadMultipartRequest:
    parts: list[MultipartFile]
    path: str = ""


@dataclass
class FileRequest:
    path: str


@dataclass
class ProxyRequest:
    port: int
    method: str = "GET"
    path: str = ""
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ConnectFrame:
    flags: int
    payload: bytes

    def is_end(self) -> bool:
        return (self.flags & 0x02) != 0

    def json(self) -> Any:
        import json

        return json.loads(self.payload.decode("utf-8"))


class ConnectStream:
    def __init__(self, response: BinaryIO) -> None:
        self._response = response

    @property
    def response(self) -> BinaryIO:
        return self._response

    def close(self) -> None:
        self._response.close()

    def next_frame(self) -> ConnectFrame | None:
        import struct

        header = self._read_exact(5)
        if header is None:
            return None
        flags = header[0]
        length = struct.unpack(">I", header[1:5])[0]
        payload = self._read_exact(length)
        if payload is None:
            raise EOFError("unexpected end of connect stream")
        return ConnectFrame(flags=flags, payload=payload)

    def next_json(self) -> Any | None:
        while True:
            frame = self.next_frame()
            if frame is None:
                return None
            if not frame.payload:
                if frame.is_end():
                    return None
                continue
            return frame.json()

    def _read_exact(self, size: int) -> bytes | None:
        if size == 0:
            return b""
        data = self._response.read(size)
        if not data:
            return None
        while len(data) < size:
            chunk = self._response.read(size - len(data))
            if not chunk:
                return None
            data += chunk
        return data


class ProcessStream(ConnectStream):
    def next(self) -> Mapping[str, Any] | None:
        return self.next_json()


class FilesystemWatchStream(ConnectStream):
    def next(self) -> Mapping[str, Any] | None:
        return self.next_json()
