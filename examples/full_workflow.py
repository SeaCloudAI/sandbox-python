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

from sandbox import Client, Template, default_build_logger
from sandbox.control.models import SandboxLogsParams


def main() -> None:
    base_url = must_env("SEACLOUD_BASE_URL")
    api_key = must_env("SEACLOUD_API_KEY")
    runtime_base_image = must_env("SANDBOX_EXAMPLE_RUNTIME_BASE_IMAGE")
    keep_resources = env_enabled("SANDBOX_EXAMPLE_KEEP_RESOURCES")

    client = Client(
        base_url=base_url,
        api_key=api_key,
        timeout=180,
    )

    log_metric_line("control", client.metrics)
    log_metric_line("build", client.build.metrics)

    template_name = f"python-full-workflow-{time.time_ns()}"
    created_sandbox = None
    template_id = ""
    build_id = ""
    build_log_count = [0]
    build_logger = default_build_logger()
    try:
        built = client.build_template(
            Template()
            .from_image(runtime_base_image)
            .run_cmd("mkdir -p /workspace && printf 'hello from python full workflow\\n' >/workspace/built-by-template.txt")
            .set_ready_cmd("test -f /workspace/built-by-template.txt"),
            template_name,
            wait=True,
            poll_interval=2.0,
            on_build_logs=lambda entry: _log_build_entry(entry, build_logger, build_log_count),
        )
        template_id = built["templateID"]
        build_id = built["buildID"]
        print("build ready:", template_id, build_id, built["status"])
        print("build detail:", built.get("build", {}).get("status"), built.get("build", {}).get("image"))

        build_status = client.get_template_build_status(
            {"templateID": template_id, "buildID": build_id},
            limit=20,
        )
        print("build logs:", build_log_count[0], latest_build_log(build_status))

        template_detail = client.get_template(template_id)
        print(
            "template detail:",
            template_detail.get("templateID"),
            len(template_detail.get("builds", [])),
            template_detail.get("extensions", {}).get("seacloud", {}).get("imageSource"),
        )

        created_sandbox = client.create(template_id, timeout=1800, waitReady=True)
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
                client.delete_template(template_id)
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


def first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def log_metric_line(name: str, fn) -> None:
    try:
        print(f"{name} metrics:", first_non_empty_line(fn()))
    except Exception as error:
        print(f"{name} metrics warning:", error)


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
