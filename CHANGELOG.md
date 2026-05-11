# Changelog

All notable changes to this project will be documented in this file.

This project follows Semantic Versioning for public SDK APIs.

## [0.2.0] - 2026-05-11

### Changed

- Removed the old client-first entrypoint and aligned the public SDK flow around environment-based configuration plus E2B-style static helpers.
- Added high-level code interpreter helpers and examples on top of the current runtime APIs.
- Narrowed public template create/update writes to the current builder contract and removed legacy fields such as `workspaceId`.
- Updated examples, tests, and release docs to match the new no-backward-compat API surface.

## [0.1.4] - 2026-05-08

### Fixed

- Aligned high-level command and PTY kill helpers with the runtime signal enum by sending `SIGNAL_SIGKILL`.
- Normalized high-level `kill()` helpers to return `false` for both `404` and runtime `ESRCH` missing-process responses.
- Fixed high-level stdin and streamed output handling so command/PTTY helpers round-trip base64 payloads correctly.
- Added PTY wait fallback so reconnect output still lands in `pty` when the runtime emits it through `stdout` / `stderr`.

### Changed

- Added a one-time retry for transient TLS EOF / remote-close errors while opening runtime reconnect streams such as `connect()` and `watch_dir()`.
- Added a manual GitHub Actions integration-smoke workflow for disposable real-environment validation.
- Documented watcher filesystem limitations and high-level runtime normalization behavior.

## [0.1.3] - 2026-04-25

### Fixed

- Included the `sandbox.build` package in the published source tree and wheel.
- Fixed Python CI by ensuring the build package is present in git and tightening workflow test setup.

### Changed

- Expanded the high-level template builder toward the E2B design for supported features: build/status helpers, tags, Dockerfile import/export, image helpers, `copy_items`, `skip_cache`, `run_cmd(..., user=...)`, and local COPY tar options (`mode`, `resolve_symlinks`).

## [0.1.2] - 2026-04-24

### Changed

- Refined README and examples around the unified gateway flow and environment-based configuration.
- Added a full end-to-end workflow example covering template creation, sandbox startup, runtime execution, and cleanup.
- Reduced build request surface to the user-facing fields needed for production SDK usage.

## [0.1.1] - 2026-04-24

### Changed

- Renamed the published PyPI package to `seacloud-sandbox`.
- Added GitHub Actions PyPI publishing through Trusted Publishing.

## [0.1.0] - 2026-04-23

### Added

- Initial Python SDK for SeaCloudAI sandbox control-plane, build-plane, and runtime CMD APIs.
- Unified root client initialization with `Client(base_url=..., api_key=...)`.
- Build namespace through `client.build`.
- Runtime helpers through `client.runtime(...)`, `client.runtime_from_sandbox(...)`, and bound sandbox objects.
- Typed API errors with retry classification.
- Configurable request timeout for long `waitReady` workflows.
- Examples, unit tests, and integration-test scaffolding.
