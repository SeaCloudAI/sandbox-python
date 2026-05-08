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
    root = "/root/workspace"

    client = Client(base_url=base_url, api_key=api_key)
    created = client.create(template_id, timeout=1800, waitReady=True)

    try:
        file_path = f"{root}/python-cmd-example.txt"

        created.files.write(file_path, b"hello from python example")

        print("file content:", created.files.read(file_path))

        listing = created.files.list(root)
        print("directory entries:", len(listing))

        run = created.commands.run("sh", args=["-lc", f"cat {file_path}"])
        print("run result:", run["exitCode"], repr(run["stdout"]))

    finally:
        if not keep_resources:
            created.delete()


if __name__ == "__main__":
    main()
