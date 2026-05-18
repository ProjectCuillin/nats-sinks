# Contributing

Thanks for helping improve `nats-sinks`.

This project is intended for public use by teams that rely on message delivery
and durable writes. Contributions should therefore be easy to review, clearly
documented, and careful about failure behavior. When in doubt, prefer a small
change with strong tests over a large change that is difficult to reason about.

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

- Keep changes small and reviewable.
- Use descriptive commit messages.
- Update tests when behavior changes.
- Update documentation and `CHANGELOG.md` for user-visible changes.
- Preserve public API compatibility unless the change is intentionally breaking and documented.

## Pull Requests

Pull requests should include:

- A clear problem statement.
- The implementation approach.
- Test coverage or a reason tests are not applicable.
- Documentation updates for public behavior changes.

The commit-then-acknowledge invariant is non-negotiable: core owns delivery semantics, and sinks must never ACK JetStream messages.
