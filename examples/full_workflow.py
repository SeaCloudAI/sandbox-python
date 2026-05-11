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

from sandbox import Sandbox, Template, default_build_logger
from sandbox.control.models import SandboxLogsParams


def main() -> None:
    must_env("E2B_API_KEY")
    runtime_base_image = must_env("SANDBOX_EXAMPLE_RUNTIME_BASE_IMAGE")
    keep_resources = env_enabled("SANDBOX_EXAMPLE_KEEP_RESOURCES")

    template_name = f"python-full-workflow-{time.time_ns()}"
    created_sandbox = None
    template_id = ""
    build_id = ""
    build_log_count = [0]
    build_logger = default_build_logger()
    try:
        built = Template.build(
            Template()
            .from_image(runtime_base_image)
            .run_cmd("mkdir -p /workspace && printf 'hello from python full workflow\\n' >/workspace/built-by-template.txt")
            .set_ready_cmd("test -f /workspace/built-by-template.txt"),
            template_name,
            wait=True,
            poll_interval=2.0,
            on_build_logs=lambda entry: _log_build_entry(entry, build_logger, build_log_count),
            timeout=180.0,
        )
        template_id = built["template_id"]
        build_id = built["build_id"]
        print("build started:", template_id, build_id)

        build_status = Template.get_build_status(
            {"template_id": template_id, "build_id": build_id},
            limit=20,
        )
        print("build ready:", build_status.get("templateId"), build_status.get("buildId"), build_status.get("status"))
        print("build logs:", build_log_count[0], latest_build_log(build_status))

        template_detail = Template.get(template_id)
        print(
            "template detail:",
            template_detail.get("templateID"),
            len(template_detail.get("builds", [])),
            template_detail.get("extensions", {}).get("imageSource"),
        )

        created_sandbox = Sandbox.create(template_id, timeout=1800, waitReady=True)
        print("sandbox created:", created_sandbox.sandbox_id, created_sandbox.raw.get("status"))

        sandbox_detail = created_sandbox.reload()
        print("sandbox detail:", sandbox_detail.raw.get("state"), sandbox_detail.raw.get("status"))

        try:
            sandbox_logs = sandbox_detail.logs(SandboxLogsParams(limit=10, direction="forward"))
            print(
                "sandbox logs:",
                len(sandbox_logs.get("logs", [])),
                latest_sandbox_log(sandbox_logs),
            )
        except Exception as error:
            print("sandbox logs warning:", error)

        connected = sandbox_detail.connect(timeout=1800)
        print("sandbox connected:", connected.sandbox_id, connected.raw.get("status"))

        try:
            runtime_metrics = connected.get_metrics()
            print(
                "runtime metrics:",
                f"cpu={runtime_metrics.get('cpu_used_pct')}",
                f"mem={runtime_metrics.get('mem_used_mib')}/{runtime_metrics.get('mem_total_mib')}",
                f"disk={runtime_metrics.get('disk_used')}/{runtime_metrics.get('disk_total')}",
            )
        except Exception as error:
            print("runtime metrics warning:", error)

        listing = connected.files.list("/workspace")
        print("workspace entries:", len(listing))

        run = connected.commands.run(
            "sh",
            args=["-lc", "cat /workspace/built-by-template.txt && echo workflow-ok"],
        )
        print("run result:", run.get("exit_code"), repr(run.get("stdout", "")), repr(run.get("stderr", "")))

        if keep_resources:
            print("kept resources:", template_id, created_sandbox.sandbox_id)
    finally:
        if not keep_resources and created_sandbox is not None:
            try:
                created_sandbox.delete()
                print("deleted sandbox:", created_sandbox.sandbox_id)
            except Exception as error:
                print("delete sandbox warning:", error)
        if not keep_resources:
            try:
                Template.delete(template_id)
                print("deleted template:", template_id)
            except Exception as error:
                print("delete template warning:", error)


def must_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def _log_build_entry(entry, logger, counter: list[int]) -> None:
    counter[0] += 1
    logger(entry)


def latest_build_log(build_status: dict) -> str:
    log_entries = build_status.get("logEntries", [])
    if log_entries:
        return str(log_entries[-1].get("message", ""))
    raw_logs = build_status.get("logs", [])
    if raw_logs:
        return str(raw_logs[-1])
    return ""


def latest_sandbox_log(logs: dict) -> str:
    entries = logs.get("logs", [])
    if not entries:
        return ""
    return str(entries[-1].get("message", ""))


if __name__ == "__main__":
    main()
