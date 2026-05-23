# Edge Spool Example

This example configures `nats-sink` to use the encrypted edge spool sink. The
runner ACKs JetStream after each message is committed to the local encrypted
spool directory. Operators can replay those records later into a final sink
configuration.

Generate a local development key:

```bash
export NATS_SINKS_SPOOL_KEY_B64="$(
  python - <<'PY'
import base64
import secrets
print(base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
PY
)"
```

Validate the configuration:

```bash
nats-sink validate examples/spool-basic/config.json
```

Run the spool worker:

```bash
nats-sink run examples/spool-basic/config.json
```

Replay into a final sink by providing a second normal nats-sinks config file:

```bash
nats-sink replay-spool examples/spool-basic/config.json examples/file-basic/config.json --dry-run
```

Do not commit real spool keys or generated spool files. Keep production keys in
a secret manager or approved runtime secret source.
