from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .enrichment import known_format_posts, resolve_as_of
from .evidence import has_direct_target_content_evidence
from .models import (
    CandidateMetrics,
    CreatorProfile,
    EligibilityDecision,
    SignalEvidence,
)

if TYPE_CHECKING:
    from .account_types import AccountTypeAssessment

MIN_USABLE_ENGAGEMENT_POSTS = 6
MAX_RECENCY_DAYS = 90.0
MIN_KNOWN_FORMAT_POSTS = 1


def is_instagram_profile_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme in {"http", "https"}
        and host in {"instagram.com", "www.instagram.com"}
        and len(parts) == 1
        and parts[0].casefold() not in {"p", "reel", "reels", "tv"}
    )


def is_instagram_post_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme in {"http", "https"}
        and host in {"instagram.com", "www.instagram.com"}
        and len(parts) >= 2
        and parts[0].casefold() in {"p", "reel", "reels", "tv"}
        and bool(parts[1])
    )


def _has_recent_evidence_url(
    profile: CreatorProfile,
    *,
    as_of: datetime | None,
    max_recency_days: float,
) -> bool:
    effective_as_of = resolve_as_of(profile, as_of)
    for post in profile.recent_posts:
        if not is_instagram_post_url(post.url) or post.timestamp is None:
            continue
        timestamp = (
            post.timestamp.replace(tzinfo=effective_as_of.tzinfo)
            if post.timestamp.tzinfo is None
            else post.timestamp.astimezone(effective_as_of.tzinfo)
        )
        age_days = (effective_as_of - timestamp).total_seconds() / 86_400
        if 0 <= age_days <= max_recency_days:
            return True
    return False


def evaluate_candidate_eligibility(
    profile: CreatorProfile,
    metrics: CandidateMetrics,
    evidence: Mapping[str, Iterable[SignalEvidence]],
    *,
    excluded_reason: str | None = None,
    is_duplicate: bool = False,
    as_of: datetime | None = None,
    minimum_usable_posts: int = MIN_USABLE_ENGAGEMENT_POSTS,
    max_recency_days: float = MAX_RECENCY_DAYS,
    account_assessment: AccountTypeAssessment | None = None,
    require_known_post_format: bool = True,
) -> EligibilityDecision:
    reasons: list[str] = []

    if profile.identity.platform.casefold() != "instagram":
        reasons.append("unsupported_platform")
    if profile.private is not False:
        reasons.append("profile_not_public")
    if profile.accessible is not True:
        reasons.append("profile_not_accessible")
    if not is_instagram_profile_url(profile.identity.canonical_profile_url):
        reasons.append("invalid_profile_url")
    if "enriched record has no valid provider profile URL" in profile.validation_issues:
        reasons.append("provider_profile_url_missing_or_malformed")
    if excluded_reason:
        reasons.append(f"source_excluded:{excluded_reason}")
    if is_duplicate:
        reasons.append("duplicate_candidate")
    if account_assessment is not None:
        if account_assessment.account_type.value != "personal_creator":
            reasons.append(
                "account_type_not_personal:"
                f"{account_assessment.account_type.value}"
            )
        elif not account_assessment.theme_relevant:
            reasons.append("target_theme_not_relevant")
    if metrics.followers is None or metrics.followers <= 0:
        reasons.append("followers_missing_or_non_positive")
    if minimum_usable_posts < MIN_USABLE_ENGAGEMENT_POSTS:
        raise ValueError(
            f"minimum_usable_posts cannot be below "
            f"{MIN_USABLE_ENGAGEMENT_POSTS}"
        )
    if metrics.usable_posts < minimum_usable_posts:
        reasons.append(
            "insufficient_engagement_data:"
            f"{metrics.usable_posts}<{minimum_usable_posts}"
        )
    if metrics.last_post_date is None:
        reasons.append("last_post_date_missing")
    elif metrics.recency_days is None or metrics.recency_days > max_recency_days:
        reasons.append(f"latest_post_older_than_{max_recency_days:g}_days")
    if (
        account_assessment is None
        and not has_direct_target_content_evidence(evidence)
    ):
        reasons.append("target_content_evidence_missing")
    if (
        require_known_post_format
        and known_format_posts(profile) < MIN_KNOWN_FORMAT_POSTS
    ):
        reasons.append("post_format_data_missing")
    if not _has_recent_evidence_url(
        profile, as_of=as_of, max_recency_days=max_recency_days
    ):
        reasons.append("recent_evidence_url_missing")
    if profile.identity.identity_conflict:
        reasons.append("identity_conflict")

    if not reasons:
        return EligibilityDecision(eligible=True, status="eligible", reasons=())

    if profile.identity.identity_conflict:
        status = "needs_review"
    elif metrics.usable_posts < minimum_usable_posts:
        status = "insufficient_data"
    else:
        status = "ineligible"
    return EligibilityDecision(eligible=False, status=status, reasons=tuple(reasons))
