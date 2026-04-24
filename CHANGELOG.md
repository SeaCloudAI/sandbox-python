# Changelog

All notable changes to this project will be documented in this file.

This project follows Semantic Versioning for public SDK APIs.

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
