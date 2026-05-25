# Oracle MySQL Bug Hunt Public Reproduction Evidence

This sanitized evidence note is safe to attach to public GitHub bug comments.
It points to the executable regression file without repeating local connection
settings or credential-shaped test arguments.

## Focused Regression

Run:

```sh
python -m pytest tests/unit/test_bug_hunt_mysql_sink_hardening.py -q
```

Expected result after the fix:

```text
18 passed
```

## Covered Areas

- Oracle MySQL secret-source configuration validation.
- Oracle MySQL connection, TLS, and pool-name validation.
- Oracle MySQL table and column identifier validation.
- Oracle MySQL idempotency-key validation.
- Oracle MySQL auto-create DDL naming for max-length identifiers.
- Oracle MySQL startup cleanup after schema setup failure.
- Oracle MySQL write cleanup behavior after commit or schema errors.
