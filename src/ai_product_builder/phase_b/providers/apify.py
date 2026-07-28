"""Configurable Apify Instagram adapter for Phase B live mode."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Mapping, Sequence
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
from ..models import CandidateIdentity, CreatorProfile, DiscoveryHit, QuerySpec
from ..normalization import normalize_username
from .base import (
    missing_profile,
    normalize_discovery_record,
    normalize_profile_record,
    value_at,
)

_DISCOVERY_REQUIRED = {"username", "profile_url"}
_PROFILE_REQUIRED = {
    "username",
    "profile_url",
    "followers",
    "private",
    "accessible",
    "recent_posts",
}
_POST_REQUIRED = {"url", "caption", "likes", "comments", "timestamp", "post_format"}
_SUCCESS_STATES = {"SUCCEEDED"}
_FAILURE_STATES = {"FAILED", "ABORTED", "TIMED-OUT"}


class ApifyInstagramProvider:
    provider_name = "apify"

    def __init__(
        self,
        config: ApifyProviderConfig,
        token: str,
        *,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not token:
            raise ProviderAuthError("APIFY_TOKEN is required for live mode")
        self.config = config
        self._token = token
        self._opener = opener
        self._sleeper = sleeper
        self._validate_mappings()
        self.provider_run_ids: list[str] = []

    def discover(self, queries: Sequence[QuerySpec]) -> list[DiscoveryHit]:
        payload = _render_template(
            self.config.discovery_input_template,
            {
                "queries": [query.to_dict() for query in queries],
                "query_texts": [query.query_text for query in queries],
                "maximum_items": self.config.maximum_items,
            },
        )
        records, run_id = self._run_actor(
            self.config.discovery_actor_id, payload
        )
        return [
            normalize_discovery_record(
                record,
                mapping=self.config.discovery_field_mapping,
                provider=self.provider_name,
                # A run may contain several search queries, but that does not
                # prove that every result was supported by every query. When
                # the actor does not return per-item query provenance, retain
                # an empty tuple rather than manufacturing multi-query support.
                default_query_ids=(),
                provider_run_id=run_id,
            )
            for record in records[: self.config.maximum_items]
        ]

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
        mapped_records: dict[str, Mapping[str, Any]] = {}
        malformed = 0
        username_path = self.config.profile_field_mapping["username"]
        url_path = self.config.profile_field_mapping["profile_url"]
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
                    missing_profile(
                        identity,
                        self.provider_name,
                        f"Apify returned no matching enrichment record{suffix}",
                    )
                )
                continue
            profile = normalize_profile_record(
                record,
                mapping=self.config.profile_field_mapping,
                post_mapping=self.config.post_field_mapping,
                provider=self.provider_name,
                requested_identity=identity,
                provider_run_id=run_id,
            )
            if profile is not None:
                profiles.append(profile)
        return profiles

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

    def _run_actor(
        self, actor_id: str, actor_input: Mapping[str, Any]
    ) -> tuple[list[Any], str]:
        actor_ref = quote(actor_id.replace("/", "~"), safe="~")
        query = urlencode({"waitForFinish": self.config.timeout_seconds})
        response = self._request_json(
            "POST",
            f"{self.config.api_base_url}/acts/{actor_ref}/runs?{query}",
            actor_input,
        )
        data = response.get("data") if isinstance(response, Mapping) else None
        if not isinstance(data, Mapping) or not data.get("id"):
            raise ProviderSchemaError("Apify actor response is missing data.id")
        run_id = str(data["id"])
        self.provider_run_ids.append(run_id)
        deadline = time.monotonic() + self.config.timeout_seconds
        status = str(data.get("status") or "")
        while status not in _SUCCESS_STATES | _FAILURE_STATES:
            if time.monotonic() >= deadline:
                raise ProviderTimeoutError(
                    f"Apify actor {actor_id} exceeded {self.config.timeout_seconds}s",
                    details={"run_id": run_id},
                )
            self._sleeper(min(2.0, max(0.0, deadline - time.monotonic())))
            poll = self._request_json(
                "GET", f"{self.config.api_base_url}/actor-runs/{quote(run_id)}"
            )
            data = poll.get("data") if isinstance(poll, Mapping) else None
            if not isinstance(data, Mapping):
                raise ProviderSchemaError("Apify run response is missing data")
            status = str(data.get("status") or "")
        if status != "SUCCEEDED":
            raise ProviderActorError(
                f"Apify actor {actor_id} ended with status {status}",
                details={"run_id": run_id, "status": status},
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
        )
        if not isinstance(records, list):
            raise ProviderSchemaError(
                "Apify dataset items response must be an array",
                details={"run_id": run_id},
            )
        return records, run_id

    def _request_json(
        self,
        method: str,
        url: str,
        body: Mapping[str, Any] | None = None,
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
            try:
                with self._opener(
                    request, timeout=self.config.timeout_seconds
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
                retriable = exc.code == 429 or 500 <= exc.code < 600
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
                self._sleeper(_retry_delay(exc, attempt))
            except (TimeoutError, URLError) as exc:
                if attempt >= self.config.max_retries:
                    raise ProviderTimeoutError(
                        "Apify request timed out",
                        details={"url": _redacted_url(url), "attempt": attempt + 1},
                    ) from exc
                self._sleeper(_backoff(attempt))
        raise AssertionError("retry loop must return or raise")


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
