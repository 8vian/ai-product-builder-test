from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai_product_builder.phase_b.account_types import (
    AccountType,
    assess_account_type,
)
from ai_product_builder.phase_b.models import (
    CandidateIdentity,
    CreatorProfile,
    RecentPost,
)


def _profile(
    username: str,
    *,
    full_name: str = "",
    biography: str = "",
    captions: tuple[str, ...] = (),
) -> CreatorProfile:
    collected_at = datetime(2026, 7, 28, 19, tzinfo=timezone.utc)
    post_captions = captions or ("A recent personal update",)
    posts = tuple(
        RecentPost(
            post_id=f"{username}-{index}",
            url=f"https://www.instagram.com/p/{username.replace('.', '')}{index}/",
            caption=caption,
            likes=100,
            comments=10,
            timestamp=collected_at - timedelta(days=index),
            post_format="short_video",
        )
        for index, caption in enumerate(post_captions, start=1)
    )
    return CreatorProfile(
        identity=CandidateIdentity(
            platform="instagram",
            username=username,
            normalized_username=username.casefold(),
            profile_url=f"https://www.instagram.com/{username}/",
            canonical_profile_url=f"https://www.instagram.com/{username}/",
        ),
        full_name=full_name,
        biography=biography,
        followers=20_000,
        posts_count=100,
        private=False,
        accessible=True,
        recent_posts=posts,
        provider="fixture",
        collected_at=collected_at,
    )


@pytest.mark.parametrize(
    ("profile", "expected"),
    (
        (
            _profile(
                "official.label",
                full_name="Official Label",
                biography="Official account of Label. New clothing collection.",
            ),
            AccountType.BRAND,
        ),
        (
            _profile(
                "fashion.market",
                full_name="Fashion Market",
                biography="Marketplace for fashion products and clothing.",
            ),
            AccountType.MARKETPLACE,
        ),
        (
            _profile(
                "dress.shop",
                full_name="Dress Shop",
                biography="Online store. Shop now for dresses.",
            ),
            AccountType.STORE,
        ),
        (
            _profile(
                "creator.platform",
                full_name="Creator Platform",
                biography=(
                    "Connecting brands with creators. Join our creator platform."
                ),
            ),
            AccountType.AGENCY_OR_PLATFORM,
        ),
    ),
)
def test_institutional_account_types_are_not_personal(
    profile: CreatorProfile,
    expected: AccountType,
) -> None:
    assessment = assess_account_type(profile)
    assert assessment.account_type is expected
    assert assessment.eligible_account is False
    assert any(
        item.signal_type == "account_type_negative"
        for item in assessment.evidence
    )


def test_pet_page_with_beauty_word_is_rejected() -> None:
    profile = _profile(
        "beauty_cat_daily",
        full_name="Beauty And Bean",
        biography="Daily beauty cat videos. Cat mom and meow translator.",
        captions=(
            "Our kitten wakes up",
            "Daily cat routine",
            "The pet wants breakfast",
            "More meow content",
        ),
    )
    assessment = assess_account_type(profile)
    assert (
        assessment.account_type
        is AccountType.THEMATIC_NON_PERSONAL_PAGE
    )
    assert assessment.theme_relevant is False
    assert "pet" in assessment.negative_topics


def test_personal_beauty_creator_with_course_is_not_a_store() -> None:
    profile = _profile(
        "anna.beauty_",
        full_name="Anna Petrova",
        biography=(
            "Content creator and makeup artist. My online makeup course and "
            "own product are linked below."
        ),
        captions=("Skincare and makeup routine",),
    )
    assessment = assess_account_type(profile)
    assert assessment.account_type is AccountType.PERSONAL_CREATOR
    assert assessment.theme_relevant is True
    assert "beauty" in assessment.relevant_dimensions


def test_personal_fashion_creator_has_direct_topic_evidence() -> None:
    profile = _profile(
        "style.by_mira",
        full_name="Mira Chen",
        biography="Content creator sharing outfit try-ons and wardrobe ideas.",
    )
    assessment = assess_account_type(profile)
    assert assessment.account_type is AccountType.PERSONAL_CREATOR
    assert assessment.theme_relevant is True
    assert "fashion" in assessment.relevant_dimensions
    assert any(
        item.signal_type == "fashion"
        and item.observation_type == "direct"
        for item in assessment.evidence
    )


def test_single_generic_word_is_not_enough_for_theme_eligibility() -> None:
    profile = _profile(
        "generic.creator",
        full_name="Imane Zed",
        biography="Lifestyle | Creator of authentic reels | Business finance",
        captions=("A recent update", "Another reel"),
    )
    assessment = assess_account_type(profile)
    assert assessment.account_type is AccountType.PERSONAL_CREATOR
    assert assessment.theme_relevant is False


def test_unclear_account_stays_unclear() -> None:
    assessment = assess_account_type(_profile("dots._preserved"))
    assert assessment.account_type is AccountType.UNCLEAR
    assert assessment.eligible_account is False


def test_saved_profile_round_trip_preserves_punctuation_and_post_issues() -> None:
    profile = _profile(
        "dots._and__underscores",
        full_name="Mira Chen",
        biography="Content creator with outfit try-ons.",
    )
    post = profile.recent_posts[0]
    raw = profile.to_dict()
    raw["recent_posts"][0]["validation_issues"] = [
        "saved provider issue"
    ]
    restored = CreatorProfile.from_dict(raw)
    assert restored.identity.username == "dots._and__underscores"
    assert (
        restored.identity.canonical_profile_url
        == "https://www.instagram.com/dots._and__underscores/"
    )
    assert restored.recent_posts[0].post_id == post.post_id
    assert restored.recent_posts[0].validation_issues == (
        "saved provider issue",
    )
