"""Credential-free deterministic Instagram provider."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..errors import InputValidationError
from ..models import CandidateIdentity, CreatorProfile, DiscoveryHit, QuerySpec, parse_datetime
from ..normalization import canonicalize_profile_url, normalize_username
from .base import (
    missing_profile,
    normalize_discovery_record,
    normalize_profile_record,
)

_DISCOVERY_MAPPING = {
    "platform": "platform",
    "username": "username",
    "profile_url": "profile_url",
    "display_name": "display_name",
    "biography": "biography",
    "followers": "followers",
    "private": "private",
    "accessible": "accessible",
    "provider_identity_confidence": "provider_identity_confidence",
    "provider_id": "provider_id",
    "provider_run_id": "provider_run_id",
    "query_ids": "query_ids",
    "collected_at": "collected_at",
}
_PROFILE_MAPPING = {
    "platform": "platform",
    "username": "username",
    "profile_url": "profile_url",
    "full_name": "full_name",
    "biography": "biography",
    "followers": "followers",
    "posts_count": "posts_count",
    "private": "private",
    "accessible": "accessible",
    "recent_posts": "recent_posts",
    "external_urls": "external_urls",
    "provider_identity_confidence": "provider_identity_confidence",
    "provider_run_ids": "provider_run_ids",
    "query_ids": "query_ids",
    "collected_at": "collected_at",
}
_POST_MAPPING = {
    "post_id": "post_id",
    "url": "url",
    "caption": "caption",
    "likes": "likes",
    "comments": "comments",
    "timestamp": "timestamp",
    "post_format": "post_format",
    "mentions": "mentions",
    "hashtags": "hashtags",
    "tagged_usernames": "tagged_usernames",
    "paid_partnership": "paid_partnership",
}


class FixtureInstagramProvider:
    provider_name = "fixture"

    def __init__(self, discovery_path: Path, profiles_path: Path):
        self.discovery_path = discovery_path
        self.profiles_path = profiles_path

    def discover(self, queries: Sequence[QuerySpec]) -> list[DiscoveryHit]:
        records = _read_records(self.discovery_path)
        known_query_ids = {query.query_id for query in queries}
        hits: list[DiscoveryHit] = []
        for record in records:
            hit = normalize_discovery_record(
                record,
                mapping=_DISCOVERY_MAPPING,
                provider=self.provider_name,
            )
            unknown_ids = sorted(set(hit.query_ids) - known_query_ids)
            if known_query_ids and unknown_ids:
                hit = DiscoveryHit(
                    **{
                        **hit.to_dict(include_raw=True),
                        "collected_at": hit.collected_at,
                        "raw_payload": hit.raw_payload,
                        "validation_issues": (
                            *hit.validation_issues,
                            f"unknown fixture query_ids: {', '.join(unknown_ids)}",
                        ),
                    }
                )
            hits.append(hit)
        return hits

    def enrich(
        self, identities: Sequence[CandidateIdentity]
    ) -> list[CreatorProfile]:
        records = [
            _expand_post_series(record)
            for record in _read_records(self.profiles_path)
        ]
        by_identity: dict[str, Mapping[str, Any]] = {}
        for record in records:
            if not isinstance(record, Mapping):
                continue
            key = normalize_username(
                str(record.get("fixture_lookup_username"))
                if record.get("fixture_lookup_username")
                else (
                    str(record.get("username")) if record.get("username") else None
                )
            ) or normalize_username(
                str(record.get("profile_url")) if record.get("profile_url") else None
            )
            if key and key not in by_identity:
                by_identity[key] = record
        profiles: list[CreatorProfile] = []
        for identity in identities:
            record = by_identity.get(identity.normalized_username)
            if record is None:
                profiles.append(
                    missing_profile(
                        identity,
                        self.provider_name,
                        "fixture has no enrichment record for this identity",
                    )
                )
                continue
            profile = normalize_profile_record(
                record,
                mapping=_PROFILE_MAPPING,
                post_mapping=_POST_MAPPING,
                provider=self.provider_name,
                requested_identity=identity,
                provider_run_id=_first_run_id(record),
            )
            if profile is not None:
                profiles.append(profile)
        return profiles


def _read_records(path: Path) -> list[Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputValidationError(f"fixture file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputValidationError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    records = payload.get("records") if isinstance(payload, Mapping) else payload
    if not isinstance(records, list):
        raise InputValidationError(f"{path}: fixture records must be an array")
    return records


def _first_run_id(record: Mapping[str, Any]) -> str:
    raw = record.get("provider_run_ids")
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, list) and raw:
        return str(raw[0])
    return "fixture-enrich-v1"


def _expand_post_series(record: Any) -> Any:
    if not isinstance(record, Mapping) or "post_series" not in record:
        return record
    result = dict(record)
    series = result.pop("post_series")
    if not isinstance(series, Mapping):
        result["recent_posts"] = []
        return result
    try:
        count = int(series.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    base_date = parse_datetime(series.get("base_date"))
    interval = float(series.get("interval_days", 7))
    formats = series.get("formats", ["short_video"])
    if not isinstance(formats, list) or not formats:
        formats = ["unknown"]
    missing_likes = set(series.get("missing_like_indices", []))
    missing_comments = set(series.get("missing_comment_indices", []))
    paid = set(series.get("paid_partnership_indices", []))
    username = normalize_username(str(result.get("username") or "")) or "invalid"
    slug = str(series.get("url_slug") or username.replace(".", "").replace("_", ""))
    include_urls = bool(series.get("include_urls", True))
    posts: list[dict[str, Any]] = []
    for index in range(max(0, count)):
        likes_start = int(series.get("likes_start", 100))
        likes_step = int(series.get("likes_step", 3))
        comments_start = int(series.get("comments_start", 5))
        comments_step = int(series.get("comments_step", 1))
        posts.append(
            {
                "post_id": f"{slug}-{index + 1:02d}",
                "url": (
                    f"https://www.instagram.com/p/{slug}{index + 1:02d}/"
                    if include_urls
                    else None
                ),
                "caption": str(series.get("caption") or ""),
                "likes": -1
                if index in missing_likes
                else max(0, likes_start + likes_step * index),
                "comments": -1
                if index in missing_comments
                else max(0, comments_start + comments_step * index),
                "timestamp": (
                    (base_date - timedelta(days=interval * index)).isoformat()
                    if base_date
                    else None
                ),
                "post_format": str(formats[index % len(formats)]),
                "mentions": list(series.get("mentions", [])),
                "hashtags": list(series.get("hashtags", [])),
                "tagged_usernames": list(series.get("tagged_usernames", [])),
                "paid_partnership": index in paid,
            }
        )
    result["recent_posts"] = posts
    return result
