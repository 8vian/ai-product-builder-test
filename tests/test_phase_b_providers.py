from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from ai_product_builder.phase_b.config import ApifyProviderConfig
from ai_product_builder.phase_b.eligibility import evaluate_candidate_eligibility
from ai_product_builder.phase_b.enrichment import calculate_candidate_metrics
from ai_product_builder.phase_b.errors import (
    ConfigurationError,
    InputValidationError,
    ProviderAuthError,
    ProviderRateLimitError,
)
from ai_product_builder.phase_b.evidence import collect_signal_evidence
from ai_product_builder.phase_b.models import CandidateIdentity, QuerySpec
from ai_product_builder.phase_b.normalization import (
    canonicalize_profile_url,
    normalize_username,
)
from ai_product_builder.phase_b.providers.apify import ApifyInstagramProvider
from ai_product_builder.phase_b.providers.base import InstagramProvider
from ai_product_builder.phase_b.providers.fixtures import FixtureInstagramProvider


AS_OF = datetime(2026, 2, 1, tzinfo=timezone.utc)

DISCOVERY_MAPPING = {
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
    "search_term": "search_term",
    "provider_run_id": "provider_run_id",
    "query_ids": "query_ids",
    "collected_at": "collected_at",
}
PROFILE_MAPPING = {
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
    "provider_ids": "provider_ids",
    "provider_identity_confidence": "provider_identity_confidence",
    "provider_run_ids": "provider_run_ids",
    "query_ids": "query_ids",
    "collected_at": "collected_at",
}
POST_MAPPING = {
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


def _query() -> QuerySpec:
    return QuerySpec(
        query_id="fashion_style_01",
        query_family="fashion_style",
        search_terms=("fashion",),
        reason="test",
        source_fields=("ideal_creator_profile.signal_prevalence.fashion",),
    )


def _discovery_record(username: str = "new.creator__") -> dict[str, object]:
    return {
        "platform": "instagram",
        "username": username,
        "profile_url": f"https://www.instagram.com/{username}/",
        "display_name": "New Creator",
        "biography": "fashion creator",
        "followers": 12_500,
        "private": False,
        "accessible": True,
        "provider_identity_confidence": 0.91,
        "provider_id": "provider-1",
        "search_term": "fashion",
        "provider_run_id": "fixture-discovery-v1",
        "query_ids": ["fashion_style_01"],
        "collected_at": AS_OF.isoformat(),
    }


def _profile_record(
    username: str = "new.creator__", *, missing_post_url: bool = False
) -> dict[str, object]:
    return {
        "platform": "instagram",
        "username": username,
        "profile_url": f"https://www.instagram.com/{username}/",
        "full_name": "New Creator",
        "biography": "fashion creator",
        "followers": 12_500,
        "posts_count": 120,
        "private": False,
        "accessible": True,
        "external_urls": [],
        "provider_identity_confidence": 0.91,
        "provider_run_ids": ["fixture-enrich-v1"],
        "query_ids": ["fashion_style_01"],
        "collected_at": AS_OF.isoformat(),
        "recent_posts": [
            {
                "post_id": f"post-{index}",
                "url": None
                if missing_post_url
                else f"https://www.instagram.com/p/newcreator{index}/",
                "caption": "fashion dress review",
                "likes": 100 + index,
                "comments": 5 + index,
                "timestamp": (AS_OF - timedelta(days=index + 1)).isoformat(),
                "post_format": "reel" if index % 2 == 0 else "image",
                "mentions": [],
                "hashtags": ["fashion"],
                "tagged_usernames": [],
                "paid_partnership": False,
            }
            for index in range(6)
        ],
    }


def _identity(username: str = "new.creator__") -> CandidateIdentity:
    normalized = normalize_username(username)
    assert normalized is not None
    url = canonicalize_profile_url(username)
    assert url is not None
    return CandidateIdentity(
        platform="instagram",
        username=username,
        normalized_username=normalized,
        profile_url=url,
        canonical_profile_url=url,
        query_ids=("fashion_style_01",),
        provider_ids=("provider-1",),
    )


def _apify_config(**overrides: object) -> ApifyProviderConfig:
    values: dict[str, object] = {
        "discovery_actor_id": "owner/discovery",
        "enrichment_actor_id": "owner/enrichment",
        "discovery_input_template": {"queries": "$query_texts"},
        "enrichment_input_template": {"usernames": "$usernames"},
        "discovery_field_mapping": DISCOVERY_MAPPING,
        "profile_field_mapping": PROFILE_MAPPING,
        "post_field_mapping": POST_MAPPING,
        "maximum_items": 100,
        "timeout_seconds": 10,
        "max_retries": 0,
        "max_total_charge_usd": 1.0,
    }
    values.update(overrides)
    return ApifyProviderConfig(**values)  # type: ignore[arg-type]


def test_fixture_and_apify_implement_the_same_typed_provider_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    discovery_record = _discovery_record()
    profile_record = _profile_record()
    discovery_path = tmp_path / "discovery.json"
    profiles_path = tmp_path / "profiles.json"
    discovery_path.write_text(
        json.dumps([discovery_record]), encoding="utf-8"
    )
    profiles_path.write_text(json.dumps([profile_record]), encoding="utf-8")

    fixture = FixtureInstagramProvider(discovery_path, profiles_path)
    apify = ApifyInstagramProvider(_apify_config(), "test-token")
    monkeypatch.setattr(
        apify,
        "_run_actor",
        lambda actor_id, _: (
            ([discovery_record], "apify-discovery-run")
            if actor_id == "owner/discovery"
            else ([profile_record], "apify-enrichment-run")
        ),
    )

    assert isinstance(fixture, InstagramProvider)
    assert isinstance(apify, InstagramProvider)
    fixture_hit = fixture.discover([_query()])[0]
    apify_hit = apify.discover([_query()])[0]
    for field in (
        "platform",
        "username",
        "profile_url",
        "query_ids",
        "followers",
        "private",
        "accessible",
        "provider_identity_confidence",
    ):
        assert getattr(fixture_hit, field) == getattr(apify_hit, field)

    identity = _identity()
    fixture_profile = fixture.enrich([identity])[0]
    apify_profile = apify.enrich([identity])[0]
    for field in (
        "identity",
        "full_name",
        "biography",
        "followers",
        "posts_count",
        "private",
        "accessible",
        "recent_posts",
        "external_urls",
        "provider_identity_confidence",
        "query_ids",
        "collected_at",
    ):
        assert getattr(fixture_profile, field) == getattr(apify_profile, field)
    assert fixture_profile.provider == "fixture"
    assert apify_profile.provider == "apify"
    assert apify_profile.provider_run_ids[-1] == "apify-enrichment-run"


def test_fixture_provider_preserves_duplicate_discoveries_for_pipeline_audit(
    tmp_path: Path,
) -> None:
    record = _discovery_record()
    discovery_path = tmp_path / "discovery.json"
    profiles_path = tmp_path / "profiles.json"
    discovery_path.write_text(json.dumps([record, record]), encoding="utf-8")
    profiles_path.write_text("[]", encoding="utf-8")

    hits = FixtureInstagramProvider(discovery_path, profiles_path).discover([_query()])
    assert len(hits) == 2
    assert hits[0].username == hits[1].username == "new.creator__"
    assert normalize_username(hits[0].profile_url) == "new.creator__"


@pytest.mark.parametrize("payload", [{"records": {}}, {}, "not-an-array"])
def test_fixture_provider_rejects_malformed_top_level_records(
    tmp_path: Path, payload: object
) -> None:
    discovery_path = tmp_path / "discovery.json"
    profiles_path = tmp_path / "profiles.json"
    discovery_path.write_text(json.dumps(payload), encoding="utf-8")
    profiles_path.write_text("[]", encoding="utf-8")

    with pytest.raises(InputValidationError, match="records must be an array"):
        FixtureInstagramProvider(discovery_path, profiles_path).discover([_query()])


def test_fixture_provider_returns_traceable_malformed_items_and_empty_results(
    tmp_path: Path,
) -> None:
    discovery_path = tmp_path / "discovery.json"
    profiles_path = tmp_path / "profiles.json"
    discovery_path.write_text(json.dumps([None, {"username": "bad-name"}]), encoding="utf-8")
    profiles_path.write_text("[]", encoding="utf-8")
    provider = FixtureInstagramProvider(discovery_path, profiles_path)

    hits = provider.discover([_query()])
    assert len(hits) == 2
    assert all(hit.validation_issues for hit in hits)

    discovery_path.write_text("[]", encoding="utf-8")
    assert provider.discover([_query()]) == []


def test_missing_enrichment_record_is_preserved_as_inaccessible(
    tmp_path: Path,
) -> None:
    discovery_path = tmp_path / "discovery.json"
    profiles_path = tmp_path / "profiles.json"
    discovery_path.write_text("[]", encoding="utf-8")
    profiles_path.write_text("[]", encoding="utf-8")

    profile = FixtureInstagramProvider(discovery_path, profiles_path).enrich(
        [_identity()]
    )[0]
    assert profile.identity.username == "new.creator__"
    assert profile.accessible is False
    assert profile.recent_posts == ()
    assert "no enrichment record" in profile.validation_issues[0]


def test_missing_post_urls_make_candidate_ineligible(tmp_path: Path) -> None:
    discovery_path = tmp_path / "discovery.json"
    profiles_path = tmp_path / "profiles.json"
    discovery_path.write_text("[]", encoding="utf-8")
    profiles_path.write_text(
        json.dumps([_profile_record(missing_post_url=True)]), encoding="utf-8"
    )
    profile = FixtureInstagramProvider(discovery_path, profiles_path).enrich(
        [_identity()]
    )[0]
    metrics = calculate_candidate_metrics(profile, as_of=AS_OF)
    decision = evaluate_candidate_eligibility(
        profile,
        metrics,
        collect_signal_evidence(profile),
        as_of=AS_OF,
    )

    assert decision.eligible is False
    assert "recent_evidence_url_missing" in decision.reasons


def test_provider_profile_url_must_be_directly_returned_and_valid(
    tmp_path: Path,
) -> None:
    record = _profile_record()
    record.pop("profile_url")
    discovery_path = tmp_path / "discovery.json"
    profiles_path = tmp_path / "profiles.json"
    discovery_path.write_text("[]", encoding="utf-8")
    profiles_path.write_text(json.dumps([record]), encoding="utf-8")
    profile = FixtureInstagramProvider(
        discovery_path, profiles_path
    ).enrich([_identity()])[0]

    decision = evaluate_candidate_eligibility(
        profile,
        calculate_candidate_metrics(profile, as_of=AS_OF),
        collect_signal_evidence(profile),
        as_of=AS_OF,
    )

    assert "enriched record has no valid provider profile URL" in (
        profile.validation_issues
    )
    assert decision.eligible is False
    assert "provider_profile_url_missing_or_malformed" in decision.reasons


def test_apify_rejects_missing_token_and_incomplete_mappings() -> None:
    with pytest.raises(ProviderAuthError):
        ApifyInstagramProvider(_apify_config(), "")

    incomplete = dict(DISCOVERY_MAPPING)
    incomplete.pop("profile_url")
    with pytest.raises(ConfigurationError, match="profile_url"):
        ApifyInstagramProvider(
            _apify_config(discovery_field_mapping=incomplete),
            "test-token",
        )


def test_apify_empty_enrichment_results_do_not_fall_back_to_fixtures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApifyInstagramProvider(_apify_config(), "test-token")
    monkeypatch.setattr(provider, "_run_actor", lambda *_: ([], "live-empty-run"))

    profiles = provider.enrich([_identity()])
    assert len(profiles) == 1
    assert profiles[0].provider == "apify"
    assert profiles[0].accessible is False
    assert "Apify returned no matching" in profiles[0].validation_issues[0]


def test_apify_does_not_manufacture_query_support_when_record_omits_query_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _discovery_record()
    record.pop("query_ids")
    record.pop("search_term")
    provider = ApifyInstagramProvider(_apify_config(), "test-token")
    monkeypatch.setattr(
        provider,
        "_run_actor",
        lambda *_: ([record], "apify-discovery-run"),
    )

    hit = provider.discover([_query()])[0]
    assert hit.query_ids == ()


class _JsonResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_apify_honors_retry_after_with_a_bounded_retry() -> None:
    attempts = 0
    sleeps: list[float] = []

    def opener(*_: object, **__: object):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            headers = Message()
            headers["Retry-After"] = "0"
            raise HTTPError(
                "https://api.apify.com/v2/test",
                429,
                "rate limited",
                headers,
                None,
            )
        return _JsonResponse({"ok": True})

    provider = ApifyInstagramProvider(
        _apify_config(max_retries=1),
        "test-token",
        opener=opener,
        sleeper=sleeps.append,
    )

    assert provider._request_json("GET", "https://api.apify.com/v2/test") == {
        "ok": True
    }
    assert attempts == 2
    assert sleeps == [0.0]


def test_apify_exhausted_429_raises_typed_rate_limit_error() -> None:
    headers = Message()
    headers["Retry-After"] = "0"

    def opener(*_: object, **__: object):
        raise HTTPError(
            "https://api.apify.com/v2/test",
            429,
            "rate limited",
            headers,
            None,
        )

    provider = ApifyInstagramProvider(
        _apify_config(max_retries=0),
        "test-token",
        opener=opener,
        sleeper=lambda _: None,
    )
    with pytest.raises(ProviderRateLimitError):
        provider._request_json("GET", "https://api.apify.com/v2/test")
