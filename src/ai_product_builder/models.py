from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class ProfileStatus(StrEnum):
    CREATOR = "creator"
    BRAND_REFERENCE = "brand_reference"
    PRIVATE = "private"
    UNRESOLVED_NOT_FOUND = "unresolved_not_found"
    INSUFFICIENT_DATA = "insufficient_data"


class PostFormat(StrEnum):
    SHORT_VIDEO = "short_video"
    VIDEO = "video"
    CAROUSEL = "carousel"
    IMAGE = "image"
    UNKNOWN = "unknown"


def optional_int(value: Any, field_name: str, issues: list[str]) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        issues.append(f"{field_name}: boolean is not a valid integer")
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        issues.append(f"{field_name}: expected integer, received {value!r}")
        return None
    if parsed < 0:
        issues.append(f"{field_name}: negative value {parsed} treated as missing")
        return None
    return parsed


def optional_datetime(value: Any, field_name: str, issues: list[str]) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        issues.append(f"{field_name}: expected ISO timestamp, received {value!r}")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        issues.append(f"{field_name}: invalid ISO timestamp {value!r}")
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class LatestPost:
    post_id: str | None
    post_type: str | None
    product_type: str | None
    caption: str
    likes: int | None
    comments: int | None
    timestamp: datetime | None
    mentions: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()
    tagged_usernames: tuple[str, ...] = ()
    paid_partnership: bool = False
    validation_issues: tuple[str, ...] = ()

    @classmethod
    def from_raw(cls, raw: Any, profile_index: int, post_index: int) -> "LatestPost":
        issues: list[str] = []
        if not isinstance(raw, dict):
            return cls(
                post_id=None,
                post_type=None,
                product_type=None,
                caption="",
                likes=None,
                comments=None,
                timestamp=None,
                validation_issues=(
                    f"profile[{profile_index}].latestPosts[{post_index}]: expected object",
                ),
            )
        prefix = f"profile[{profile_index}].latestPosts[{post_index}]"
        mentions = raw.get("mentions") if isinstance(raw.get("mentions"), list) else []
        hashtags = raw.get("hashtags") if isinstance(raw.get("hashtags"), list) else []
        tagged = raw.get("taggedUsers") if isinstance(raw.get("taggedUsers"), list) else []
        tagged_usernames = tuple(
            str(item["username"])
            for item in tagged
            if isinstance(item, dict) and item.get("username")
        )
        return cls(
            post_id=str(raw["id"]) if raw.get("id") is not None else None,
            post_type=str(raw["type"]) if raw.get("type") is not None else None,
            product_type=(
                str(raw["productType"]) if raw.get("productType") is not None else None
            ),
            caption=str(raw.get("caption") or ""),
            likes=optional_int(raw.get("likesCount"), f"{prefix}.likesCount", issues),
            comments=optional_int(
                raw.get("commentsCount"), f"{prefix}.commentsCount", issues
            ),
            timestamp=optional_datetime(
                raw.get("timestamp"), f"{prefix}.timestamp", issues
            ),
            mentions=tuple(str(value) for value in mentions if value),
            hashtags=tuple(str(value) for value in hashtags if value),
            tagged_usernames=tagged_usernames,
            paid_partnership=bool(raw.get("paidPartnership", False)),
            validation_issues=tuple(issues),
        )

    @property
    def format(self) -> PostFormat:
        post_type = (self.post_type or "").casefold()
        product_type = (self.product_type or "").casefold()
        if product_type in {"clips", "reels", "reel"}:
            return PostFormat.SHORT_VIDEO
        if post_type in {"sidecar", "carousel"}:
            return PostFormat.CAROUSEL
        if post_type == "video":
            return PostFormat.VIDEO
        if post_type in {"image", "photo"}:
            return PostFormat.IMAGE
        return PostFormat.UNKNOWN


@dataclass(slots=True)
class Profile:
    source_index: int
    input_url: str | None
    username: str | None
    full_name: str
    biography: str
    followers: int | None
    posts_count: int | None
    private: bool | None
    error: str | None
    error_description: str | None
    business_category: str | None
    is_business: bool | None
    external_urls: tuple[str, ...]
    latest_posts: tuple[LatestPost, ...]
    validation_issues: tuple[str, ...] = ()
    status: ProfileStatus | None = None
    exclusion_reason: str | None = None

    @classmethod
    def from_raw(cls, raw: Any, index: int) -> "Profile":
        if not isinstance(raw, dict):
            return cls(
                source_index=index,
                input_url=None,
                username=None,
                full_name="",
                biography="",
                followers=None,
                posts_count=None,
                private=None,
                error="invalid_record",
                error_description="Top-level record is not an object",
                business_category=None,
                is_business=None,
                external_urls=(),
                latest_posts=(),
                validation_issues=(f"profile[{index}]: expected object",),
            )
        issues: list[str] = []
        username = raw.get("username")
        if username is not None and not isinstance(username, str):
            issues.append(f"profile[{index}].username: expected string")
            username = str(username)
        external: list[str] = []
        for field_name in ("externalUrl", "externalUrlShimmed"):
            if isinstance(raw.get(field_name), str) and raw[field_name]:
                external.append(raw[field_name])
        if isinstance(raw.get("externalUrls"), list):
            external.extend(str(value) for value in raw["externalUrls"] if value)
        raw_posts = raw.get("latestPosts")
        if raw_posts is None:
            raw_posts = []
        if not isinstance(raw_posts, list):
            issues.append(f"profile[{index}].latestPosts: expected array")
            raw_posts = []
        latest_posts = tuple(
            LatestPost.from_raw(post, index, post_index)
            for post_index, post in enumerate(raw_posts)
        )
        for post in latest_posts:
            issues.extend(post.validation_issues)
        return cls(
            source_index=index,
            input_url=str(raw["inputUrl"]) if raw.get("inputUrl") else None,
            username=username,
            full_name=str(raw.get("fullName") or ""),
            biography=str(raw.get("biography") or ""),
            followers=optional_int(
                raw.get("followersCount"), f"profile[{index}].followersCount", issues
            ),
            posts_count=optional_int(
                raw.get("postsCount"), f"profile[{index}].postsCount", issues
            ),
            private=raw.get("private") if isinstance(raw.get("private"), bool) else None,
            error=str(raw["error"]) if raw.get("error") else None,
            error_description=(
                str(raw["errorDescription"]) if raw.get("errorDescription") else None
            ),
            business_category=(
                str(raw["businessCategoryName"])
                if raw.get("businessCategoryName")
                else None
            ),
            is_business=(
                raw.get("isBusinessAccount")
                if isinstance(raw.get("isBusinessAccount"), bool)
                else None
            ),
            external_urls=tuple(dict.fromkeys(external)),
            latest_posts=latest_posts,
            validation_issues=tuple(issues),
        )

    def serializable(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value if self.status else None
        for post in result["latest_posts"]:
            if post["timestamp"]:
                post["timestamp"] = post["timestamp"].isoformat()
        return result


@dataclass(slots=True)
class SignalEvidence:
    signal_type: str
    source_field: str
    source_reference: str
    evidence_text: str
    observation_type: str = "direct"

    def serializable(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SignalResult:
    detected: bool
    matches: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    provenance: tuple[SignalEvidence, ...] = ()

    def serializable(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "matches": self.matches,
            "evidence": self.evidence,
            "provenance": [item.serializable() for item in self.provenance],
        }


@dataclass(slots=True)
class ProfileMetrics:
    followers: int | None
    posts_count: int | None
    sampled_posts: int
    median_likes: float | None
    median_comments: float | None
    engagement_rate_pct: float | None
    usable_engagement_posts: int
    engagement_confidence: float | None
    format_counts: dict[str, int]
    format_distribution: dict[str, float]
    short_video_share: float | None
    latest_post_at: datetime | None
    posting_recency_days: float | None
    posts_per_week: float | None
    median_post_interval_days: float | None
    signals: dict[str, SignalResult] = field(default_factory=dict)

    def serializable(self) -> dict[str, Any]:
        result = asdict(self)
        result["latest_post_at"] = (
            self.latest_post_at.isoformat() if self.latest_post_at else None
        )
        return result


@dataclass(slots=True)
class ScoreComponent:
    name: str
    score: float
    maximum: float
    explanation: str

    def serializable(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProfileScore:
    username: str
    total: float
    components: tuple[ScoreComponent, ...]
    explanation: str
    raw_engagement_score: float
    engagement_confidence: float

    def serializable(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "total": self.total,
            "components": [item.serializable() for item in self.components],
            "explanation": self.explanation,
            "raw_engagement_score": self.raw_engagement_score,
            "engagement_confidence": self.engagement_confidence,
        }
