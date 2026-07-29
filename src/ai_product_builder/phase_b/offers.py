from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime

from .eligibility import is_instagram_post_url
from .enrichment import resolve_as_of
from .errors import EvidenceValidationError
from .evidence import evidence_for_post
from .models import (
    CampaignBrief,
    CreatorProfile,
    ManualVerificationStatus,
    OfferDraft,
    RecentPost,
    SignalEvidence,
)

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_UNSUPPORTED_CLAIM_PATTERNS = (
    re.compile(r"\bвы любите\b", re.IGNORECASE),
    re.compile(r"\bты любишь\b", re.IGNORECASE),
    re.compile(r"\bвы принимаете бартер\b", re.IGNORECASE),
    re.compile(r"\bсогласны на бартер\b", re.IGNORECASE),
    re.compile(r"\bуже (?:использовали|пользовались)\b", re.IGNORECASE),
    re.compile(r"\bваша аудитория\b", re.IGNORECASE),
    re.compile(r"\bваши подписчики\b", re.IGNORECASE),
    re.compile(r"\bвысокая вовлеч", re.IGNORECASE),
    re.compile(r"\bгарантирован", re.IGNORECASE),
    re.compile(r"\byou (?:already )?love\b", re.IGNORECASE),
    re.compile(r"\byou accept barter\b", re.IGNORECASE),
    re.compile(r"\byour audience\b", re.IGNORECASE),
)


def safe_caption_topic(caption: str, *, maximum_length: int = 110) -> str:
    cleaned = _URL_PATTERN.sub("", caption)
    cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned).strip(" \t\r\n\"'«»")
    if not cleaned:
        raise EvidenceValidationError(
            "A caption-derived topic is required for a personalized offer."
        )
    if len(cleaned) <= maximum_length:
        return cleaned
    shortened = cleaned[: maximum_length - 1].rstrip(" ,.;:!?—-")
    return f"{shortened}…"


def _post_age_days(
    post: RecentPost, profile: CreatorProfile, as_of: datetime | None
) -> float | None:
    if post.timestamp is None:
        return None
    effective_as_of = resolve_as_of(profile, as_of)
    timestamp = (
        post.timestamp.replace(tzinfo=effective_as_of.tzinfo)
        if post.timestamp.tzinfo is None
        else post.timestamp.astimezone(effective_as_of.tzinfo)
    )
    return (effective_as_of - timestamp).total_seconds() / 86_400


def select_offer_post(
    profile: CreatorProfile,
    evidence: Mapping[str, Iterable[SignalEvidence]],
    *,
    as_of: datetime | None = None,
    max_age_days: float = 90.0,
    required_signal_types: Iterable[str] | None = None,
) -> RecentPost:
    allowed_signals = (
        frozenset(required_signal_types)
        if required_signal_types is not None
        else frozenset(
            {
                "fashion",
                "beauty",
                "lifestyle",
                "ugc",
                "marketplace",
                "native_product_integration",
            }
        )
    )
    evidenced_candidates: list[RecentPost] = []
    fallback_candidates: list[RecentPost] = []
    for post in profile.recent_posts:
        age_days = _post_age_days(post, profile, as_of)
        if (
            not is_instagram_post_url(post.url)
            or not post.caption.strip()
            or age_days is None
            or not 0 <= age_days <= max_age_days
        ):
            continue
        fallback_candidates.append(post)
        post_evidence = evidence_for_post(evidence, post)
        if any(
            item.observation_type == "direct"
            and item.signal_type in allowed_signals
            for item in post_evidence
        ):
            evidenced_candidates.append(post)
    candidates = (
        evidenced_candidates
        if required_signal_types is not None
        else evidenced_candidates or fallback_candidates
    )
    if not candidates:
        raise EvidenceValidationError(
            "No recent post has both a working Instagram URL and a caption."
        )
    return max(candidates, key=lambda item: item.timestamp)


def _russian_offer(
    profile: CreatorProfile,
    campaign: CampaignBrief,
    post: RecentPost,
    topic: str,
) -> str:
    greeting_name = profile.full_name.strip() or f"@{profile.identity.username}"
    return (
        f"Здравствуйте, {greeting_name}!\n\n"
        f"Увидели вашу недавнюю публикацию «{topic}»: {post.url}\n"
        f"Нам кажется, такой формат контента может естественно сочетаться с "
        f"категорией «{campaign.product_category}».\n\n"
        f"Мы — {campaign.brand_name}. Хотим предложить для обсуждения "
        f"{campaign.barter_item}: {campaign.product_name}. "
        f"Если идея вам интересна, возможный формат — "
        f"{campaign.desired_content_format}; детали и творческую подачу "
        "согласуем вместе.\n\n"
        "Будем рады обсудить предложение, если оно вам подходит; "
        "никаких обязательств до согласования условий нет.\n\n"
        "[Черновик: требуется ручная проверка и одобрение перед использованием.]"
    )


def _english_offer(
    profile: CreatorProfile,
    campaign: CampaignBrief,
    post: RecentPost,
    topic: str,
) -> str:
    greeting_name = profile.full_name.strip() or f"@{profile.identity.username}"
    return (
        f"Hello {greeting_name},\n\n"
        f"We noticed your recent post “{topic}”: {post.url}\n"
        f"Its format may be a natural fit for {campaign.product_category}.\n\n"
        f"We are {campaign.brand_name}, and would like to discuss "
        f"{campaign.barter_item}: {campaign.product_name}. If the idea is "
        f"interesting to you, a possible deliverable is "
        f"{campaign.desired_content_format}; we would agree the details and "
        "creative approach together.\n\n"
        "We would be happy to discuss it if it feels relevant—there is no "
        "obligation before the terms are agreed.\n\n"
        "[Draft: manual review and approval are required before use.]"
    )


def validate_offer_draft(
    draft: OfferDraft,
    *,
    profile: CreatorProfile,
    campaign: CampaignBrief,
) -> tuple[str, ...]:
    errors: list[str] = []
    profile_post_urls = {
        post.url for post in profile.recent_posts if post.url is not None
    }
    if draft.recent_post_url not in profile_post_urls:
        errors.append("recent_post_url_does_not_belong_to_candidate")
    if not is_instagram_post_url(draft.recent_post_url):
        errors.append("recent_post_url_is_not_valid_instagram_evidence")
    if draft.recent_post_url not in draft.text:
        errors.append("recent_post_url_missing_from_draft")
    for required_value, error in (
        (campaign.brand_name, "brand_name_missing"),
        (campaign.product_name, "product_name_missing"),
        (campaign.barter_item, "barter_item_missing"),
        (campaign.desired_content_format, "requested_format_missing"),
    ):
        if required_value not in draft.text:
            errors.append(error)
    if draft.manual_verification_status is not ManualVerificationStatus.PENDING:
        errors.append("manual_verification_status_must_be_pending")
    personalization = [
        item
        for item in draft.evidence
        if item.signal_type == "offer_personalization"
        and item.observation_type == "direct"
        and item.url == draft.recent_post_url
    ]
    if not personalization:
        errors.append("personalized_claim_has_no_direct_evidence")
    if not any(
        item.observation_type == "direct"
        and item.signal_type
        in {
            "fashion",
            "beauty",
            "lifestyle",
            "ugc",
            "marketplace",
            "native_product_integration",
        }
        for item in draft.evidence
    ):
        errors.append("content_fit_claim_has_no_direct_evidence")
    for pattern in _UNSUPPORTED_CLAIM_PATTERNS:
        if pattern.search(draft.text):
            errors.append(f"unsupported_claim:{pattern.pattern}")
    for claim in campaign.prohibited_claims:
        if claim.casefold() in draft.text.casefold():
            errors.append(f"prohibited_claim:{claim}")
    return tuple(dict.fromkeys(errors))


def generate_deterministic_offer(
    profile: CreatorProfile,
    campaign: CampaignBrief,
    evidence: Mapping[str, Iterable[SignalEvidence]],
    *,
    as_of: datetime | None = None,
    max_age_days: float = 90.0,
    required_signal_types: Iterable[str] | None = None,
) -> OfferDraft:
    post = select_offer_post(
        profile,
        evidence,
        as_of=as_of,
        max_age_days=max_age_days,
        required_signal_types=required_signal_types,
    )
    topic = safe_caption_topic(post.caption)
    text = (
        _russian_offer(profile, campaign, post, topic)
        if campaign.language.casefold().startswith("ru")
        else _english_offer(profile, campaign, post, topic)
    )
    personalization = SignalEvidence(
        signal_type="offer_personalization",
        source_field="recent_posts.caption",
        source_reference=post.post_id or post.url or "unknown",
        evidence_text=f"Caption-derived topic excerpt used: {topic}",
        observation_type="direct",
        url=post.url,
    )
    greeting_evidence = SignalEvidence(
        signal_type="offer_greeting",
        source_field="full_name",
        source_reference="profile",
        evidence_text=(
            f"Observed profile name used in greeting: {profile.full_name.strip()}"
            if profile.full_name.strip()
            else f"Observed username used in greeting: @{profile.identity.username}"
        ),
        observation_type="direct",
        url=profile.identity.profile_url,
    )
    supporting = tuple(
        dict.fromkeys(
            item
            for items in evidence.values()
            for item in items
            if item.observation_type == "direct"
            and (
                item in evidence_for_post(evidence, post)
                or item.signal_type
                in {
                    "fashion",
                    "beauty",
                    "lifestyle",
                    "ugc",
                    "marketplace",
                    "native_product_integration",
                }
            )
        )
    )
    draft = OfferDraft(
        text=text,
        generation_mode="deterministic_template",
        recent_post_url=post.url or "",
        evidence=(personalization, greeting_evidence, *supporting),
        validation_errors=(),
        manual_verification_status=ManualVerificationStatus.PENDING,
    )
    validation_errors = validate_offer_draft(
        draft, profile=profile, campaign=campaign
    )
    if validation_errors:
        raise EvidenceValidationError(
            "Deterministic offer failed factual-grounding validation.",
            details={"validation_errors": list(validation_errors)},
        )
    return draft
