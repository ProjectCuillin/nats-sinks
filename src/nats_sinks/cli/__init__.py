# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Command-line interface package.

The CLI package is intentionally separated from the core runtime so importing
`nats_sinks` stays light and side-effect free.  User-facing command
implementations live in `nats_sinks.cli.main`; this package marker exists for
console-script discovery and future CLI-specific helpers.

CLI commands are expected to use the same safe configuration loader and sink
registry as embedded Python applications.  That keeps behavior consistent
between `nats-sink run config.json` and direct construction of
`JetStreamSinkRunner`.
"""
