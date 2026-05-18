# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from nats_sinks.core.batching import chunked, iter_chunked
from nats_sinks.core.errors import ConfigurationError


def test_chunked_splits_sequence() -> None:
    assert chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_iter_chunked_splits_iterable() -> None:
    assert list(iter_chunked(iter([1, 2, 3]), 2)) == [[1, 2], [3]]


def test_chunked_rejects_invalid_size() -> None:
    with pytest.raises(ConfigurationError):
        chunked([1], 0)
