from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_product_builder.analysis import (
    analyze_dataset,
    normalize_username_from_url,
)
from ai_product_builder.models import ProfileStatus


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


@pytest.fixture(scope="module")
def result():
    return analyze_dataset(
        RAW / "instagram_profiles.json",
        RAW / "manual_verification_audit.json",
        RAW / "Блогеры.xlsx",
    )


def test_status_counts_reconcile_to_source(result) -> None:
    source_count = len(
        json.loads((RAW / "instagram_profiles.json").read_text(encoding="utf-8"))
    )
    counts = result.dataset_summary["counts_by_status"]
    assert sum(counts.values()) == source_count
    assert len(result.profiles) == source_count
    assert result.dataset_summary["scored_creators"] == counts["creator"]


def test_every_record_is_classified(result) -> None:
    assert all(profile.status is not None for profile in result.profiles)
    assert all(
        profile.exclusion_reason
        for profile in result.profiles
        if profile.status != ProfileStatus.CREATOR
    )


def test_brand_references_are_separate_and_unscored(result) -> None:
    brands = [
        profile
        for profile in result.profiles
        if profile.status == ProfileStatus.BRAND_REFERENCE
    ]
    assert {profile.username.casefold() for profile in brands} == {"nike", "apple"}
    assert all(profile.source_index not in result.scores for profile in brands)
    assert all(
        item["excluded_from_creator_statistics"]
        for item in result.brand_reference_signals
    )


def test_score_components_sum_and_have_explanations(result) -> None:
    expected_maxima = [30.0, 20.0, 15.0, 15.0, 10.0, 10.0]
    for score in result.scores.values():
        assert [component.maximum for component in score.components] == expected_maxima
        assert score.total == pytest.approx(
            sum(component.score for component in score.components), abs=0.01
        )
        assert 0 <= score.total <= 100
        assert all(component.explanation for component in score.components)
        assert score.explanation


def test_metrics_use_median_engagement_and_preserve_missing(result) -> None:
    creator = next(
        profile
        for profile in result.profiles
        if profile.status == ProfileStatus.CREATOR
    )
    metrics = result.metrics[creator.source_index]
    assert metrics.sampled_posts == len(creator.latest_posts)
    assert metrics.median_likes is not None
    assert metrics.engagement_rate_pct is not None
    unresolved = next(
        profile
        for profile in result.profiles
        if profile.status == ProfileStatus.UNRESOLVED_NOT_FOUND
    )
    unresolved_metrics = result.metrics[unresolved.source_index]
    assert unresolved_metrics.followers is None
    assert unresolved_metrics.engagement_rate_pct is None
    assert unresolved.source_index not in result.scores


def test_workbook_hyperlinks_and_manual_audit_reconcile(result) -> None:
    reconciliation = result.workbook_reconciliation
    assert reconciliation["workbook_reference_count"] == len(result.profiles)
    assert reconciliation["display_target_mismatch_count"] > 0
    assert reconciliation["manual_correction_count"] == len(
        result.manual_audit["manual_human_in_the_loop_recovery"][
            "confirmed_corrections"
        ]
    )
    assert reconciliation["reconciled"] is True
    assert reconciliation["manual_corrections"]["__aparina"] == "nikaanow"
    assert result.manual_audit["remaining_unresolved"] == [
        "nev_pollyy",
        "19.voron",
        "miysta_fatt_",
    ]
    false_leads = result.manual_audit["manual_human_in_the_loop_recovery"][
        "rejected_false_leads"
    ]
    assert any(
        item["source_username"] == "__aparina"
        and item["candidate_username"] == "aparina_"
        and item["decision"] == "rejected"
        for item in false_leads
    )


def test_verified_nikaanow_is_public_creator_with_post_data(result) -> None:
    profile = next(profile for profile in result.profiles if profile.username == "nikaanow")
    assert profile.status == ProfileStatus.CREATOR
    assert profile.private is False
    assert profile.latest_posts
    assert profile.source_index in result.scores


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.instagram.com/example/profilecard/?x=1", "example"),
        ("https://instagram.com/a.b_c?igsh=1", "a.b_c"),
        ("@plain_name", "plain_name"),
        ("not a username", None),
    ],
)
def test_username_normalization(value: str, expected: str | None) -> None:
    assert normalize_username_from_url(value) == expected
