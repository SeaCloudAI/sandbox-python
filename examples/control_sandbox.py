from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_local_src() -> None:
    examples_dir = Path(__file__).resolve().parent
    src_dir = examples_dir.parent / "src"
    src = str(src_dir)
    if src_dir.is_dir() and src not in sys.path:
        sys.path.insert(0, src)


_bootstrap_local_src()

from sandbox import Client


def main() -> None:
    base_url = os.getenv("SEACLOUD_BASE_URL", "").strip()
    if not base_url:
        raise RuntimeError("SEACLOUD_BASE_URL is required")

    api_key = os.getenv("SEACLOUD_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SEACLOUD_API_KEY is required")

    template_id = os.getenv("SANDBOX_EXAMPLE_TEMPLATE_ID", "").strip()
    if not template_id:
        raise RuntimeError("SANDBOX_EXAMPLE_TEMPLATE_ID is required")

    keep_resources = os.getenv("SANDBOX_EXAMPLE_KEEP_RESOURCES", "").strip().lower() in {"1", "true", "yes"}

    client = Client(
        base_url=base_url,
        api_key=api_key,
    )

    created = client.create_sandbox({
        "templateID": template_id,
        "timeout": 1800,
        "waitReady": True,
    })
    print("created sandbox:", created["sandboxID"], created["status"], created.get("envdUrl"))
    if created.get("envdUrl"):
        print("bound runtime base_url:", created.runtime.base_url)

    try:
        detail = created.reload()
        print("sandbox detail:", detail["sandboxID"], detail.get("state"), detail["status"])
    finally:
        if not keep_resources:
            created.delete()
            print("deleted sandbox:", created["sandboxID"])


if __name__ == "__main__":
    main()
