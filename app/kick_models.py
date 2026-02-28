from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


Json = dict[str, Any]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class KickChannel:
    slug: str
    username: str
    url: str
    profile_picture: str = ""

    @classmethod
    def from_campaign_channel(cls, data: Json) -> "KickChannel | None":
        slug = str(data.get("slug") or "").strip()
        if not slug:
            return None
        user = data.get("user") or {}
        username = str(user.get("username") or slug)
        return cls(
            slug=slug,
            username=username,
            url=f"https://kick.com/{slug}",
            profile_picture=str(user.get("profile_picture") or ""),
        )


@dataclass(slots=True)
class KickReward:
    id: str
    name: str
    required_units: int = 0
    image_url: str = ""
    claimed: bool = False
    progress: float = 0.0

    @classmethod
    def from_api(cls, data: Json) -> "KickReward":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "Unknown Reward"),
            required_units=_safe_int(data.get("required_units")),
            image_url=str(data.get("image_url") or data.get("image") or ""),
            claimed=bool(data.get("claimed", False)),
            progress=max(0.0, min(1.0, _safe_float(data.get("progress"), 0.0))),
        )

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(slots=True)
class KickCampaign:
    id: str
    name: str
    game: str
    game_slug: str = ""
    game_image: str = ""
    category_id: int | None = None
    status: str = "unknown"
    starts_at: str | None = None
    ends_at: str | None = None
    rewards: list[KickReward] = field(default_factory=list)
    channels: list[KickChannel] = field(default_factory=list)
    progress_status: str = "not_started"
    progress_units: int = 0
    raw: Json = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: Json) -> "KickCampaign":
        category = data.get("category") or {}
        rewards = [KickReward.from_api(r) for r in (data.get("rewards") or []) if isinstance(r, dict)]
        channels: list[KickChannel] = []
        for channel_data in data.get("channels") or []:
            if not isinstance(channel_data, dict):
                continue
            channel = KickChannel.from_campaign_channel(channel_data)
            if channel is not None:
                channels.append(channel)
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "Unknown Campaign"),
            game=str(category.get("name") or "Unknown Game"),
            game_slug=str(category.get("slug") or ""),
            game_image=str(category.get("image_url") or ""),
            category_id=_safe_int(category.get("id"), default=0) or None,
            status=str(data.get("status") or "unknown"),
            starts_at=data.get("starts_at"),
            ends_at=data.get("ends_at"),
            rewards=rewards,
            channels=channels,
            raw=data,
        )

    @property
    def max_required_minutes(self) -> int:
        return max((reward.required_units for reward in self.rewards), default=0)

    def to_dict(self) -> Json:
        return {
            "id": self.id,
            "name": self.name,
            "game": self.game,
            "game_slug": self.game_slug,
            "game_image": self.game_image,
            "category_id": self.category_id,
            "status": self.status,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "progress_status": self.progress_status,
            "progress_units": self.progress_units,
            "rewards": [r.to_dict() for r in self.rewards],
            "channels": [asdict(c) for c in self.channels],
        }


@dataclass(slots=True)
class KickProgressReward:
    id: str
    progress: float
    claimed: bool
    required_units: int
    name: str = ""

    @classmethod
    def from_api(cls, data: Json) -> "KickProgressReward":
        return cls(
            id=str(data.get("id") or ""),
            progress=max(0.0, min(1.0, _safe_float(data.get("progress"), 0.0))),
            claimed=bool(data.get("claimed", False)),
            required_units=_safe_int(data.get("required_units")),
            name=str(data.get("name") or ""),
        )


@dataclass(slots=True)
class KickProgressCampaign:
    id: str
    name: str
    status: str
    progress_units: int
    category_name: str = "Unknown"
    rewards: list[KickProgressReward] = field(default_factory=list)
    raw: Json = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: Json) -> "KickProgressCampaign":
        category = data.get("category") or {}
        rewards = [
            KickProgressReward.from_api(r)
            for r in (data.get("rewards") or [])
            if isinstance(r, dict)
        ]
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "Unknown Campaign"),
            status=str(data.get("status") or "unknown"),
            progress_units=_safe_int(data.get("progress_units")),
            category_name=str(category.get("name") or "Unknown"),
            rewards=rewards,
            raw=data,
        )

    def to_dict(self) -> Json:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "progress_units": self.progress_units,
            "category": {"name": self.category_name},
            "rewards": [
                {
                    "id": r.id,
                    "name": r.name,
                    "progress": r.progress,
                    "claimed": r.claimed,
                    "required_units": r.required_units,
                }
                for r in self.rewards
            ],
        }


@dataclass(slots=True)
class QueueItem:
    url: str
    minutes_target: int = 0
    elapsed_seconds: int = 0
    status: str = "PENDING"
    campaign_id: str | None = None
    campaign_name: str | None = None
    category_id: int | None = None
    notes: str = ""

    @property
    def slug(self) -> str:
        return self.url.rstrip("/").split("/")[-1].strip()

    @property
    def done(self) -> bool:
        return self.minutes_target > 0 and self.elapsed_seconds >= self.minutes_target * 60

    def to_dict(self) -> Json:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Json) -> "QueueItem":
        return cls(
            url=str(data.get("url") or ""),
            minutes_target=_safe_int(data.get("minutes_target"), _safe_int(data.get("minutes"))),
            elapsed_seconds=_safe_int(data.get("elapsed_seconds"), _safe_int(data.get("elapsed"))),
            status=str(data.get("status") or "PENDING"),
            campaign_id=data.get("campaign_id"),
            campaign_name=data.get("campaign_name"),
            category_id=(_safe_int(data.get("category_id"), 0) or None),
            notes=str(data.get("notes") or ""),
        )


def parse_campaigns_response(payload: Json) -> list[KickCampaign]:
    data = payload.get("data") or []
    if not isinstance(data, list):
        return []
    campaigns = [KickCampaign.from_api(item) for item in data if isinstance(item, dict)]
    return [campaign for campaign in campaigns if campaign.id]


def parse_progress_response(payload: Json) -> list[KickProgressCampaign]:
    data = payload.get("data") or []
    if not isinstance(data, list):
        return []
    progress = [KickProgressCampaign.from_api(item) for item in data if isinstance(item, dict)]
    return [campaign for campaign in progress if campaign.id]


def merge_campaigns_with_progress(
    campaigns: list[KickCampaign],
    progress: list[KickProgressCampaign],
) -> list[KickCampaign]:
    progress_map = {p.id: p for p in progress}
    for campaign in campaigns:
        p = progress_map.get(campaign.id)
        if p is None:
            campaign.progress_status = "not_started"
            campaign.progress_units = 0
            continue
        campaign.progress_status = p.status
        campaign.progress_units = p.progress_units
        reward_map = {r.id: r for r in p.rewards}
        for reward in campaign.rewards:
            if reward.id in reward_map:
                pr = reward_map[reward.id]
                reward.progress = pr.progress
                reward.claimed = pr.claimed
                if pr.required_units > 0:
                    reward.required_units = pr.required_units
    return campaigns
