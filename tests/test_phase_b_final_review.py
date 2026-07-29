from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_product_builder.phase_b.account_types import (
    AccountType,
    AccountTypeAssessment,
)
from ai_product_builder.phase_b.barter_signals import assess_barter_signals
from ai_product_builder.phase_b.compatibility import assess_compatibility
from ai_product_builder.phase_b.enrichment import calculate_candidate_metrics
from ai_product_builder.phase_b.final_review import (
    FINAL_REVIEW_ARTIFACTS,
    _campaign_classification,
    run_final_campaign_review,
)
from ai_product_builder.phase_b.models import (
    CampaignBrief,
    CandidateIdentity,
    CreatorProfile,
    RecentPost,
)
from ai_product_builder.phase_b.providers.apify import ApifyInstagramProvider


COLLECTED_AT = datetime(2026, 7, 28, 19, tzinfo=timezone.utc)


def _campaign(*, geography: str | None = None) -> CampaignBrief:
    return CampaignBrief(
        brand_name="LD Latte",
        product_name="новая коллекция",
        product_category="женская одежда",
        barter_item="товар бренда в обмен на контент",
        desired_content_format="Reels или нативный обзор",
        language="ru",
        tone="дружелюбный",
        geography=geography,
    )


def _profile(
    username: str = "fashion.creator_",
    *,
    biography: str = "Я fashion-блогер: одежда, образы и примерки.",
    followers: int = 20_000,
    captions: tuple[str, ...] | None = None,
    formats: tuple[str, ...] | None = None,
) -> CreatorProfile:
    captions = captions or tuple(
        f"Мой образ и примерка одежды #{index}" for index in range(6)
    )
    formats = formats or ("short_video",) * len(captions)
    posts = tuple(
        RecentPost(
            post_id=f"post-{index}",
            url=f"https://www.instagram.com/reel/post{index}/",
            caption=caption,
            likes=100 + index,
            comments=10 + index,
            timestamp=COLLECTED_AT - timedelta(days=index),
            post_format=formats[index],
        )
        for index, caption in enumerate(captions)
    )
    return CreatorProfile(
        identity=CandidateIdentity(
            platform="instagram",
            username=username,
            normalized_username=username.casefold(),
            profile_url=f"https://www.instagram.com/{username}/",
            canonical_profile_url=f"https://www.instagram.com/{username}/",
            query_ids=("test",),
            provider_ids=("saved-id",),
        ),
        full_name="Personal Creator",
        biography=biography,
        followers=followers,
        posts_count=100,
        private=False,
        accessible=True,
        recent_posts=posts,
        provider="saved_apify_artifact",
        provider_run_ids=("saved-run",),
        provider_identity_confidence=1.0,
        query_ids=("test",),
        collected_at=COLLECTED_AT,
    )


def _personal_assessment() -> AccountTypeAssessment:
    return AccountTypeAssessment(
        account_type=AccountType.PERSONAL_CREATOR,
        theme_relevant=True,
        explanation="Test fixture directly evidences a personal fashion author.",
        evidence=(),
        relevant_dimensions=("fashion",),
    )


def _classification(
    profile: CreatorProfile,
    *,
    threshold: float = 500_000,
) -> tuple[str, str, tuple[str, ...], bool, str]:
    metrics = calculate_candidate_metrics(profile)
    return _campaign_classification(
        profile,
        metrics,
        _personal_assessment(),
        assess_barter_signals(profile),
        assess_compatibility(profile, _campaign(geography="Москва")),
        maximum_recency_days=90,
        minimum_usable_posts=6,
        extreme_audience_threshold=threshold,
        evidence={"fashion": ()},
    )


@pytest.mark.parametrize(
    "statement",
    (
        "Я не работаю по бартеру.",
        "Бартер не рассматриваю.",
    ),
)
def test_explicit_no_barter_statement_is_detected(statement: str) -> None:
    assessment = assess_barter_signals(
        _profile(captions=(statement, *("Новый образ",) * 5))
    )

    assert assessment.explicit_refusal is True
    assert assessment.explicit_barter_readiness is False
    assert assessment.no_barter_evidence[0].signal_type == "no_barter"
    assert assessment.no_barter_evidence[0].observation_type == "direct"


@pytest.mark.parametrize(
    "statement",
    (
        "Только платное сотрудничество.",
        "Работаю исключительно на платной основе.",
        "Only paid collaborations.",
    ),
)
def test_only_paid_collaboration_is_a_barter_refusal(statement: str) -> None:
    assessment = assess_barter_signals(
        _profile(captions=(statement, *("Новый образ",) * 5))
    )

    assert assessment.explicit_refusal is True


def test_price_list_or_manager_without_refusal_is_not_no_barter() -> None:
    assessment = assess_barter_signals(
        _profile(
            biography=(
                "Fashion creator. Прайс по запросу, менеджер для связи, "
                "коммерческие условия обсуждаются."
            )
        )
    )

    assert assessment.explicit_refusal is False
    assert assessment.no_barter_evidence == ()


def test_all_missing_post_formats_are_insufficient_data() -> None:
    profile = _profile(formats=("unknown",) * 6)

    bucket, status, reasons, _, _ = _classification(profile)

    assert bucket == "ineligible_or_insufficient"
    assert status == "insufficient_data"
    assert "post_format_data_missing" in reasons


def test_one_known_post_format_satisfies_format_rule() -> None:
    profile = _profile(
        formats=("short_video", *("unknown",) * 5),
        biography=(
            "Я fashion-блогер: одежда и примерки. Рассматриваю бартер."
        ),
    )

    _, status, reasons, _, _ = _classification(profile)

    assert status != "insufficient_data"
    assert "post_format_data_missing" not in reasons


def test_high_audience_requires_manual_barter_review() -> None:
    profile = _profile(
        followers=769_642,
        biography=(
            "Я fashion-блогер: одежда и примерки. Рассматриваю бартер."
        ),
    )

    bucket, status, reasons, high_audience, _ = _classification(
        profile,
        threshold=700_000,
    )

    assert bucket == "needs_manual_review"
    assert status == "needs_review"
    assert high_audience is True
    assert "high_audience_barter_review_required" in reasons


def test_campaign_language_mismatch_requires_review_not_exclusion() -> None:
    profile = _profile(
        biography="I am a personal fashion creator and outfit stylist.",
        captions=tuple(
            f"My clothing outfit and try-on review {index}"
            for index in range(6)
        ),
    )
    compatibility = assess_compatibility(
        profile,
        _campaign(geography="London"),
    )
    bucket, status, reasons, _, _ = _campaign_classification(
        profile,
        calculate_candidate_metrics(profile),
        _personal_assessment(),
        assess_barter_signals(profile),
        compatibility,
        maximum_recency_days=90,
        minimum_usable_posts=6,
        extreme_audience_threshold=500_000,
        evidence={"fashion": ()},
    )

    assert compatibility.detected_content_language == "en"
    assert compatibility.campaign_language_compatible is False
    assert bucket == "needs_manual_review"
    assert status == "needs_review"
    assert "campaign_language_mismatch" in reasons


def test_geography_is_not_guessed_from_username() -> None:
    profile = _profile(
        username="fashion_creator_kazakhstan",
        biography="I am a personal fashion creator and outfit stylist.",
        captions=tuple(
            f"My clothing outfit and try-on review {index}"
            for index in range(6)
        ),
    )

    compatibility = assess_compatibility(profile, _campaign())

    assert compatibility.detected_geography is None
    assert compatibility.delivery_market_review_required is True


def _tree_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): hashlib.sha256(
            item.read_bytes()
        ).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file() and not item.name.startswith("~$")
    }


def test_final_review_reprocesses_saved_artifacts_without_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path("output/phase_b/live-20260728T191026Z")
    reviewed = Path("output/phase_b/live-20260728T191026Z-reviewed")
    output = tmp_path / "final-review"
    source_hashes = _tree_hashes(source)
    reviewed_hashes = _tree_hashes(reviewed)

    def _network_forbidden(*_args, **_kwargs):
        raise AssertionError("provider construction or request is forbidden")

    monkeypatch.setattr(
        ApifyInstagramProvider,
        "__init__",
        _network_forbidden,
    )
    monkeypatch.setattr(
        ApifyInstagramProvider,
        "discover",
        _network_forbidden,
    )
    monkeypatch.setattr(
        ApifyInstagramProvider,
        "enrich",
        _network_forbidden,
    )

    manifest, paths, top_ten = run_final_campaign_review(
        source,
        reviewed,
        output,
        config_path=Path("config/phase_b.live.example.json"),
        review_path=Path(
            "data/reviews/phase_b/live-20260728T191026Z.json"
        ),
    )

    assert manifest.provider_requests_made == 0
    assert manifest.budget_spent_usd == 0.0
    assert manifest.counts["personal_accounts"] == 16
    assert manifest.counts["barter_ready"] == 0
    assert manifest.counts["needs_manual_review"] == 3
    assert manifest.counts["ineligible_or_insufficient"] == 37
    assert manifest.counts["personal_ineligible_or_insufficient"] == 13
    assert len(top_ten) == 10
    assert {path.name for path in paths} == set(FINAL_REVIEW_ARTIFACTS)

    classifications = {
        item["username"]: item
        for item in json.loads(
            (output / "personal_candidate_classification.json").read_text(
                encoding="utf-8"
            )
        )
    }
    bainur = classifications["bainur_beauty"]
    assert bainur["status"] == "ineligible"
    assert bainur["status_reasons"] == [
        "explicit_no_barter_statement"
    ]
    assert "paid campaign" in bainur["alternative_campaign_note"]

    marwadi = classifications["marwadi._.reels_29"]
    assert marwadi["status"] == "insufficient_data"
    assert "post_format_data_missing" in marwadi["status_reasons"]

    beauty_newnew = classifications["beauty_newnew"]
    assert beauty_newnew["campaign_bucket"] == "needs_manual_review"
    assert beauty_newnew["barter_feasibility_review_required"] is True

    reviewed_candidates = json.loads(
        (output / "needs_manual_review.json").read_text(encoding="utf-8")
    )
    beauty_result = next(
        item
        for item in reviewed_candidates
        if item["username"] == "beauty_newnew"
    )
    assert beauty_result["manual_verification_status"] == "pending"
    assert beauty_result["barter_offer"].startswith(
        "[PRELIMINARY DRAFT — DO NOT SEND BEFORE MANUAL APPROVAL]"
    )

    ineligible_csv = (output / "ineligible_or_insufficient.csv").read_text(
        encoding="utf-8-sig"
    )
    assert "bainur_beauty" in ineligible_csv
    assert "marwadi._.reels_29" in ineligible_csv
    assert "PRELIMINARY DRAFT" not in ineligible_csv
    assert (
        len(
            (output / "eligible_candidates.csv").read_text(
                encoding="utf-8-sig"
            ).splitlines()
        )
        == 1
    )
    assert source_hashes == _tree_hashes(source)
    assert reviewed_hashes == _tree_hashes(reviewed)
