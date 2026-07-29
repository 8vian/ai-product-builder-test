from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from ai_product_builder.phase_b.config import load_phase_b_config
from ai_product_builder.phase_b.eligibility import (
    evaluate_candidate_eligibility,
)
from ai_product_builder.phase_b.enrichment import calculate_candidate_metrics
from ai_product_builder.phase_b.errors import (
    ConfigurationError,
    ProviderActorError,
    ProviderTimeoutError,
)
from ai_product_builder.phase_b.evidence import collect_signal_evidence
from ai_product_builder.phase_b.models import CandidateIdentity, QuerySpec
from ai_product_builder.phase_b.normalization import (
    canonicalize_profile_url,
    normalize_username,
)
from ai_product_builder.phase_b.providers.apify import ApifyInstagramProvider


ROOT = Path(__file__).resolve().parents[1]
LIVE_CONFIG = ROOT / "config/phase_b.live.example.json"
CONTRACT_DIR = ROOT / "data/fixtures/phase_b/apify_contract"
AS_OF = datetime(2026, 7, 28, tzinfo=timezone.utc)


def _queries() -> tuple[QuerySpec, ...]:
    return (
        QuerySpec(
            query_id="fashion_style_01",
            query_family="fashion_style",
            search_terms=("fashion", "creator"),
            reason="contract",
            source_fields=("test",),
        ),
        QuerySpec(
            query_id="beauty_lifestyle_01",
            query_family="beauty_lifestyle",
            search_terms=("beauty", "lifestyle"),
            reason="contract",
            source_fields=("test",),
        ),
        QuerySpec(
            query_id="ugc_creator_01",
            query_family="ugc_creator",
            search_terms=("ugc", "creator"),
            reason="contract",
            source_fields=("test",),
        ),
    )


def _apify_config():
    config = load_phase_b_config(LIVE_CONFIG)
    assert config.provider.apify is not None
    return config.provider.apify


def _identity(
    username: str, *, query_ids: tuple[str, ...] = ("fashion_style_01",)
) -> CandidateIdentity:
    normalized = normalize_username(username)
    profile_url = canonicalize_profile_url(username)
    assert normalized is not None
    assert profile_url is not None
    return CandidateIdentity(
        platform="instagram",
        username=username,
        normalized_username=normalized,
        profile_url=profile_url,
        canonical_profile_url=profile_url,
        query_ids=query_ids,
        provider_ids=(f"search-{username}",),
    )


def _load_records(name: str) -> list[object]:
    payload = json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def test_official_discovery_template_renders_csv_and_preserves_legacy_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    config = replace(
        _apify_config(),
        discovery_input_template={
            "search": "$query_text_csv",
            "legacyQueryTexts": "$query_texts",
            "searchType": "user",
        },
    )
    provider = ApifyInstagramProvider(
        config,
        "contract-token",
        clock=lambda: AS_OF,
    )

    def fake_run(actor_id: str, payload: object):
        captured["actor_id"] = actor_id
        captured["payload"] = payload
        return _load_records("search_results.json"), "search-run-001"

    monkeypatch.setattr(provider, "_run_actor", fake_run)

    provider.discover(_queries())

    assert captured["actor_id"] == "apify/instagram-search-scraper"
    assert captured["payload"] == {
        "search": "fashion creator,beauty lifestyle,ugc creator",
        "legacyQueryTexts": [
            "fashion creator",
            "beauty lifestyle",
            "ugc creator",
        ],
        "searchType": "user",
    }


def test_search_term_provenance_is_exact_normalized_and_never_fabricated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApifyInstagramProvider(
        _apify_config(),
        "contract-token",
        clock=lambda: AS_OF,
    )
    monkeypatch.setattr(
        provider,
        "_run_actor",
        lambda *_: (_load_records("search_results.json"), "search-run-001"),
    )

    hits = provider.discover(_queries())
    by_username = {hit.username: hit for hit in hits}

    assert by_username["contract.creator__"].query_ids == (
        "fashion_style_01",
    )
    assert by_username["beauty.contract_"].query_ids == (
        "beauty_lifestyle_01",
    )
    assert by_username["ugc.contract_"].query_ids == ("ugc_creator_01",)
    assert by_username["unknown.contract_"].query_ids == ()
    assert all(len(hit.query_ids) <= 1 for hit in hits)
    assert all(hit.provider_identity_confidence == 1.0 for hit in hits)
    assert all(hit.collected_at == AS_OF for hit in hits)


def test_actor_safe_search_text_preserves_raw_context_and_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = QuerySpec(
        query_id="ugc_punctuation_01",
        query_family="ugc_creator",
        search_terms=("UGC", "контент-креатор", "#ugc"),
        reason="contract",
        source_fields=("test",),
    )
    captured: dict[str, object] = {}
    config = replace(
        _apify_config(),
        discovery_input_template={
            "search": "$query_text_csv",
            "rawQueryTexts": "$query_texts",
            "searchType": "user",
        },
    )
    provider = ApifyInstagramProvider(
        config,
        "contract-token",
        clock=lambda: AS_OF,
    )
    record = dict(_load_records("search_results.json")[0])  # type: ignore[arg-type]
    record["searchTerm"] = "ugc контент креатор ugc"

    def fake_run(actor_id: str, payload: object):
        captured["actor_id"] = actor_id
        captured["payload"] = payload
        return [record], "search-run-safe"

    monkeypatch.setattr(provider, "_run_actor", fake_run)

    hit = provider.discover((query,))[0]

    assert captured["payload"] == {
        "search": "UGC контент креатор ugc",
        "rawQueryTexts": ["UGC контент-креатор #ugc"],
        "searchType": "user",
    }
    assert hit.query_ids == ("ugc_punctuation_01",)


def test_duplicate_normalized_query_text_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate = replace(_queries()[0], query_id="duplicate_query_id")
    provider = ApifyInstagramProvider(
        _apify_config(),
        "contract-token",
        clock=lambda: AS_OF,
    )
    monkeypatch.setattr(
        provider,
        "_run_actor",
        lambda *_: (_load_records("search_results.json")[:1], "search-run"),
    )

    hit = provider.discover((_queries()[0], duplicate))[0]

    assert hit.query_ids == ()


def test_official_profile_contract_normalizes_accessibility_and_complex_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _load_records("profile_results.json")
    provider = ApifyInstagramProvider(
        _apify_config(),
        "contract-token",
        clock=lambda: AS_OF,
    )
    monkeypatch.setattr(
        provider,
        "_run_actor",
        lambda *_: (records, "profile-run-001"),
    )
    usernames = (
        "contract.creator__",
        "private.contract_",
        "error.contract_",
        "description.error_",
        "malformed.url_",
        "restricted.contract_",
        "missing.core_",
        "missing.contract_",
    )

    profiles = provider.enrich([_identity(username) for username in usernames])
    by_username = {
        profile.identity.normalized_username: profile for profile in profiles
    }

    public = by_username["contract.creator__"]
    assert public.accessible is True
    assert public.private is False
    assert public.collected_at == AS_OF
    assert public.provider_identity_confidence == 1.0
    assert public.identity.provider_ids == (
        "search-contract.creator__",
        "profile-001",
    )
    assert public.external_urls == (
        "https://creator.example/portfolio",
        (
            "https://l.instagram.com/"
            "?u=https%3A%2F%2Ft.me%2Fcontract_creator"
        ),
    )
    assert public.recent_posts[0].tagged_usernames == ("demo_brand",)
    assert public.recent_posts[0].mentions == ("demo_brand",)
    assert public.recent_posts[0].hashtags == ("fashion", "review")
    assert public.recent_posts[0].paid_partnership is False
    assert public.recent_posts[1].likes is None
    assert "negative sentinel" in public.recent_posts[1].validation_issues[0]

    metrics = calculate_candidate_metrics(public, as_of=AS_OF)
    assert metrics.sampled_posts == 7
    assert metrics.usable_posts == 6
    assert metrics.data_completeness == pytest.approx(6 / 7)
    assert metrics.median_likes == 525.0
    assert metrics.median_comments == 25.0

    private = by_username["private.contract_"]
    assert private.accessible is True
    assert private.private is True
    private_decision = evaluate_candidate_eligibility(
        private,
        calculate_candidate_metrics(private, as_of=AS_OF),
        collect_signal_evidence(private),
        as_of=AS_OF,
    )
    assert private_decision.reasons == ("profile_not_public",)

    assert by_username["error.contract_"].accessible is False
    assert by_username["description.error_"].accessible is False
    assert by_username["malformed.url_"].accessible is False
    assert by_username["restricted.contract_"].accessible is False
    assert by_username["missing.core_"].accessible is False
    assert by_username["missing.contract_"].accessible is False


class _JsonResponse:
    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_actor_start_uses_server_cost_limits_and_header_only_token() -> None:
    requests = []
    responses = iter(
        (
            {
                "data": {
                    "id": "run-001",
                    "status": "SUCCEEDED",
                    "defaultDatasetId": "dataset-001",
                    "usageTotalUsd": 0.125,
                }
            },
            [],
        )
    )

    def opener(request, **_: object):
        requests.append(request)
        return _JsonResponse(next(responses))

    provider = ApifyInstagramProvider(
        _apify_config(),
        "sentinel-contract-token",
        opener=opener,
        sleeper=lambda _: None,
        clock=lambda: AS_OF,
    )

    records, run_id = provider._run_actor(
        provider.config.discovery_actor_id,
        {"search": "fashion creator"},
    )

    assert records == []
    assert run_id == "run-001"
    assert provider.provider_requests_made == 1
    assert provider.budget_spent_usd == pytest.approx(0.125)
    assert provider.actor_run_usage == [
        {
            "stage": "search",
            "actor_id": "apify/instagram-search-scraper",
            "run_id": "run-001",
            "charge_limit_usd": 1.0,
            "charge_usd": 0.125,
        }
    ]
    start_request = requests[0]
    parsed = urlparse(start_request.full_url)
    assert parse_qs(parsed.query) == {
        "waitForFinish": ["60"],
        "maxItems": ["50"],
        "maxTotalChargeUsd": ["1.0"],
    }
    assert start_request.get_header("Authorization") == (
        "Bearer sentinel-contract-token"
    )
    assert "sentinel-contract-token" not in start_request.full_url
    assert "sentinel-contract-token" not in (
        start_request.data or b""
    ).decode("utf-8")


def test_actor_start_wait_is_part_of_the_overall_client_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    provider = ApifyInstagramProvider(
        _apify_config(),
        "contract-token",
        sleeper=lambda _: None,
    )

    def fake_request(method: str, *_: object, **__: object):
        if method == "POST":
            now[0] += provider.config.timeout_seconds + 1
            return {"data": {"id": "slow-run", "status": "RUNNING"}}
        raise AssertionError("polling must not start after the deadline")

    monkeypatch.setattr(
        "ai_product_builder.phase_b.providers.apify.time.monotonic",
        lambda: now[0],
    )
    monkeypatch.setattr(provider, "_request_json", fake_request)

    with pytest.raises(
        ProviderTimeoutError,
        match=r"exceeded 180s",
    ):
        provider._run_actor(
            provider.config.discovery_actor_id,
            {"search": "fashion creator"},
        )


def test_poll_and_dataset_requests_use_only_the_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    requests: list[tuple[str, float]] = []

    def opener(request, *, timeout: float):
        requests.append((request.full_url, timeout))
        if "/runs?" in request.full_url:
            now[0] += 60.0
            return _JsonResponse(
                {"data": {"id": "run-remaining", "status": "RUNNING"}}
            )
        if "/actor-runs/" in request.full_url:
            return _JsonResponse(
                {
                    "data": {
                        "id": "run-remaining",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "dataset-remaining",
                        "usageTotalUsd": 0.25,
                    }
                }
            )
        return _JsonResponse([])

    def sleeper(delay: float) -> None:
        now[0] += delay

    monkeypatch.setattr(
        "ai_product_builder.phase_b.providers.apify.time.monotonic",
        lambda: now[0],
    )
    provider = ApifyInstagramProvider(
        _apify_config(),
        "contract-token",
        opener=opener,
        sleeper=sleeper,
    )

    records, run_id = provider._run_actor(
        provider.config.discovery_actor_id,
        {"search": "fashion creator"},
    )

    assert records == []
    assert run_id == "run-remaining"
    assert requests[0][1] == pytest.approx(180.0)
    assert requests[1][1] == pytest.approx(118.0)
    assert requests[2][1] == pytest.approx(118.0)


def test_provider_forbids_second_billable_run_for_same_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApifyInstagramProvider(
        _apify_config(),
        "contract-token",
        sleeper=lambda _: None,
    )
    responses = iter(
        (
            {
                "data": {
                    "id": "single-search-run",
                    "status": "SUCCEEDED",
                    "defaultDatasetId": "single-search-dataset",
                    "usageTotalUsd": 0.1,
                }
            },
            [],
        )
    )
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *_args, **_kwargs: next(responses),
    )

    provider._run_actor(
        provider.config.discovery_actor_id,
        {"search": "narrow fashion"},
    )
    with pytest.raises(ProviderActorError, match="already started"):
        provider._run_actor(
            provider.config.discovery_actor_id,
            {"search": "must not run twice"},
        )


def test_failed_actor_run_still_records_actual_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApifyInstagramProvider(
        _apify_config(),
        "contract-token",
        sleeper=lambda _: None,
    )
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda *_args, **_kwargs: {
            "data": {
                "id": "failed-search-run",
                "status": "FAILED",
                "usageTotalUsd": 0.04,
            }
        },
    )

    with pytest.raises(ProviderActorError, match="status FAILED"):
        provider._run_actor(
            provider.config.discovery_actor_id,
            {"search": "narrow fashion"},
        )

    assert provider.provider_requests_made == 1
    assert provider.budget_spent_usd == pytest.approx(0.04)


def test_actor_start_does_not_retry_ambiguous_failure_or_leak_token() -> None:
    attempts = 0

    def opener(request, **_: object):
        nonlocal attempts
        attempts += 1
        raise HTTPError(request.full_url, 500, "server error", {}, None)

    provider = ApifyInstagramProvider(
        replace(_apify_config(), max_retries=2),
        "sentinel-contract-token",
        opener=opener,
        sleeper=lambda _: None,
    )

    with pytest.raises(ProviderActorError) as raised:
        provider._run_actor(
            provider.config.discovery_actor_id,
            {"search": "fashion creator"},
        )

    assert attempts == 1
    assert "sentinel-contract-token" not in str(raised.value)
    assert "sentinel-contract-token" not in json.dumps(
        raised.value.details, sort_keys=True
    )


def test_untrusted_api_base_and_unknown_template_placeholder_are_rejected() -> None:
    with pytest.raises(ConfigurationError, match="untrusted host"):
        ApifyInstagramProvider(
            replace(_apify_config(), api_base_url="https://attacker.example/v2"),
            "must-not-leak",
        )

    with pytest.raises(ConfigurationError, match=r"\$unknown"):
        ApifyInstagramProvider(
            replace(
                _apify_config(),
                discovery_input_template={"search": "$unknown"},
            ),
            "contract-token",
        )
