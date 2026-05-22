# Development

This guide is for contributors working on the package locally.

## Contributor

The current named contributor is Johan Louwers, [louwersj@gmail.com](mailto:louwersj@gmail.com).

Repository: [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,oracle,crypto,docs]"
```

## Checks

```bash
ruff format --check .
ruff check .
mypy src
pytest
pytest tests/unit/test_property_generators.py
bandit -q -r src
python -m build
python scripts/update-dependency-manifests.py --check
scripts/sbom.sh
twine check dist/*.whl dist/*.tar.gz
```

`tests/unit/test_property_generators.py` contains deterministic bounded
generator tests for validators and normalizers. It intentionally uses the
existing pytest stack rather than a new dependency, so CI remains repeatable and
dependency manifests stay unchanged. Add new generator cases when a validator
accepts external input, especially around configuration, NATS subject patterns,
payload handling, metadata handling, or filesystem paths.

## Synthetic Test Harness

For day-to-day development, use the synthetic harness when you need mission-like
test messages without live NATS, Oracle, credentials, or operational payloads.
The harness creates deterministic `NatsEnvelope` objects that exercise valid
payloads, malformed JSON-like text, duplicates, stale timestamps, fake
encrypted-payload envelopes, classification values, priority values, and
labels.

Generate a core-only sanitized report:

```bash
python scripts/run-synthetic-harness.py --message-count 18
```

Run the same profile through the file sink:

```bash
python scripts/run-synthetic-harness.py \
  --sink file \
  --message-count 18 \
  --output-dir .local/synthetic-file-smoke \
  --preserve-files
```

Keep generated files and reports under `.local/` or another ignored directory.
Do not paste retained payload files, local paths, or live configuration into
GitHub issues. For future sinks, add a small adapter that accepts the generated
envelopes and returns a sanitized `SyntheticScenarioReport` rather than
building a sink-specific generator from scratch.

## Documentation

```bash
python scripts/check-markdown-links.py
scripts/check-docs.sh
```

`scripts/check-docs.sh` builds both the Read the Docs canonical site and the
GitHub Pages canonical site in isolated temporary directories. Use that helper
for local verification and automation instead of starting two `mkdocs build`
commands against the shared `site/` directory. MkDocs cleans its output
directory before building, so two overlapping builds that share `site/` can
remove files that the other build is still reading.

Mermaid diagrams are written directly in Markdown code fences so GitHub and MkDocs render the same conceptual flows where supported.

The repository also includes a GitHub Actions `Docs` workflow and a
`.readthedocs.yaml` configuration file. Pull requests that change
documentation, README links, MkDocs settings, or documentation dependencies
should pass the same checks that Read the Docs will use after merge. See
[Read the Docs](read-the-docs.md).

The repository also includes a GitHub Pages workflow at
`.github/workflows/pages.yml`. After a maintainer enables Pages with
`Settings` -> `Pages` -> `Source: GitHub Actions`, pushes to `main` can publish
a current-branch documentation mirror at
[projectcuillin.github.io/nats-sinks](https://projectcuillin.github.io/nats-sinks/).
See [GitHub Pages](github-pages.md).

## Change Rules

- Preserve commit-then-acknowledge.
- Treat GitHub Issues as the live backlog. User-visible feature work should
  have a detailed issue before implementation. Assign the issue, post a
  sanitized plan, apply the release label, update documentation and
  `CHANGELOG.md`, execute the test plan, post sanitized evidence, tick the
  Acceptance Criteria, and let release automation close the issue only after
  the associated GitHub Release exists. See
  [Backlog Management](backlog-management.md).
- Keep unit tests deterministic.
- Keep integration tests marked.
- Update docs for public behavior changes.
- Update `CHANGELOG.md` for user-visible changes.
- Avoid new dependencies unless there is a clear reason.
- Do not add a sink that can silently lose messages.
- When dependency metadata changes, edit `pyproject.toml`, regenerate
  `requirements*.txt` with `python scripts/update-dependency-manifests.py`,
  and keep the generated manifests committed. See
  [Dependency Management](dependency-management.md).

## Local Services

```bash
docker compose -f examples/docker-compose.nats.json up
docker compose -f examples/docker-compose.oracle.json up
```

Use only local test credentials and disposable data.
