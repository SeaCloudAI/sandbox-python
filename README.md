# Sandbox Python SDK

Python SDK for Sandbox control-plane, build-plane, and nano-executor CMD APIs.

## Install

```bash
pip install seacloud-sandbox
```

If you previously installed `0.1.2`, upgrade to `0.1.3` or later. `0.1.2` shipped without the `sandbox.build` package in the published artifact.

## Entrypoints

Preferred public API:

- preferred sandbox entrypoint: `Sandbox.create(...)`
- additional sandbox lifecycle helpers: `Sandbox.connect(...)`, `Sandbox.list(...)`, `Sandbox.get_info(...)`, `Sandbox.kill(...)`, `Sandbox.set_timeout(...)`
- sandbox runtime modules from the returned object: `sandbox.commands`, `sandbox.files`, `sandbox.git`, `sandbox.pty`
- preferred template entrypoint: `Template.build(...)`, `Template.build_in_background(...)`, `Template.list(...)`, `Template.get(...)`, `Template.delete(...)`
- low-level transport modules: `sandbox.control`, `sandbox.build`, `sandbox.cmd`

High-level `Sandbox` / `Template` helpers read gateway config from `E2B_DOMAIN` / `E2B_API_KEY`. Low-level `sandbox.control`, `sandbox.build`, and `sandbox.cmd` clients can still be initialized explicitly when needed. Runtime access is derived from sandbox create/detail/connect responses; callers should not hardcode runtime endpoints or tokens.

## E2B Alignment

- Supported alignment target: sandbox lifecycle, files, commands, git, PTY, and the high-level template DSL are designed to follow the same public workflow as `e2b-docs/sdk`.
- Code interpreter alignment: `sandbox.run_code(...)` is available for `python`, `javascript`, `typescript`, `bash`, `r`, and `java`. Python results support `display(...)`, last-expression capture, tables, Matplotlib PNG/chart payloads, a persistent default execution context, and stateful `create_code_context/list_code_contexts/restart_code_context/remove_code_context` helpers. Non-Python contexts use the same API surface but currently behave as stateless execution profiles.
- Known unsupported area: snapshot APIs are not exposed because the underlying platform does not support them yet.
- Known partial area: only Python contexts are stateful. Non-Python contexts still execute in isolated one-shot processes.
- Runtime normalization note: the SDK smooths a few runtime-specific quirks so the high-level behavior stays E2B-like, such as missing-process `kill()` results, PTY reconnect output framing, and a one-time retry for transient reconnect-stream open failures.

## Environment

Use environment variables for gateway configuration in all examples and quick starts:

- `E2B_DOMAIN`: preferred gateway entrypoint
- `E2B_API_KEY`: preferred API key
- `SEACLOUD_PROJECT_ID`: optional project routing key for project-scoped gateways

Set them once in your shell:

```bash
export E2B_DOMAIN="https://sandbox-gateway.cloud.seaart.ai"
export E2B_API_KEY="..."
export SEACLOUD_PROJECT_ID="project-..."
```

Default production gateway:

```text
https://sandbox-gateway.cloud.seaart.ai
```

High-level `Sandbox.create(...)` omits `templateID` when you do not pass one, so Atlas can apply its server-side default of the latest official `base` template. For production integrations, prefer passing a concrete template ID such as `tpl-...` or a stable official template type such as `base`, `code-interpreter`, `claude`, or `codex` when your environment publishes those official templates.

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
- Timeout semantics: public sandbox, command, PTY, git, and code-execution `timeout` values are in seconds. Lifecycle TTL fields exposed through Atlas control-plane APIs accept `0` when the service defines a zero-value meaning, such as connect without TTL refresh. `request_timeout` is only the SDK HTTP request timeout in seconds.

## Quick Start

### Control Plane

```python
from sandbox import Sandbox

sandbox = Sandbox.create(
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
import os

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

For Python, repeated `sandbox.run_code(...)` calls reuse the sandbox's default code context. You can create additional Python contexts with `create_code_context(...)` when you need isolated state. For other languages, `create_code_context(...)` returns a reusable execution profile that supplies default `language`, `cwd`, and `timeout` values, but each run still executes in a fresh one-shot process.

Bound sandbox helpers currently include:

- lifecycle: `reload`, `connect`, `resume`, `get_info`, `logs`, `pause`, `kill`, `delete`, `refresh`, `set_timeout`, `is_running`
- runtime conveniences: `get_metrics`, `get_host`, `proxy`
- code interpreter: `run_code`, `create_code_context`, `list_code_contexts`, `restart_code_context`, `remove_code_context`
- commands module: `run`, `exec`, `list`, `connect`, `kill`, `send_stdin`
- filesystem module: `exists`, `get_info`, `list`, `make_dir`, `read`, `write`, `write_files`, `remove`, `rename`, `watch_dir`
- git module: `clone`, `pull`, `checkout`, `status`
- pty module: `create`, `connect`, `kill`, `send_stdin`, `resize`

## Recommended Usage

For most integrations, prefer the env-first high-level flow:

- set `E2B_DOMAIN`, `E2B_API_KEY`, and optionally `SEACLOUD_PROJECT_ID`
- create sandboxes with `Sandbox.create(...)`
- continue through `sandbox.commands`, `sandbox.files`, `sandbox.git`, and `sandbox.pty`
- build templates with `Template.build(...)` and `Template.build_in_background(...)`
- only drop to `sandbox.control`, `sandbox.build`, or `sandbox.cmd` when you need transport-level request control

Low-level methods remain available when you need tighter request control:

- continue from the returned sandbox object with `reload()`, `connect()`, `resume()`, `get_info()`, `get_metrics()`, `get_host()`, `logs()`, `pause()`, `refresh()`, `set_timeout()`, `kill()`, `delete()`, and `is_running()`
- use `Template.build(...)`, `Template.build_in_background(...)`, `Template.exists(...)`, `Template.get_build_status(...)`, `Template.list(...)`, `Template.get(...)`, and `Template.delete(...)` for the preferred template workflow
- use `ControlService`, `BuildService`, and runtime service helpers from the submodules only for raw control/build/cmd workflows
- use `template_build()` when you want a small fluent helper that expands into the public build request payload

Low-level submodules remain available when you need direct request/response models or tighter transport control.

## API Surface

### Control Plane APIs

- high-level lifecycle: `create`, `connect`, `list`
- follow-up control actions from the returned object: `reload()`, `connect()`, `resume()`, `get_info()`, `get_metrics()`, `get_host()`, `logs()`, `pause()`, `refresh()`, `set_timeout()`, `kill()`, `delete()`, `is_running()`
- low-level control module: `ControlService` from `sandbox.control`
- low-level service methods: `metrics`, `shutdown`, `create_sandbox`, `list_sandboxes`, `get_sandbox`, `delete_sandbox`, `get_sandbox_logs`, `pause_sandbox`, `connect_sandbox`, `set_sandbox_timeout`, `refresh_sandbox`, `send_heartbeat`

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

### Build Plane Namespace

Low-level `BuildService` from `sandbox.build` exposes:

- system: `metrics`
- direct build: `direct_build`
- templates: `create_template`, `list_templates`, `get_template_by_alias`, `resolve_template_ref`, `get_template`, `update_template`, `delete_template`
- builds: `create_build`, `get_build_file`, `rollback_template`, `list_builds`, `get_build`, `get_build_status`, `get_build_logs`

The public template contract is split into three layers: top-level create fields (`name`, `tags`, `cpuCount`, `memoryMB`), template extensions under `extensions` (`baseTemplateID`, `visibility`, `envs`, `storageType`, `storageSizeGB`, `volumeMounts`), and build-only fields on `create_build` (`fromImage`, `fromTemplate`, `steps`, `startCmd`, `readyCmd`, registry credentials, `filesHash`).
When `extensions.storageType="nfs"`, the public API still does not expose `nfsHostPath`; each `volumeMounts[i].name` is treated as the per-sandbox NFS subdirectory name under the inherited base template's NFS root, and `volumeMounts[i].path` is the container mount path. A mount named `workspace` becomes the primary workspace path.
Runtime behavior defaults from the image source: templates inheriting SeaCloud base/runtime templates keep the managed runtime, while direct external images run as plain business containers. `startCmd` and `readyCmd` only provide startup and readiness commands on top of that default.
Public create/update calls reject legacy top-level write fields such as `alias`, `teamID`, and `public`.

`create_template` and `update_template` reject `visibility="official"` on public routes, including `extensions.visibility == "official"`.

`create_build` now follows the wire contract directly: callers pass top-level `filesHash` when needed, and the SDK returns the raw `202 {}` trigger response without adding helper fields.

`get_template_by_alias` is a pure alias lookup endpoint. It should only be used with an actual published alias value.

`resolve_template_ref` is the SeaCloud stable-ref lookup endpoint. It resolves a template by `templateID`, official template `type`, or visible alias.

## Resource Safety

- The quick starts are written for disposable resources and should be adapted before copy-pasting into production jobs.
- Prefer explicit cleanup with `sandbox.delete()` and `Template.delete(...)` when running probes, smoke tests, or CI.
- For long-lived workloads, move cleanup and timeout policy into your own lifecycle manager instead of relying on sample code defaults.

### Runtime Namespace

Bound sandbox runtime modules and low-level CMD services expose:

- system: `metrics`, `envs`, `configure`, `ports`
- proxy and file transfer: `proxy`, `download`, `files_content`, `upload_bytes`, `upload_json`, `upload_multipart`, `write_batch`, `compose_files`, `read_file`, `write_file`
- filesystem RPC: `list_dir`, `stat`, `make_dir`, `remove`, `move`, `edit`
- watchers: `watch_dir`, `create_watcher`, `get_watcher_events`, `remove_watcher`
- process RPC: `start`, `connect`, `list_processes`, `send_input`, `send_signal`, `close_stdin`, `update`, `stream_input`, `get_result`, `run`

Useful CMD helpers:

- `sandbox.cmd.CmdRequestOptions`: username, signature, signature expiration, range, timeout, extra headers
- `ProcessStream` and `FilesystemWatchStream`: Connect-RPC stream readers
- `ConnectFrame`: low-level frame parser

## Module Layout

- `sandbox`: root high-level `Sandbox` / `Template` facade
- `sandbox.control`: control-plane models and low-level APIs
- `sandbox.build`: build-plane models and low-level APIs
- `sandbox.cmd`: runtime models and low-level APIs
- `sandbox.core`: shared transport and error primitives

## Notes

- High-level helpers always read gateway auth and endpoint from `E2B_DOMAIN` / `E2B_API_KEY`. Only low-level transport clients should be initialized with explicit `base_url` / `api_key`.
- Runtime access should be derived from bound sandbox objects or low-level sandbox instances.
- Low-level create/detail responses include `envdUrl` and `envdAccessToken` when nano-executor access is enabled.
- Runtime file/process APIs require a template image that starts nano-executor and returns runtime access fields; if runtime APIs return `404`, verify the selected template supports CMD runtime routes.
- Runtime requests can override timeout per call through `CmdRequestOptions(timeout=...)`.
- `waitReady=True` can take longer than the default timeout in production; pass a larger timeout on the high-level create/build call for long-wait workflows.
- HTTP errors are classified into typed exceptions such as `NotFoundError`, `RateLimitError`, and `ServerError`. Transport timeouts raise `RequestTimeoutError`.
- High-level `kill()` helpers send `SIGNAL_SIGKILL` and return `False` when the runtime reports a missing process through either `404` or `ESRCH`.
- PTY handles normalize reconnect output into `pty` even when the runtime emits the bytes through `stdout` / `stderr`.
- Runtime reconnect streams such as `connect()` and `watch_dir()` retry once on transient TLS EOF / remote-close failures before surfacing the transport error.
- Sandbox timeout is validated to `0..86400`; refresh duration to `0..3600`.
- Build validation accepts E2B-style `COPY` / `ENV` / `RUN` / `WORKDIR` / `USER` steps, `force`, and structured `fromImageRegistry` credentials (`registry` / `aws` / `gcp`).
- Some gateways do not expose `/admin/*` or `/build`; integration tests skip those cases on `404`.
- Some filesystem layouts reject watcher APIs entirely; the integration suite skips watcher coverage when the runtime reports that limitation.

## Security

- Do not commit `E2B_API_KEY`, `envdAccessToken`, or sandbox access tokens.
- Treat runtime tokens as sandbox-scoped secrets. Prefer bound sandbox objects or low-level sandbox instances so response-scoped runtime access is not copied into configuration.
- Do not log raw API keys or runtime tokens. SDK exceptions may include response bodies, so avoid logging full error payloads in shared systems.
- Set `SEACLOUD_PROJECT_ID` when your gateway requires explicit project routing. The SDK sends it as `X-Project-ID`.

## Production Smoke

Use production smoke tests only with explicitly provided credentials and disposable sandboxes:

```bash
SANDBOX_RUN_INTEGRATION=1 \
SANDBOX_TEST_BASE_URL="${E2B_DOMAIN}" \
SANDBOX_TEST_API_KEY="${E2B_API_KEY}" \
SANDBOX_TEST_TEMPLATE_ID=tpl-base-dc11799b9f9f4f9e \
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

`tpl-base-dc11799b9f9f4f9e` is a known-good SeaCloudAI runtime template for validating CMD routes such as `list_dir`, `read_file`, `write_file`, and `run`.
You can also run the same disposable smoke flow from GitHub Actions with `.github/workflows/integration-smoke.yml` after setting the `SANDBOX_TEST_API_KEY` repository secret.

## Integration Tests

```bash
SANDBOX_RUN_INTEGRATION=1 \
SANDBOX_TEST_BASE_URL="${E2B_DOMAIN}" \
SANDBOX_TEST_API_KEY="${E2B_API_KEY}" \
SANDBOX_TEST_TEMPLATE_ID=... \
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

Use a runtime-enabled template for CMD integration coverage. For SeaCloudAI production smoke tests, `tpl-base-dc11799b9f9f4f9e` is a known-good runtime template.
The same smoke suite is available as a manual GitHub Actions dispatch in `.github/workflows/integration-smoke.yml`.

## Release

- See `CHANGELOG.md` for release notes.
- See `RELEASE_CHECKLIST.md` before tagging or publishing a new version.
- GitHub Actions can publish to PyPI through Trusted Publishing with `.github/workflows/publish.yml`.
