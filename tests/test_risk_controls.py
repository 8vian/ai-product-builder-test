from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_product_builder.analysis import (
    analyze_dataset,
    average_tie_percentile,
    calculate_metrics,
    classify_profile,
    detect_signals,
    distribution_summary,
    frequency_points,
    percentile,
    read_json,
    recency_points,
    score_profiles,
)
from ai_product_builder.cli import main
from ai_product_builder.models import Profile, ProfileStatus


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
AS_OF = datetime(2026, 7, 27, tzinfo=timezone.utc)


def raw_post(
    index: int,
    *,
    likes: int = 10,
    comments: int = 2,
    caption: str = "",
    mentions: list[str] | None = None,
    paid_partnership: bool = False,
) -> dict[str, object]:
    return {
        "id": str(index),
        "type": "Video",
        "productType": "clips",
        "caption": caption,
        "likesCount": likes,
        "commentsCount": comments,
        "timestamp": AS_OF.isoformat(),
        "mentions": mentions or [],
        "paidPartnership": paid_partnership,
    }


def profile_with_posts(posts: list[dict[str, object]], biography: str = "") -> Profile:
    profile = Profile.from_raw(
        {
            "inputUrl": "https://instagram.com/a.b_c__",
            "username": "a.b_c__",
            "fullName": "",
            "biography": biography,
            "followersCount": 5_000,
            "postsCount": 100,
            "private": False,
            "latestPosts": posts,
        },
        0,
    )
    classify_profile(profile)
    return profile


def test_exact_component_formula_calculations() -> None:
    posts = [
        raw_post(
            index,
            caption="fashion beauty lifestyle ugc wildberries",
            mentions=["product"],
        )
        for index in range(6)
    ]
    profile = profile_with_posts(posts)
    metrics = {0: calculate_metrics(profile, AS_OF)}
    score = score_profiles([profile], metrics, None)[0]
    components = {item.name: item.score for item in score.components}
    assert components == {
        "content_and_aesthetic_fit": 28.0,
        "native_product_integration_potential": 14.0,
        "short_video_consistency": 15.0,
        "engagement": 15.0,
        "barter_feasibility": 8.0,
        "recent_activity": 6.0,
    }
    assert score.raw_engagement_score == 15.0
    assert score.engagement_confidence == 1.0
    assert score.total == 86.0


def test_ugc_does_not_create_commercial_pr_signal() -> None:
    profile = profile_with_posts(
        [raw_post(index, caption="UGC creator") for index in range(6)]
    )
    signals = detect_signals(profile)
    assert signals["ugc"].detected is True
    assert signals["commercial_pr"].detected is False
    assert "ugc-as-commercial-signal" not in signals["commercial_pr"].matches


def test_contact_only_does_not_create_commercial_and_gets_only_contact_points() -> None:
    profile = profile_with_posts(
        [raw_post(index) for index in range(6)],
        biography="contact@example.com Telegram available",
    )
    metrics = {0: calculate_metrics(profile, AS_OF)}
    signals = metrics[0].signals
    assert signals["contact"].detected is True
    assert signals["commercial_pr"].detected is False
    assert signals["native_product_integration"].detected is False
    score = score_profiles([profile], metrics, None)[0]
    components = {item.name: item.score for item in score.components}
    assert components["native_product_integration_potential"] == 2.0
    assert components["barter_feasibility"] == 5.0


def test_paid_partnership_only_is_commercial_not_native_integration() -> None:
    profile = profile_with_posts(
        [
            raw_post(index, paid_partnership=(index == 0))
            for index in range(6)
        ]
    )
    signals = detect_signals(profile)
    assert signals["commercial_pr"].detected is True
    assert signals["native_product_integration"].detected is False
    assert {
        item.source_field for item in signals["commercial_pr"].provenance
    } == {"latestPosts.paidPartnership"}


def test_native_integration_requires_independent_post_level_evidence() -> None:
    empty_profile = profile_with_posts([raw_post(index) for index in range(6)])
    integrated_profile = profile_with_posts(
        [
            raw_post(0, caption="Product review and promo code SAVE10"),
            *[raw_post(index) for index in range(1, 6)],
        ]
    )
    assert detect_signals(empty_profile)["native_product_integration"].detected is False
    integration = detect_signals(integrated_profile)["native_product_integration"]
    assert integration.detected is True
    assert integration.provenance
    assert all(
        item.signal_type == "native_product_integration"
        and item.source_field.startswith("latestPosts.")
        and item.observation_type == "direct"
        for item in integration.provenance
    )


def test_paid_partnership_and_product_content_support_distinct_dimensions() -> None:
    profile = profile_with_posts(
        [
            raw_post(
                0,
                caption="Unboxing and product review",
                paid_partnership=True,
            ),
            *[raw_post(index) for index in range(1, 6)],
        ]
    )
    signals = detect_signals(profile)
    assert signals["commercial_pr"].detected is True
    assert signals["native_product_integration"].detected is True
    assert {
        item.source_field for item in signals["commercial_pr"].provenance
    } == {"latestPosts.paidPartnership"}
    assert {
        item.source_field for item in signals["native_product_integration"].provenance
    } == {"latestPosts.caption"}


def test_no_signal_is_created_only_because_another_signal_is_true() -> None:
    contact_only = profile_with_posts(
        [raw_post(index) for index in range(6)],
        biography="contact@example.com",
    )
    paid_only = profile_with_posts(
        [raw_post(index, paid_partnership=(index == 0)) for index in range(6)]
    )
    ugc_only = profile_with_posts(
        [raw_post(index, caption="UGC creator") for index in range(6)]
    )
    assert detect_signals(contact_only)["commercial_pr"].detected is False
    assert detect_signals(paid_only)["native_product_integration"].detected is False
    ugc_signals = detect_signals(ugc_only)
    assert ugc_signals["commercial_pr"].detected is False
    assert ugc_signals["native_product_integration"].detected is False


def test_every_detected_scoring_signal_has_explicit_provenance() -> None:
    profile = profile_with_posts(
        [
            raw_post(
                0,
                caption="UGC fashion product review with promo code",
                mentions=["brand.account"],
                paid_partnership=True,
            ),
            *[raw_post(index) for index in range(1, 6)],
        ],
        biography="PR collaboration; contact@example.com",
    )
    for signal_type, signal in detect_signals(profile).items():
        if not signal.detected:
            continue
        assert signal.provenance, signal_type
        assert all(
            item.signal_type == signal_type
            and item.source_field
            and item.source_reference
            and item.evidence_text
            and item.observation_type in {"direct", "derived"}
            for item in signal.provenance
        )


def test_partial_minus_one_likes_adjusts_engagement_without_zero_imputation() -> None:
    posts = [raw_post(index) for index in range(6)]
    posts.extend(raw_post(index, likes=-1) for index in range(6, 8))
    profile = profile_with_posts(posts)
    metrics = calculate_metrics(profile, AS_OF)
    assert profile.status == ProfileStatus.CREATOR
    assert metrics.sampled_posts == 8
    assert metrics.usable_engagement_posts == 6
    assert metrics.engagement_confidence == pytest.approx(0.75)
    assert metrics.median_likes == 10
    assert metrics.engagement_rate_pct == pytest.approx(0.24)
    score = score_profiles([profile], {0: metrics}, None)[0]
    assert score.raw_engagement_score == 15.0
    assert score.components[3].score == 11.25


def test_fewer_than_six_usable_engagement_posts_is_insufficient() -> None:
    posts = [raw_post(index) for index in range(5)]
    posts.append(raw_post(5, likes=-1))
    profile = profile_with_posts(posts)
    assert profile.status == ProfileStatus.INSUFFICIENT_DATA
    assert "6+ posts with usable likes and comments" in (profile.exclusion_reason or "")


def test_percentile_ties_and_small_cohorts() -> None:
    assert average_tie_percentile({7: 2.0}) == {7: 1.0}
    assert average_tie_percentile({1: 1.0, 2: 1.0, 3: 3.0}) == {
        1: 0.25,
        2: 0.25,
        3: 1.0,
    }


def test_quartile_linear_interpolation() -> None:
    values = [0.0, 10.0, 20.0, 30.0]
    assert percentile(values, 0.25) == 7.5
    assert distribution_summary(values) == {
        "q1": 7.5,
        "median": 15.0,
        "q3": 22.5,
    }


def test_all_six_excel_mismatches_are_exact_and_traceable() -> None:
    result = analyze_dataset(
        RAW / "instagram_profiles.json",
        RAW / "manual_verification_audit.json",
        next(RAW.glob("*.xlsx")),
    )
    actual = [
        (
            item["row"],
            item["display_username"],
            item["selected_username"],
        )
        for item in result.workbook_reconciliation["display_target_mismatches"]
    ]
    assert actual == [
        (10, "demoiselle._.rie", "mademoiselle._.marie"),
        (20, "sha_obzor.wb", "masha_obzor.wb"),
        (27, None, "mishandkatya"),
        (47, "habakher", "ksiushabakher"),
        (52, "ri_vls", "mari_vls"),
        (57, "rtini.a13", "martini.a13"),
    ]
    provenance = result.manual_audit["manual_human_in_the_loop_recovery"][
        "raw_provenance"
    ]
    assert provenance["issue_detected_by"] == "Pavel"
    assert provenance["verified_mismatch_count"] == 6


def test_mademoiselle_is_insufficient_not_unresolved() -> None:
    result = analyze_dataset(
        RAW / "instagram_profiles.json",
        RAW / "manual_verification_audit.json",
        next(RAW.glob("*.xlsx")),
    )
    profile = next(
        item for item in result.profiles if item.username == "mademoiselle._.marie"
    )
    assert profile.status == ProfileStatus.INSUFFICIENT_DATA
    assert result.metrics[profile.source_index].usable_engagement_posts == 0


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (None, 0.0),
        (7, 6.0),
        (7.0001, 5.0),
        (14, 5.0),
        (14.0001, 4.0),
        (30, 4.0),
        (30.0001, 2.0),
        (60, 2.0),
        (60.0001, 1.0),
        (90, 1.0),
        (90.0001, 0.0),
    ],
)
def test_recency_boundaries(days: float | None, expected: float) -> None:
    assert recency_points(days) == expected


@pytest.mark.parametrize(
    ("posts_per_week", "expected"),
    [(None, 0.0), (0.0, 0.0), (0.5, 1.0), (1.0, 2.0), (2.0, 4.0), (3.0, 4.0)],
)
def test_frequency_boundaries(posts_per_week: float | None, expected: float) -> None:
    assert frequency_points(posts_per_week) == expected


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    malformed = tmp_path / "broken.json"
    malformed.write_text("[}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON at line"):
        read_json(malformed)


def test_wrong_top_level_input_shape_is_rejected(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({"not": "an array"}), encoding="utf-8")
    with pytest.raises(ValueError, match="top-level JSON value must be an array"):
        analyze_dataset(
            profiles,
            RAW / "manual_verification_audit.json",
            next(RAW.glob("*.xlsx")),
        )


def test_missing_input_files_return_exit_code_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["demo", "--input-dir", str(tmp_path)]) == 2
    error = capsys.readouterr().err
    assert "Missing required input file(s)" in error
    assert "instagram_profiles.json" in error


def test_dots_and_underscores_are_preserved_exactly() -> None:
    raw = json.loads((RAW / "instagram_profiles.json").read_text(encoding="utf-8"))
    profiles = [Profile.from_raw(item, index) for index, item in enumerate(raw)]
    assert [profile.username for profile in profiles] == [
        item.get("username") for item in raw
    ]
    names = {profile.username for profile in profiles}
    assert {
        "_crazy___unicorn_",
        "mademoiselle._.marie",
        "irina.titovaaaa",
        "miysta_fatt_",
    } <= names
