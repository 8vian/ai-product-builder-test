from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from ai_product_builder.analysis import (
    audience_barter_points,
    frequency_points,
    recency_points,
)

from .eligibility import (
    MIN_USABLE_ENGAGEMENT_POSTS,
    is_instagram_post_url,
    is_instagram_profile_url,
)
from .enrichment import normalized_post_format
from .evidence import (
    TARGET_CONTENT_SIGNALS,
    count_evidenced_posts,
    signal_is_present,
)
from .models import (
    CandidateMetrics,
    CandidateScore,
    CreatorProfile,
    ScoreComponent,
    SignalEvidence,
)

COMPONENT_MAXIMUMS = {
    "content_and_aesthetic_fit": 30.0,
    "native_product_integration_potential": 20.0,
    "short_video_consistency": 15.0,
    "engagement": 15.0,
    "barter_feasibility": 10.0,
    "recent_activity": 10.0,
}

TOPIC_WEIGHTS = {
    "fashion": 7.0,
    "beauty": 5.0,
    "lifestyle": 4.0,
    "ugc": 5.0,
    "marketplace": 3.0,
}


@dataclass(frozen=True, slots=True)
class PhaseAReferenceCohort:
    engagement_rates: tuple[float, ...]
    brand_short_video_reference: float | None
    source_csv: str
    eligible_creator_count: int


@dataclass(frozen=True, slots=True)
class DiscoveryConfidence:
    score: float
    components: dict[str, float]
    explanation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "components": dict(self.components),
            "explanation": self.explanation,
        }


def load_phase_a_reference(
    source_csv: str | Path,
    ideal_profile_json: str | Path | None = None,
) -> PhaseAReferenceCohort:
    source_path = Path(source_csv)
    rates: list[float] = []
    eligible_count = 0
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "creator":
                continue
            eligible_count += 1
            raw_rate = (row.get("engagement_rate_pct") or "").strip()
            if raw_rate:
                rates.append(float(raw_rate))
    if not rates:
        raise ValueError(
            f"{source_path}: no eligible Phase A engagement reference values"
        )

    brand_reference: float | None = None
    if ideal_profile_json is not None:
        ideal_path = Path(ideal_profile_json)
        payload = json.loads(ideal_path.read_text(encoding="utf-8"))
        brand_values = [
            float(item["short_video_share"])
            for item in payload.get("brand_reference_signals", [])
            if item.get("short_video_share") is not None
        ]
        if brand_values:
            brand_reference = float(median(brand_values))
    return PhaseAReferenceCohort(
        engagement_rates=tuple(sorted(rates)),
        brand_short_video_reference=brand_reference,
        source_csv=str(source_path),
        eligible_creator_count=eligible_count,
    )


def percentile_against_reference(
    value: float, reference_values: Iterable[float]
) -> float:
    """Return a stable 0..1 percentile against a frozen reference cohort.

    Exact ties use the Phase A average-rank rule. Values between observed
    reference points use linear interpolation without adding the candidate to
    the cohort, so discovery-pool composition cannot change the score.
    """

    ordered = sorted(float(item) for item in reference_values)
    if not ordered:
        raise ValueError("reference cohort must contain at least one value")
    if len(ordered) == 1:
        return 0.0 if value < ordered[0] else 1.0

    denominator = len(ordered) - 1
    groups: list[tuple[float, float]] = []
    start = 0
    while start < len(ordered):
        end = start
        while end + 1 < len(ordered) and math.isclose(
            ordered[end + 1],
            ordered[start],
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            end += 1
        groups.append((ordered[start], ((start + end) / 2) / denominator))
        start = end + 1

    for reference_value, percentile in groups:
        if math.isclose(
            reference_value, value, rel_tol=1e-12, abs_tol=1e-12
        ):
            return percentile
    if value < groups[0][0]:
        return 0.0
    if value > groups[-1][0]:
        return 1.0

    for index in range(1, len(groups)):
        lower_value, lower_percentile = groups[index - 1]
        upper_value, upper_percentile = groups[index]
        if lower_value < value < upper_value:
            fraction = (value - lower_value) / (upper_value - lower_value)
            return lower_percentile + (
                upper_percentile - lower_percentile
            ) * fraction
    return 1.0


def _signal_evidence(
    evidence: Mapping[str, Iterable[SignalEvidence]], *signal_types: str
) -> tuple[SignalEvidence, ...]:
    return tuple(
        item
        for signal_type in signal_types
        for item in evidence.get(signal_type, ())
        if item.observation_type == "direct"
    )


def _derived_evidence(
    profile: CreatorProfile, component: str, reason: str
) -> SignalEvidence:
    return SignalEvidence(
        signal_type=component,
        source_field="candidate_metrics",
        source_reference=profile.identity.username,
        evidence_text=reason,
        observation_type="derived",
        url=profile.identity.profile_url,
    )


def score_candidate(
    profile: CreatorProfile,
    metrics: CandidateMetrics,
    evidence: Mapping[str, Iterable[SignalEvidence]],
    reference: PhaseAReferenceCohort,
) -> CandidateScore:
    if metrics.sampled_posts <= 0 or metrics.usable_posts > metrics.sampled_posts:
        raise ValueError("engagement observation counts are inconsistent")
    expected_completeness = metrics.usable_posts / metrics.sampled_posts
    if not math.isclose(
        metrics.data_completeness,
        expected_completeness,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError(
            "data_completeness must equal usable_posts / sampled_posts"
        )
    if metrics.usable_posts < MIN_USABLE_ENGAGEMENT_POSTS:
        raise ValueError(
            f"candidate is insufficient_data: fewer than "
            f"{MIN_USABLE_ENGAGEMENT_POSTS} usable engagement posts"
        )

    detected = {
        name: signal_is_present(evidence, name)
        for name in (
            *TOPIC_WEIGHTS,
            "commercial_pr",
            "contact",
            "native_product_integration",
        )
    }

    topic_points = sum(
        weight for name, weight in TOPIC_WEIGHTS.items() if detected[name]
    )
    format_points = 0.0
    if (metrics.short_video_share or 0.0) > 0:
        format_points += 3.0
    if any(
        normalized_post_format(post) in {"video", "carousel", "image"}
        for post in profile.recent_posts
    ):
        format_points += 1.0
    if any(post.caption.strip() for post in profile.recent_posts):
        format_points += 1.0
    brand_alignment = 0.0
    if (
        reference.brand_short_video_reference is not None
        and metrics.short_video_share is not None
    ):
        brand_alignment = max(
            0.0,
            1.0
            - abs(
                metrics.short_video_share
                - reference.brand_short_video_reference
            ),
        )
    content_score = min(30.0, topic_points + format_points + brand_alignment)
    content_reason = (
        f"{topic_points:.2f}/24 topical + {format_points:.2f}/5 format readiness "
        f"+ {brand_alignment:.2f}/1 Nike/Apple format alignment."
    )

    integration_posts = count_evidenced_posts(
        evidence, "native_product_integration"
    )
    integration_ratio = (
        integration_posts / metrics.sampled_posts if metrics.sampled_posts else 0.0
    )
    integration_score = min(
        20.0,
        (6.0 if detected["ugc"] else 0.0)
        + (4.0 if detected["marketplace"] else 0.0)
        + (4.0 if detected["commercial_pr"] else 0.0)
        + min(4.0, integration_ratio * 8.0)
        + (2.0 if detected["contact"] else 0.0),
    )
    integration_reason = (
        f"UGC={detected['ugc']}, marketplace={detected['marketplace']}, "
        f"commercial/PR={detected['commercial_pr']}, contact={detected['contact']}; "
        f"{integration_posts}/{metrics.sampled_posts} posts have independently "
        "observed native-integration evidence."
    )

    short_share = metrics.short_video_share or 0.0
    short_score = min(15.0, max(0.0, short_share) * 15.0)
    short_reason = (
        f"Short video share {short_share:.3f} × 15 = {short_score:.2f}."
    )

    engagement_percentile = (
        percentile_against_reference(
            metrics.engagement_rate, reference.engagement_rates
        )
        if metrics.engagement_rate is not None
        else 0.0
    )
    raw_engagement_score = engagement_percentile * 15.0
    confidence = expected_completeness
    engagement_score = raw_engagement_score * confidence
    engagement_reason = (
        f"Frozen Phase A percentile {engagement_percentile:.6f} × 15 = "
        f"{raw_engagement_score:.2f} raw; × completeness "
        f"{metrics.usable_posts}/{metrics.sampled_posts} ({confidence:.6f}) = "
        f"{engagement_score:.2f} adjusted."
    )

    audience_points, audience_label = audience_barter_points(metrics.followers)
    barter_score = min(
        10.0,
        audience_points
        + (2.0 if detected["commercial_pr"] else 0.0)
        + (2.0 if detected["ugc"] else 0.0)
        + (1.0 if detected["marketplace"] else 0.0),
    )
    barter_reason = (
        f"{audience_points:.1f}/5 for {audience_label}; "
        f"commercial/PR={detected['commercial_pr']} (+2), "
        f"UGC={detected['ugc']} (+2), marketplace={detected['marketplace']} (+1)."
    )

    recency_score = recency_points(metrics.recency_days)
    posting_frequency_score = frequency_points(metrics.posts_per_week)
    activity_score = recency_score + posting_frequency_score
    activity_reason = (
        f"{recency_score:.2f}/6 recency + "
        f"{posting_frequency_score:.2f}/4 posting frequency."
    )

    components = (
        ScoreComponent(
            name="content_and_aesthetic_fit",
            score=round(content_score, 2),
            max_score=30.0,
            explanation=content_reason,
            evidence=(
                *_signal_evidence(evidence, *TOPIC_WEIGHTS),
                _derived_evidence(
                    profile, "content_and_aesthetic_fit", content_reason
                ),
            ),
        ),
        ScoreComponent(
            name="native_product_integration_potential",
            score=round(integration_score, 2),
            max_score=20.0,
            explanation=integration_reason,
            evidence=(
                *_signal_evidence(
                    evidence,
                    "ugc",
                    "marketplace",
                    "commercial_pr",
                    "contact",
                    "native_product_integration",
                ),
                _derived_evidence(
                    profile,
                    "native_product_integration_potential",
                    integration_reason,
                ),
            ),
        ),
        ScoreComponent(
            name="short_video_consistency",
            score=round(short_score, 2),
            max_score=15.0,
            explanation=short_reason,
            evidence=(
                _derived_evidence(
                    profile, "short_video_consistency", short_reason
                ),
            ),
        ),
        ScoreComponent(
            name="engagement",
            score=round(engagement_score, 2),
            max_score=15.0,
            explanation=engagement_reason,
            evidence=(
                _derived_evidence(profile, "engagement", engagement_reason),
            ),
        ),
        ScoreComponent(
            name="barter_feasibility",
            score=round(barter_score, 2),
            max_score=10.0,
            explanation=barter_reason,
            evidence=(
                *_signal_evidence(
                    evidence, "commercial_pr", "ugc", "marketplace"
                ),
                _derived_evidence(
                    profile, "barter_feasibility", barter_reason
                ),
            ),
        ),
        ScoreComponent(
            name="recent_activity",
            score=round(activity_score, 2),
            max_score=10.0,
            explanation=activity_reason,
            evidence=(
                _derived_evidence(profile, "recent_activity", activity_reason),
            ),
        ),
    )
    total = round(sum(item.score for item in components), 2)
    strongest = max(
        components, key=lambda item: item.score / item.max_score
    )
    weakest = min(
        components, key=lambda item: item.score / item.max_score
    )
    return CandidateScore(
        score=total,
        components=components,
        explanation=(
            f"Total {total:.2f}/100. Strongest relative component: "
            f"{strongest.name} ({strongest.score:.2f}/{strongest.max_score:.0f}); "
            f"lowest: {weakest.name} "
            f"({weakest.score:.2f}/{weakest.max_score:.0f})."
        ),
    )


def calculate_discovery_confidence(
    profile: CreatorProfile,
    evidence: Mapping[str, Iterable[SignalEvidence]],
) -> DiscoveryConfidence:
    identity = min(1.0, max(0.0, profile.provider_identity_confidence))
    query_count = len(set((*profile.query_ids, *profile.identity.query_ids)))
    multi_query = min(1.0, query_count / 3.0)
    profile_link = is_instagram_profile_url(
        profile.identity.canonical_profile_url
    )
    post_link = any(is_instagram_post_url(post.url) for post in profile.recent_posts)
    link_verification = (float(profile_link) + float(post_link)) / 2.0
    relevant_signals = sum(
        signal_is_present(evidence, name) for name in TARGET_CONTENT_SIGNALS
    )
    query_relevance = min(1.0, relevant_signals / 3.0)
    components = {
        "provider_identity_confidence": identity,
        "multi_query_support": multi_query,
        "profile_and_post_link_verification": link_verification,
        "query_relevance_evidence": query_relevance,
    }
    score = (
        0.40 * identity
        + 0.20 * multi_query
        + 0.20 * link_verification
        + 0.20 * query_relevance
    )
    return DiscoveryConfidence(
        score=round(score, 6),
        components={name: round(value, 6) for name, value in components.items()},
        explanation=(
            "0.40×provider identity "
            f"({identity:.3f}) + 0.20×multi-query support "
            f"({multi_query:.3f}; {query_count} distinct queries, full credit at 3) "
            f"+ 0.20×profile/post link verification ({link_verification:.3f}) "
            f"+ 0.20×query relevance ({query_relevance:.3f}; "
            f"{relevant_signals} directly evidenced target dimensions) "
            f"= {score:.3f}."
        ),
    )
