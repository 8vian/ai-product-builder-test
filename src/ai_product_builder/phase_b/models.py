"""Typed, provider-neutral Phase B domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: Any) -> datetime | None:
    """Parse a provider timestamp without inventing a missing value."""

    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        # Instagram providers commonly use either seconds or milliseconds.
        seconds = float(value) / 1000 if abs(float(value)) > 10_000_000_000 else float(value)
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def non_negative_int(value: Any) -> int | None:
    """Return a non-negative integer; provider sentinels remain missing."""

    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in {"true", "yes", "1"}:
            return True
        if folded in {"false", "no", "0"}:
            return False
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value in {0, 1}
    ):
        return bool(value)
    return None


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_serialize(item) for item in value]
    return value


class ManualVerificationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    INSUFFICIENT_DATA = "insufficient_data"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class CampaignBrief:
    brand_name: str
    product_name: str
    product_category: str
    barter_item: str
    desired_content_format: str
    language: str
    tone: str
    geography: str | None = None
    target_content_languages: tuple[str, ...] = ()
    delivery_markets: tuple[str, ...] = ()
    prohibited_claims: tuple[str, ...] = ()
    preferred_geographies: tuple[str, ...] = ()
    collaboration_type: str = ""
    automatic_outreach: bool = False
    manual_review_required: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CampaignBrief":
        required = (
            "brand_name",
            "product_name",
            "product_category",
            "barter_item",
            "desired_content_format",
            "language",
            "tone",
        )
        values: dict[str, str] = {}
        for key in required:
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"campaign.{key} must be a non-empty string")
            values[key] = value.strip()
        geography = raw.get("geography")
        if geography is not None and not isinstance(geography, str):
            raise ValueError("campaign.geography must be a string or null")
        normalized_geography = (
            geography.strip() if isinstance(geography, str) else None
        )
        target_languages = _campaign_string_list(
            raw,
            "target_content_languages",
            fallback=(values["language"],),
        )
        delivery_markets = _campaign_string_list(
            raw,
            "delivery_markets",
            fallback=(
                (normalized_geography,) if normalized_geography else ()
            ),
            allow_empty=True,
        )
        if values["language"].casefold() not in {
            item.casefold() for item in target_languages
        }:
            raise ValueError(
                "campaign.language must be included in "
                "campaign.target_content_languages"
            )
        if normalized_geography and normalized_geography.casefold() not in {
            item.casefold() for item in delivery_markets
        }:
            raise ValueError(
                "campaign.geography must be included in "
                "campaign.delivery_markets"
            )
        claims = raw.get("prohibited_claims", ())
        if not isinstance(claims, (list, tuple)) or not all(
            isinstance(item, str) for item in claims
        ):
            raise ValueError("campaign.prohibited_claims must be an array of strings")
        preferred_geographies = _campaign_string_list(
            raw,
            "preferred_geographies",
            fallback=(),
            allow_empty=True,
        )
        collaboration_type = raw.get(
            "collaboration_type", values["barter_item"]
        )
        if (
            not isinstance(collaboration_type, str)
            or not collaboration_type.strip()
        ):
            raise ValueError(
                "campaign.collaboration_type must be a non-empty string"
            )
        automatic_outreach = raw.get("automatic_outreach", False)
        if automatic_outreach is not False:
            raise ValueError(
                "campaign.automatic_outreach must be false; Phase B never sends"
            )
        manual_review_required = raw.get(
            "manual_review_required", True
        )
        if manual_review_required is not True:
            raise ValueError(
                "campaign.manual_review_required must be true"
            )
        return cls(
            **values,
            geography=normalized_geography,
            target_content_languages=target_languages,
            delivery_markets=delivery_markets,
            prohibited_claims=tuple(item.strip() for item in claims if item.strip()),
            preferred_geographies=preferred_geographies,
            collaboration_type=collaboration_type.strip(),
            automatic_outreach=False,
            manual_review_required=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class QuerySpec:
    query_id: str
    query_family: str
    search_terms: tuple[str, ...]
    reason: str
    source_fields: tuple[str, ...]
    hashtags: tuple[str, ...] = ()
    geography: str | None = None
    audience_min: int | None = None
    audience_max: int | None = None
    required_signals: tuple[str, ...] = ()
    query_text_override: str | None = None

    @property
    def query_text(self) -> str:
        if self.query_text_override and self.query_text_override.strip():
            return self.query_text_override.strip()
        parts = [*self.search_terms, *(f"#{tag.lstrip('#')}" for tag in self.hashtags)]
        if self.geography:
            parts.append(self.geography)
        return " ".join(dict.fromkeys(part.strip() for part in parts if part.strip()))

    def to_dict(self) -> dict[str, Any]:
        result = _serialize(self)
        if result.get("query_text_override") is None:
            result.pop("query_text_override", None)
        result["query_text"] = self.query_text
        return result


@dataclass(frozen=True, slots=True)
class DiscoveryHit:
    platform: str
    username: str
    profile_url: str
    provider: str
    query_ids: tuple[str, ...] = ()
    display_name: str = ""
    biography: str = ""
    followers: int | None = None
    private: bool | None = None
    accessible: bool | None = None
    provider_identity_confidence: float = 0.0
    provider_id: str | None = None
    provider_run_id: str | None = None
    collected_at: datetime | None = None
    raw_payload: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    validation_issues: tuple[str, ...] = ()

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        result = _serialize(self)
        if not include_raw:
            result.pop("raw_payload", None)
        return result


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    platform: str
    username: str
    normalized_username: str
    profile_url: str
    canonical_profile_url: str
    query_ids: tuple[str, ...] = ()
    provider_ids: tuple[str, ...] = ()
    identity_conflict: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class RecentPost:
    post_id: str | None
    url: str | None
    caption: str
    likes: int | None
    comments: int | None
    timestamp: datetime | None
    post_format: str
    mentions: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()
    tagged_usernames: tuple[str, ...] = ()
    paid_partnership: bool = False
    validation_issues: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, index: int = 0) -> "RecentPost":
        issues: list[str] = []
        likes = non_negative_int(
            raw.get("likes", raw.get("likesCount", raw.get("like_count")))
        )
        comments = non_negative_int(
            raw.get("comments", raw.get("commentsCount", raw.get("comment_count")))
        )
        raw_likes = raw.get("likes", raw.get("likesCount", raw.get("like_count")))
        raw_comments = raw.get(
            "comments", raw.get("commentsCount", raw.get("comment_count"))
        )
        if raw_likes not in (None, "") and likes is None:
            issues.append(f"post[{index}].likes is invalid or a negative sentinel")
        if raw_comments not in (None, "") and comments is None:
            issues.append(f"post[{index}].comments is invalid or a negative sentinel")
        timestamp_raw = raw.get("timestamp", raw.get("date", raw.get("takenAt")))
        timestamp = parse_datetime(timestamp_raw)
        if timestamp_raw not in (None, "") and timestamp is None:
            issues.append(f"post[{index}].timestamp is invalid")
        post_format = str(
            raw.get("post_format")
            or raw.get("format")
            or raw.get("productType")
            or raw.get("type")
            or "unknown"
        ).casefold()
        if post_format in {"clips", "reel", "reels"}:
            post_format = "short_video"
        elif post_format == "sidecar":
            post_format = "carousel"
        elif post_format not in {"short_video", "video", "carousel", "image"}:
            post_format = "unknown"
        url = raw.get("url", raw.get("post_url"))
        issues.extend(_string_tuple(raw.get("validation_issues")))
        return cls(
            post_id=(
                str(raw.get("post_id") or raw.get("id") or raw.get("shortCode"))
                if raw.get("post_id") or raw.get("id") or raw.get("shortCode")
                else None
            ),
            url=str(url).strip() if isinstance(url, str) and url.strip() else None,
            caption=str(raw.get("caption") or ""),
            likes=likes,
            comments=comments,
            timestamp=timestamp,
            post_format=post_format,
            mentions=_string_tuple(raw.get("mentions")),
            hashtags=_string_tuple(raw.get("hashtags")),
            tagged_usernames=_tagged_tuple(
                raw.get("tagged_usernames", raw.get("taggedUsers"))
            ),
            paid_partnership=bool(
                optional_bool(
                    raw.get("paid_partnership", raw.get("paidPartnership", False))
                )
            ),
            validation_issues=tuple(dict.fromkeys(issues)),
        )

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class CreatorProfile:
    identity: CandidateIdentity
    full_name: str
    biography: str
    followers: int | None
    posts_count: int | None
    private: bool | None
    accessible: bool | None
    recent_posts: tuple[RecentPost, ...]
    external_urls: tuple[str, ...] = ()
    provider: str = "unknown"
    provider_run_ids: tuple[str, ...] = ()
    provider_identity_confidence: float = 0.0
    query_ids: tuple[str, ...] = ()
    collected_at: datetime | None = None
    validation_issues: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CreatorProfile":
        """Restore a normalized profile from a saved provider artifact.

        The loader is intentionally lossless for usernames and URLs: it does
        not strip punctuation, dots, or underscores from the saved identity.
        """

        identity_raw = raw.get("identity")
        if not isinstance(identity_raw, Mapping):
            raise ValueError("saved profile.identity must be an object")
        required_identity = (
            "platform",
            "username",
            "normalized_username",
            "profile_url",
            "canonical_profile_url",
        )
        missing = [
            field
            for field in required_identity
            if not isinstance(identity_raw.get(field), str)
            or not str(identity_raw.get(field)).strip()
        ]
        if missing:
            raise ValueError(
                "saved profile.identity is missing required field(s): "
                + ", ".join(missing)
            )
        recent_posts_raw = raw.get("recent_posts", ())
        if not isinstance(recent_posts_raw, (list, tuple)):
            raise ValueError("saved profile.recent_posts must be an array")
        if not all(isinstance(item, Mapping) for item in recent_posts_raw):
            raise ValueError(
                "saved profile.recent_posts entries must be objects"
            )
        return cls(
            identity=CandidateIdentity(
                platform=str(identity_raw["platform"]),
                username=str(identity_raw["username"]),
                normalized_username=str(identity_raw["normalized_username"]),
                profile_url=str(identity_raw["profile_url"]),
                canonical_profile_url=str(
                    identity_raw["canonical_profile_url"]
                ),
                query_ids=_string_tuple(identity_raw.get("query_ids")),
                provider_ids=_string_tuple(identity_raw.get("provider_ids")),
                identity_conflict=bool(
                    optional_bool(
                        identity_raw.get("identity_conflict", False)
                    )
                ),
            ),
            full_name=str(raw.get("full_name") or ""),
            biography=str(raw.get("biography") or ""),
            followers=non_negative_int(raw.get("followers")),
            posts_count=non_negative_int(raw.get("posts_count")),
            private=optional_bool(raw.get("private")),
            accessible=optional_bool(raw.get("accessible")),
            recent_posts=tuple(
                RecentPost.from_dict(item, index=index)
                for index, item in enumerate(recent_posts_raw)
            ),
            external_urls=_string_tuple(raw.get("external_urls")),
            provider=str(raw.get("provider") or "unknown"),
            provider_run_ids=_string_tuple(raw.get("provider_run_ids")),
            provider_identity_confidence=float(
                raw.get("provider_identity_confidence") or 0.0
            ),
            query_ids=_string_tuple(raw.get("query_ids")),
            collected_at=parse_datetime(raw.get("collected_at")),
            validation_issues=_string_tuple(raw.get("validation_issues")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class CandidateMetrics:
    followers: int | None
    median_likes: float | None
    median_comments: float | None
    engagement_rate: float | None
    usable_posts: int
    sampled_posts: int
    data_completeness: float
    short_video_share: float | None
    last_post_date: datetime | None
    posts_per_week: float | None = None
    recency_days: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class SignalEvidence:
    signal_type: str
    source_field: str
    source_reference: str
    evidence_text: str
    observation_type: str = "direct"
    url: str | None = None

    def __post_init__(self) -> None:
        if self.observation_type not in {"direct", "derived"}:
            raise ValueError("observation_type must be 'direct' or 'derived'")

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligible: bool
    status: EligibilityStatus | str
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        status = (
            self.status
            if isinstance(self.status, EligibilityStatus)
            else EligibilityStatus(self.status)
        )
        object.__setattr__(self, "status", status)
        if self.eligible != (status is EligibilityStatus.ELIGIBLE):
            raise ValueError("eligible flag and eligibility status disagree")

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    name: str
    score: float
    max_score: float
    explanation: str
    evidence: tuple[SignalEvidence, ...] = ()

    def __post_init__(self) -> None:
        if self.max_score < 0 or not 0 <= self.score <= self.max_score:
            raise ValueError(f"{self.name} score must be between 0 and max_score")

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class CandidateScore:
    score: float
    components: tuple[ScoreComponent, ...]
    explanation: str

    def __post_init__(self) -> None:
        component_total = sum(component.score for component in self.components)
        if abs(self.score - component_total) > 0.011:
            raise ValueError(
                f"candidate score {self.score} does not equal component sum "
                f"{component_total}"
            )
        if not 0 <= self.score <= 100:
            raise ValueError("candidate score must be in the range 0-100")

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class OfferDraft:
    text: str
    generation_mode: str
    recent_post_url: str
    evidence: tuple[SignalEvidence, ...] = ()
    validation_errors: tuple[str, ...] = ()
    manual_verification_status: ManualVerificationStatus | str = (
        ManualVerificationStatus.PENDING
    )

    def __post_init__(self) -> None:
        status = (
            self.manual_verification_status
            if isinstance(self.manual_verification_status, ManualVerificationStatus)
            else ManualVerificationStatus(self.manual_verification_status)
        )
        object.__setattr__(self, "manual_verification_status", status)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True, slots=True)
class CandidateResult:
    platform: str
    username: str
    profile_url: str
    followers: int | None
    median_likes: float | None
    median_comments: float | None
    engagement_rate: float | None
    usable_posts: int
    sampled_posts: int
    data_completeness: float
    short_video_share: float | None
    last_post_date: datetime | None
    score: float
    score_components: tuple[ScoreComponent, ...]
    selection_explanation: str
    evidence: tuple[SignalEvidence, ...]
    recent_post_url: str
    barter_offer: str
    manual_verification_status: ManualVerificationStatus | str
    verification_notes: str
    discovery_confidence: float
    eligibility_status: EligibilityStatus | str
    eligibility_reasons: tuple[str, ...]
    query_ids: tuple[str, ...]
    provider: str
    collected_at: datetime
    offer_generation_mode: str
    source_exclusion_check: str
    account_type: str = "unclear"
    account_type_explanation: str = ""
    barter_feasibility_review_required: bool = False
    barter_feasibility_explanation: str = ""
    content_themes: tuple[str, ...] = ()
    known_format_posts: int = 0
    detected_content_language: str = "undetermined"
    campaign_language_compatible: bool = False
    detected_geography: str | None = None
    delivery_market_review_required: bool = True
    compatibility_explanation: str = ""
    barter_evidence: tuple[SignalEvidence, ...] = ()
    no_barter_evidence: tuple[SignalEvidence, ...] = ()
    campaign_bucket: str = ""
    campaign_status_reasons: tuple[str, ...] = ()
    alternative_campaign_note: str = ""
    outreach_status: str = "not_sent"

    def __post_init__(self) -> None:
        manual_status = (
            self.manual_verification_status
            if isinstance(self.manual_verification_status, ManualVerificationStatus)
            else ManualVerificationStatus(self.manual_verification_status)
        )
        eligibility_status = (
            self.eligibility_status
            if isinstance(self.eligibility_status, EligibilityStatus)
            else EligibilityStatus(self.eligibility_status)
        )
        object.__setattr__(self, "manual_verification_status", manual_status)
        object.__setattr__(self, "eligibility_status", eligibility_status)
        if not 0 <= self.discovery_confidence <= 1:
            raise ValueError("discovery_confidence must be in the range 0-1")
        if not 0 <= self.data_completeness <= 1:
            raise ValueError("data_completeness must be in the range 0-1")
        if abs(self.score - sum(item.score for item in self.score_components)) > 0.011:
            raise ValueError("score must equal score_components total")
        allowed_account_types = {
            "personal_creator",
            "brand",
            "marketplace",
            "store",
            "showroom",
            "agency_or_platform",
            "thematic_non_personal_page",
            "professional_portfolio",
            "unclear",
        }
        if self.account_type not in allowed_account_types:
            raise ValueError(f"unsupported account_type: {self.account_type}")
        allowed_buckets = {
            "",
            "barter_ready",
            "needs_manual_review",
            "ineligible_or_insufficient",
        }
        if self.campaign_bucket not in allowed_buckets:
            raise ValueError(
                f"unsupported campaign_bucket: {self.campaign_bucket}"
            )
        if self.outreach_status != "not_sent":
            raise ValueError(
                "Phase B supports drafts only; outreach_status must be not_sent"
            )

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(slots=True)
class PhaseBRunManifest:
    run_id: str
    mode: str
    status: str
    started_at: datetime
    provider: str
    config_digest: str
    completed_at: datetime | None = None
    provider_run_ids: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    cache: dict[str, int] = field(default_factory=dict)
    offline_reselection: bool = False
    source_run_id: str | None = None
    source_run_path: str | None = None
    provider_requests_made: int = 0
    budget_spent_usd: float = 0.0
    review_summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def started(
        cls, *, run_id: str, mode: str, provider: str, config_digest: str
    ) -> "PhaseBRunManifest":
        return cls(
            run_id=run_id,
            mode=mode,
            status="running",
            started_at=_utc_now(),
            provider=provider,
            config_digest=config_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


def _campaign_string_list(
    raw: Mapping[str, Any],
    key: str,
    *,
    fallback: tuple[str, ...],
    allow_empty: bool = False,
) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None:
        return fallback
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(
            f"campaign.{key} must be an array of non-empty strings"
        )
    normalized = tuple(
        dict.fromkeys(item.strip() for item in value)
    )
    if not normalized and not allow_empty:
        raise ValueError(f"campaign.{key} must not be empty")
    return normalized


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    )


def _tagged_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            username = item.get("username")
            if username:
                result.append(str(username))
        elif item not in (None, ""):
            result.append(str(item))
    return tuple(result)
