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
    must_env("E2B_API_KEY")
    template_id = must_env("SANDBOX_EXAMPLE_TEMPLATE_ID")
    keep_resources = env_enabled("SANDBOX_EXAMPLE_KEEP_RESOURCES")
    if looks_like_base_template(template_id):
        print("warning: code_interpreter.py expects a code-interpreter template; base is usually not enough")

    sandbox = None
    try:
        sandbox = Sandbox.create(template_id, timeout=1800, waitReady=True)
        print("sandbox created:", sandbox.sandbox_id, sandbox.raw.get("status"))

        python1 = sandbox.run_code("x = 41\nx")
        python2 = sandbox.run_code("x + 1")
        print("default python context:", python1.text, "->", python2.text)

        python_context = sandbox.create_code_context(
            language="python",
            cwd="/workspace",
            timeout=30,
        )
        sandbox.run_code("name = 'python-sdk'", context=python_context)
        python_isolated = sandbox.run_code("name.upper()", context=python_context)
        print("explicit python context:", python_isolated.text)

        bash_context = sandbox.create_code_context(
            language="bash",
            cwd="/workspace",
            timeout=10,
        )
        bash_run = sandbox.run_code("pwd && echo bash-ok", context=bash_context)
        print("bash profile output:", bash_run.logs.stdout)

        contexts = sandbox.list_code_contexts()
        print("contexts:", [
            {
                "context_id": context.context_id,
                "language": context.language,
                "cwd": context.cwd,
            }
            for context in contexts
        ])

        sandbox.restart_code_context(python_context)
        sandbox.remove_code_context(bash_context)
        sandbox.remove_code_context(python_context)
    finally:
        if not keep_resources and sandbox is not None:
            try:
                sandbox.delete()
            except Exception as error:
                print("delete sandbox warning:", error)


def must_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def looks_like_base_template(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized == "base" or normalized.startswith("tpl-base")


if __name__ == "__main__":
    main()
