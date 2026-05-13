# Release Checklist

Use this checklist before tagging and publishing a release.

## Preflight

- Confirm `README.md`, `CHANGELOG.md`, and examples match the released API.
- Confirm no API keys, access tokens, or production URLs are committed.
- Run `PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'`.
- Run `python -m build` from a clean checkout.
- Run production smoke only with an explicitly provided API key and a runtime-enabled template.

## Production Smoke

```bash
SANDBOX_RUN_INTEGRATION=1 \
SANDBOX_TEST_BASE_URL="${SEACLOUD_BASE_URL}" \
SANDBOX_TEST_API_KEY="${SEACLOUD_API_KEY}" \
SANDBOX_TEST_TEMPLATE_ID=tpl-base-dc11799b9f9f4f9e \
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

## Publish

- Update `pyproject.toml` version and `CHANGELOG.md`.
- Build with `python -m build`.
- For GitHub Actions publishing, configure a PyPI Trusted Publisher for `SeaCloudAI/sandbox-python` and this workflow file: `.github/workflows/publish.yml`.
  Repository: `SeaCloudAI/sandbox-python`
  Workflow: `publish.yml`
  Environment: `pypi`
- Manual fallback: upload with `python -m twine upload dist/*`.
- Create and push a signed tag, for example `git tag -s v0.1.0 -m "v0.1.0"`.
