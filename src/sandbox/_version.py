from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _resolve_version() -> str:
    try:
        value = version("seacloudai-sandbox").strip()
    except PackageNotFoundError:
        return "dev"
    return value or "dev"


SDK_VERSION = _resolve_version()
