# Changelog

All notable changes to this project will be documented in this file.

This project follows Semantic Versioning for public SDK APIs.

## [0.1.3] - 2026-04-25

### Fixed

- Included the `sandbox.build` package in the published source tree and wheel.
- Fixed Python CI by ensuring the build package is present in git and tightening workflow test setup.

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
