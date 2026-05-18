# Local Development

Start NATS:

```bash
docker compose -f examples/docker-compose.nats.json up
```

Or run directly:

```bash
nats-server -c examples/local-dev/nats-server.conf
```

Create a stream:

```bash
nats stream add ORDERS --subjects "orders.*"
```
