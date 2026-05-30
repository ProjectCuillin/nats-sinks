# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>

FROM container-registry.oracle.com/os/oraclelinux:9-slim-fips

LABEL org.opencontainers.image.title="nats-sinks HTTP sink NGINX test endpoint" \
      org.opencontainers.image.description="Local-only NGINX endpoint for nats-sinks HTTP sink e2e testing." \
      org.opencontainers.image.source="https://github.com/ProjectCuillin/nats-sinks" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.base.name="container-registry.oracle.com/os/oraclelinux:9-slim-fips"

RUN microdnf install -y --setopt=install_weak_deps=0 nginx python3 \
    && microdnf clean all \
    && groupadd --system --gid 10001 nats-sinks-http \
    && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --shell /sbin/nologin nats-sinks-http \
    && mkdir -p /etc/nats-sinks-http /var/lib/nats-sinks-http /tmp/nginx/client_body /tmp/nginx/proxy \
    && chown -R 10001:10001 /var/lib/nats-sinks-http /tmp/nginx \
    && chmod 0750 /var/lib/nats-sinks-http /tmp/nginx

COPY examples/http-nginx-fips-test/nginx.conf /etc/nats-sinks-http/nginx.conf
COPY examples/http-nginx-fips-test/capture_server.py /usr/local/bin/nats-sinks-http-capture
COPY examples/http-nginx-fips-test/entrypoint.sh /usr/local/bin/nats-sinks-http-endpoint-entrypoint

RUN chmod 0444 /etc/nats-sinks-http/nginx.conf \
    && chmod 0555 /usr/local/bin/nats-sinks-http-capture /usr/local/bin/nats-sinks-http-endpoint-entrypoint

USER 10001:10001
EXPOSE 8080

STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read()" || exit 1

ENTRYPOINT ["/usr/local/bin/nats-sinks-http-endpoint-entrypoint"]
