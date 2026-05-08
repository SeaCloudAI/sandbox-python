from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_BASE_URL = "https://sandbox-gateway.cloud.seaart.ai"


@dataclass(slots=True)
class GatewayConfig:
    base_url: str
    api_key: str
    project_id: str = ""
    timeout: float = 30.0


def resolve_gateway_config(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    project_id: str | None = None,
    timeout: float = 30.0,
) -> GatewayConfig:
    return GatewayConfig(
        base_url=base_url or os.environ.get("SEACLOUD_BASE_URL") or DEFAULT_BASE_URL,
        api_key=api_key or os.environ.get("SEACLOUD_API_KEY") or "",
        project_id=project_id if project_id is not None else os.environ.get("SEACLOUD_PROJECT_ID", ""),
        timeout=timeout,
    )
