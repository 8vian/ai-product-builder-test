"""Deterministic discovery-query generation from frozen Phase A evidence."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import InputValidationError
from .models import CampaignBrief, QuerySpec


def generate_queries(
    ideal_profile_document: Mapping[str, Any],
    campaign: CampaignBrief,
) -> list[QuerySpec]:
    """Create the fixed MVP query families without source-account seeds."""

    ideal = ideal_profile_document.get(
        "ideal_creator_profile", ideal_profile_document
    )
    if not isinstance(ideal, Mapping):
        raise InputValidationError("ideal_creator_profile must be an object")
    audience = ideal.get("audience")
    formats = ideal.get("formats")
    prevalence = ideal.get("signal_prevalence")
    if not isinstance(audience, Mapping) or not isinstance(formats, Mapping):
        raise InputValidationError(
            "ideal profile is missing audience or format distributions"
        )
    followers = audience.get("followers")
    short_video = formats.get("short_video_share")
    if not isinstance(followers, Mapping) or not isinstance(short_video, Mapping):
        raise InputValidationError(
            "ideal profile is missing follower or short-video quartiles"
        )
    audience_min = _non_negative_int(followers.get("q1"), "followers.q1")
    audience_max = _non_negative_int(followers.get("q3"), "followers.q3")
    if audience_max < audience_min:
        raise InputValidationError("followers.q3 cannot be below followers.q1")
    target_share = _bounded_float(
        short_video.get("median"), "short_video_share.median"
    )
    if prevalence is not None and not isinstance(prevalence, Mapping):
        raise InputValidationError("signal_prevalence must be an object")

    category = campaign.product_category
    geography = campaign.geography
    shared_sources = (
        "ideal_creator_profile.audience.followers.q1",
        "ideal_creator_profile.audience.followers.q3",
    )
    return [
        QuerySpec(
            query_id="fashion_style_01",
            query_family="fashion_style",
            search_terms=(category, "мода", "стиль", "образы"),
            hashtags=("стиль", "женскаяодежда"),
            reason=(
                "Find fashion/style creators within the Phase A audience IQR "
                f"({audience_min}-{audience_max} followers)."
            ),
            source_fields=(
                *shared_sources,
                "ideal_creator_profile.signal_prevalence.fashion",
                "campaign.product_category",
            ),
            geography=geography,
            audience_min=audience_min,
            audience_max=audience_max,
            required_signals=("fashion",),
        ),
        QuerySpec(
            query_id="beauty_lifestyle_01",
            query_family="beauty_lifestyle",
            search_terms=("beauty", "lifestyle", category),
            hashtags=("бьютиблог", "лайфстайл"),
            reason="Extend fashion discovery into adjacent beauty/lifestyle content.",
            source_fields=(
                *shared_sources,
                "ideal_creator_profile.signal_prevalence.beauty",
                "ideal_creator_profile.signal_prevalence.lifestyle",
            ),
            geography=geography,
            audience_min=audience_min,
            audience_max=audience_max,
            required_signals=("beauty", "lifestyle"),
        ),
        QuerySpec(
            query_id="ugc_creator_01",
            query_family="ugc_creator",
            search_terms=("UGC creator", "контент-креатор", category),
            hashtags=("ugc", "ugccreator"),
            reason="Find creators with explicitly evidenced UGC capability.",
            source_fields=(
                *shared_sources,
                "ideal_creator_profile.signal_prevalence.ugc",
            ),
            geography=geography,
            audience_min=audience_min,
            audience_max=audience_max,
            required_signals=("ugc",),
        ),
        QuerySpec(
            query_id="marketplace_reviews_01",
            query_family="marketplace_reviews",
            search_terms=("Wildberries", "обзор одежды", "распаковка", category),
            hashtags=("wildberries", "обзорпокупок"),
            reason="Find marketplace and product-review experience.",
            source_fields=(
                *shared_sources,
                "ideal_creator_profile.signal_prevalence.marketplace",
            ),
            geography=geography,
            audience_min=audience_min,
            audience_max=audience_max,
            required_signals=("marketplace",),
        ),
        QuerySpec(
            query_id="reels_integrations_01",
            query_family="reels_product_integrations",
            search_terms=("Reels", "примерка", "нативный обзор", category),
            hashtags=("reels", "примерка"),
            reason=(
                "Find independently observable product integration in short video; "
                f"Phase A median short-video share is {target_share:.4f}."
            ),
            source_fields=(
                *shared_sources,
                "ideal_creator_profile.formats.short_video_share.median",
                "ideal_creator_profile.signal_prevalence.native_product_integration",
            ),
            geography=geography,
            audience_min=audience_min,
            audience_max=audience_max,
            required_signals=("native_product_integration",),
        ),
    ]


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise InputValidationError(f"{name} must be a non-negative number")
    return int(round(float(value)))


def _bounded_float(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 1
    ):
        raise InputValidationError(f"{name} must be between 0 and 1")
    return float(value)
