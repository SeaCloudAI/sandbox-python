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
from sandbox.build import template_build
from sandbox.build.models import BuildStatusParams


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

    alias = f"python-build-example-{time.time_ns()}"
    created = client.build.create_template({
        "name": alias,
        "alias": alias,
    })
    aliased = client.build.get_template_by_alias(alias)
    resolved = client.build.resolve_template_ref(alias)
    requested_build_id = f"build-{time.time_ns():x}"[:32]
    client.build.create_build(
        created["templateID"],
        requested_build_id,
        template_build()
        .from_image(image)
        .run("echo 'hello from python build example' >/tmp/built-by-python-example.txt")
        .to_request(),
    )
    print(
        "created template:",
        created["templateID"],
        "alias=",
        alias,
        "aliasLookup=",
        aliased["templateID"],
        "resolved=",
        resolved["templateID"],
    )
    print("triggered build:", requested_build_id)

    try:
        build_status = wait_for_build_ready(client, created["templateID"], requested_build_id)
        build_detail = client.build.get_build(created["templateID"], requested_build_id)
        history = client.build.list_builds(created["templateID"])
        detail = client.build.get_template(created["templateID"])
        print(
            "template detail:",
            detail["templateID"],
            len(detail.get("builds", [])),
            detail.get("extensions", {}).get("seacloud", {}).get("visibility"),
            build_status.get("status"),
            build_detail.get("image"),
            history.get("total"),
        )
    finally:
        if not keep_resources:
            client.build.delete_template(created["templateID"])
            print("deleted template:", created["templateID"])


def wait_for_build_ready(client: Client, template_id: str, build_id: str) -> dict:
    deadline = time.time() + 180
    last = None
    while time.time() < deadline:
        status = client.build.get_build_status(template_id, build_id, BuildStatusParams(limit=20))
        last = status
        if status.get("status") == "ready":
            return status
        if status.get("status") == "error":
            raise RuntimeError(f"build failed: {status}")
        time.sleep(2)
    raise RuntimeError(f"build did not complete before deadline: {last}")


if __name__ == "__main__":
    main()
