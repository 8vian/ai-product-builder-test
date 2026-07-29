"""Provider-neutral serialization helpers for Phase B outputs.

The I/O layer deliberately accepts either Phase B dataclasses or ordinary mappings.
That keeps spreadsheet and artifact writers independent from orchestration details and
makes the adapters straightforward to exercise with fakes in unit tests.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


PHASE_B_COLUMNS: tuple[str, ...] = (
    "platform",
    "username",
    "profile_url",
    "account_type",
    "account_type_explanation",
    "followers",
    "median_likes",
    "median_comments",
    "engagement_rate",
    "usable_posts",
    "sampled_posts",
    "data_completeness",
    "short_video_share",
    "last_post_date",
    "score",
    "score_components",
    "selection_explanation",
    "evidence",
    "recent_post_url",
    "barter_offer",
    "manual_verification_status",
    "verification_notes",
    "outreach_status",
    "barter_feasibility_review_required",
    "barter_feasibility_explanation",
    "content_themes",
    "known_format_posts",
    "detected_content_language",
    "campaign_language_compatible",
    "detected_geography",
    "delivery_market_review_required",
    "compatibility_explanation",
    "barter_evidence",
    "no_barter_evidence",
    "campaign_bucket",
    "campaign_status_reasons",
    "alternative_campaign_note",
    "discovery_confidence",
    "eligibility_status",
    "eligibility_reasons",
    "query_ids",
    "provider",
    "collected_at",
    "offer_generation_mode",
    "source_exclusion_check",
)

EXCLUDED_COLUMNS: tuple[str, ...] = (
    "platform",
    "username",
    "profile_url",
    "normalized_username",
    "exclusion_reason",
    "exclusion_reasons",
    "query_ids",
    "provider",
)

MANUAL_VERIFICATION_STATUSES: tuple[str, ...] = (
    "pending",
    "approved",
    "rejected",
    "needs_review",
)

NESTED_COLUMNS = frozenset(
    {
        "score_components",
        "evidence",
        "eligibility_reasons",
        "query_ids",
        "exclusion_reasons",
        "content_themes",
        "barter_evidence",
        "no_barter_evidence",
        "campaign_status_reasons",
    }
)


def as_mapping(value: Any) -> dict[str, Any]:
    """Return a shallow mapping for a supported typed object."""

    if isinstance(value, Mapping):
        return dict(value)
    for method_name in ("to_dict", "serializable"):
        method = getattr(value, method_name, None)
        if callable(method):
            converted = method()
            if not isinstance(converted, Mapping):
                raise TypeError(f"{method_name}() must return a mapping")
            return dict(converted)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(
        "Expected a mapping, dataclass, or object exposing to_dict()/serializable()"
    )


def json_safe(value: Any) -> Any:
    """Recursively convert typed values to deterministic JSON-compatible values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((json_safe(item) for item in value), key=str)
    if is_dataclass(value):
        return json_safe(asdict(value))
    for method_name in ("to_dict", "serializable"):
        method = getattr(value, method_name, None)
        if callable(method):
            return json_safe(method())
    return str(value)


def json_text(value: Any) -> str:
    return json.dumps(
        json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def candidate_mapping(value: Any) -> dict[str, Any]:
    """Normalize a candidate while preserving the public output schema."""

    item = as_mapping(value)
    offer = item.get("barter_offer")
    if offer is not None and not isinstance(offer, str):
        offer_mapping: dict[str, Any] | None = None
        try:
            offer_mapping = as_mapping(offer)
        except TypeError:
            pass
        if offer_mapping:
            for field in ("text", "draft", "body", "content"):
                if offer_mapping.get(field):
                    item["barter_offer"] = offer_mapping[field]
                    break
    return item


def cell_value(field: str, value: Any) -> Any:
    """Serialize a value without converting genuine missing values to zero."""

    if value is None:
        return ""
    safe = json_safe(value)
    if field in NESTED_COLUMNS or isinstance(safe, (dict, list)):
        return json_text(safe)
    return safe


def candidate_row(value: Any) -> dict[str, Any]:
    item = candidate_mapping(value)
    return {field: cell_value(field, item.get(field)) for field in PHASE_B_COLUMNS}


def normalized_identity(value: Any) -> str:
    """Build a case-insensitive username key while preserving dots/underscores."""

    item = as_mapping(value) if not isinstance(value, str) else {"username": value}
    raw = str(item.get("username") or "").strip()
    if not raw:
        raw = str(item.get("normalized_username") or "").strip()
    if not raw:
        raw = str(item.get("profile_url") or item.get("Ссылка") or "").strip()
    raw = raw.lstrip("@")
    if "://" in raw or raw.casefold().startswith(("instagram.com/", "www.instagram.com/")):
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        raw = parsed.path.strip("/").split("/", 1)[0]
    else:
        raw = raw.split("?", 1)[0].rstrip("/")
    return raw.casefold()


__all__ = [
    "EXCLUDED_COLUMNS",
    "MANUAL_VERIFICATION_STATUSES",
    "PHASE_B_COLUMNS",
    "as_mapping",
    "candidate_mapping",
    "candidate_row",
    "cell_value",
    "json_safe",
    "json_text",
    "normalized_identity",
]
