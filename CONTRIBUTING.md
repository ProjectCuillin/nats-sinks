# Contributing

Thanks for helping improve `nats-sinks`.

This project is intended for public use by teams that rely on message delivery
and durable writes. Contributions should therefore be easy to review, clearly
documented, and careful about failure behavior. When in doubt, prefer a small
change with strong tests over a large change that is difficult to reason about.

Some users will evaluate this project for operational, public-sector, or
defence-adjacent environments. Contributions should therefore avoid casual
handling of secrets, payloads, logs, message metadata, and failure semantics.
Use clear wording, deterministic tests, and conservative defaults so reviewers
can understand how a change behaves under recovery, replay, and audit.

Repository: [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/)

Named contributor: Johan Louwers, [louwersj@gmail.com](mailto:louwersj@gmail.com).

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,oracle,docs]"
pre-commit install
```

## Checks

Run these before opening a pull request:

```bash
ruff format --check .
ruff check .
mypy src
pytest
python -m build
twine check dist/*
```

Unit tests must be deterministic and must not make network calls. Integration tests must be marked with `integration`.

## Branch and Commit Flow

- Do not commit directly to `main`. Work on `release-*`, `feature-*`,
  `bugfix-*`, or `hotfix-*` branches and merge to `main` only through reviewed
  pull requests.
- Push small commits to the work branch as the change develops. Branch pushes
  are intentionally quiet. When the branch is ready for merge or release
  validation, open or refresh a draft pull request with
  `scripts/open-release-pr.sh`, mark it ready, and run
  `scripts/run-release-validation.sh`.
- Keep changes small and reviewable.
- Use GitHub Issues as the live backlog. Create or link a detailed feature
  request before implementing user-visible work, unless the change is a small
  typo or mechanical maintenance item.
- Use descriptive commit messages.
- Update tests when behavior changes.
- Update documentation and `CHANGELOG.md` for user-visible changes.
- Preserve public API compatibility unless the change is intentionally breaking and documented.

## Pull Requests

Pull requests should include:

- A clear problem statement.
- A linked issue or a clear explanation for why no issue was needed.
- The implementation approach.
- Test coverage or a reason tests are not applicable.
- Documentation updates for public behavior changes.
- Detailed close-out notes when a feature request is completed, including what
  shipped, which checks passed, which docs changed, and any follow-up issues.

The commit-then-acknowledge invariant is non-negotiable: core owns delivery semantics, and sinks must never ACK JetStream messages.

See [Backlog Management](https://nats-sinks.readthedocs.io/en/latest/backlog-management/)
for the full issue and close-out workflow.

See
[Branch-First Development And Release Workflow](https://nats-sinks.readthedocs.io/en/latest/branch-workflow/)
for branch protection, release pull requests, and tag rules.
