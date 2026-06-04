# SeaCloudAI Sandbox Python SDK

Run code, agent workflows, and lightweight services in isolated cloud sandboxes from Python. The SDK gives you a simple E2B-style workflow for sandbox lifecycle, files, commands, PTY, code execution, service proxying, and reusable template builds.

## Why SeaCloudAI Sandbox

- **Cloud sandboxes without infrastructure work**: create disposable isolated runtimes without managing containers, runtime tokens, or service proxies yourself.
- **One object for the full workflow**: create a sandbox, write files, run commands, open a PTY, clone git repos, expose a web service, inspect logs, and clean up from the same SDK.
- **Official templates for fast starts**: use `base` for files, commands, git, and PTY; use `code-interpreter` for multi-language code execution; use agent templates such as `claude` or `codex` when your environment publishes them.
- **Reusable environments**: prototype in a running sandbox, then bake stable setup into a custom `tpl-...` template that can be pinned and reused in production.
- **Familiar migration path**: lifecycle, filesystem, commands, PTY, code interpreter, and template helpers follow E2B-style patterns while using SeaCloudAI gateway and runtime configuration.
- **Same concepts across SDKs**: Node, Python, and Go expose the same core sandbox, runtime, and template-building model.

## Install

```bash
pip install seacloud-sandbox
```

## Configure

Use environment variables for gateway configuration in all examples and quick starts:

- `SEACLOUD_BASE_URL`: required full gateway API root
- `SEACLOUD_API_KEY`: preferred API key

Set them once in your shell:

```bash
export SEACLOUD_BASE_URL="https://sandbox-gateway.cloud.seaart.ai/api/v1"
export SEACLOUD_API_KEY="..."
```

Example production gateway API root:

```text
https://sandbox-gateway.cloud.seaart.ai/api/v1
```

The SeaCloudAI production gateway is currently hosted under the `seaart.ai` domain.

For gateways that mount the sandbox API under another path, keep the same client code and configure the full API root:

```bash
export SEACLOUD_BASE_URL="https://seacloud-sandbox-service.dev.seaart.dev/api/v1/sandbox"
```

High-level `Sandbox.create(...)` requires an explicit template reference. Pass a concrete template ID such as `tpl-...` or a stable official template type such as `base`, `code-interpreter`, `claude`, or `codex` when your environment publishes those official templates.

## 60-Second Quickstart

```python
from sandbox import Sandbox

sandbox = Sandbox.create("base", timeout=1800, waitReady=True)

try:
    sandbox.files.write("/root/workspace/hello.txt", "hello from SeaCloudAI\n")

    result = sandbox.commands.run(
        "sh",
        args=["-lc", "cat /root/workspace/hello.txt && uname -a"],
    )

    print(result["stdout"])
finally:
    sandbox.delete()
```

That is the core loop: create an isolated cloud runtime, move files in, run real commands, and clean it up. Use `sandbox.get_host(port)` when you start an HTTP service and want a public proxy URL.

## Network Control

Use `network` on create to restrict sandbox egress. `allowOut` and `denyOut` accept IPv4 CIDRs or single IPv4 addresses; the control plane normalizes single IPs to `/32`.

```python
sandbox = Sandbox.create(
    "base",
    waitReady=True,
    network={
        "allowInternetAccess": False,
        "allowOut": ["1.1.1.1"],
    },
)
```

The platform keeps required DNS and control-plane heartbeat traffic working automatically. Domain allowlists are not supported yet; use IP/CIDR rules.

## Main Entrypoints

- `Sandbox.create(...)`, `Sandbox.connect(...)`, `Sandbox.list(...)`: create and manage sandboxes.
- `sandbox.files`, `sandbox.commands`, `sandbox.git`, `sandbox.pty`: work inside a running sandbox.
- `sandbox.run_code(...)`: execute code in `code-interpreter` templates.
- `Template()`, `Template.build(...)`, `Template.build_in_background(...)`: build reusable templates.
- `sandbox.control`, `sandbox.build`, `sandbox.cmd`: low-level transports for advanced integrations.

Runtime access is derived from sandbox create/detail/connect responses. Do not hardcode runtime endpoints or tokens.

## E2B-Style Workflow

The high-level lifecycle, filesystem, command, git, PTY, code interpreter, and template APIs are designed to feel familiar to E2B users. Snapshot APIs are not exposed yet because the underlying platform does not support them. Python code contexts are stateful; non-Python code contexts currently behave as reusable execution profiles over isolated one-shot processes.

## Guided Walkthrough

This section is the recommended path for first-time users. It starts from environment setup, then creates sandboxes from official templates, runs commands, exposes a frontend through `envdUrl`, and finally builds a reusable custom template from local code.

### 1. Configure Environment

```bash
export SEACLOUD_BASE_URL="https://sandbox-gateway.cloud.seaart.ai/api/v1"
export SEACLOUD_API_KEY="..."
```

Run the examples from `packages/python`:

```bash
pip install -e .
python examples/zero_to_one.py
```

### 2. Create A Base Sandbox

Use `base` for normal sandbox lifecycle, files, commands, git, and PTY. It is the right starting point for command execution and filesystem work.

```python
from sandbox import Sandbox

sandbox = Sandbox.create("base", timeout=1800, waitReady=True)

try:
    print("sandbox", sandbox.sandbox_id, sandbox.sandbox_domain)

    sandbox.files.write("/root/workspace/hello.txt", "hello\n")
    print(sandbox.files.read("/root/workspace/hello.txt"))

    result = sandbox.commands.run(
        "sh",
        args=["-lc", "pwd && uname -a && ls -la /root/workspace"],
    )
    print(result["exit_code"], result["stdout"])
finally:
    sandbox.delete()
```

### 3. Pick Official Templates By Workload

| Workload | Template | Use it for |
| --- | --- | --- |
| Basic shell, files, git, PTY, and lightweight services | `base` | General sandbox lifecycle and filesystem/command workflows. |
| Multi-language code execution | `code-interpreter` | `sandbox.run_code(...)` for Python, JavaScript, TypeScript, Bash, R, and Java. Python contexts are stateful. |
| Agent CLI workflows | `claude` / `codex` | Environments where those official agent templates are published with the CLIs preinstalled. |
| Reproducible production workloads | `tpl-...` | A concrete custom or official template ID pinned from config. |

```python
code_sandbox = Sandbox.create("code-interpreter", waitReady=True)

try:
    execution = code_sandbox.run_code("x = 41\nx + 1")
    print(execution.text)
finally:
    code_sandbox.delete()
```

### 4. Manage Lifecycle

Lifecycle `timeout` values are seconds. Runtime command `timeout_ms` values are milliseconds.

```python
sandbox = Sandbox.create("base", timeout=1800, waitReady=True)

info = sandbox.get_info()
print(info["sandbox_id"], info["state"])

sandbox.set_timeout(3600)

paused = sandbox.pause()
print("paused", paused)

sandbox.connect(timeout=1800)
print("running", sandbox.is_running())

sandbox.delete()
```

### 5. Deploy A Frontend And Open It Through `envdUrl`

Use a template that has Python or Node available. `code-interpreter` is a convenient default for this static frontend example because it can run `python3 -m http.server`.

```python
app = Sandbox.create("code-interpreter", timeout=1800, waitReady=True)

try:
    app.files.make_dir("/root/workspace/frontend")
    app.files.write(
        "/root/workspace/frontend/index.html",
        "<h1>Hello from sandbox</h1>",
    )

    app.commands.run(
        "python3",
        args=["-m", "http.server", "3000", "--bind", "0.0.0.0"],
        cwd="/root/workspace/frontend",
        background=True,
    )

    print("open", app.get_host(3000))
finally:
    app.delete()
```

`get_host(3000)` derives a public proxy URL from the sandbox `envdUrl`. Keep `envdAccessToken` / `traffic_access_token` private; they are sandbox-scoped secrets.

Service access notes:

- Bind HTTP services to `0.0.0.0`, not `127.0.0.1`, so the runtime proxy can reach them.
- Use `sandbox.get_host(port)` instead of constructing proxy URLs manually.
- If the URL does not open, check that the process is still running, the port matches, and the selected template exposes runtime access fields.

### 6. Upload Local Code Files

There are two common upload paths:

- Runtime upload to an existing sandbox: use `sandbox.files.write(...)` / `write_files(...)` when you want to place generated files into a running sandbox.
- Template build upload: use `Template.copy(...)` when you want local files or directories baked into a reusable template image.

Upload a local file into a running sandbox:

```python
from pathlib import Path

data = Path("./my-frontend/index.html").read_bytes()
sandbox.files.write("/root/workspace/frontend/index.html", data)
```

Upload one local file into a template build:

```python
Template() \
    .from_template("base") \
    .copy("./package.json", "/workspace/app/package.json", force_upload=True)
```

Upload a local directory recursively:

```python
Template() \
    .from_template("base") \
    .copy(
        "./my-frontend",
        "/workspace/frontend",
        force_upload=True,
        mode=0o755,
        resolve_symlinks=True,
    )
```

The first argument is a local path on your machine. The second argument is the destination path inside the template filesystem. `force_upload=True` is useful during development when the local files change frequently and you want the SDK to re-upload them instead of reusing a cached content hash.

Template build contexts are packed as `tar.gz` archives and must fit the server-side `maxContextBytes` limit returned by the build file handshake. The SDK validates the archive size before upload and sends the required GCS content-length-range header.

### 7. Build Your Own Template From Local Code

This uploads a local directory into the build context with `copy(...)`, builds a reusable template image, and sets a startup command for future sandboxes created from that template.

```python
from sandbox import Template, wait_for_port

built = Template.build(
    Template()
    .from_node_image("20-alpine")
    .copy("./my-frontend", "/app", force_upload=True)
    .run_cmd("cd /app && npm install")
    .set_start_cmd("cd /app && npm start", wait_for_port(3000)),
    "my-frontend:v1",
    wait=True,
    poll_interval=2.0,
    request_timeout_ms=180_000,
)

print(built["template_id"], built["build_id"])
```

Use `from_template("base")` when you want to inherit a published SeaCloud template, or `from_node_image(...)`, `from_python_image(...)`, and other image helpers when a public base image is enough. Advanced storage options such as NFS, block volumes, and object storage are documented later in the build reference.

Create a sandbox from the new template:

```python
sandbox = Sandbox.create(built["template_id"], waitReady=True)
print(sandbox.get_host(3000))
```

### 8. Recommended Production Flow

1. Prototype with an official template such as `base` or `code-interpreter`.
2. Upload local files to a running sandbox for fast iteration.
3. Move stable setup into `Template.copy(...)`, `run_cmd(...)`, `set_start_cmd(...)`, and `set_ready_cmd(...)`.
4. Build and pin the resulting `tpl-...` value in application config.
5. Keep sandbox cleanup in `finally` blocks or a lifecycle manager, and set explicit lifecycle `timeout` values for each workload.

## Troubleshooting

- `401` / `403`: verify `SEACLOUD_API_KEY` and that the process sees the environment variable.
- Requests go to the wrong gateway: check `SEACLOUD_BASE_URL`; include the `https://` scheme.
- Runtime APIs return `404`: use a template that supports managed runtime access and returns `envdUrl` / `envdAccessToken`.
- `waitReady` or builds time out: increase lifecycle `timeout` and SDK HTTP `request_timeout_ms` for long starts or image builds.
- Frontend URL is unreachable: bind to `0.0.0.0`, confirm the port passed to `get_host(...)`, and inspect whether the background process exited.
- Build with local files fails: make sure `Template.copy(...)` points to an existing local path and use `force_upload=True` while iterating.

## Diagnostics

The SDK is quiet by default. Pass `debug=True` for stderr-style request diagnostics, or pass `logger` to receive structured, sanitized lifecycle events. Every SDK request carries `X-Request-ID`; response and error events include the same ID when available.

```python
from sandbox._client import GatewayClient

client = GatewayClient(
    logger=lambda event: print(
        event["type"],
        event["method"],
        event["path"],
        event["request_id"],
        event.get("status"),
    ),
)

client.list_sandboxes({"limit": 10})
```

Diagnostic events include method, path, request ID, status, duration, error kind, and retryability. They intentionally exclude request/response bodies and credential headers; sensitive query values such as tokens, signatures, and `api_key` are redacted, including when a transport error embeds a URL. Logger failures are ignored so diagnostics cannot change request behavior.

## Production Readiness

- Initialize environment variables once per process and reuse bound sandbox/template objects.
- Treat every quick start as creating billable or quota-bound resources unless it explicitly cleans them up.
- Prefer explicit template references from configuration over hardcoded example values.
- In SeaCloudAI environments, prefer official template types such as `base`, `code-interpreter`, `claude`, or `codex` when you want a stable platform-managed entrypoint.
- Template semantics matter: `base` is the minimal runtime template for lifecycle, files, commands, git, and PTY. It does not imply a multi-language execution environment. Use `code-interpreter` for `sandbox.run_code(...)`, and use agent-specific templates such as `claude` or `codex` when you need those CLIs preinstalled.
- Use longer SDK HTTP timeouts for `waitReady` flows and image builds.
- Derive runtime access from sandbox responses instead of storing runtime endpoints or tokens in config.

## Compatibility

- Python: use a supported CPython version for the published package and pin the SDK version in production deployments.
- API model: this SDK targets the unified SeaCloudAI sandbox gateway and keeps public template APIs limited to user-facing fields.
- Stability: operator/admin routes may exist on the gateway, but they are not part of the public SDK workflow described in this README.
- Retry model: treat create/delete/build operations as remote control-plane actions; add idempotency and retry policy in your application layer according to your workload.
- Timeout semantics: sandbox lifecycle uses E2B-style `timeout` seconds. Commands, PTY, git, and code execution helpers use `timeout_ms` milliseconds. `request_timeout_ms` is only the SDK HTTP request timeout in milliseconds.

## Additional Examples

### Control Plane

```python
from sandbox import Sandbox

sandbox = Sandbox.create(
    "base",
    timeout=1800,
    waitReady=True,
)
try:
    print(sandbox.sandbox_id, sandbox.sandbox_domain)
finally:
    sandbox.delete()
```

### Bound Sandbox Workflow

```python
from sandbox import Sandbox

listed = Sandbox.list()

for sandbox in listed:
    print(sandbox["sandboxID"], sandbox.get("state") or sandbox.get("status"))
```

### Template Build

```python
from sandbox import Template, wait_for_file

built = Template.build(
    Template()
    .from_image("docker.io/library/alpine:3.20")
    .run_cmd("echo hello-from-python >/tmp/hello.txt")
    .set_ready_cmd(wait_for_file("/tmp/hello.txt")),
    "demo:v1",
)

print(built["template_id"], built["build_id"])
```

High-level template helpers currently include:

- lifecycle and status: `Template.build`, `Template.build_in_background`, `Template.exists`, `Template.get_build_status`, `Template.list`, `Template.get`, `Template.delete`
- serialization: `Template.to_json`, `Template.to_dockerfile`
- base images and registries: `from_dockerfile`, `from_base_image`, `from_node_image`, `from_python_image`, `from_bun_image`, `from_ubuntu_image`, `from_debian_image`, `from_aws_registry`, `from_gcp_registry`
- build-step helpers: `copy`, `copy_items`, `skip_cache`, `apt_install`, `git_clone`, `make_dir`, `make_symlink`, `npm_install`, `pip_install`, `bun_install`, `remove`, `rename`
- execution and config helpers: `run_cmd`, `set_envs`, `set_workdir`, `set_user`, `set_start_cmd`, `set_ready_cmd`, `files_hash`
- supported local copy options: `force_upload`, `mode`, `resolve_symlinks`, `user`
- supported command and path options: `run_cmd(..., user=...)`, `git_clone(..., user=...)`, `make_dir(..., user=...)`, `make_symlink(..., user=...)`, `remove(..., user=...)`, `rename(..., user=...)`
- intentionally not exposed yet: MCP server helpers and devcontainer helpers

### Runtime Modules

```python
from sandbox import Sandbox

sandbox = Sandbox.create(
    "base",
    waitReady=True,
)

try:
    sandbox.files.write("/root/workspace/hello.txt", b"hello from python")
    print(sandbox.files.read("/root/workspace/hello.txt"))
    print(sandbox.get_host(3000))
finally:
    sandbox.delete()
```

### Code Interpreter

Use a template that actually includes the code-interpreter environment here. In SeaCloudAI environments, prefer an official `code-interpreter` template or a concrete `tpl-code-interpreter-...` template ID. Do not use `base` for this example.

```python
from sandbox import Sandbox

sandbox = Sandbox.create(
    "code-interpreter",
    waitReady=True,
)

try:
    execution = sandbox.run_code(
        """
import pandas as pd

df = pd.DataFrame([{"name": "Ada", "score": 99}])
display(df)
99
        """,
        on_stdout=lambda chunk: print("stdout:", chunk.line),
        on_stderr=lambda chunk: print("stderr:", chunk.line),
        on_result=lambda result: print("result:", result),
    )

    print(execution.text)
finally:
    sandbox.delete()
```

For Python, repeated `sandbox.run_code(...)` calls reuse the sandbox's default code context. You can create additional Python contexts with `create_code_context(...)` when you need isolated state. For other languages, `create_code_context(...)` returns a reusable execution profile that supplies default `language`, `cwd`, and `timeout_ms` values, but each run still executes in a fresh one-shot process.

Bound sandbox helpers currently include:

- lifecycle: `reload`, `connect`, `resume`, `get_info`, `get_full_info`, `logs`, `pause`, `kill`, `delete`, `refresh`, `set_timeout`, `is_running`
  `pause()` returns `True` when a running sandbox is newly paused and `False` when it was already paused.
  `get_info()` / `get_full_info()` return normalized sandbox info with `sandbox_id`, `template_id`, `sandbox_domain`, `traffic_access_token`, `started_at`, `end_at`, and `state`.
  Lifecycle helpers accept `request_timeout_ms` for SDK HTTP request timeout overrides.
- runtime conveniences: `get_metrics`, `get_host`, `download_url`, `upload_url`, `proxy`
- code interpreter: `run_code`, `create_code_context`, `list_code_contexts`, `restart_code_context`, `remove_code_context`
- commands module: `run`, `exec`, `list`, `connect`, `kill`, `send_stdin`
  `run()` / `exec()` accept `timeout_ms`, `stdin`, `on_stdout`, `on_stderr`, and `user`; callbacks and open-stdin mode use the runtime streaming protocol.
  `connect(pid, on_stdout=..., on_stderr=...)` attaches output callbacks to an existing process stream. Command handles expose both `send_stdin(...)` and the E2B-style `send_input(...)` alias.
- filesystem module: `exists`, `get_info`, `list`, `make_dir`, `read`, `write`, `write_files`, `remove`, `rename`, `watch_dir`
  `get_info()` / `list()` / `rename()` return normalized entries with `type: "file" | "dir" | "symlink"` and `modified_time`.
  `write()` / `write_files()` return E2B-style write info with `name`, `path`, and `type`.
  `read(..., format="text"|"bytes"|"stream")` returns text, bytes, or the raw response stream.
  File methods accept `user`; `make_dir()` returns `False` when the path already exists.
  `watch_dir(path, on_event, ...)` returns a stop handle instead of the raw stream. It also supports `user`, `timeout_ms`, and `on_exit`.
- git module: `clone`, `pull`, `checkout`, `status`
- pty module: `create`, `connect`, `kill`, `send_stdin`, `send_input`, `resize`
  `pty.connect(pid, on_stdout=..., on_stderr=...)` attaches output callbacks when reconnecting to a PTY.

## Recommended Usage

For most integrations, prefer the env-first high-level flow:

- set `SEACLOUD_BASE_URL` and `SEACLOUD_API_KEY`
- create sandboxes with `Sandbox.create(...)`
- continue through `sandbox.commands`, `sandbox.files`, `sandbox.git`, and `sandbox.pty`
- build templates with `Template.build(...)` and `Template.build_in_background(...)`
- only drop to `sandbox.control`, `sandbox.build`, or `sandbox.cmd` when you need transport-level request control

Low-level methods remain available when you need tighter request control:

- continue from the returned sandbox object with `reload()`, `connect()`, `resume()`, `get_info()`, `get_full_info()`, `get_metrics()`, `get_host()`, `logs()`, `pause()`, `refresh()`, `set_timeout()`, `kill()`, `delete()`, and `is_running()`
- use `Template.build(...)`, `Template.build_in_background(...)`, `Template.exists(...)`, `Template.get_build_status(...)`, `Template.list(...)`, `Template.get(...)`, and `Template.delete(...)` for the preferred template workflow
- use `ControlService`, `BuildService`, and runtime service helpers from the submodules only for raw control/build/cmd workflows
- use `template_build()` when you want a small fluent helper that expands into the public build request payload

Low-level submodules remain available when you need direct request/response models or tighter transport control.

## API Surface

### Control Plane APIs

- high-level lifecycle: `create`, `connect`, `list`
- follow-up control actions from the returned object: `reload()`, `connect()`, `resume()`, `get_info()`, `get_full_info()`, `get_metrics()`, `get_host()`, `logs()`, `pause()`, `refresh()`, `set_timeout()`, `kill()`, `delete()`, `is_running()`
- low-level control module: `ControlService` from `sandbox.control`
- low-level service methods: `metrics`, `shutdown`, `create_sandbox`, `list_sandboxes`, `get_sandbox`, `get_sandbox_metrics`, `list_sandbox_metrics`, `delete_sandbox`, `get_sandbox_logs`, `pause_sandbox`, `connect_sandbox`, `set_sandbox_timeout`, `refresh_sandbox`, `send_heartbeat`

### Monitoring And Metrics

The SDK exposes two different metrics surfaces:

- **Control-plane sandbox metrics** use the platform runtime metrics service through the gateway. Prefer these for dashboards and fleet monitoring because they return user-facing load average, CPU breakdown, memory pressure, disk I/O, network throughput, and task-count fields.
- **Runtime metrics** call the sandbox runtime `/metrics` endpoint through `envdUrl`. Use these when you are already connected to one runtime and only need the direct in-sandbox snapshot. The runtime payload includes CPU, memory, disk, and cumulative network byte counters.

Control-plane metrics:

```python
import os

from sandbox.control import ControlService, SandboxMetricsParams

client = ControlService(
    base_url=os.environ["SEACLOUD_BASE_URL"],
    api_key=os.environ["SEACLOUD_API_KEY"],
)

single = client.get_sandbox_metrics("sandbox-abc")
print(single.get("load1"), single.get("memoryUsagePercent"))
print(single.get("networkSentBytesPerSecond"), single.get("diskWriteBytesPerSecond"))

batch = client.list_sandbox_metrics(
    SandboxMetricsParams(sandbox_ids=["sandbox-abc", "sandbox-def"], limit=2)
)
print([item["sandboxID"] for item in batch["items"]])
```

Control-plane snapshot fields include:

- identity and status: `sandboxID`, `collectedAt`, `error`
- CPU: `load1`, `load5`, `load15`, `cpuUserRate`, `cpuSystemRate`, `cpuIOWaitRate`, `cpuStealRate`
- memory: `memoryAvailableBytes`, `memoryUsagePercent`, `swapTotalBytes`, `swapFreeBytes`, `swapCachedBytes`
- disk: `diskReadOpsPerSecond`, `diskWriteOpsPerSecond`, `diskReadBytesPerSecond`, `diskWriteBytesPerSecond`
- network: `networkRecvBytesPerSecond`, `networkSentBytesPerSecond`, packet/error/drop rates
- tasks: `taskCurrent`, `taskMax`

Runtime metrics:

```python
runtime_metrics = sandbox.get_metrics()
print(runtime_metrics["cpu_used_pct"])
print(runtime_metrics["mem_used_mib"], runtime_metrics["mem_total_mib"])
print(runtime_metrics["disk_used"], runtime_metrics["disk_total"])
print(runtime_metrics["net_rx_bytes"], runtime_metrics["net_tx_bytes"])
```

Use `client.metrics()` or `client.build.metrics()` only when you need the Prometheus text output for the gateway services themselves. Those service metrics are not per-sandbox runtime metrics.

### Operator APIs

The low-level control service also includes operator-oriented methods such as `get_pool_status`, `start_rolling_update`, `get_rolling_update_status`, and `cancel_rolling_update`.

These routes are intended for platform operators, not normal application workloads. Keep them out of business-facing integrations unless you are explicitly building operational tooling.

### Template Facade

Preferred template path:

- `Template()` for build DSL
- `Template.build(...)` and `Template.build_in_background(...)` for create + build + optional polling
- `Template.list(...)`, `Template.get(...)`, `Template.delete(...)`, `Template.exists(...)`, `Template.get_build_status(...)` for lifecycle and status
- `Template.to_json(...)`, `Template.to_dockerfile(...)` for export helpers

Template builder conveniences include:

- base images and registries: `from_dockerfile`, `from_base_image`, `from_node_image`, `from_python_image`, `from_bun_image`, `from_ubuntu_image`, `from_debian_image`, `from_aws_registry`, `from_gcp_registry`
- file and command helpers: `copy`, `copy_items`, `skip_cache`, `apt_install`, `git_clone`, `make_dir`, `make_symlink`, `npm_install`, `pip_install`, `bun_install`, `remove`, `rename`, `run_cmd`
- execution and config helpers: `set_envs`, `set_workdir`, `set_user`, `set_start_cmd`, `set_ready_cmd`, `files_hash`
- supported local copy options: `force_upload`, `mode`, `resolve_symlinks`, `user`
- supported command and path options: `run_cmd(..., user=...)`, `git_clone(..., user=...)`, `make_dir(..., user=...)`, `make_symlink(..., user=...)`, `remove(..., user=...)`, `rename(..., user=...)`
- intentionally not exposed yet: MCP server helpers and devcontainer helpers

### Build Plane Module

Low-level `BuildService` from `sandbox.build` exposes:

- system: `metrics`
- templates: `create_template`, `list_templates`, `get_template_by_alias`, `resolve_template_ref`, `get_template`, `update_template`, `delete_template`
- builds: `create_build`, `get_build_file`, `rollback_template`, `list_builds`, `get_build`, `get_build_status`, `get_build_logs`
- tags: `assign_template_tags`, `delete_template_tags`, `list_template_tags`

Build logs are served by the platform log API. `get_build_logs` returns structured log entries, pagination metadata, and an optional empty-result diagnostic without exposing the underlying log storage. `get_build` and `get_build_status` may include `timeline` for user-facing build progress; `get_build_status` may also include `steps` for a compact per-step summary.

### Observability Summary

Use `client.get_observability_summary()` to get one user/Project-level view of sandbox usage, template/build usage, and the public diagnostic endpoints:

```python
summary = client.get_observability_summary()
print(summary["status"], summary.get("usage", {}))
```

The summary intentionally returns public product fields only. Use `summary["actions"]` for next steps, and use endpoint hints from `summary["endpoints"]` for sandbox logs, build logs, and full usage-limit checks. Sandbox and build detail/status responses may include a public `timeline` for phase-level progress. Sandbox responses may include `diagnostic` for startup or paused-state guidance; empty sandbox or build log responses may include a public `diagnostic` object explaining whether filters, cursor position, or lack of output caused the empty result.

The public template contract is split into three layers: E2B create fields (`name`, `tags`, `cpuCount`, `memoryMB`), Atlas extension fields under `extensions` (`baseTemplateID`, `visibility`, `envs`, `volumeMounts`, `workdir`), E2B update field `public`, and build-only fields on `create_build` (`fromImage`, `fromTemplate`, `steps`, `tags`, `startCmd`, `readyCmd`, registry credentials, `steps[].filesHash`).
Template tags are version pointers to build artifacts. Build requests without explicit tags use `default`; `assign_template_tags("template:v1", ["stable"])` moves `stable` to the build behind `v1`, and sandboxes can reference `template:stable` or `template:buildID`.
Each mount declares its own storage through `volumeMounts[i].storageType` plus the matching storage fields such as `nfsHostPath`, `storageClass`/`storageSizeGB`, `persistentVolumeClaim`, or `objectBucket`. `workdir` sets the sandbox default working directory and file API root; it does not create a mount by itself.
Runtime behavior defaults from the image source: templates inheriting SeaCloud base/runtime templates keep the managed runtime, while direct external images run as plain business containers. `startCmd` and `readyCmd` only provide startup and readiness commands on top of that default.
Public create calls reject unsupported top-level write fields such as `alias` and `public`; public update calls only accept `public`.

New custom templates default to `type: "custom"`, `version: "v0.1.0"`, `cpuCount: 1`, `memoryMB: 512`, `ttlSeconds: 300`, and resource limit ratios of `1.0`. Server-generated template IDs use `tpl-{type}-{16 lowercase hex}` and server-generated initial build IDs use `build-{16 lowercase hex}`. Client-supplied build IDs passed to `create_build(template_id, build_id, ...)` must be lowercase DNS labels up to 63 characters; the SDK recommends the `build-` prefix.

`create_template`, `list_templates`, and `get_template` responses include `type` and `version` when the platform returns them. Treat `type` as the stable template family and `version` as that family's version marker.

`create_template` rejects `visibility="official"` on public routes, including `extensions.visibility == "official"`.

`create_build` now follows the E2B wire contract directly: COPY contexts are passed through `steps[].filesHash`, and the SDK returns the raw `202 {}` trigger response without adding helper fields.

`fromImage` switches the template to an already-built image and does not start a new image build by itself. `fromTemplate` resolves a ready template image and uses it as the build base for supported E2B steps. `from_dockerfile` is a client-side convenience that parses a supported Dockerfile subset into `fromImage`, `steps`, `startCmd`, and `readyCmd`; it is not the platform admin raw-Dockerfile build route.

Build records can move through `uploaded`, `waiting`, `building`, `ready`, and `error`. `uploaded` means a referenced COPY context is still missing; upload it through the file handshake and call `create_build` again with the same `build_id`.

`get_template_by_alias` is a pure alias lookup endpoint. It should only be used with an actual published alias value.

`resolve_template_ref` is the SeaCloud stable-ref lookup endpoint. It resolves a template by `templateID`, official template `type`, or visible alias.

## Resource Safety

- The quick starts are written for disposable resources and should be adapted before copy-pasting into production jobs.
- Prefer explicit cleanup with `sandbox.delete()` and `Template.delete(...)` when running probes, smoke tests, or CI.
- For long-lived workloads, move cleanup and timeout policy into your own lifecycle manager instead of relying on sample code defaults.

### Runtime Module

Bound sandbox runtime modules and low-level CMD services expose:

- system: `metrics`, `envs`, `configure`, `ports`
- proxy and file transfer: `proxy`, `download`, `files_content`, `upload_bytes`, `upload_json`, `upload_multipart`, `write_batch`, `compose_files`, `read_file`, `write_file`
- filesystem RPC: `list_dir`, `stat`, `make_dir`, `remove`, `move`, `edit`
- watchers: `watch_dir`, `create_watcher`, `get_watcher_events`, `remove_watcher`
- process RPC: `start`, `connect`, `list_processes`, `send_input`, `send_signal`, `close_stdin`, `update`, `stream_input`, `get_result`, `run`

Useful CMD helpers:

- `sandbox.cmd.CmdRequestOptions`: username, signature, signature expiration, range, request_timeout_ms, extra headers
- `ProcessStream` and `FilesystemWatchStream`: Connect-RPC stream readers
- `ConnectFrame`: low-level frame parser

## Module Layout

- `sandbox`: root high-level `Sandbox` / `Template` facade
- `sandbox.control`: control-plane models and low-level APIs
- `sandbox.build`: build-plane models and low-level APIs
- `sandbox.cmd`: runtime models and low-level APIs
- `sandbox.core`: shared transport and error primitives

## Notes

- High-level helpers always read gateway auth and endpoint from `SEACLOUD_BASE_URL` / `SEACLOUD_API_KEY`. Only low-level transport clients should be initialized with explicit `base_url` / `api_key`.
- Runtime access should be derived from bound sandbox objects or low-level sandbox instances.
- Low-level create/detail responses include `envdUrl` and `envdAccessToken` when managed runtime access is enabled.
- Runtime file/process APIs require a template image that supports managed runtime access; if runtime APIs return `404`, verify the selected template supports CMD runtime routes.
- Runtime requests can override the SDK HTTP timeout per call through `CmdRequestOptions(request_timeout_ms=...)`.
- The bound sandbox exposes `traffic_access_token` as an E2B-style alias of the runtime access token returned by the gateway.
- `waitReady=True` can take longer than the default lifecycle wait in production; pass a larger `timeout` on sandbox create/connect flows when you need a longer ready/pause wait budget.
- HTTP errors are classified into typed exceptions such as `NotFoundError`, `RateLimitError`, and `ServerError`. Transport timeouts raise `RequestTimeoutError`. Quota `429` responses expose `error.details` and typed `error.usage_limit` when the gateway returns public limit diagnostics.
- High-level `kill()` helpers send `SIGNAL_SIGKILL` and return `False` when the runtime reports a missing process through either `404` or `ESRCH`.
- PTY handles normalize reconnect output into `pty` even when the runtime emits the bytes through `stdout` / `stderr`.
- Runtime reconnect streams such as `connect()` and `watch_dir()` retry once on transient TLS EOF / remote-close failures before surfacing the transport error.
- Sandbox lifecycle timeout is validated to `0..86400` seconds; refresh duration to `0..3600` seconds.
- Build validation accepts E2B-style `COPY` / `ENV` / `RUN` / `WORKDIR` / `USER` steps, `force`, and structured `fromImageRegistry` credentials (`registry` / `aws` / `gcp`).
- Some gateways do not expose `/admin/*`; integration tests skip those cases on `404`.
- Some filesystem layouts reject watcher APIs entirely; the integration suite skips watcher coverage when the runtime reports that limitation.

## Security

- Do not commit `SEACLOUD_API_KEY`, `envdAccessToken`, or sandbox access tokens.
- Treat runtime tokens as sandbox-scoped secrets. Prefer bound sandbox objects or low-level sandbox instances so response-scoped runtime access is not copied into configuration.
- Do not log raw API keys or runtime tokens. SDK exceptions may include response bodies, so avoid logging full error payloads in shared systems.

## Production Smoke

Use production smoke tests only with explicitly provided credentials and disposable sandboxes:

```bash
SANDBOX_RUN_INTEGRATION=1 \
SANDBOX_TEST_BASE_URL="${SEACLOUD_BASE_URL}" \
SANDBOX_TEST_API_KEY="${SEACLOUD_API_KEY}" \
SANDBOX_TEST_TEMPLATE_ID=tpl-base-dc11799b9f9f4f9e \
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

`tpl-base-dc11799b9f9f4f9e` is a known-good SeaCloudAI runtime template for validating CMD routes such as `list_dir`, `read_file`, `write_file`, and `run`.
You can also run the same disposable smoke flow from GitHub Actions with `.github/workflows/integration-smoke.yml` after setting the `SANDBOX_TEST_API_KEY` repository secret.

## Integration Tests

```bash
SANDBOX_RUN_INTEGRATION=1 \
SANDBOX_TEST_BASE_URL="${SEACLOUD_BASE_URL}" \
SANDBOX_TEST_API_KEY="${SEACLOUD_API_KEY}" \
SANDBOX_TEST_TEMPLATE_ID=... \
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

Use a runtime-enabled template for CMD integration coverage. For SeaCloudAI production smoke tests, `tpl-base-dc11799b9f9f4f9e` is a known-good runtime template.
The same smoke suite is available as a manual GitHub Actions dispatch in `.github/workflows/integration-smoke.yml`.

## Release

- See `CHANGELOG.md` for release notes.
- See `RELEASE_CHECKLIST.md` before tagging or publishing a new version.
- GitHub Actions can publish to PyPI through Trusted Publishing with `.github/workflows/publish.yml`.
