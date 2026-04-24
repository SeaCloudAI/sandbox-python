from __future__ import annotations

import os
import sys
import time
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

    image = os.getenv("SANDBOX_EXAMPLE_BUILD_IMAGE", "").strip() or "docker.io/library/alpine:3.20"
    keep_resources = os.getenv("SANDBOX_EXAMPLE_KEEP_RESOURCES", "").strip().lower() in {"1", "true", "yes"}

    client = Client(
        base_url=base_url,
        api_key=api_key,
    )

    created = client.build.create_template({
        "name": f"python-build-example-{time.time_ns()}",
        "image": image,
    })
    print("created template:", created["templateID"], created["buildID"], created["names"])

    try:
        detail = client.build.get_template(created["templateID"])
        print("template detail:", detail["templateID"], detail["image"], detail["visibility"])
    finally:
        if not keep_resources:
            client.build.delete_template(created["templateID"])
            print("deleted template:", created["templateID"])


if __name__ == "__main__":
    main()
