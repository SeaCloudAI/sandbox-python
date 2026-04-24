# Sandbox Python SDK

Python SDK for Sandbox control-plane, build-plane, and nano-executor CMD APIs.

## Install

```bash
pip install seacloudai-sandbox
```

## Client Initialization

- unified gateway client: `Client(base_url=..., api_key=...)`
- build plane via root client: `client.build`
- runtime helper: `sandbox.runtime` or `client.runtime_from_sandbox(sandbox)`

`control` and `build` talk to the gateway. Runtime access is derived from sandbox create/detail/connect responses; callers should not hardcode runtime endpoints or tokens.

## Recommended Workflow

Most applications only need the root client:

1. Initialize `Client(base_url=..., api_key=...)`.
2. Create, list, get, or connect sandboxes from the root client.
3. Keep working from the bound sandbox object:
   `reload()`, `logs()`, `pause()`, `refresh()`, `set_timeout()`, `connect()`, `delete()`.
4. When the sandbox exposes `envdUrl`, switch into runtime operations through `sandbox.runtime`.
5. Use `client.build` only for template/build workflows.

## Quick Start

### Control Plane

```python
import os

from sandbox import Client

client = Client(
    base_url="https://sandbox-gateway.cloud.seaart.ai",
    api_key=os.environ["SEACLOUD_API_KEY"],
    timeout=180,
)

sandbox = client.create_sandbox({
    "templateID": "base",
    "workspaceId": "python-sdk-demo",
    "timeout": 1800,
    "waitReady": True,
})

print(sandbox["sandboxID"], sandbox.get("envdUrl"))
if sandbox.get("envdUrl"):
    print(sandbox.runtime.base_url)
```

### Bound Sandbox Workflow

```python
listed = client.list_sandboxes()

for sandbox in listed:
    detail = sandbox.reload()
    print(detail["sandboxID"], detail["status"])
```

### Build Plane Through Root Client

```python
import os

from sandbox import Client

client = Client(
    base_url="https://sandbox-gateway.cloud.seaart.ai",
    api_key=os.environ["SEACLOUD_API_KEY"],
)

template = client.build.create_template({
    "name": "demo",
    "visibility": "personal",
    "image": "docker.io/library/alpine:3.20",
})

print(template["templateID"], template["buildID"])
```

### Runtime Helper

```python
import os

from sandbox import Client
from sandbox.cmd import FileRequest, UploadBytesRequest

client = Client(
    base_url="https://sandbox-gateway.cloud.seaart.ai",
    api_key=os.environ["SEACLOUD_API_KEY"],
)

created = client.create_sandbox({
    "templateID": os.environ["SANDBOX_EXAMPLE_TEMPLATE_ID"],
    "waitReady": True,
})

runtime = created.runtime

try:
    runtime.write_file(UploadBytesRequest(
        path="/root/workspace/hello.txt",
        data=b"hello from python",
    ))

    with runtime.read_file(FileRequest(path="/root/workspace/hello.txt")) as response:
        print(response.read().decode("utf-8"))
finally:
    created.delete()
```

## Root Client First

For most integrations, stay on the root client as long as possible:

- initialize once with `Client(base_url=..., api_key=...)`
- use `create_sandbox`, `list_sandboxes`, `get_sandbox`, `connect_sandbox`
- continue from the returned sandbox object with `reload()`, `logs()`, `pause()`, `refresh()`, `set_timeout()`, `connect()`, `delete()`
- only switch to runtime with `runtime` when you need file/process/stream operations
- use `client.build` only for template/build workflows

Low-level submodules remain available when you want direct stateless calls or need request/response models explicitly.

## API Surface

### Control Plane APIs

`sandbox.Client` exposes control-plane methods directly and build-plane methods under `client.build`:

- system: `metrics`, `shutdown`
- sandboxes: `create_sandbox`, `list_sandboxes`, `get_sandbox`, `delete_sandbox`
- sandbox operations: `get_sandbox_logs`, `pause_sandbox`, `connect_sandbox`, `set_sandbox_timeout`, `refresh_sandbox`, `send_heartbeat`
- admin: `get_pool_status`, `start_rolling_update`, `get_rolling_update_status`, `cancel_rolling_update`

Recommended root-client path:

- sandbox lifecycle: `create_sandbox`, `list_sandboxes`, `get_sandbox`, `connect_sandbox`
- follow-up control actions from the returned object: `reload()`, `logs()`, `pause()`, `refresh()`, `set_timeout()`, `connect()`, `delete()`
- runtime actions from objects that include `envdUrl`: `runtime`

Low-level direct methods like `delete_sandbox` and `get_sandbox_logs` remain available on the root client when you want stateless calls.

### Build Plane Namespace

`client.build` exposes:

- system: `metrics`
- direct build: `direct_build`
- templates: `create_template`, `list_templates`, `get_template_by_alias`, `get_template`, `update_template`, `delete_template`
- builds: `create_build`, `get_build_file`, `rollback_template`, `list_builds`, `get_build`, `get_build_status`, `get_build_logs`

### Runtime Namespace

The object returned by `sandbox.runtime` or `client.runtime_from_sandbox(...)` exposes:

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

- `sandbox`: root `Client` and recommended entrypoint
- `sandbox.control`: control-plane models and low-level APIs
- `sandbox.build`: build-plane models and low-level APIs
- `sandbox.cmd`: runtime models and low-level APIs
- `sandbox.core`: shared transport and error primitives

## Notes

- The gateway entrypoint only needs `base_url + api_key`.
- Runtime access should be derived from sandbox response objects with `sandbox.runtime` or `runtime_from_sandbox(...)`.
- `create_sandbox` and `get_sandbox` return `envdUrl` and `envdAccessToken` when nano-executor access is enabled.
- Runtime file/process APIs require a template image that starts nano-executor and returns runtime access fields; if runtime APIs return `404`, verify the selected template supports CMD runtime routes.
- Runtime requests can override timeout per call through `CmdRequestOptions(timeout=...)`.
- `waitReady=True` can take longer than the default timeout in production; pass `timeout=...` to `Client(...)` for long-wait workflows.
- HTTP errors are classified into typed exceptions such as `NotFoundError`, `RateLimitError`, and `ServerError`. Transport timeouts raise `RequestTimeoutError`.
- Sandbox timeout is validated to `0..86400`; refresh duration to `0..3600`.
- Build validation currently rejects unsupported `fromImageRegistry`, `force`, and per-step `args`/`force`.
- Some gateways do not expose `/admin/*` or `/build`; integration tests skip those cases on `404`.

## Security

- Do not commit `SEACLOUD_API_KEY`, `envdAccessToken`, or sandbox access tokens.
- Treat runtime tokens as sandbox-scoped secrets. Prefer `sandbox.runtime` or `client.runtime_from_sandbox(...)` so response-scoped runtime access is not copied into configuration.
- Do not log raw API keys or runtime tokens. SDK exceptions may include response bodies, so avoid logging full error payloads in multi-tenant systems.
- The SDK does not construct tenant routing headers. Gateway routing context is derived from the API key.

## Production Smoke

Use production smoke tests only with explicitly provided credentials and disposable sandboxes:

```bash
SANDBOX_RUN_INTEGRATION=1 \
SANDBOX_TEST_BASE_URL=https://sandbox-gateway.cloud.seaart.ai \
SANDBOX_TEST_API_KEY=... \
SANDBOX_TEST_TEMPLATE_ID=tpl-base-dc11799b9f9f4f9e \
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

`tpl-base-dc11799b9f9f4f9e` is a known-good SeaCloudAI runtime template for validating CMD routes such as `list_dir`, `read_file`, `write_file`, and `run`.

## Integration Tests

```bash
SANDBOX_RUN_INTEGRATION=1 \
SANDBOX_TEST_BASE_URL=https://sandbox-gateway.cloud.seaart.ai \
SANDBOX_TEST_API_KEY=... \
SANDBOX_TEST_TEMPLATE_ID=... \
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

Use a runtime-enabled template for CMD integration coverage. For SeaCloudAI production smoke tests, `tpl-base-dc11799b9f9f4f9e` is a known-good runtime template.

## Release

- See `CHANGELOG.md` for release notes.
- See `RELEASE_CHECKLIST.md` before tagging or publishing a new version.
