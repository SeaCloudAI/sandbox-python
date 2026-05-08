# Python Examples

Run examples from the package root.

Shared env:

- `SEACLOUD_BASE_URL`
- `SEACLOUD_API_KEY`

Before running any example, export these variables once in your shell. Use the gateway entrypoint documented in the root `README.md`.

Example-specific inputs intentionally use the `SANDBOX_EXAMPLE_*` prefix so they do not collide with the production-oriented variables shown in the package root `README.md`.
Examples focus on the stable lifecycle, template, command, and PTY flows. Watcher APIs are covered in tests instead, because some sandbox filesystem layouts reject them entirely.

Recommended reading order:

1. `full_workflow.py`: create a template -> trigger an E2B-style build -> wait for build -> start sandbox -> connect runtime -> run -> logs/metrics -> cleanup
2. `template_features.py`: `from_dockerfile` -> local `copy(..., mode=..., resolve_symlinks=...)` -> `client.build_template_in_background()` -> `client.get_template_build_status()` -> existence/detail
3. `control_sandbox.py`: root client -> create sandbox -> bound sandbox helpers -> cleanup
4. `cmd_smoke.py`: create a sandbox through the gateway, then write/read/list/run through runtime
5. `build_template.py`: minimal `Template()` plus `client.build_template()`

## Full Workflow

This is the primary example when evaluating the SDK end to end:

- create a template
- trigger a build from a runtime-enabled base image plus E2B-style steps
- wait for the build to finish
- inspect build status, build logs, and template detail
- start a sandbox from that template
- reload, fetch sandbox logs, connect, inspect runtime metrics, and run a command
- delete the sandbox and template unless `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

Required env:

- `SANDBOX_EXAMPLE_RUNTIME_BASE_IMAGE`

Optional env:

- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

The base image must already be runtime-enabled for CMD APIs. The example build starts from that image and adds app-specific content under `/workspace` through a `RUN` step.

```bash
python examples/full_workflow.py
```

## Control Plane

This example shows the preferred workflow:

- initialize the root `Client`
- create a sandbox from the root client
- keep operating through the returned bound sandbox object
- reload once to show the bound-object workflow
- cleanup through the same object

Required env:

- `SANDBOX_EXAMPLE_TEMPLATE_ID`

Optional env:

- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

```bash
python examples/control_sandbox.py
```

## Build Plane

Recommended path: the example uses `Template()` plus `client.build_template()`.
The flow shows the current client-first template workflow directly: template DSL -> build polling -> template detail -> cleanup.

Required env: none

Optional env:

- `SANDBOX_EXAMPLE_BUILD_IMAGE`
- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

```bash
python examples/build_template.py
```

## Template Features

This example covers the supported template helpers that are not obvious from the minimal build flow:

- parse a Dockerfile from disk with `from_dockerfile`
- inspect the generated request with `Template.to_json(...)` and `Template.to_dockerfile(...)`
- add extra steps with `skip_cache()` and `run_cmd(..., user=...)`
- upload a local symlink target with `copy(..., mode=..., resolve_symlinks=...)`
- initialize the root `Client`
- trigger `client.build_template_in_background(...)` and poll with `client.get_template_build_status(...)`
- verify template existence and inspect template detail

Required env: none

Optional env:

- `SANDBOX_EXAMPLE_BUILD_IMAGE`
- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

```bash
python examples/template_features.py
```

## CMD Plane

Recommended path: the example uses the root `Client`, creates a sandbox through the gateway, then derives runtime access from the returned sandbox object.
The selected template must include nano-executor runtime support; otherwise file/process/RPC calls can return `404`.
The flow stays minimal: write file -> read file -> list directory -> run command.
The example writes under `/root/workspace`, which is the writable sandbox workspace in the current SeaCloud runtime.

Required env:

- `SANDBOX_EXAMPLE_TEMPLATE_ID`

Optional env:

- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

```bash
python examples/cmd_smoke.py
```

For SeaCloudAI production smoke tests, `tpl-base-dc11799b9f9f4f9e` is a known-good template to use when creating the runtime-enabled sandbox.
