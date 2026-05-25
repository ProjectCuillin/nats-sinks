# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>

FROM container-registry.oracle.com/os/oraclelinux:9-slim

ARG NATS_SINKS_EXTRAS=""
ARG NATS_SINKS_VERSION="0.0.0+local"
ARG NATS_SINKS_REVISION="unknown"
ARG NATS_SINKS_CREATED="unknown"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/var/lib/nats-sinks \
    XDG_CACHE_HOME=/tmp/nats-sinks-cache \
    TMPDIR=/tmp

LABEL org.opencontainers.image.title="nats-sinks" \
      org.opencontainers.image.description="Oracle Linux slim based nats-sinks JetStream sink runner image with non-root runtime defaults." \
      org.opencontainers.image.source="https://github.com/ProjectCuillin/nats-sinks" \
      org.opencontainers.image.url="https://github.com/ProjectCuillin/nats-sinks" \
      org.opencontainers.image.documentation="https://nats-sinks.readthedocs.io/en/latest/container-hardening/" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="ProjectCuillin" \
      org.opencontainers.image.version="${NATS_SINKS_VERSION}" \
      org.opencontainers.image.revision="${NATS_SINKS_REVISION}" \
      org.opencontainers.image.created="${NATS_SINKS_CREATED}" \
      org.opencontainers.image.base.name="container-registry.oracle.com/os/oraclelinux:9-slim"

WORKDIR /opt/nats-sinks

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN microdnf install -y --setopt=install_weak_deps=0 python3.11 python3.11-pip shadow-utils ca-certificates \
    && microdnf clean all \
    && rm -rf /var/cache/dnf /var/cache/yum /root/.cache \
    && python3.11 -m pip install --no-cache-dir ".${NATS_SINKS_EXTRAS:+[$NATS_SINKS_EXTRAS]}" \
    && python3.11 -m pip check \
    && groupadd --gid 10001 nats-sinks \
    && useradd --uid 10001 --gid 10001 --home-dir /var/lib/nats-sinks --shell /sbin/nologin --no-create-home nats-sinks \
    && mkdir -p /etc/nats-sinks /var/lib/nats-sinks/file /var/lib/nats-sinks/metrics /var/lib/nats-sinks/spool /tmp/nats-sinks-cache \
    && chown -R nats-sinks:nats-sinks /etc/nats-sinks /var/lib/nats-sinks /tmp/nats-sinks-cache \
    && chmod -R go-w /opt/nats-sinks \
    && chmod 1777 /tmp

USER 10001:10001

VOLUME ["/etc/nats-sinks", "/var/lib/nats-sinks"]

STOPSIGNAL SIGTERM
HEALTHCHECK NONE

ENTRYPOINT ["nats-sink"]
CMD ["--help"]
