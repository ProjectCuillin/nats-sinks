# Security Policy

Repository: [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/)

Named contributor: Johan Louwers, [louwersj@gmail.com](mailto:louwersj@gmail.com).

`nats-sinks` is infrastructure software. It receives messages, handles
credentials, writes to external systems, and controls when JetStream messages
are acknowledged. Please report security concerns privately so maintainers can
investigate and coordinate a safe fix before public disclosure.

## Supported Versions

During the `0.x` series, security fixes are applied to the latest minor release line.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately to the maintainers. Do not open a public issue until a fix and disclosure plan are agreed.

Include:

- A description of the vulnerability.
- A minimal reproduction when possible.
- Affected versions or commits.
- Any known mitigations.

## Secret Handling

Never commit credentials, tokens, private keys, Oracle passwords, NATS credentials, or real payloads containing sensitive data. Use `password_env` and secret stores in production.

`nats-sinks` redacts secret-looking configuration fields in CLI output and avoids payload logging by default.

## Dependency Hygiene

Dependencies are kept intentionally small. Dependabot, CodeQL, dependency review, Ruff, mypy, pytest, and Bandit are configured for continuous checks.
