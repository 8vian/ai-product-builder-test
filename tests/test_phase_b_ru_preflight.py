from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_product_builder.phase_b.compatibility import assess_compatibility
from ai_product_builder.phase_b.config import load_phase_b_config
from ai_product_builder.phase_b.errors import ConfigurationError
from ai_product_builder.phase_b.exclusions import ExclusionMatch, ExclusionRegistry
from ai_product_builder.phase_b.models import (
    CampaignBrief,
    CandidateIdentity,
    CreatorProfile,
    DiscoveryHit,
    EligibilityDecision,
    RecentPost,
)
from ai_product_builder.phase_b.pipeline import (
    _apply_live_campaign_guards,
    _phase_a_follower_outer_fence,
    deduplicate_discovery_hits,
)
from ai_product_builder.phase_b.queries import generate_queries


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RU_QUERIES = [
    "блогер женская одежда стиль образы Россия",
    "fashion блогер примерки одежды Россия",
    "UGC креатор одежда Россия",
    "обзор одежды Wildberries российский блогер",
    "Reels примерка нативный обзор одежды Россия",
    "стилист женские образы Москва",
    "блогер мода красота лайфстайл Россия",
]


def _campaign() -> CampaignBrief:
    return CampaignBrief(
        brand_name="LD Latte",
        product_name="новая коллекция",
        product_category="женская одежда",
        barter_item="товар в обмен на согласованный контент",
        desired_content_format="Reels или нативный обзор",
        language="ru",
        target_content_languages=("ru",),
        tone="дружелюбный",
        geography="Россия",
        delivery_markets=("Россия",),
    )


def _profile(
    *,
    username: str = "russian.creator_",
    biography: str,
    captions: tuple[str, ...],
) -> CreatorProfile:
    collected_at = datetime(2026, 7, 29, tzinfo=timezone.utc)
    posts = tuple(
        RecentPost(
            post_id=f"post-{index}",
            url=f"https://www.instagram.com/reel/ru{index}/",
            caption=caption,
            likes=100 + index,
            comments=10 + index,
            timestamp=collected_at - timedelta(days=index),
            post_format="short_video",
        )
        for index, caption in enumerate(captions)
    )
    return CreatorProfile(
        identity=CandidateIdentity(
            platform="instagram",
            username=username,
            normalized_username=username.casefold(),
            profile_url=f"https://www.instagram.com/{username}/",
            canonical_profile_url=f"https://www.instagram.com/{username}/",
        ),
        full_name="Personal Creator",
        biography=biography,
        followers=20_000,
        posts_count=100,
        private=False,
        accessible=True,
        recent_posts=posts,
        collected_at=collected_at,
    )


def test_ru_russia_query_generation_uses_exact_approved_texts() -> None:
    ideal = json.loads(
        (ROOT / "output/ideal_creator_profile.json").read_text(
            encoding="utf-8"
        )
    )

    queries = generate_queries(ideal, _campaign())

    assert [item.query_text for item in queries] == EXPECTED_RU_QUERIES
    assert len(queries) == 7
    assert all(item.geography == "Россия" for item in queries)
    assert all(item.audience_min == 9781 for item in queries)
    assert all(item.audience_max == 82754 for item in queries)


def test_campaign_and_run_level_exclusions_are_typed_and_validated(
    tmp_path: Path,
) -> None:
    raw = json.loads(
        (ROOT / "config/phase_b.live.example.json").read_text(
            encoding="utf-8"
        )
    )
    raw["campaign"].update(
        {
            "geography": "Россия",
            "target_content_languages": ["ru"],
            "delivery_markets": ["Россия"],
        }
    )
    raw["run_level_exclusions"] = [
        {
            "username": "dots.and_under__",
            "profile_url": (
                "https://www.instagram.com/dots.and_under__/"
            ),
            "reason": "previous_live_run_exclusion",
        }
    ]
    path = tmp_path / "phase_b.live.ru.json"
    path.write_text(
        json.dumps(raw, ensure_ascii=False),
        encoding="utf-8",
    )

    config = load_phase_b_config(path)

    assert config.campaign.target_content_languages == ("ru",)
    assert config.campaign.delivery_markets == ("Россия",)
    assert config.run_level_exclusions[0].username == "dots.and_under__"
    assert config.run_level_exclusions[0].reason == (
        "previous_live_run_exclusion"
    )

    raw["run_level_exclusions"][0]["profile_url"] = (
        "https://www.instagram.com/different/"
    )
    path.write_text(
        json.dumps(raw, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="identity mismatch"):
        load_phase_b_config(path)


def test_previous_live_run_exclusion_has_dedicated_reason() -> None:
    registry = ExclusionRegistry(()).extended(
        (
            ExclusionMatch(
                normalized_username="seen.creator_",
                canonical_profile_url=(
                    "https://www.instagram.com/seen.creator_/"
                ),
                reasons=("previous_live_run_exclusion",),
                source_values=(
                    "seen.creator_",
                    "https://www.instagram.com/seen.creator_/",
                ),
            ),
        )
    )
    hit = DiscoveryHit(
        platform="instagram",
        username="seen.creator_",
        profile_url="https://www.instagram.com/seen.creator_/",
        provider="fixture",
    )

    result = deduplicate_discovery_hits((hit,), registry)

    assert result.identities == ()
    assert result.excluded_records[0]["exclusion_reason"] == (
        "previous_live_run_exclusion"
    )
    assert result.excluded_records[0]["exclusion_reasons"] == [
        "previous_live_run_exclusion"
    ]


def test_russian_language_requires_lexical_not_only_cyrillic_evidence() -> None:
    russian = _profile(
        biography="Я блогер: мода, одежда и женские образы. Москва.",
        captions=("Новая примерка одежды и мой образ.",) * 6,
    )
    undetermined = _profile(
        username="cyrillic.creator_",
        biography="ЖҚҰ ӘҒӨ ҮҚҢ",
        captions=("ЖҚҰ ӘҒӨ ҮҚҢ",) * 6,
    )

    russian_result = assess_compatibility(russian, _campaign())
    undetermined_result = assess_compatibility(
        undetermined,
        _campaign(),
    )

    assert russian_result.campaign_language_compatible is True
    assert russian_result.detected_geography == "Москва, Россия"
    assert russian_result.delivery_market_conflict is False
    assert undetermined_result.detected_content_language == (
        "cyrillic_undetermined"
    )
    assert undetermined_result.campaign_language_compatible is False


def test_direct_foreign_geography_conflicts_with_russia_delivery() -> None:
    profile = _profile(
        biography="I am a fashion creator from Kolkata.",
        captions=("Fashion clothing outfit and try-on review.",) * 6,
    )

    result = assess_compatibility(profile, _campaign())

    assert result.detected_geography == "Kolkata"
    assert result.delivery_market_conflict is True


@pytest.mark.parametrize(
    (
        "refusal",
        "language",
        "delivery_conflict",
        "high_audience",
        "expected_status",
        "expected_reason",
    ),
    [
        (
            True,
            True,
            False,
            False,
            "ineligible",
            "explicit_no_barter_statement",
        ),
        (
            False,
            False,
            False,
            False,
            "ineligible",
            "campaign_language_mismatch",
        ),
        (
            False,
            True,
            True,
            False,
            "ineligible",
            "delivery_market_conflict",
        ),
        (
            False,
            True,
            False,
            True,
            "needs_review",
            "high_audience_barter_review_required",
        ),
    ],
)
def test_live_campaign_guards_prevent_automatic_selection(
    refusal: bool,
    language: bool,
    delivery_conflict: bool,
    high_audience: bool,
    expected_status: str,
    expected_reason: str,
) -> None:
    decision = _apply_live_campaign_guards(
        EligibilityDecision(eligible=True, status="eligible"),
        explicit_barter_refusal=refusal,
        language_compatible=language,
        delivery_market_conflict=delivery_conflict,
        high_audience=high_audience,
    )

    assert decision.status.value == expected_status
    assert expected_reason in decision.reasons


def test_phase_a_outer_fence_remains_frozen_reference_rule() -> None:
    ideal = json.loads(
        (ROOT / "output/ideal_creator_profile.json").read_text(
            encoding="utf-8"
        )
    )

    q1, q3, threshold = _phase_a_follower_outer_fence(ideal)

    assert (q1, q3, threshold) == (9781.0, 82754.5, 301675.0)
