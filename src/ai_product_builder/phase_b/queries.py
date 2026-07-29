"""Deterministic discovery-query generation from frozen Phase A evidence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

from .errors import InputValidationError
from .models import CampaignBrief, QuerySpec


def generate_queries(
    ideal_profile_document: Mapping[str, Any],
    campaign: CampaignBrief,
    *,
    query_texts: Sequence[str] = (),
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
    target_languages = campaign.target_content_languages or (
        campaign.language,
    )
    delivery_markets = campaign.delivery_markets or (
        ((campaign.geography,) if campaign.geography else ())
    )
    if query_texts:
        return _configured_queries(
            query_texts,
            audience_min=audience_min,
            audience_max=audience_max,
            geography=geography,
            shared_sources=shared_sources,
        )
    if (
        any(item.casefold().split("-", 1)[0] == "ru" for item in target_languages)
        and any(
            item.casefold() in {"россия", "russia"}
            for item in delivery_markets
        )
    ):
        return _russian_russia_queries(
            audience_min=audience_min,
            audience_max=audience_max,
            geography=geography,
            shared_sources=shared_sources,
            target_share=target_share,
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


def _configured_queries(
    query_texts: Sequence[str],
    *,
    audience_min: int,
    audience_max: int,
    geography: str | None,
    shared_sources: tuple[str, ...],
) -> list[QuerySpec]:
    """Return explicitly configured discovery queries without rewriting them."""

    normalized: list[str] = []
    for index, value in enumerate(query_texts, start=1):
        if not isinstance(value, str) or not value.strip():
            raise InputValidationError(
                f"discovery.query_texts[{index - 1}] must be non-empty"
            )
        text = value.strip()
        if text in normalized:
            raise InputValidationError(
                f"discovery.query_texts contains duplicate query: {text}"
            )
        normalized.append(text)
    return [
        QuerySpec(
            query_id=f"configured_narrow_{index:02d}",
            query_family="configured_narrow_fashion",
            search_terms=(text,),
            reason=(
                "Explicit campaign-approved narrow Russian fashion query. "
                f"Phase A follower IQR remains {audience_min}-{audience_max}."
            ),
            source_fields=(
                *shared_sources,
                "discovery.query_texts",
                "campaign.language",
                "campaign.geography",
                "campaign.delivery_markets",
            ),
            geography=geography,
            audience_min=audience_min,
            audience_max=audience_max,
            required_signals=("fashion", "native_product_integration"),
            query_text_override=text,
        )
        for index, text in enumerate(normalized, start=1)
    ]


def _russian_russia_queries(
    *,
    audience_min: int,
    audience_max: int,
    geography: str | None,
    shared_sources: tuple[str, ...],
    target_share: float,
) -> list[QuerySpec]:
    """Return the approved RU/Russia discovery set verbatim."""

    definitions = (
        (
            "ru_fashion_style_01",
            "fashion_style",
            "блогер женская одежда стиль образы Россия",
            ("fashion",),
            "Direct Russian-language fashion, clothing, style, and outfit discovery.",
        ),
        (
            "ru_fashion_tryon_02",
            "fashion_tryons",
            "fashion блогер примерки одежды Россия",
            ("fashion", "native_product_integration"),
            "Russian fashion creators with directly observable clothing try-ons.",
        ),
        (
            "ru_ugc_clothing_03",
            "ugc_creator",
            "UGC креатор одежда Россия",
            ("ugc", "fashion"),
            "Russian UGC creators working with clothing content.",
        ),
        (
            "ru_marketplace_reviews_04",
            "marketplace_reviews",
            "обзор одежды Wildberries российский блогер",
            ("marketplace", "native_product_integration"),
            "Personal Russian creators with clothing-review or marketplace experience.",
        ),
        (
            "ru_reels_native_05",
            "reels_product_integrations",
            "Reels примерка нативный обзор одежды Россия",
            ("native_product_integration",),
            (
                "Russian short-video try-on and native-review creators; "
                f"Phase A median short-video share is {target_share:.4f}."
            ),
        ),
        (
            "ru_stylist_moscow_06",
            "fashion_style",
            "стилист женские образы Москва",
            ("fashion", "lifestyle"),
            "Moscow-based personal stylists with women’s outfit evidence.",
        ),
        (
            "ru_fashion_beauty_lifestyle_07",
            "fashion_beauty_lifestyle",
            "блогер мода красота лайфстайл Россия",
            ("fashion", "beauty", "lifestyle"),
            "Russian personal creators across fashion, beauty, and compatible lifestyle.",
        ),
    )
    return [
        QuerySpec(
            query_id=query_id,
            query_family=query_family,
            search_terms=(query_text,),
            reason=(
                f"{reason} Phase A follower IQR remains "
                f"{audience_min}-{audience_max}."
            ),
            source_fields=(
                *shared_sources,
                "campaign.language",
                "campaign.target_content_languages",
                "campaign.geography",
                "campaign.delivery_markets",
            ),
            geography=geography,
            audience_min=audience_min,
            audience_max=audience_max,
            required_signals=required_signals,
            query_text_override=query_text,
        )
        for (
            query_id,
            query_family,
            query_text,
            required_signals,
            reason,
        ) in definitions
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
