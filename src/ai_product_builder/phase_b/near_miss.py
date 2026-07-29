"""Conservative near-miss analysis for failed and recovered Phase B runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .enrichment import known_format_posts
from .models import CreatorProfile


NEAR_MISS_COLUMNS: tuple[str, ...] = (
    "username",
    "profile_url",
    "followers",
    "score",
    "discovery_confidence",
    "account_type",
    "detected_language",
    "detected_geography",
    "usable_posts",
    "known_format_posts",
    "fashion_post_count",
    "short_video_count",
    "latest_post_date",
    "original_exclusion_reasons",
    "exact_saved_evidence",
    "reviewable_reasons",
    "nonreviewable_reasons",
    "recent_fashion_post_url",
    "recommendation",
    "manual_review_status",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _exact_saved_evidence(
    profile: CreatorProfile,
    evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    references = {
        str(item.get("source_reference"))
        for signal in ("fashion", "content_language", "detected_geography")
        for item in evidence.get(signal, ())
        if isinstance(item, Mapping)
    }
    rows: list[dict[str, Any]] = []
    if "profile" in references and profile.biography:
        rows.append(
            {
                "source_field": "biography",
                "source_reference": "profile",
                "evidence_text": profile.biography,
                "url": profile.identity.canonical_profile_url,
            }
        )
    for index, post in enumerate(profile.recent_posts):
        reference = post.post_id or post.url or f"index:{index}"
        if reference not in references:
            continue
        rows.append(
            {
                "source_field": "recent_posts.caption",
                "source_reference": reference,
                "evidence_text": post.caption,
                "url": post.url,
                "post_format": post.post_format,
                "timestamp": (
                    post.timestamp.isoformat() if post.timestamp else None
                ),
            }
        )
    return rows


def _reason_groups(
    record: Mapping[str, Any],
    profile: CreatorProfile,
) -> tuple[list[str], list[str]]:
    eligibility = _mapping(record.get("eligibility"))
    reasons = _strings(eligibility.get("reasons"))
    metrics = _mapping(record.get("metrics"))
    account = _mapping(record.get("account_type_assessment"))
    barter = _mapping(record.get("barter_signal_assessment"))
    compatibility = _mapping(record.get("compatibility_assessment"))
    live = _mapping(record.get("live_campaign_assessment"))

    reviewable: list[str] = []
    nonreviewable: list[str] = []
    fashion_posts = int(live.get("recent_fashion_posts") or 0)
    short_videos = int(live.get("recent_short_video_posts") or 0)
    usable_posts = int(metrics.get("usable_posts") or 0)
    recency_days = metrics.get("recency_days")
    detected_language = str(
        compatibility.get("detected_content_language") or "undetermined"
    )
    detected_geography = compatibility.get("detected_geography")
    account_type = str(account.get("account_type") or "unclear")

    if account_type == "professional_portfolio":
        if fashion_posts >= 2 and len(profile.recent_posts) >= 3:
            reviewable.append("professional_stylist_creator_review")
        else:
            nonreviewable.append("professional_portfolio_without_creator_evidence")
    elif account_type != "personal_creator":
        nonreviewable.append(f"account_type_not_personal:{account_type}")

    if bool(account.get("own_fashion_brand")):
        nonreviewable.append("own_fashion_brand")
    if bool(account.get("own_clothing_store_or_showroom")):
        nonreviewable.append("clothing_store_or_showroom")
    if bool(account.get("commercial_conflict")):
        nonreviewable.append("commercial_account_conflict")
    if bool(barter.get("explicit_refusal")):
        nonreviewable.append("explicit_no_barter")
    if profile.private is not False or profile.accessible is not True:
        nonreviewable.append("unavailable_or_private")
    if profile.identity.identity_conflict:
        nonreviewable.append("identity_conflict")
    if not bool(account.get("theme_relevant")):
        nonreviewable.append("target_theme_not_relevant")
    if fashion_posts < 2:
        nonreviewable.append("fewer_than_two_recent_fashion_posts")
    elif fashion_posts == 2:
        reviewable.append("two_fashion_posts_instead_of_three")

    if usable_posts == 0:
        nonreviewable.append("zero_usable_posts")
    elif usable_posts < 6:
        nonreviewable.append("insufficient_engagement_data")

    if detected_language.startswith("mixed:") and "ru" in detected_language:
        if not bool(compatibility.get("campaign_language_compatible")):
            reviewable.append("mixed_language_with_russian_evidence")
    elif not bool(compatibility.get("campaign_language_compatible")):
        nonreviewable.append("foreign_profile_without_russian_context")

    if bool(compatibility.get("delivery_market_conflict")):
        nonreviewable.append("delivery_market_conflict")
    elif bool(compatibility.get("delivery_market_review_required")):
        if detected_geography is None and "ru" in detected_language:
            reviewable.append("russian_profile_geography_unverified")
        else:
            reviewable.append("delivery_market_review_required")

    known_formats = known_format_posts(profile)
    reel_url_observed = any(
        post.url
        and any(
            marker in post.url.casefold()
            for marker in ("/reel/", "/reels/", "/tv/")
        )
        for post in profile.recent_posts
    )
    if known_formats == 0:
        if reel_url_observed:
            reviewable.append("video_url_observed_but_format_field_missing")
        else:
            nonreviewable.append("post_format_data_missing")
    elif short_videos == 0 and reel_url_observed:
        reviewable.append("video_format_mapping_review")

    if isinstance(recency_days, (int, float)) and recency_days > 90:
        if recency_days <= 120:
            reviewable.append("activity_slightly_older_than_90_days")
        else:
            nonreviewable.append("activity_too_old")

    if bool(live.get("audience_review_required")):
        reviewable.append("high_audience_barter_review")
    followers = metrics.get("followers")
    if isinstance(followers, int) and 0 < followers < 1_000:
        reviewable.append("small_audience")

    ignored_consequences = {
        "blogger_content_insufficient",
        "short_video_evidence_missing",
        "delivery_market_compatibility_unverified",
        "campaign_language_mismatch",
    }
    for reason in reasons:
        if reason in ignored_consequences:
            continue
        if reason.startswith("insufficient_recent_fashion_posts:"):
            continue
        if reason.startswith("latest_post_older_than_"):
            continue
        if reason.startswith("account_type_not_personal:professional_portfolio"):
            continue
        if reason == "high_audience_barter_review_required":
            continue
        if reason.startswith("insufficient_engagement_data:") and usable_posts < 6:
            continue
        if reason in {
            "post_format_data_missing",
            "target_theme_not_relevant",
            "identity_conflict",
            "profile_not_public",
            "profile_not_accessible",
        }:
            continue
        nonreviewable.append(reason)

    return (
        list(dict.fromkeys(reviewable)),
        list(dict.fromkeys(nonreviewable)),
    )


def build_near_miss_candidates(
    enriched_records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return ranked manual-review candidates without promoting any of them."""

    candidates: list[dict[str, Any]] = []
    for record in enriched_records:
        eligibility = _mapping(record.get("eligibility"))
        if bool(eligibility.get("eligible")):
            continue
        profile_raw = _mapping(record.get("profile"))
        try:
            profile = CreatorProfile.from_dict(profile_raw)
        except (TypeError, ValueError):
            continue
        reviewable, nonreviewable = _reason_groups(record, profile)
        if nonreviewable or not reviewable or len(reviewable) > 2:
            continue

        metrics = _mapping(record.get("metrics"))
        account = _mapping(record.get("account_type_assessment"))
        compatibility = _mapping(record.get("compatibility_assessment"))
        live = _mapping(record.get("live_campaign_assessment"))
        evidence = _mapping(record.get("evidence"))
        score_preview = _mapping(record.get("score_preview"))
        fashion_urls = _strings(live.get("recent_fashion_post_urls"))
        candidates.append(
            {
                "username": profile.identity.username,
                "profile_url": profile.identity.canonical_profile_url,
                "followers": metrics.get("followers"),
                "score": score_preview.get("score"),
                "discovery_confidence": record.get(
                    "discovery_confidence"
                ),
                "account_type": account.get("account_type", "unclear"),
                "detected_language": compatibility.get(
                    "detected_content_language", "undetermined"
                ),
                "detected_geography": compatibility.get(
                    "detected_geography"
                ),
                "usable_posts": int(metrics.get("usable_posts") or 0),
                "known_format_posts": known_format_posts(profile),
                "fashion_post_count": int(
                    live.get("recent_fashion_posts") or 0
                ),
                "short_video_count": int(
                    live.get("recent_short_video_posts") or 0
                ),
                "latest_post_date": metrics.get("last_post_date"),
                "original_exclusion_reasons": list(
                    _strings(eligibility.get("reasons"))
                ),
                "exact_saved_evidence": _exact_saved_evidence(
                    profile, evidence
                ),
                "reviewable_reasons": reviewable,
                "nonreviewable_reasons": [],
                "recent_fashion_post_url": (
                    fashion_urls[0] if fashion_urls else None
                ),
                "recommendation": (
                    "Open the saved profile and cited recent fashion post(s); "
                    "resolve only the listed borderline conditions before any "
                    "manual shortlist decision."
                ),
                "manual_review_status": "pending",
            }
        )
    candidates.sort(
        key=lambda item: (
            -(float(item["score"]) if item["score"] is not None else -1.0),
            str(item["username"]).casefold(),
        )
    )
    return candidates


__all__ = ["NEAR_MISS_COLUMNS", "build_near_miss_candidates"]
