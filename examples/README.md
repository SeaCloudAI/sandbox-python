# Python Examples

Run examples from the package root.

Shared env:

- `SEACLOUD_BASE_URL`
- `SEACLOUD_API_KEY`

Before running any example, export these variables once in your shell. Use the gateway entrypoint documented in the root `README.md`.

Recommended reading order:

1. `full_workflow.py`: create a template -> trigger an E2B-style build -> wait for build -> start sandbox -> connect runtime -> run -> logs/metrics -> cleanup
2. `control_sandbox.py`: root client -> create sandbox -> bound sandbox helpers -> cleanup
3. `cmd_smoke.py`: create a sandbox through the gateway, then write/read/list/run through runtime
4. `build_template.py`: template/build workflows through `client.build` plus `template_build()`

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

Recommended path: the example uses the root `Client`, `client.build`, and `template_build()`.
The flow now shows the current public builder contract more explicitly: create template with alias -> alias lookup / stable resolve -> client-generated `buildID` -> build request through the fluent helper -> status polling -> build history + template detail -> cleanup.
If you need SeaCloud-specific template settings such as `visibility`, `baseTemplateID`, or storage options, pass them through `extensions.seacloud` on `create_template` / `update_template`.

Required env:

Optional env:

- `SANDBOX_EXAMPLE_BUILD_IMAGE`
- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

```bash
python examples/build_template.py
```

## CMD Plane

Recommended path: the example uses the root `Client`, creates a sandbox through the gateway, then derives runtime access from the returned sandbox object.
The selected template must include nano-executor runtime support; otherwise file/process/RPC calls can return `404`.
The flow stays minimal: write file -> read file -> list directory -> run command.

Required env:

- `SANDBOX_EXAMPLE_TEMPLATE_ID`

Optional env:

- `SANDBOX_EXAMPLE_KEEP_RESOURCES=1`

```bash
python examples/cmd_smoke.py
```

For SeaCloudAI production smoke tests, `tpl-base-dc11799b9f9f4f9e` is a known-good template to use when creating the runtime-enabled sandbox.
