# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the documented GitHub branch protection policy.

The repository currently has one write-access maintainer.  GitHub does not
permit self-approval, so the branch-protection script must keep the pull
request boundary and automated checks without requiring an approving review.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_branch_protection_policy_supports_solo_maintainer_releases() -> None:
    """The main-branch policy should not require impossible self-approval."""

    script = (ROOT / "scripts" / "apply-branch-protection.sh").read_text(encoding="utf-8")

    assert '"branch-first-policy"' in script
    assert '"dependency-review"' in script
    assert '"required_approving_review_count": 0' in script
    assert '"require_code_owner_reviews": false' in script
    assert '"required_conversation_resolution": true' in script
    assert '"allow_force_pushes": false' in script
    assert '"allow_deletions": false' in script
    assert '"required_approving_review_count": 1' not in script
    assert '"require_code_owner_reviews": true' not in script
