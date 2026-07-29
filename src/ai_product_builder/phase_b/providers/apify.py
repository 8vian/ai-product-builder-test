"""Configurable Apify Instagram adapter for Phase B live mode."""

from __future__ import annotations

import json
import math
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..config import ApifyProviderConfig
from ..errors import (
    ConfigurationError,
    ProviderActorError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderSchemaError,
    ProviderTimeoutError,
)
from ..models import (
    CandidateIdentity,
    CreatorProfile,
    DiscoveryHit,
    QuerySpec,
    non_negative_int,
    optional_bool,
)
from ..normalization import normalize_username
from .base import (
    missing_profile,
    is_instagram_profile_url,
    normalize_discovery_record,
    normalize_profile_record,
    value_at,
)

_DISCOVERY_REQUIRED = {
    "username",
    "profile_url",
    "display_name",
    "biography",
    "followers",
    "private",
    "provider_id",
    "search_term",
}
_PROFILE_REQUIRED = {
    "username",
    "profile_url",
    "full_name",
    "biography",
    "followers",
    "posts_count",
    "private",
    "recent_posts",
    "external_urls",
    "provider_ids",
}
_POST_REQUIRED = {
    "post_id",
    "url",
    "caption",
    "likes",
    "comments",
    "timestamp",
    "post_format",
    "mentions",
    "hashtags",
    "tagged_usernames",
}
_SUCCESS_STATES = {"SUCCEEDED"}
_FAILURE_STATES = {"FAILED", "ABORTED", "TIMED-OUT"}
_OFFICIAL_API_BASE_URL = "https://api.apify.com/v2"
_MAX_WAIT_FOR_FINISH_SECONDS = 60
_DISCOVERY_TEMPLATE_CONTEXT = frozenset(
    {"queries", "query_texts", "query_text_csv", "maximum_items"}
)
_ENRICHMENT_TEMPLATE_CONTEXT = frozenset(
    {"usernames", "profile_urls", "maximum_items"}
)
_SEARCH_TERM_FORBIDDEN_CHARACTERS = frozenset(
    "!?.,:;-+=*&%$#@/\\~^|<>()[]{}" + "\"'`"
)


class ApifyInstagramProvider:
    provider_name = "apify"

    def __init__(
        self,
        config: ApifyProviderConfig,
        token: str,
        *,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ):
        if not token:
            raise ProviderAuthError("APIFY_TOKEN is required for live mode")
        self.config = config
        self._token = token
        self._opener = opener
        self._sleeper = sleeper
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._validate_mappings()
        self._validate_api_base_url()
        self._validate_templates()
        self.provider_run_ids: list[str] = []
        self.actor_run_usage: list[dict[str, Any]] = []
        self._started_actor_stages: set[str] = set()

    @property
    def provider_requests_made(self) -> int:
        """Count accepted, potentially billable Actor runs."""

        return len(self.provider_run_ids)

    @property
    def budget_spent_usd(self) -> float:
        return round(
            sum(
                float(item["charge_usd"])
                for item in self.actor_run_usage
            ),
            6,
        )

    def discover(self, queries: Sequence[QuerySpec]) -> list[DiscoveryHit]:
        query_texts = [query.query_text for query in queries]
        actor_query_texts = [
            _actor_safe_search_term(query_text) for query_text in query_texts
        ]
        payload = _render_template(
            self.config.discovery_input_template,
            {
                "queries": [query.to_dict() for query in queries],
                "query_texts": query_texts,
                "query_text_csv": ",".join(actor_query_texts),
                "maximum_items": self.config.maximum_items,
            },
        )
        records, run_id = self._run_actor(
            self.config.discovery_actor_id, payload
        )
        return normalize_apify_discovery_records(
            records,
            config=self.config,
            queries=queries,
            run_id=run_id,
            collected_at=_as_utc(self._clock()),
        )

    def enrich(
        self, identities: Sequence[CandidateIdentity]
    ) -> list[CreatorProfile]:
        payload = _render_template(
            self.config.enrichment_input_template,
            {
                "usernames": [identity.normalized_username for identity in identities],
                "profile_urls": [
                    identity.canonical_profile_url for identity in identities
                ],
                "maximum_items": min(
                    self.config.maximum_items, max(1, len(identities))
                ),
            },
        )
        records, run_id = self._run_actor(
            self.config.enrichment_actor_id, payload
        )
        return normalize_apify_profile_records(
            records,
            config=self.config,
            identities=identities,
            run_id=run_id,
            collected_at=_as_utc(self._clock()),
        )

    def _validate_mappings(self) -> None:
        groups = (
            (
                "discovery",
                self.config.discovery_field_mapping,
                _DISCOVERY_REQUIRED,
            ),
            ("profile", self.config.profile_field_mapping, _PROFILE_REQUIRED),
            ("post", self.config.post_field_mapping, _POST_REQUIRED),
        )
        for name, mapping, required in groups:
            missing = sorted(required - set(mapping))
            if missing:
                raise ConfigurationError(
                    f"Apify {name} field mapping is missing: {', '.join(missing)}"
                )
            invalid = [
                key
                for key, path in mapping.items()
                if not isinstance(path, str) or not path.strip()
            ]
            if invalid:
                raise ConfigurationError(
                    f"Apify {name} mapping has empty paths: {', '.join(invalid)}"
                )

    def _validate_api_base_url(self) -> None:
        if self.config.api_base_url.rstrip("/") != _OFFICIAL_API_BASE_URL:
            raise ConfigurationError(
                "provider.apify.api_base_url must be "
                f"'{_OFFICIAL_API_BASE_URL}' so APIFY_TOKEN is never sent "
                "to an untrusted host"
            )

    def _validate_templates(self) -> None:
        groups = (
            (
                "discovery",
                self.config.discovery_input_template,
                _DISCOVERY_TEMPLATE_CONTEXT,
            ),
            (
                "enrichment",
                self.config.enrichment_input_template,
                _ENRICHMENT_TEMPLATE_CONTEXT,
            ),
        )
        for name, template, allowed in groups:
            unknown = sorted(_template_placeholders(template) - allowed)
            if unknown:
                raise ConfigurationError(
                    f"Apify {name} template has unsupported placeholders: "
                    + ", ".join(f"${item}" for item in unknown)
                )

    def _run_actor(
        self, actor_id: str, actor_input: Mapping[str, Any]
    ) -> tuple[list[Any], str]:
        stage = self._actor_stage(actor_id)
        if stage in self._started_actor_stages:
            raise ProviderActorError(
                f"Apify {stage} Actor run was already started; a second "
                "billable run is forbidden"
            )
        projected_cap = (
            len(self._started_actor_stages) + 1
        ) * self.config.max_total_charge_usd
        if projected_cap > self.config.max_combined_charge_usd + 1e-9:
            raise ProviderActorError(
                "Starting this Actor could exceed the configured combined "
                "budget cap",
                details={
                    "stage": stage,
                    "projected_cap_usd": projected_cap,
                    "combined_cap_usd": (
                        self.config.max_combined_charge_usd
                    ),
                },
            )
        self._started_actor_stages.add(stage)
        deadline = time.monotonic() + self.config.timeout_seconds
        actor_ref = quote(actor_id.replace("/", "~"), safe="~")
        query = urlencode(
            {
                "waitForFinish": min(
                    self.config.timeout_seconds,
                    _MAX_WAIT_FOR_FINISH_SECONDS,
                ),
                "maxItems": self.config.maximum_items,
                "maxTotalChargeUsd": self.config.max_total_charge_usd,
            }
        )
        response = self._request_json(
            "POST",
            f"{self.config.api_base_url}/acts/{actor_ref}/runs?{query}",
            actor_input,
            retry_ambiguous=False,
            deadline=deadline,
        )
        data = response.get("data") if isinstance(response, Mapping) else None
        if not isinstance(data, Mapping) or not data.get("id"):
            raise ProviderSchemaError("Apify actor response is missing data.id")
        run_id = str(data["id"])
        self.provider_run_ids.append(run_id)
        status = str(data.get("status") or "")
        while status not in _SUCCESS_STATES | _FAILURE_STATES:
            if time.monotonic() >= deadline:
                raise ProviderTimeoutError(
                    f"Apify actor {actor_id} exceeded {self.config.timeout_seconds}s",
                    details={"run_id": run_id},
                )
            self._sleeper(min(2.0, max(0.0, deadline - time.monotonic())))
            poll = self._request_json(
                "GET",
                f"{self.config.api_base_url}/actor-runs/{quote(run_id)}",
                deadline=deadline,
            )
            data = poll.get("data") if isinstance(poll, Mapping) else None
            if not isinstance(data, Mapping):
                raise ProviderSchemaError("Apify run response is missing data")
            status = str(data.get("status") or "")
        charge_usd = _usage_total_usd(data)
        if charge_usd is None:
            usage_response = self._request_json(
                "GET",
                f"{self.config.api_base_url}/actor-runs/{quote(run_id)}",
                deadline=deadline,
            )
            usage_data = (
                usage_response.get("data")
                if isinstance(usage_response, Mapping)
                else None
            )
            charge_usd = _usage_total_usd(usage_data)
        if charge_usd is None:
            raise ProviderSchemaError(
                "Apify run response is missing usageTotalUsd; actual spend "
                "cannot be audited",
                details={"run_id": run_id, "stage": stage},
            )
        self.actor_run_usage.append(
            {
                "stage": stage,
                "actor_id": actor_id,
                "run_id": run_id,
                "charge_limit_usd": self.config.max_total_charge_usd,
                "charge_usd": charge_usd,
            }
        )
        if self.budget_spent_usd > (
            self.config.max_combined_charge_usd + 1e-9
        ):
            raise ProviderActorError(
                "Actual Apify spend exceeded the configured combined cap",
                details={
                    "spent_usd": self.budget_spent_usd,
                    "combined_cap_usd": (
                        self.config.max_combined_charge_usd
                    ),
                },
            )
        if status != "SUCCEEDED":
            raise ProviderActorError(
                f"Apify actor {actor_id} ended with status {status}",
                details={
                    "run_id": run_id,
                    "status": status,
                    "charge_usd": charge_usd,
                },
            )
        dataset_id = data.get("defaultDatasetId")
        if not dataset_id:
            raise ProviderSchemaError(
                "Apify successful run is missing defaultDatasetId",
                details={"run_id": run_id},
            )
        dataset_query = urlencode(
            {
                "clean": "true",
                "format": "json",
                "limit": self.config.maximum_items,
            }
        )
        records = self._request_json(
            "GET",
            f"{self.config.api_base_url}/datasets/{quote(str(dataset_id))}/items?"
            f"{dataset_query}",
            deadline=deadline,
        )
        if not isinstance(records, list):
            raise ProviderSchemaError(
                "Apify dataset items response must be an array",
                details={"run_id": run_id},
            )
        return records, run_id

    def _actor_stage(self, actor_id: str) -> str:
        if actor_id == self.config.discovery_actor_id:
            return "search"
        if actor_id == self.config.enrichment_actor_id:
            return "profile"
        return actor_id

    def _request_json(
        self,
        method: str,
        url: str,
        body: Mapping[str, Any] | None = None,
        *,
        retry_ambiguous: bool = True,
        deadline: float | None = None,
    ) -> Any:
        payload = (
            json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None
            else None
        )
        request = Request(
            url,
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "ai-product-builder-phase-b/0.1",
            },
        )
        for attempt in range(self.config.max_retries + 1):
            request_timeout = self._remaining_request_timeout(
                deadline,
                url=url,
                attempt=attempt,
            )
            try:
                with self._opener(
                    request, timeout=request_timeout
                ) as response:
                    content = response.read()
                try:
                    return json.loads(content.decode("utf-8")) if content else {}
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProviderSchemaError(
                        "Apify returned a non-JSON response"
                    ) from exc
            except HTTPError as exc:
                if exc.code in {401, 403}:
                    raise ProviderAuthError(
                        f"Apify authentication failed with HTTP {exc.code}"
                    ) from exc
                retriable = exc.code == 429 or (
                    retry_ambiguous and 500 <= exc.code < 600
                )
                if not retriable or attempt >= self.config.max_retries:
                    error_class = (
                        ProviderRateLimitError
                        if exc.code == 429
                        else ProviderActorError
                    )
                    raise error_class(
                        f"Apify request failed with HTTP {exc.code}",
                        details={"url": _redacted_url(url), "attempt": attempt + 1},
                    ) from exc
                self._bounded_sleep(
                    _retry_delay(exc, attempt),
                    deadline=deadline,
                    url=url,
                    attempt=attempt,
                )
            except (TimeoutError, URLError) as exc:
                if not retry_ambiguous or attempt >= self.config.max_retries:
                    raise ProviderTimeoutError(
                        "Apify request timed out",
                        details={"url": _redacted_url(url), "attempt": attempt + 1},
                    ) from exc
                self._bounded_sleep(
                    _backoff(attempt),
                    deadline=deadline,
                    url=url,
                    attempt=attempt,
                )
        raise AssertionError("retry loop must return or raise")

    def _remaining_request_timeout(
        self,
        deadline: float | None,
        *,
        url: str,
        attempt: int,
    ) -> float:
        if deadline is None:
            return float(self.config.timeout_seconds)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderTimeoutError(
                "Apify request timed out",
                details={"url": _redacted_url(url), "attempt": attempt + 1},
            )
        return min(float(self.config.timeout_seconds), remaining)

    def _bounded_sleep(
        self,
        delay: float,
        *,
        deadline: float | None,
        url: str,
        attempt: int,
    ) -> None:
        if deadline is None:
            self._sleeper(delay)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderTimeoutError(
                "Apify request timed out",
                details={"url": _redacted_url(url), "attempt": attempt + 1},
            )
        self._sleeper(min(delay, remaining))


def normalize_apify_discovery_records(
    records: Sequence[Any],
    *,
    config: ApifyProviderConfig,
    queries: Sequence[QuerySpec],
    run_id: str,
    collected_at: datetime,
) -> list[DiscoveryHit]:
    """Map already-saved Search dataset items without starting an Actor."""

    actor_query_texts = [
        _actor_safe_search_term(query.query_text) for query in queries
    ]
    query_lookup = _query_lookup(queries, actor_query_texts)
    search_term_path = config.discovery_field_mapping.get("search_term")
    mapping = dict(config.discovery_field_mapping)
    if search_term_path:
        # Official results expose searchTerm, not queryIds. When searchTerm is
        # configured it is the sole provenance source for this record.
        mapping.pop("query_ids", None)
    hits: list[DiscoveryHit] = []
    for record in records[: config.maximum_items]:
        query_ids = _query_ids_for_record(
            record,
            search_term_path=search_term_path,
            query_lookup=query_lookup,
        )
        hit = normalize_discovery_record(
            record,
            mapping=mapping,
            provider=ApifyInstagramProvider.provider_name,
            default_query_ids=query_ids,
            provider_run_id=run_id,
        )
        replacements: dict[str, Any] = {}
        if "provider_identity_confidence" not in mapping:
            replacements["provider_identity_confidence"] = (
                _derived_identity_confidence(record, mapping)
            )
        if hit.collected_at is None:
            replacements["collected_at"] = _as_utc(collected_at)
        hits.append(replace(hit, **replacements) if replacements else hit)
    return hits


def normalize_apify_profile_records(
    records: Sequence[Any],
    *,
    config: ApifyProviderConfig,
    identities: Sequence[CandidateIdentity],
    run_id: str,
    collected_at: datetime,
) -> list[CreatorProfile]:
    """Map already-saved Profile dataset items without starting an Actor."""

    mapped_records: dict[str, Mapping[str, Any]] = {}
    malformed = 0
    username_path = config.profile_field_mapping["username"]
    url_path = config.profile_field_mapping["profile_url"]
    for record in records:
        if not isinstance(record, Mapping):
            malformed += 1
            continue
        normalized = normalize_username(
            str(value_at(record, username_path) or "")
        ) or normalize_username(str(value_at(record, url_path) or ""))
        if not normalized:
            malformed += 1
            continue
        mapped_records.setdefault(normalized, record)
    profiles: list[CreatorProfile] = []
    for identity in identities:
        record = mapped_records.get(identity.normalized_username)
        if record is None:
            suffix = (
                f"; {malformed} malformed provider record(s) were ignored"
                if malformed
                else ""
            )
            profiles.append(
                replace(
                    missing_profile(
                        identity,
                        ApifyInstagramProvider.provider_name,
                        f"Apify returned no matching enrichment record{suffix}",
                    ),
                    collected_at=_as_utc(collected_at),
                )
            )
            continue
        accessible, accessibility_issues = _derive_accessibility(
            record,
            config.profile_field_mapping,
            requested_username=identity.normalized_username,
        )
        profile = normalize_profile_record(
            record,
            mapping=config.profile_field_mapping,
            post_mapping=config.post_field_mapping,
            provider=ApifyInstagramProvider.provider_name,
            requested_identity=identity,
            provider_run_id=run_id,
        )
        if profile is not None:
            confidence = profile.provider_identity_confidence
            if "provider_identity_confidence" not in (
                config.profile_field_mapping
            ):
                confidence = _derived_identity_confidence(
                    record,
                    config.profile_field_mapping,
                    requested_username=identity.normalized_username,
                )
            profiles.append(
                replace(
                    profile,
                    accessible=accessible,
                    provider_identity_confidence=confidence,
                    collected_at=profile.collected_at
                    or _as_utc(collected_at),
                    validation_issues=tuple(
                        dict.fromkeys(
                            (
                                *profile.validation_issues,
                                *accessibility_issues,
                            )
                        )
                    ),
                )
            )
    return profiles


def _usage_total_usd(data: Any) -> float | None:
    if not isinstance(data, Mapping):
        return None
    value = data.get("usageTotalUsd")
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _render_template(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$") and value[1:] in context:
        return context[value[1:]]
    if isinstance(value, Mapping):
        return {
            str(key): _render_template(item, context)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_render_template(item, context) for item in value]
    return value


def _template_placeholders(value: Any) -> set[str]:
    if isinstance(value, str) and value.startswith("$") and len(value) > 1:
        return {value[1:]}
    if isinstance(value, Mapping):
        result: set[str] = set()
        for item in value.values():
            result.update(_template_placeholders(item))
        return result
    if isinstance(value, list):
        result = set()
        for item in value:
            result.update(_template_placeholders(item))
        return result
    return set()


def _normalize_query_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).casefold()
    return normalized or None


def _actor_safe_search_term(value: str) -> str:
    cleaned = "".join(
        " " if character in _SEARCH_TERM_FORBIDDEN_CHARACTERS else character
        for character in value
    )
    normalized = " ".join(cleaned.split())
    if not normalized:
        raise ConfigurationError(
            "A generated discovery query is empty after applying the official "
            "Instagram Search Scraper character restrictions"
        )
    return normalized


def _query_lookup(
    queries: Sequence[QuerySpec], actor_query_texts: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for query, actor_query_text in zip(
        queries, actor_query_texts, strict=True
    ):
        for source_text in dict.fromkeys(
            (query.query_text, actor_query_text)
        ):
            normalized = _normalize_query_text(source_text)
            if normalized:
                grouped.setdefault(normalized, []).append(query.query_id)
    return {
        text: tuple(dict.fromkeys(query_ids))
        for text, query_ids in grouped.items()
    }


def _query_ids_for_record(
    record: Any,
    *,
    search_term_path: str | None,
    query_lookup: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    if not isinstance(record, Mapping) or not search_term_path:
        return ()
    normalized = _normalize_query_text(value_at(record, search_term_path))
    if normalized is None:
        return ()
    matches = query_lookup.get(normalized, ())
    # A duplicated normalized query text is ambiguous: searchTerm alone cannot
    # prove which QuerySpec produced the result.
    return matches if len(matches) == 1 else ()


def _derived_identity_confidence(
    record: Mapping[str, Any],
    mapping: Mapping[str, str],
    *,
    requested_username: str | None = None,
) -> float:
    username_path = mapping.get("username")
    profile_url_path = mapping.get("profile_url")
    raw_username = value_at(record, username_path) if username_path else None
    raw_url = value_at(record, profile_url_path) if profile_url_path else None
    username = normalize_username(
        str(raw_username) if raw_username is not None else None
    )
    profile_username = (
        normalize_username(str(raw_url))
        if is_instagram_profile_url(raw_url)
        else None
    )
    if not username or not profile_username or username != profile_username:
        return 0.0
    if requested_username and username != requested_username:
        return 0.0
    return 1.0


def _derive_accessibility(
    record: Mapping[str, Any],
    mapping: Mapping[str, str],
    *,
    requested_username: str,
) -> tuple[bool, tuple[str, ...]]:
    issues: list[str] = []
    if _derived_identity_confidence(
        record, mapping, requested_username=requested_username
    ) != 1.0:
        issues.append(
            "provider record username/profile URL is missing, malformed, "
            "conflicting, or does not match the requested identity"
        )
    for field_name in ("error", "errorDescription"):
        if _has_non_empty_value(record.get(field_name)):
            issues.append(f"provider record contains non-empty {field_name}")
    if _is_unusable_restricted(record):
        issues.append("provider record is restricted and unusable")

    core_fields = (
        ("followers", non_negative_int),
        ("posts_count", non_negative_int),
        ("private", optional_bool),
    )
    for field_name, parser in core_fields:
        path = mapping.get(field_name)
        value = value_at(record, path) if path else None
        if parser(value) is None:
            issues.append(
                f"provider record has no usable core field: {field_name}"
            )
    posts_path = mapping.get("recent_posts")
    raw_posts = value_at(record, posts_path) if posts_path else None
    if not isinstance(raw_posts, list):
        issues.append("provider record recent_posts is not an array")
    return not issues, tuple(issues)


def _has_non_empty_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset, Mapping)):
        return bool(value)
    return bool(value)


def _is_unusable_restricted(record: Mapping[str, Any]) -> bool:
    for field_name in ("restricted", "isRestricted"):
        value = record.get(field_name)
        parsed = optional_bool(value)
        if parsed is True:
            return True
        if parsed is None and _has_non_empty_value(value):
            return True
    for field_name in (
        "restrictionReason",
        "restrictedReason",
        "restrictedStatus",
    ):
        if _has_non_empty_value(record.get(field_name)):
            return True
    return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _retry_delay(error: HTTPError, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        try:
            return max(0.0, min(60.0, float(retry_after)))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(retry_after)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(
                    0.0,
                    min(
                        60.0,
                        (parsed - datetime.now(timezone.utc)).total_seconds(),
                    ),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return _backoff(attempt)


def _backoff(attempt: int) -> float:
    # Bounded jitter prevents synchronized retries without permitting long sleeps.
    return min(30.0, (2**attempt) + random.uniform(0, 0.25))


def _redacted_url(url: str) -> str:
    # Tokens are sent in Authorization headers, but defensively remove query tokens.
    return url.split("token=", 1)[0]
