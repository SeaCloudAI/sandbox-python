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
from sandbox.cmd import FileRequest, UploadBytesRequest


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
    root = (os.getenv("SANDBOX_EXAMPLE_SANDBOX_ROOT", "").strip() or "/root/workspace").rstrip("/")

    client = Client(
        base_url=base_url,
        api_key=api_key,
    )

    created = client.create_sandbox({
        "templateID": template_id,
        "timeout": 1800,
        "waitReady": True,
    })

    try:
        runtime = created.runtime
        file_path = f"{root}/python-cmd-example.txt"

        runtime.write_file(UploadBytesRequest(path=file_path, data=b"hello from python example"))

        with runtime.read_file(FileRequest(path=file_path)) as response:
            print("file content:", response.read().decode("utf-8"))

        listing = runtime.list_dir({"path": root, "depth": 1})
        print("directory entries:", len(listing["entries"]))

        run = runtime.run({
            "cmd": "sh",
            "args": ["-lc", f"cat {file_path}"],
        })
        print("run result:", run["exit_code"], repr(run["stdout"]))

        stream = runtime.start({
            "process": {"cmd": "cat"},
            "tag": "python-cmd-example",
        })
        try:
            first_frame = stream.next()
            print("stream started:", first_frame["event"]["start"]["pid"], first_frame["event"]["start"]["cmdId"])
        finally:
            stream.close()
    finally:
        if not keep_resources:
            created.delete()


if __name__ == "__main__":
    main()
