# Python Examples

Run examples from the package root.

Shared env:

- `SEACLOUD_BASE_URL`
- `SEACLOUD_API_KEY`

Before running any example, export these variables once in your shell. Use the gateway entrypoint documented in the package `README.md`.

Example-specific inputs intentionally use the `SANDBOX_EXAMPLE_*` prefix so they do not collide with the production-oriented variables shown in the package `README.md`.
Examples focus on the stable lifecycle, template, command, and PTY flows. Watcher APIs are covered in tests instead, because some sandbox filesystem layouts reject them entirely.

Recommended reading order:

1. `zero_to_one.py`: env setup -> official templates -> lifecycle -> command/files -> frontend URL -> local-code template build
2. `code_interpreter.py`: default Python context -> explicit Python context -> non-Python stateless `context`
3. `full_workflow.py`: pure high-level facade flow -> create a template -> trigger an E2B-style build -> wait for build -> start sandbox -> connect runtime -> run -> logs/metrics -> cleanup
4. `template_features.py`: `from_dockerfile` -> local `copy(..., mode=..., resolve_symlinks=..., user=...)` -> `Template.build_in_background()` -> `Template.get_build_status()` -> existence/detail
5. `control_sandbox.py`: `Sandbox.create()` -> bound sandbox helpers -> cleanup
6. `cmd_smoke.py`: create a sandbox through the gateway, then write/read/list/run through runtime
7. `build_template.py`: minimal `Template.build(...)`

## Zero To One

This is the tutorial-style example for first-time users:

- create a `base` sandbox and run basic file/command operations
- pause and resume the sandbox to show lifecycle management
- create a `code-interpreter` sandbox and run Python code
- deploy a tiny static frontend inside a sandbox and print the public proxy URL from `get_host(3000)`
- build a new template by uploading local frontend files with `Template.copy(...)`

Optional env:

- `SANDBOX_EXAMPLE_BASE_TEMPLATE=base`
- `SANDBOX_EXAMPLE_CODE_TEMPLATE=code-interpreter`
- `SANDBOX_EXAMPLE_FRONTEND_TEMPLATE=code-interpreter`
- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

```bash
python examples/zero_to_one.py
```

## Code Interpreter

This example focuses on the E2B-style code interpreter facade:

- repeated `sandbox.run_code(...)` calls sharing the default Python context
- explicit stateful Python contexts with `create_code_context(...)`
- non-Python contexts acting as reusable execution profiles for `language`, `cwd`, and `timeout_ms`
- requires a template that actually bundles the code-interpreter environment; `base` is not enough

Required env:

- `SANDBOX_EXAMPLE_TEMPLATE_ID`

Optional env:

- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

```bash
python examples/code_interpreter.py
```

For SeaCloudAI environments, prefer an official `code-interpreter` template or a concrete `tpl-code-interpreter-...` template ID for this example.

## Full Workflow

This is the primary example when evaluating the SDK end to end:

- create a template
- trigger a build from a runtime-enabled image plus E2B-style steps
- wait for the build to finish
- inspect build status, build logs, and template detail
- start a sandbox from that template
- reload, fetch sandbox logs, connect, inspect runtime metrics, and run a command
- delete the sandbox and template unless `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

Required env:

- `SANDBOX_EXAMPLE_RUNTIME_BASE_IMAGE`

Optional env:

- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

The source image must already be runtime-enabled for CMD APIs. The example build starts from that image and adds app-specific content under `/workspace` through a `RUN` step.

```bash
python examples/full_workflow.py
```

## Control Plane

This example shows the preferred workflow:

- call `Sandbox.create(...)` directly
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

Recommended path: the example uses `Template.build(...)`.
The flow shows the env-first high-level template workflow directly: template DSL -> build polling -> template detail -> cleanup.

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
- upload a local symlink target with `copy(..., mode=..., resolve_symlinks=..., user=...)`
- trigger `Template.build_in_background(...)` and poll with `Template.get_build_status(...)`
- verify template existence with `Template.exists(...)` and inspect template detail with `Template.get(...)`

Required env: none

Optional env:

- `SANDBOX_EXAMPLE_BUILD_IMAGE`
- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

```bash
python examples/template_features.py
```

## CMD Plane

Recommended path: the example uses `Sandbox.create(...)` and then stays on the returned bound sandbox object.
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

For SeaCloudAI production smoke tests, `tpl-base-dc11799b9f9f4f9e` is a known-good template for CMD/runtime examples such as this one. Use a `code-interpreter` template instead when you want to run `sandbox.run_code(...)`.
