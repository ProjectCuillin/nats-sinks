# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>

FROM container-registry.oracle.com/os/oraclelinux:9-slim

ARG ORACLE_MYSQL_VERSION="9.7.0"
ARG ORACLE_MYSQL_CREATED="unknown"

ENV ORACLE_MYSQL_TEST_DATABASE="nats_sinks_test" \
    ORACLE_MYSQL_TEST_USER="nats_sinks_test" \
    ORACLE_MYSQL_ROOT_PASSWORD_FILE="/run/secrets/oracle-mysql-root-password" \
    ORACLE_MYSQL_APP_PASSWORD_FILE="/run/secrets/oracle-mysql-app-password" \
    ORACLE_MYSQL_SOCKET="/run/mysqld/mysql.sock" \
    ORACLE_MYSQL_PORT="3306"

LABEL org.opencontainers.image.title="nats-sinks Oracle MySQL test database" \
      org.opencontainers.image.description="Short-lived Oracle MySQL test database image for nats-sinks Oracle MySQL sink development and e2e certification." \
      org.opencontainers.image.source="https://github.com/ProjectCuillin/nats-sinks" \
      org.opencontainers.image.url="https://github.com/ProjectCuillin/nats-sinks" \
      org.opencontainers.image.documentation="https://nats-sinks.readthedocs.io/en/latest/oracle-mysql-test-container/" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="ProjectCuillin" \
      org.opencontainers.image.version="${ORACLE_MYSQL_VERSION}" \
      org.opencontainers.image.created="${ORACLE_MYSQL_CREATED}" \
      org.opencontainers.image.base.name="container-registry.oracle.com/os/oraclelinux:9-slim"

RUN printf '%s\n' \
      '[ol9_mysql97_community]' \
      'name=Oracle Linux 9 Oracle MySQL 9.7 Community Server' \
      'baseurl=https://yum.oracle.com/repo/OracleLinux/OL9/MySQL97/community/$basearch/' \
      'gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-oracle' \
      'gpgcheck=1' \
      'enabled=1' \
      > /etc/yum.repos.d/oracle-mysql97-community.repo \
    && microdnf install -y --setopt=install_weak_deps=0 mysql-community-server mysql-community-client ca-certificates shadow-utils \
    && microdnf clean all \
    && rm -rf /var/cache/dnf /var/cache/yum /root/.cache \
    && mkdir -p /var/lib/mysql /run/mysqld /docker-entrypoint-initdb.d \
    && chown -R mysql:mysql /var/lib/mysql /run/mysqld /docker-entrypoint-initdb.d \
    && chmod 0750 /var/lib/mysql /run/mysqld \
    && chmod 1777 /tmp

COPY examples/oracle-mysql-test/entrypoint.sh /usr/local/bin/oracle-mysql-test-entrypoint

RUN chmod 0755 /usr/local/bin/oracle-mysql-test-entrypoint

VOLUME ["/var/lib/mysql"]

EXPOSE 3306

STOPSIGNAL SIGTERM
HEALTHCHECK NONE

ENTRYPOINT ["/usr/local/bin/oracle-mysql-test-entrypoint"]
