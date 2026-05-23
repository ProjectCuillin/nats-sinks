# Cross-Domain Handoff Package Example

This directory contains a sanitized example package for the public
cross-domain handoff package blueprint. It is safe to commit because it uses
synthetic stream names, subjects, identifiers, payload values, hashes, and
review labels.

The example is not a cross-domain guard, release approval, data diode,
sanitizer, or certification artifact. It is a documentation fixture used by
unit tests to prove the package shape remains bounded, path-safe, and free of
obvious secret material.

Files:

- `manifest.json` is the package index and safety summary.
- `metadata.json` shows normalized message metadata.
- `payload.encrypted.json` shows an encrypted-payload envelope shape with
  synthetic values.
- `evidence.json` shows sink evidence and hash summaries.
