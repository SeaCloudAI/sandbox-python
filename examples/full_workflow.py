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
from sandbox.build.models import BuildLogsParams, BuildStatusParams
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
    created_template = client.build.create_template({
        "name": template_name,
        "visibility": "personal",
        "dockerfile": dockerfile(runtime_base_image),
    })
    template_id = created_template["templateID"]
    build_id = created_template.get("buildID", "")
    print("template created:", template_id, build_id)

    created_sandbox = None
    try:
        if not build_id:
            build_id = client.build.get_template(template_id).get("buildID", "")
        if not build_id:
            raise RuntimeError("buildID is empty")

        build_status = wait_for_build_ready(client, template_id, build_id)
        print("build ready:", template_id, build_id, build_status["status"])

        build_detail = client.build.get_build(template_id, build_id)
        print("build detail:", build_detail.get("status"), build_detail.get("image"))

        try:
            build_logs = client.build.get_build_logs(
                template_id,
                build_id,
                BuildLogsParams(limit=10, direction="forward", source="persistent"),
            )
            print(
                "build logs:",
                len(build_logs.get("logs", [])),
                latest_build_log(build_logs, build_status),
            )
        except Exception as error:
            print("build logs warning:", error)

        template_detail = client.build.get_template(template_id)
        print(
            "template detail:",
            template_detail.get("name"),
            template_detail.get("imageSource"),
            template_detail.get("buildStatus"),
        )

        created_sandbox = client.create_sandbox({
            "templateID": template_id,
            "timeout": 1800,
            "waitReady": True,
        })
        print("sandbox created:", created_sandbox["sandboxID"], created_sandbox["status"])

        sandbox_detail = created_sandbox.reload()
        print("sandbox detail:", sandbox_detail.get("state"), sandbox_detail["status"])

        try:
            sandbox_logs = sandbox_detail.logs(SandboxLogsParams(limit=10, direction="forward"))
            print(
                "sandbox logs:",
                len(sandbox_logs.get("logs", [])),
                latest_sandbox_log(sandbox_logs),
            )
        except Exception as error:
            print("sandbox logs warning:", error)

        connected = sandbox_detail.connect({"timeout": 1800})
        print("sandbox connected:", connected.status_code, connected.sandbox["sandboxID"])

        runtime = connected.sandbox.runtime

        try:
            runtime_metrics = runtime.metrics()
            print(
                "runtime metrics:",
                f"cpu={runtime_metrics.get('cpu_used_pct')}",
                f"mem={runtime_metrics.get('mem_used_mib')}/{runtime_metrics.get('mem_total_mib')}",
                f"disk={runtime_metrics.get('disk_used')}/{runtime_metrics.get('disk_total')}",
            )
        except Exception as error:
            print("runtime metrics warning:", error)

        listing = runtime.list_dir({"path": "/workspace"})
        print("workspace entries:", len(listing.get("entries", [])))

        run = runtime.run({
            "cmd": "sh",
            "args": ["-lc", "cat /workspace/built-by-template.txt && echo workflow-ok"],
        })
        print("run result:", run.get("exit_code"), repr(run.get("stdout", "")), repr(run.get("stderr", "")))

        if keep_resources:
            print("kept resources:", template_id, created_sandbox["sandboxID"])
    finally:
        if not keep_resources and created_sandbox is not None:
            try:
                created_sandbox.delete()
                print("deleted sandbox:", created_sandbox["sandboxID"])
            except Exception as error:
                print("delete sandbox warning:", error)
        if not keep_resources:
            try:
                client.build.delete_template(template_id)
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


def dockerfile(runtime_base_image: str) -> str:
    return (
        f"FROM {runtime_base_image}\n"
        "RUN mkdir -p /workspace && printf 'hello from python full workflow\\n' >/workspace/built-by-template.txt\n"
    )


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


def latest_build_log(build_logs: dict, build_status: dict) -> str:
    logs = build_logs.get("logs", [])
    if logs:
        return str(logs[-1].get("message", ""))
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
