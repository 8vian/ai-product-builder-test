"""Shared provider protocol and mapping helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from ..models import (
    CandidateIdentity,
    CreatorProfile,
    DiscoveryHit,
    QuerySpec,
    RecentPost,
    non_negative_int,
    optional_bool,
    parse_datetime,
)
from ..normalization import canonicalize_profile_url, normalize_username


@runtime_checkable
class InstagramProvider(Protocol):
    """Provider-neutral, synchronous MVP boundary."""

    provider_name: str

    def discover(self, queries: Sequence[QuerySpec]) -> list[DiscoveryHit]:
        """Return normalized discovery hits, including traceable invalid hits."""

    def enrich(
        self, identities: Sequence[CandidateIdentity]
    ) -> list[CreatorProfile]:
        """Return one normalized profile per requested identity when possible."""


def value_at(record: Mapping[str, Any], path: str, default: Any = None) -> Any:
    """Resolve a configurable dotted mapping path."""

    if path in {"", "."}:
        return record
    current: Any = record
    for segment in path.split("."):
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        else:
            return default
    return current


def normalize_discovery_record(
    record: Any,
    *,
    mapping: Mapping[str, str],
    provider: str,
    default_query_ids: Sequence[str] = (),
    provider_run_id: str | None = None,
) -> DiscoveryHit:
    if not isinstance(record, Mapping):
        return DiscoveryHit(
            platform="instagram",
            username="",
            profile_url="",
            provider=provider,
            query_ids=tuple(default_query_ids),
            provider_run_id=provider_run_id,
            validation_issues=("provider discovery item is not an object",),
        )
    issues: list[str] = []

    def get(name: str, default: Any = None) -> Any:
        path = mapping.get(name)
        return value_at(record, path, default) if path else default

    raw_username = get("username")
    raw_url = get("profile_url")
    username_text = str(raw_username).strip() if raw_username is not None else ""
    url_text = str(raw_url).strip() if raw_url is not None else ""
    normalized = normalize_username(username_text) or normalize_username(url_text)
    if not normalized:
        issues.append("missing or malformed Instagram username")
    if not url_text and normalized:
        url_text = canonicalize_profile_url(normalized) or ""
    elif url_text and not canonicalize_profile_url(url_text):
        issues.append("missing or malformed Instagram profile URL")
    raw_query_ids = get("query_ids", default_query_ids)
    query_ids = _string_tuple(raw_query_ids) or tuple(default_query_ids)
    raw_confidence = get("provider_identity_confidence", 0.0)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 0.0
        issues.append("provider identity confidence is invalid")
    if not 0 <= confidence <= 1:
        issues.append("provider identity confidence was clamped to 0-1")
        confidence = min(1.0, max(0.0, confidence))
    followers_raw = get("followers")
    followers = non_negative_int(followers_raw)
    if followers_raw not in (None, "") and followers is None:
        issues.append("followers is invalid or a negative sentinel")
    collected_raw = get("collected_at")
    collected_at = parse_datetime(collected_raw)
    if collected_raw not in (None, "") and collected_at is None:
        issues.append("collected_at is invalid")
    return DiscoveryHit(
        platform=str(get("platform", "instagram") or "instagram").casefold(),
        username=username_text or (normalized or ""),
        profile_url=url_text,
        provider=provider,
        query_ids=tuple(dict.fromkeys(query_ids)),
        display_name=str(get("display_name", "") or ""),
        biography=str(get("biography", "") or ""),
        followers=followers,
        private=optional_bool(get("private")),
        accessible=optional_bool(get("accessible")),
        provider_identity_confidence=confidence,
        provider_id=(
            str(get("provider_id")) if get("provider_id") not in (None, "") else None
        ),
        provider_run_id=provider_run_id
        or (
            str(get("provider_run_id"))
            if get("provider_run_id") not in (None, "")
            else None
        ),
        collected_at=collected_at,
        raw_payload=dict(record),
        validation_issues=tuple(issues),
    )


def normalize_profile_record(
    record: Any,
    *,
    mapping: Mapping[str, str],
    post_mapping: Mapping[str, str],
    provider: str,
    requested_identity: CandidateIdentity | None = None,
    provider_run_id: str | None = None,
) -> CreatorProfile | None:
    if not isinstance(record, Mapping):
        return (
            missing_profile(requested_identity, provider, "provider profile is not an object")
            if requested_identity
            else None
        )

    def get(name: str, default: Any = None) -> Any:
        path = mapping.get(name)
        return value_at(record, path, default) if path else default

    raw_username = get("username")
    raw_url = get("profile_url")
    normalized_username = normalize_username(
        str(raw_username) if raw_username is not None else None
    )
    normalized_url = normalize_username(
        str(raw_url) if raw_url is not None else None
    )
    normalized = normalized_username or normalized_url
    if requested_identity is None:
        if not normalized:
            return None
        canonical = canonicalize_profile_url(normalized) or ""
        identity = CandidateIdentity(
            platform=str(get("platform", "instagram") or "instagram").casefold(),
            username=str(raw_username or normalized),
            normalized_username=normalized,
            profile_url=str(raw_url or canonical),
            canonical_profile_url=canonical,
            query_ids=_string_tuple(get("query_ids")),
            provider_ids=_string_tuple(get("provider_ids")),
        )
    else:
        conflict = bool(
            (
                normalized_username
                and normalized_username
                != requested_identity.normalized_username
            )
            or (
                normalized_url
                and normalized_url != requested_identity.normalized_username
            )
            or (
                normalized_username
                and normalized_url
                and normalized_username != normalized_url
            )
        )
        identity = CandidateIdentity(
            platform=requested_identity.platform,
            username=requested_identity.username,
            normalized_username=requested_identity.normalized_username,
            profile_url=requested_identity.profile_url,
            canonical_profile_url=requested_identity.canonical_profile_url,
            query_ids=requested_identity.query_ids,
            provider_ids=requested_identity.provider_ids,
            identity_conflict=requested_identity.identity_conflict or conflict,
        )
    issues: list[str] = []
    if not normalized_url or not _is_instagram_profile_url_value(raw_url):
        issues.append("enriched record has no valid provider profile URL")
    if not normalized:
        issues.append("enriched record has no valid Instagram identity")
    elif (
        normalized != identity.normalized_username
        or (
            normalized_url is not None
            and normalized_url != identity.normalized_username
        )
    ):
        issues.append(
            f"identity conflict: requested {identity.normalized_username}, "
            f"received {normalized}"
        )
    raw_posts = get("recent_posts", [])
    if not isinstance(raw_posts, list):
        issues.append("recent_posts is not an array")
        raw_posts = []
    posts = tuple(
        _normalize_mapped_post(post, post_mapping, index)
        for index, post in enumerate(raw_posts)
    )
    for post in posts:
        issues.extend(post.validation_issues)
    followers_raw = get("followers")
    posts_count_raw = get("posts_count")
    followers = non_negative_int(followers_raw)
    posts_count = non_negative_int(posts_count_raw)
    if followers_raw not in (None, "") and followers is None:
        issues.append("followers is invalid or a negative sentinel")
    if posts_count_raw not in (None, "") and posts_count is None:
        issues.append("posts_count is invalid or a negative sentinel")
    confidence_raw = get("provider_identity_confidence", 0.0)
    try:
        confidence = min(1.0, max(0.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0.0
        issues.append("provider identity confidence is invalid")
    run_ids = list(_string_tuple(get("provider_run_ids")))
    if provider_run_id:
        run_ids.append(provider_run_id)
    return CreatorProfile(
        identity=identity,
        full_name=str(get("full_name", "") or ""),
        biography=str(get("biography", "") or ""),
        followers=followers,
        posts_count=posts_count,
        private=optional_bool(get("private")),
        accessible=optional_bool(get("accessible")),
        recent_posts=posts,
        external_urls=_string_tuple(get("external_urls")),
        provider=provider,
        provider_run_ids=tuple(dict.fromkeys(run_ids)),
        provider_identity_confidence=confidence,
        query_ids=tuple(
            dict.fromkeys((*identity.query_ids, *_string_tuple(get("query_ids"))))
        ),
        collected_at=parse_datetime(get("collected_at")),
        validation_issues=tuple(issues),
    )


def missing_profile(
    identity: CandidateIdentity, provider: str, reason: str
) -> CreatorProfile:
    return CreatorProfile(
        identity=identity,
        full_name="",
        biography="",
        followers=None,
        posts_count=None,
        private=None,
        accessible=False,
        recent_posts=(),
        provider=provider,
        query_ids=identity.query_ids,
        validation_issues=(reason,),
    )


def _normalize_mapped_post(
    raw: Any, mapping: Mapping[str, str], index: int
) -> RecentPost:
    if not isinstance(raw, Mapping):
        return RecentPost.from_dict({}, index=index)
    normalized: dict[str, Any] = {}
    for target, path in mapping.items():
        normalized[target] = value_at(raw, path)
    return RecentPost.from_dict(normalized, index=index)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item) for item in value if item not in (None, ""))


def _is_instagram_profile_url_value(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value.strip())
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").casefold()
        in {"instagram.com", "www.instagram.com", "m.instagram.com"}
        and len(parts) == 1
        and normalize_username(value) is not None
    )
