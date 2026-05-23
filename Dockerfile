# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>

FROM python:3.12-slim

ARG NATS_SINKS_EXTRAS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

LABEL org.opencontainers.image.title="nats-sinks" \
      org.opencontainers.image.description="Local testing image for nats-sinks JetStream sink runners." \
      org.opencontainers.image.source="https://github.com/ProjectCuillin/nats-sinks" \
      org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /opt/nats-sinks

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir ".${NATS_SINKS_EXTRAS:+[$NATS_SINKS_EXTRAS]}" \
    && python -m pip check \
    && groupadd --system nats-sinks \
    && useradd --system --gid nats-sinks --home-dir /var/lib/nats-sinks --shell /usr/sbin/nologin nats-sinks \
    && mkdir -p /etc/nats-sinks /var/lib/nats-sinks \
    && chown -R nats-sinks:nats-sinks /etc/nats-sinks /var/lib/nats-sinks

USER nats-sinks:nats-sinks

VOLUME ["/etc/nats-sinks", "/var/lib/nats-sinks"]

ENTRYPOINT ["nats-sink"]
CMD ["--help"]
