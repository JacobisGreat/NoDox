"""Instagram profile and post dataclasses — locked shape."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class InstagramProfile:
    username: str
    full_name: str
    biography: str
    followers: int
    followees: int
    is_private: bool
    is_verified: bool
    external_url: str | None
    profile_pic_url: str
    post_count_total: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InstagramPost:
    shortcode: str
    image_url: str
    caption: str | None
    location_name: str | None
    location_id: int | None
    taken_at_iso: str
    is_video: bool
    tagged_users: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
