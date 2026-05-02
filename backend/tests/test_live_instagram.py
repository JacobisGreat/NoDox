"""Live network test against Instagram's anonymous lookup endpoint.

Skipped by default. Set ``RUN_NETWORK_TESTS=1`` (and accept the slight
risk of an IP-level rate-limit) to run these.

Use this to detect when Instagram changes the public ``web_profile_info``
endpoint shape — the unit tests pin against fixtures and won't catch
upstream regressions on their own.
"""

from __future__ import annotations

import os

import pytest

from app.services.instaloader_fetch import (
    InstaProfileNotFound,
    fetch_profile_and_posts,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("RUN_NETWORK_TESTS") != "1",
        reason="Set RUN_NETWORK_TESTS=1 to enable live Instagram tests",
    ),
]


async def test_live_public_profile_returns_real_data():
    result = await fetch_profile_and_posts("instagram", max_posts=5)
    assert result.profile.username == "instagram"
    assert result.profile.is_verified is True
    assert result.profile.followers > 1_000_000
    # Inline timeline gives ~12 posts; cap is 5 here.
    assert 1 <= len(result.posts) <= 5
    for p in result.posts:
        assert p.shortcode
        assert p.image_url.startswith("http")


async def test_live_unknown_profile_raises_not_found():
    with pytest.raises(InstaProfileNotFound):
        await fetch_profile_and_posts(
            "this_user_definitely_does_not_exist_xyz999", max_posts=1
        )
