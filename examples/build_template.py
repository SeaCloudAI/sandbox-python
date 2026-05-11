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

from sandbox import Template, default_build_logger, wait_for_file


def main() -> None:
    if not os.getenv("E2B_API_KEY", "").strip():
        raise RuntimeError("E2B_API_KEY is required")

    image = os.getenv("SANDBOX_EXAMPLE_BUILD_IMAGE", "").strip() or "docker.io/library/alpine:3.20"
    keep_resources = os.getenv("SANDBOX_EXAMPLE_KEEP_RESOURCES", "").strip().lower() in {"1", "true", "yes"}

    built = Template.build(
        Template()
        .from_image(image)
        .run_cmd("echo 'hello from python build example' >/tmp/built-by-python-example.txt")
        .set_ready_cmd(wait_for_file("/tmp/built-by-python-example.txt")),
        f"python-build-example-{time.time_ns()}:v1",
        on_build_logs=default_build_logger(),
    )

    try:
        status = Template.get_build_status({"template_id": built["template_id"], "build_id": built["build_id"]}, limit=10)
        detail = Template.get(built["template_id"])
        print(
            "template detail:",
            detail["templateID"],
            len(detail.get("builds", [])),
            detail.get("extensions", {}).get("visibility"),
            status.get("status"),
            built.get("build_id"),
        )
    finally:
        if not keep_resources:
            Template.delete(built["template_id"])
            print("deleted template:", built["template_id"])


if __name__ == "__main__":
    main()
