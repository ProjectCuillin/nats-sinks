# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""First-party S3-compatible sink public API."""

from nats_sinks.s3.client import S3Client, S3PutObjectRequest, StandardS3Client
from nats_sinks.s3.config import (
    S3CompressionMode,
    S3CredentialMode,
    S3DuplicatePolicy,
    S3DurabilityMode,
    S3KeyStrategy,
    S3MetadataMode,
    S3ObjectFormat,
    S3ServerSideEncryption,
    S3SinkConfig,
)
from nats_sinks.s3.mapping import (
    S3PreparedObject,
    prepare_s3_object,
    prepare_s3_sidecar_object,
    s3_key_for_envelope,
    s3_object_metadata,
    s3_object_value_for_envelope,
    s3_sidecar_key_for_object,
    s3_sidecar_value_for_envelope,
)
from nats_sinks.s3.sink import S3Sink

__all__ = [
    "S3Client",
    "S3CompressionMode",
    "S3CredentialMode",
    "S3DuplicatePolicy",
    "S3DurabilityMode",
    "S3KeyStrategy",
    "S3MetadataMode",
    "S3ObjectFormat",
    "S3PreparedObject",
    "S3PutObjectRequest",
    "S3ServerSideEncryption",
    "S3Sink",
    "S3SinkConfig",
    "StandardS3Client",
    "prepare_s3_object",
    "prepare_s3_sidecar_object",
    "s3_key_for_envelope",
    "s3_object_metadata",
    "s3_object_value_for_envelope",
    "s3_sidecar_key_for_object",
    "s3_sidecar_value_for_envelope",
]
