from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from statistics import median

from .models import CandidateMetrics, CreatorProfile, RecentPost

SHORT_VIDEO_FORMATS = {"short_video", "reel", "reels", "clips"}
KNOWN_FORMATS = {
    *SHORT_VIDEO_FORMATS,
    "video",
    "image",
    "photo",
    "carousel",
    "sidecar",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_median(values: list[int | float]) -> float | None:
    return float(median(values)) if values else None


def post_has_usable_engagement(post: RecentPost) -> bool:
    """Return true only for non-negative, observed likes and comments."""

    return (
        post.likes is not None
        and post.comments is not None
        and post.likes >= 0
        and post.comments >= 0
    )


def normalized_post_format(post: RecentPost) -> str:
    value = (post.post_format or "").strip().casefold()
    if value in SHORT_VIDEO_FORMATS:
        return "short_video"
    if value in {"sidecar", "carousel"}:
        return "carousel"
    if value in {"image", "photo"}:
        return "image"
    if value == "video":
        return "video"
    return "unknown"


def known_format_posts(profile: CreatorProfile) -> int:
    return sum(normalized_post_format(post) != "unknown" for post in profile.recent_posts)


def resolve_as_of(
    profile: CreatorProfile, requested: datetime | None = None
) -> datetime:
    if requested is not None:
        return _utc(requested)
    if profile.collected_at is not None:
        return _utc(profile.collected_at)
    timestamps = [
        _utc(post.timestamp)
        for post in profile.recent_posts
        if post.timestamp is not None
    ]
    if timestamps:
        return max(timestamps)
    return datetime.now(timezone.utc)


def calculate_candidate_metrics(
    profile: CreatorProfile, as_of: datetime | None = None
) -> CandidateMetrics:
    """Normalize post metrics without replacing missing engagement with zero."""

    sampled_posts = len(profile.recent_posts)
    valid_likes = [
        post.likes
        for post in profile.recent_posts
        if post.likes is not None and post.likes >= 0
    ]
    valid_comments = [
        post.comments
        for post in profile.recent_posts
        if post.comments is not None and post.comments >= 0
    ]
    usable_posts = [
        post for post in profile.recent_posts if post_has_usable_engagement(post)
    ]
    engagement_rates = [
        ((post.likes + post.comments) / profile.followers) * 100
        for post in usable_posts
        if profile.followers is not None
        and profile.followers > 0
        and post.likes is not None
        and post.comments is not None
    ]

    formats = Counter(normalized_post_format(post) for post in profile.recent_posts)
    timestamps = sorted(
        (
            _utc(post.timestamp)
            for post in profile.recent_posts
            if post.timestamp is not None
        ),
        reverse=True,
    )
    usable_timestamps = sorted(
        (
            _utc(post.timestamp)
            for post in usable_posts
            if post.timestamp is not None
        ),
        reverse=True,
    )
    # Eligibility is based on the latest post whose engagement observation is
    # actually usable. A newer sentinel-only post must not make stale measured
    # activity look recent.
    last_post_date = usable_timestamps[0] if usable_timestamps else None
    effective_as_of = resolve_as_of(profile, as_of)
    recency_days = (
        max(0.0, (effective_as_of - last_post_date).total_seconds() / 86_400)
        if last_post_date is not None
        else None
    )
    span_days = (
        (timestamps[0] - timestamps[-1]).total_seconds() / 86_400
        if len(timestamps) >= 2
        else None
    )
    posts_per_week = (
        ((len(timestamps) - 1) / span_days) * 7
        if span_days is not None and span_days > 0
        else None
    )

    return CandidateMetrics(
        followers=profile.followers,
        median_likes=_safe_median(valid_likes),
        median_comments=_safe_median(valid_comments),
        engagement_rate=_safe_median(engagement_rates),
        usable_posts=len(usable_posts),
        sampled_posts=sampled_posts,
        data_completeness=(
            len(usable_posts) / sampled_posts if sampled_posts else 0.0
        ),
        short_video_share=(
            formats["short_video"] / sampled_posts if sampled_posts else None
        ),
        last_post_date=last_post_date,
        posts_per_week=posts_per_week,
        recency_days=recency_days,
    )


def select_recent_post(
    profile: CreatorProfile,
    *,
    as_of: datetime | None = None,
    max_age_days: float = 90.0,
    require_caption: bool = False,
) -> RecentPost | None:
    effective_as_of = resolve_as_of(profile, as_of)
    candidates: list[RecentPost] = []
    for post in profile.recent_posts:
        if not post.url or post.timestamp is None:
            continue
        if require_caption and not post.caption.strip():
            continue
        age_days = (effective_as_of - _utc(post.timestamp)).total_seconds() / 86_400
        if 0 <= age_days <= max_age_days:
            candidates.append(post)
    return max(candidates, key=lambda post: _utc(post.timestamp)) if candidates else None
