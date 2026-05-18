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
python -m pip install -e ".[dev,oracle,docs]"
```

## Checks

```bash
ruff format --check .
ruff check .
mypy src
pytest
bandit -q -r src
python -m build
twine check dist/*
```

## Documentation

```bash
mkdocs build --strict
```

Mermaid diagrams are written directly in Markdown code fences so GitHub and MkDocs render the same conceptual flows where supported.

## Change Rules

- Preserve commit-then-acknowledge.
- Keep unit tests deterministic.
- Keep integration tests marked.
- Update docs for public behavior changes.
- Update `CHANGELOG.md` for user-visible changes.
- Avoid new dependencies unless there is a clear reason.
- Do not add a sink that can silently lose messages.

## Local Services

```bash
docker compose -f examples/docker-compose.nats.json up
docker compose -f examples/docker-compose.oracle.json up
```

Use only local test credentials and disposable data.
