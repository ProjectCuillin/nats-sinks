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

## Hierarchical Branch Work

Do not work directly on `main`. `main` is only for released code. Start normal
work from the active release development branch, then create an issue branch
for the specific feature or bug you are working on:

```bash
git switch main
git pull --ff-only
git switch -c release-v0.4.1
git switch -c issue-123-short-description
```

If a defect is found while working on that issue, create a bug report and then
create a bug branch from the active issue branch:

```bash
git switch issue-123-short-description
git switch -c bug-456-short-description
```

Merge bug branches back into their issue branch after the regression test and
fix are complete. Merge issue branches back into the release branch after local
checks, documentation, changelog updates, and GitHub issue evidence are
complete. Merge the release branch into `main` only when the maintainer
explicitly decides to release. Branch pushes intentionally do not start the CI,
docs, CodeQL, dependency-review, backlog-sync, or bug-sync workflows.

Create or refresh the pull request locally when the branch is ready for review:

```bash
scripts/open-release-pr.sh --repo ProjectCuillin/nats-sinks --base release-v0.4.1
```

Use `--base issue-123-short-description` for a bug branch, `--base
release-v0.4.1` for an issue branch, and `--base main` only for the final
release pull request. The helper creates a draft pull request by default. When
the release branch is ready for merge and release validation, mark the pull
request ready and dispatch the validation workflows:

```bash
scripts/run-release-validation.sh --repo ProjectCuillin/nats-sinks
```

The pull request is the review boundary before `main`. Branch protection should
require CI, CODEOWNER review, resolved conversations, and no direct pushes. See
[Hierarchical Branch Development And Release Workflow](branch-workflow.md).
Before merging any pull request, use
`python scripts/merge-pr-with-comment.py --pr <number> --comment-file <file>`
with a sanitized `## Test Evidence` comment so the merge itself carries public
validation context.

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

## Connector Development

New sinks should be treated as connectors with a small public contract and a
large responsibility: they must not return success until their destination
success boundary has been crossed. Start with the generic framework before
writing destination-specific code:

1. Add or update the GitHub backlog issue with functional, non-functional,
   security, documentation, and test requirements.
2. Decide whether the sink is a first-party connector in this repository or an
   optional third-party package discovered through the safe entry-point path.
3. Implement a sink class that accepts `NatsEnvelope` objects and never sees raw
   NATS client messages.
4. Register a `SinkConnector` descriptor with a stable lowercase name,
   production-readiness state, documentation pointer, and certification labels.
5. Add a `SinkCertificationCase` and reusable helper coverage from
   [Sink Certification](sink-certification.md). The helper assertion should
   prove the sink-specific durable success boundary with a fake client, fake
   connection, temporary directory, or other deterministic test double.
6. Add public API compatibility tests when the sink exposes a documented import
   path.
7. Add deterministic fake-client unit tests before any live integration tests.
8. Add integration or end-to-end scripts behind explicit markers and ignored
   local config directories.

First-party Oracle-family sinks, including proposed OCI Object Storage,
Oracle MySQL, Oracle Berkeley DB, Oracle NoSQL Database, and OCI Streaming,
should live in this repository unless governance decides otherwise. External
connectors should use the `nats_sinks.sinks` entry-point group and should not be
enabled in production without an explicit `plugins.allowed_sinks` entry and
connector certification evidence. A sink can be experimental without
certification, but it must not be described as production-ready until the
certification page's required evidence is present.

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
