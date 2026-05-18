# Governance

`nats-sinks` is maintained by project contributors.

The governance model is intentionally lightweight for the early project stage.
The goal is to make decisions transparent for external users and contributors
while keeping the project focused on delivery safety, security, maintainability,
and clear documentation.

Project repository: [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/)

Named contributor: Johan Louwers, [louwersj@gmail.com](mailto:louwersj@gmail.com).

## Maintainer Responsibilities

- Protect the commit-then-acknowledge invariant.
- Review security-sensitive changes carefully.
- Keep releases, documentation, tests, and CI healthy.
- Avoid unnecessary dependencies.
- Preserve public API stability where practical.

## Decision Making

Changes are discussed in issues and pull requests. Maintainers seek consensus, but may make final decisions when needed to preserve correctness, security, and project direction.

## Release Authority

Only trusted maintainers should publish releases. PyPI publishing should use trusted publishing or short-lived credentials.
