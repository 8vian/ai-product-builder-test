from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_product_builder.phase_b.account_types import (
    AccountType,
    assess_account_type,
    with_account_theme_evidence,
)
from ai_product_builder.phase_b.barter_signals import (
    assess_barter_signals,
)
from ai_product_builder.phase_b.compatibility import assess_compatibility
from ai_product_builder.phase_b.config import load_phase_b_config
from ai_product_builder.phase_b.evidence import collect_signal_evidence
from ai_product_builder.phase_b.exclusions import ExclusionRegistry
from ai_product_builder.phase_b.models import (
    CandidateIdentity,
    CreatorProfile,
    EligibilityDecision,
    RecentPost,
)
from ai_product_builder.phase_b.pipeline import (
    _apply_live_campaign_guards,
    _live_campaign_assessment,
    _with_run_level_exclusions,
)
from ai_product_builder.phase_b.queries import generate_queries


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FIXTURE_PATH = (
    ROOT / "tests/fixtures/phase_b_narrow_live.json"
)
AS_OF = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
EXPECTED_QUERIES = (
    "микроблогер примерки женской одежды Москва Reels",
    "стилист блогер капсульный гардероб Москва Reels",
    "блогер женские образы на каждый день Россия",
    "UGC creator одежда Москва примерки Reels",
    "обзор российских брендов женской одежды блогер",
    "fashion блогер женщины 30 плюс Москва гардероб",
    "plus size блогер одежда Россия примерки Reels",
    "блогер стилизация одежды Москва сотрудничество",
)
PREVIOUS_RUNS = (
    ROOT / "output/phase_b/live-20260728T191026Z",
    ROOT / "output/phase_b/live-20260728T211907Z",
)


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _config_path(tmp_path: Path) -> Path:
    raw = json.loads(CONFIG_FIXTURE_PATH.read_text(encoding="utf-8"))
    exclusions: dict[str, dict[str, str]] = {}
    for run in PREVIOUS_RUNS:
        for artifact_name in (
            "discovery_pool.jsonl",
            "enriched_candidates.jsonl",
        ):
            for record in _jsonl(run / artifact_name):
                profile = record.get("profile", record)
                identity = profile.get("identity", profile)
                username = (
                    identity.get("username")
                    or profile.get("username")
                )
                profile_url = (
                    identity.get("profile_url")
                    or identity.get("canonical_profile_url")
                    or profile.get("profile_url")
                )
                if username and profile_url:
                    exclusions.setdefault(
                        username.casefold(),
                        {
                            "username": username,
                            "profile_url": profile_url,
                            "reason": "previous_live_run_exclusion",
                        },
                    )
    raw["run_level_exclusions"] = list(exclusions.values())
    for key, value in raw["inputs"].items():
        raw["inputs"][key] = str((ROOT / value).resolve())
    path = tmp_path / "phase_b_narrow_live.json"
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _saved_profile(username: str) -> CreatorProfile:
    for record in _jsonl(
        PREVIOUS_RUNS[1] / "enriched_candidates.jsonl"
    ):
        if record["profile"]["identity"]["username"] == username:
            return CreatorProfile.from_dict(record["profile"])
    raise AssertionError(f"saved profile not found: {username}")


def _creator_profile(
    *,
    username: str = "new.russian_creator",
    fashion_posts: int = 3,
    with_reel: bool = True,
    biography: str = (
        "Я блогер женской одежды. Москва, Россия. "
        "Показываю гардероб, образы и примерки."
    ),
) -> CreatorProfile:
    posts = tuple(
        RecentPost(
            post_id=f"narrow-{index}",
            url=f"https://www.instagram.com/reel/narrow{index}/",
            caption=(
                f"Моя примерка женской одежды и образ {index}"
                if index < fashion_posts
                else f"Сегодня отвечаю на ваши вопросы, выпуск {index}"
            ),
            likes=100 + index,
            comments=10 + index,
            timestamp=AS_OF - timedelta(days=index + 1),
            post_format=(
                "reel"
                if with_reel and index == 0
                else "image"
            ),
        )
        for index in range(6)
    )
    return CreatorProfile(
        identity=CandidateIdentity(
            platform="instagram",
            username=username,
            normalized_username=username.casefold(),
            profile_url=f"https://www.instagram.com/{username}/",
            canonical_profile_url=(
                f"https://www.instagram.com/{username}/"
            ),
            query_ids=("configured_narrow_01",),
            provider_ids=("saved-test",),
        ),
        full_name="Анна Иванова",
        biography=biography,
        followers=25_000,
        posts_count=200,
        private=False,
        accessible=True,
        recent_posts=posts,
        provider="saved-test",
        provider_run_ids=("saved-test-run",),
        provider_identity_confidence=1.0,
        query_ids=("configured_narrow_01",),
        collected_at=AS_OF,
    )


def _assessment(profile: CreatorProfile, config_path: Path):
    config = load_phase_b_config(config_path)
    account = assess_account_type(profile)
    evidence = with_account_theme_evidence(
        collect_signal_evidence(profile), account
    )
    compatibility = assess_compatibility(profile, config.campaign)
    barter = assess_barter_signals(profile)
    return _live_campaign_assessment(
        profile,
        evidence,
        account,
        language_compatible=(
            compatibility.campaign_language_compatible
        ),
        detected_geography=compatibility.detected_geography,
        delivery_market_review_required=(
            compatibility.delivery_market_review_required
        ),
        delivery_market_conflict=(
            compatibility.delivery_market_conflict
        ),
        explicit_no_barter=barter.explicit_refusal,
        as_of=AS_OF,
        maximum_recency_days=(
            config.eligibility.maximum_recency_days
        ),
        minimum_recent_fashion_posts=(
            config.eligibility.minimum_recent_fashion_posts
        ),
        minimum_short_video_posts=(
            config.eligibility.minimum_short_video_posts
        ),
        preferred_followers_min=(
            config.eligibility.preferred_followers_min
        ),
        preferred_followers_max=(
            config.eligibility.preferred_followers_max
        ),
    )


def test_narrow_config_preserves_exact_queries_and_safety_settings(
    tmp_path: Path,
) -> None:
    config = load_phase_b_config(_config_path(tmp_path))
    ideal = json.loads(
        config.inputs.ideal_creator_profile.read_text(encoding="utf-8")
    )
    generated = generate_queries(
        ideal,
        config.campaign,
        query_texts=config.discovery.query_texts,
    )

    assert tuple(item.query_text for item in generated) == EXPECTED_QUERIES
    assert config.discovery.target_pool_size == 50
    assert config.discovery.minimum_unique_pool == 15
    assert config.discovery.final_count == 5
    assert config.discovery.minimum_final_count == 3
    assert config.campaign.geography == "Россия"
    assert config.campaign.delivery_markets == ("Россия",)
    assert config.campaign.preferred_geographies == (
        "Москва",
        "крупные города России",
    )
    assert config.campaign.automatic_outreach is False
    assert config.campaign.manual_review_required is True
    assert config.provider.apify is not None
    assert config.provider.apify.max_total_charge_usd == 1.0
    assert config.provider.apify.max_combined_charge_usd == 2.0


def test_all_previous_live_identities_are_excluded_before_enrichment(
    tmp_path: Path,
) -> None:
    config = load_phase_b_config(_config_path(tmp_path))
    registry = _with_run_level_exclusions(
        ExclusionRegistry.from_files(
            config.inputs.instagram_profiles,
            config.inputs.manual_audit,
        ),
        config,
    )
    seen: set[str] = set()
    counts: dict[str, tuple[int, int]] = {}
    for run in PREVIOUS_RUNS:
        discovery = _jsonl(run / "discovery_pool.jsonl")
        enriched = _jsonl(run / "enriched_candidates.jsonl")
        counts[run.name] = (len(discovery), len(enriched))
        for record in (*discovery, *enriched):
            profile = record.get("profile", record)
            identity = profile.get("identity", profile)
            username = identity.get("username") or profile.get("username")
            profile_url = (
                identity.get("profile_url")
                or identity.get("canonical_profile_url")
                or profile.get("profile_url")
            )
            assert registry.match(username, profile_url) is not None
            if username:
                seen.add(username.casefold())

    assert counts == {
        "live-20260728T191026Z": (40, 40),
        "live-20260728T211907Z": (38, 26),
    }
    assert len(config.run_level_exclusions) == 66
    assert len(seen) == 66


def test_saved_stylist_portfolio_is_not_a_blogger_creator() -> None:
    assessment = assess_account_type(
        _saved_profile("stylistelenaialena")
    )

    assert assessment.account_type is (
        AccountType.PROFESSIONAL_PORTFOLIO
    )
    assert assessment.professional_portfolio is True
    assert assessment.eligible_account is False


@pytest.mark.parametrize(
    ("username", "expected_type", "brand", "store"),
    (
        ("malina_fashion", AccountType.BRAND, True, False),
        ("ayuma.style", AccountType.BRAND, True, False),
        ("miss_sunrise9", AccountType.SHOWROOM, False, True),
    ),
)
def test_saved_commercial_conflicts_are_hard_exclusions(
    username: str,
    expected_type: AccountType,
    brand: bool,
    store: bool,
) -> None:
    assessment = assess_account_type(_saved_profile(username))

    assert assessment.account_type is expected_type
    assert assessment.commercial_conflict is True
    assert assessment.own_fashion_brand is brand
    assert assessment.own_clothing_store_or_showroom is store


def test_blogger_content_requires_three_recent_fashion_posts(
    tmp_path: Path,
) -> None:
    assessment = _assessment(
        _creator_profile(fashion_posts=2, with_reel=True),
        _config_path(tmp_path),
    )

    assert assessment.recent_fashion_posts == 2
    assert assessment.short_video_ready is True
    assert assessment.blogger_content_sufficient is False


def test_blogger_content_requires_a_recent_reel(
    tmp_path: Path,
) -> None:
    assessment = _assessment(
        _creator_profile(fashion_posts=3, with_reel=False),
        _config_path(tmp_path),
    )

    assert assessment.recent_fashion_posts == 3
    assert assessment.recent_short_video_posts == 0
    assert assessment.short_video_ready is False
    assert assessment.blogger_content_sufficient is False


def test_strict_live_creator_passes_content_language_and_geography(
    tmp_path: Path,
) -> None:
    assessment = _assessment(
        _creator_profile(fashion_posts=3, with_reel=True),
        _config_path(tmp_path),
    )

    assert assessment.recent_fashion_posts == 3
    assert assessment.recent_short_video_posts == 1
    assert assessment.language_compatible is True
    assert assessment.geography_compatible is True
    assert assessment.blogger_content_sufficient is True
    assert assessment.manual_review_required is True


def test_language_and_no_barter_guards_remain_hard_exclusions() -> None:
    base = EligibilityDecision(
        eligible=True,
        status="eligible",
        reasons=(),
    )
    language = _apply_live_campaign_guards(
        base,
        explicit_barter_refusal=False,
        language_compatible=False,
        delivery_market_conflict=False,
        high_audience=False,
    )
    no_barter = _apply_live_campaign_guards(
        base,
        explicit_barter_refusal=True,
        language_compatible=True,
        delivery_market_conflict=False,
        high_audience=False,
    )

    assert language.eligible is False
    assert "campaign_language_mismatch" in language.reasons
    assert no_barter.eligible is False
    assert "explicit_no_barter_statement" in no_barter.reasons
