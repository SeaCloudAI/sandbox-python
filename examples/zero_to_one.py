from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


def _bootstrap_local_src() -> None:
    examples_dir = Path(__file__).resolve().parent
    src_dir = examples_dir.parent / "src"
    src = str(src_dir)
    if src_dir.is_dir() and src not in sys.path:
        sys.path.insert(0, src)


_bootstrap_local_src()

from sandbox import Sandbox, Template, wait_for_port


def main() -> None:
    must_env("SEACLOUD_API_KEY")

    base_template = env("SANDBOX_EXAMPLE_BASE_TEMPLATE", "base")
    code_template = env("SANDBOX_EXAMPLE_CODE_TEMPLATE", "code-interpreter")
    frontend_template = env("SANDBOX_EXAMPLE_FRONTEND_TEMPLATE", code_template)
    keep_resources = env_enabled("SANDBOX_EXAMPLE_KEEP_RESOURCES")

    base_sandbox = None
    frontend_sandbox = None
    built_template_id = ""
    temp_app_dir = ""

    try:
        base_sandbox = Sandbox.create(base_template, timeout=1800, waitReady=True)
        print("base sandbox:", base_sandbox.sandbox_id, base_sandbox.sandbox_domain)

        base_sandbox.files.write("/root/workspace/hello.txt", "hello from a sandbox\n")
        print("file read:", base_sandbox.files.read("/root/workspace/hello.txt").strip())

        command = base_sandbox.commands.run(
            "sh",
            args=["-lc", "pwd && uname -a && ls -la /root/workspace"],
        )
        print("command exit:", command.get("exit_code"))
        print(str(command.get("stdout", "")).strip())

        base_sandbox.set_timeout(1800)
        print("is running:", base_sandbox.is_running())

        paused = base_sandbox.pause()
        print("paused:", paused)
        base_sandbox.connect(timeout=1800)
        print("resumed:", base_sandbox.is_running())

        code_sandbox = Sandbox.create(code_template, timeout=1800, waitReady=True)
        try:
            result = code_sandbox.run_code("x = 41\nx + 1")
            print("code interpreter result:", result.text)
        finally:
            if not keep_resources:
                code_sandbox.delete()

        frontend_sandbox = Sandbox.create(frontend_template, timeout=1800, waitReady=True)
        frontend_sandbox.files.make_dir("/root/workspace/frontend")
        frontend_sandbox.files.write("/root/workspace/frontend/index.html", frontend_html("runtime frontend"))
        frontend_sandbox.commands.run(
            "python3",
            args=["-m", "http.server", "3000", "--bind", "0.0.0.0"],
            cwd="/root/workspace/frontend",
            background=True,
            on_stdout=lambda chunk: print(chunk, end=""),
            on_stderr=lambda chunk: print(chunk, end="", file=sys.stderr),
        )
        print("frontend url:", frontend_sandbox.get_host(3000))

        temp_app_dir = tempfile.mkdtemp(prefix="sandbox-frontend-")
        Path(temp_app_dir, "index.html").write_text(frontend_html("template frontend"), encoding="utf-8")

        built = Template.build(
            Template()
            .from_template(base_template)
            .copy(temp_app_dir, "/workspace/frontend", force_upload=True)
            .set_start_cmd(
                "cd /workspace/frontend && python3 -m http.server 3000 --bind 0.0.0.0",
                wait_for_port(3000),
            ),
            f"python-local-frontend-{time.time_ns()}:v1",
            wait=True,
            poll_interval=2.0,
            request_timeout_ms=180_000,
        )
        built_template_id = built["template_id"]
        print("built template:", built["template_id"], built["build_id"])

        if keep_resources:
            print(
                "kept resources:",
                {
                    "base_sandbox": base_sandbox.sandbox_id,
                    "frontend_sandbox": frontend_sandbox.sandbox_id,
                    "built_template_id": built_template_id,
                },
            )
    finally:
        if temp_app_dir:
            shutil.rmtree(temp_app_dir, ignore_errors=True)
        if not keep_resources and frontend_sandbox is not None:
            frontend_sandbox.delete()
        if not keep_resources and base_sandbox is not None:
            base_sandbox.delete()
        if not keep_resources and built_template_id:
            Template.delete(built_template_id)


def frontend_html(title: str) -> str:
    return f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>{title}</title></head>
  <body>
    <h1>{title}</h1>
    <p>Served from a SeaCloudAI sandbox.</p>
  </body>
</html>
"""


def env(name: str, fallback: str) -> str:
    return os.getenv(name, "").strip() or fallback


def must_env(name: str) -> str:
    value = env(name, "")
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def env_enabled(name: str) -> bool:
    return env(name, "").lower() in {"1", "true", "yes"}


if __name__ == "__main__":
    main()
