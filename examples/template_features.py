from __future__ import annotations

import json
import os
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

from sandbox import Template

TERMINAL_BUILD_STATUSES = {"ready", "failed", "error", "cancelled"}


def main() -> None:
    must_env("SEACLOUD_API_KEY")
    image = os.getenv("SANDBOX_EXAMPLE_BUILD_IMAGE", "").strip() or "docker.io/library/alpine:3.20"
    keep_resources = env_enabled("SANDBOX_EXAMPLE_KEEP_RESOURCES")

    template_name = f"python-template-features-{time.time_ns()}:v1"
    template_id = ""

    try:
        with tempfile.TemporaryDirectory(prefix="sandbox-python-template-features-") as temp_root:
            temp_path = Path(temp_root)
            dockerfile_path = prepare_dockerfile_fixture(temp_path, image)
            linked_file = temp_path / "artifact-link.txt"

            template = (
                Template()
                .from_dockerfile(str(dockerfile_path))
                .skip_cache()
                .run_cmd(
                    "printf 'extra build step from python template features\\n' >/workspace/extra-step.txt",
                    user="root",
                )
                .copy(
                    str(linked_file),
                    "/workspace/copied-link.txt",
                    mode=0o600,
                    resolve_symlinks=True,
                    user="root",
                )
            )

            request = json.loads(Template.to_json(template))
            print("template request:", request.get("fromImage"), len(request.get("steps", [])), request.get("startCmd", ""))
            print("dockerfile preview:", dockerfile_preview(Template.to_dockerfile(template)))

            built = Template.build_in_background(
                template,
                template_name,
            )
            template_id = str(built["template_id"])
            print("build started:", built["template_id"], built["build_id"])

            build_status = wait_for_build(
                template_id,
                str(built["build_id"]),
            )
            print("build finished:", build_status.get("status"), latest_build_log(build_status))
            if build_status.get("status") != "ready":
                raise RuntimeError(f"template build did not succeed: {build_status.get('status')}")

            exists = Template.exists(template_id)
            print("template exists:", exists)

            detail = Template.get(template_id)
            print(
                "template detail:",
                detail.get("templateID"),
                detail.get("buildStatus"),
                ",".join(detail.get("names", [])),
            )

            if keep_resources:
                print("kept template:", template_id)
    finally:
        if not keep_resources and template_id:
            try:
                Template.delete(template_id)
                print("deleted template:", template_id)
            except Exception as error:
                print("delete template warning:", error)


def prepare_dockerfile_fixture(root: Path, image: str) -> Path:
    source = root / "artifact.txt"
    link = root / "artifact-link.txt"
    dockerfile = root / "Dockerfile"

    source.write_text("hello from python template features\n", encoding="utf-8")
    os.symlink(source, link)
    dockerfile.write_text(
        "\n".join([
            f"FROM {image}",
            "WORKDIR /workspace",
            "COPY ./artifact.txt /workspace/from-dockerfile.txt",
            'CMD ["sleep", "infinity"]',
            "",
        ]),
        encoding="utf-8",
    )
    return dockerfile


def wait_for_build(template_id: str, build_id: str) -> dict:
    logs_offset = 0
    while True:
        status = Template.get_build_status(
            {"template_id": template_id, "build_id": build_id},
            logs_offset=logs_offset,
            limit=100,
        )

        entries = list(status.get("logEntries", []))
        for entry in entries:
            print("build log:", entry.get("level"), entry.get("step"), entry.get("message"))
        logs_offset += len(entries)

        if str(status.get("status")) in TERMINAL_BUILD_STATUSES:
            return status

        time.sleep(2.0)


def latest_build_log(build_status: dict) -> str:
    log_entries = build_status.get("logEntries", [])
    if log_entries:
        return str(log_entries[-1].get("message", ""))
    raw_logs = build_status.get("logs", [])
    if raw_logs:
        return str(raw_logs[-1])
    return ""


def dockerfile_preview(dockerfile: str) -> str:
    return " | ".join(dockerfile.splitlines()[:4])


def must_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


if __name__ == "__main__":
    main()
