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

from sandbox import Sandbox


def main() -> None:
    if not os.getenv("SEACLOUD_API_KEY", "").strip():
        raise RuntimeError("SEACLOUD_API_KEY is required")

    template_id = os.getenv("SANDBOX_EXAMPLE_TEMPLATE_ID", "").strip()
    if not template_id:
        raise RuntimeError("SANDBOX_EXAMPLE_TEMPLATE_ID is required")

    keep_resources = os.getenv("SANDBOX_EXAMPLE_KEEP_RESOURCES", "").strip().lower() in {"1", "true", "yes"}

    created = Sandbox.create(template_id, timeout=1800, waitReady=True)
    print("created sandbox:", created.sandbox_id, created.raw.get("status"), created.raw.get("envdUrl"))
    if created.sandbox_domain:
        print("sandbox domain:", created.sandbox_domain)

    try:
        created.reload()
        print("sandbox detail:", created.sandbox_id, created.raw.get("state"), created.raw.get("status"))
    finally:
        if not keep_resources:
            created.delete()
            print("deleted sandbox:", created.sandbox_id)


if __name__ == "__main__":
    main()
