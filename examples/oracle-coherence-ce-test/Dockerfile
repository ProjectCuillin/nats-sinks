# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>

ARG ORACLE_LINUX_BASE_IMAGE="container-registry.oracle.com/os/oraclelinux:9-slim"

FROM ${ORACLE_LINUX_BASE_IMAGE} AS coherence-deps

ARG ORACLE_COHERENCE_CE_VERSION="25.03.1"

RUN set -eux; \
    microdnf install -y ca-certificates java-17-openjdk-headless maven; \
    microdnf clean all; \
    rm -rf /var/cache/yum

WORKDIR /tmp/coherence-build
COPY examples/oracle-coherence-ce-test/pom.xml ./pom.xml

RUN set -eux; \
    mvn -B \
        -Dcoherence.version="${ORACLE_COHERENCE_CE_VERSION}" \
        org.apache.maven.plugins:maven-dependency-plugin:3.7.1:copy-dependencies \
        -DincludeScope=runtime \
        -DoutputDirectory=/opt/coherence/lib; \
    find /opt/coherence/lib -type f -name '*.jar' -print >/opt/coherence/runtime-jars.txt; \
    test -s /opt/coherence/runtime-jars.txt

FROM ${ORACLE_LINUX_BASE_IMAGE}

ARG ORACLE_COHERENCE_CE_VERSION="25.03.1"

LABEL org.opencontainers.image.title="nats-sinks Oracle Coherence CE test backend" \
      org.opencontainers.image.description="Test-only Oracle Coherence Community Edition backend built on Oracle Linux 9 slim." \
      org.opencontainers.image.version="${ORACLE_COHERENCE_CE_VERSION}" \
      org.opencontainers.image.base.name="container-registry.oracle.com/os/oraclelinux:9-slim" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN set -eux; \
    microdnf install -y ca-certificates java-17-openjdk-headless shadow-utils; \
    groupadd --system --gid 10001 coherence; \
    useradd --system --uid 10001 --gid 10001 --home-dir /tmp --shell /sbin/nologin coherence; \
    mkdir -p /opt/coherence/lib /logs /tmp/coherence; \
    chown -R coherence:coherence /opt/coherence /logs /tmp/coherence; \
    microdnf clean all; \
    rm -rf /var/cache/yum

COPY --from=coherence-deps --chown=coherence:coherence /opt/coherence/lib /opt/coherence/lib
COPY --from=coherence-deps --chown=coherence:coherence /opt/coherence/runtime-jars.txt /opt/coherence/runtime-jars.txt

ENV COHERENCE_CLUSTER="nats-sinks-coherence-test" \
    JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=/tmp -Duser.home=/tmp -XX:MaxRAMPercentage=75.0"

USER coherence:coherence

EXPOSE 1408

HEALTHCHECK NONE

ENTRYPOINT ["java", "-cp", "/opt/coherence/lib/*", "-Dcoherence.cluster=nats-sinks-coherence-test", "-Dcoherence.role=storage", "-Dcoherence.distributed.localstorage=true", "-Dcoherence.wka=127.0.0.1", "-Dcoherence.localhost=127.0.0.1", "-Dcoherence.ttl=0", "-Dcoherence.grpc.server.address=0.0.0.0", "-Dcoherence.grpc.server.port=1408", "com.tangosol.net.DefaultCacheServer"]
