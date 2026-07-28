from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_product_builder.cli import main
from ai_product_builder.phase_b.errors import InsufficientCandidatePoolError
from ai_product_builder.phase_b.exclusions import ExclusionRegistry
from ai_product_builder.phase_b.io import PHASE_B_COLUMNS
from ai_product_builder.phase_b.io.artifacts import ARTIFACT_FILENAMES
from ai_product_builder.phase_b.models import DiscoveryHit
from ai_product_builder.phase_b.pipeline import (
    deduplicate_discovery_hits,
    run_phase_b,
    validate_phase_b_config,
)


ROOT = Path(__file__).resolve().parents[1]
DEMO_CONFIG = ROOT / "config" / "phase_b.demo.json"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hit(
    username: str,
    *,
    profile_url: str | None = None,
    query_ids: tuple[str, ...] = ("q1",),
    platform: str = "instagram",
    provider_id: str | None = None,
) -> DiscoveryHit:
    return DiscoveryHit(
        platform=platform,
        username=username,
        profile_url=profile_url
        if profile_url is not None
        else f"https://www.instagram.com/{username}/",
        provider="fixture",
        query_ids=query_ids,
        provider_id=provider_id,
        provider_identity_confidence=0.9,
    )


def test_deduplication_merges_case_and_url_duplicates_with_query_provenance() -> None:
    hits = [
        _hit(
            "_Dots.__And_Underscores_",
            query_ids=("q1",),
            provider_id="item-1",
        ),
        _hit(
            "_dots.__and_underscores_",
            profile_url=(
                "https://www.instagram.com/_dots.__and_underscores_/?hl=ru"
            ),
            query_ids=("q2", "unknown"),
            provider_id="item-2",
        ),
        _hit("", profile_url="", provider_id="bad"),
        _hit("elsewhere", platform="tiktok"),
    ]

    outcome = deduplicate_discovery_hits(
        hits,
        ExclusionRegistry(()),
        known_query_ids=("q1", "q2"),
    )

    assert len(outcome.identities) == 1
    identity = outcome.identities[0]
    assert identity.username == "_Dots.__And_Underscores_"
    assert identity.normalized_username == "_dots.__and_underscores_"
    assert identity.canonical_profile_url == (
        "https://www.instagram.com/_dots.__and_underscores_/"
    )
    assert identity.query_ids == ("q1", "q2")
    assert identity.provider_ids == ("item-1", "item-2")
    assert identity.identity_conflict is False
    assert outcome.report["raw_hit_count"] == 4
    assert outcome.report["unique_candidate_count"] == 1
    assert outcome.report["duplicate_hit_count"] == 1
    assert outcome.report["exclusion_reason_counts"] == {
        "duplicate_merged": 1,
        "malformed_discovery_record": 1,
        "unsupported_platform": 1,
    }


def test_deduplication_excludes_phase_a_alias_before_candidate_creation() -> None:
    registry = ExclusionRegistry.from_files(
        ROOT / "data/raw/instagram_profiles.json",
        ROOT / "data/raw/manual_verification_audit.json",
    )
    outcome = deduplicate_discovery_hits(
        [
            _hit("__aparina"),
            _hit("nikaanow"),
            _hit("aparina_"),
            _hit("brand.new.creator"),
        ],
        registry,
        known_query_ids=("q1",),
    )

    assert [item.username for item in outcome.identities] == [
        "brand.new.creator"
    ]
    assert outcome.report["exclusion_reason_counts"] == {
        "source_exclusion": 3
    }
    registry_reasons = {
        row["username"]: set(
            row["exclusion_registry_match"]["reasons"]  # type: ignore[index]
        )
        for row in outcome.excluded_records
    }
    assert "historical_alias" in registry_reasons["__aparina"]
    assert "verified_replacement" in registry_reasons["nikaanow"]
    assert "rejected_false_lead" in registry_reasons["aparina_"]


def test_conflicting_username_and_profile_url_remain_traceable_for_review() -> None:
    outcome = deduplicate_discovery_hits(
        [
            _hit(
                "first.identity",
                profile_url="https://www.instagram.com/second.identity/",
            )
        ],
        ExclusionRegistry(()),
    )

    assert len(outcome.identities) == 1
    assert outcome.identities[0].identity_conflict is True
    assert outcome.report["groups"][0]["identity_conflict"] is True  # type: ignore[index]


def test_demo_config_validates_without_credentials_and_has_exact_campaign() -> None:
    config, warnings = validate_phase_b_config(
        DEMO_CONFIG, expected_mode="demo"
    )

    assert warnings == ()
    assert config.mode == "demo"
    assert config.provider.type == "fixture"
    assert config.outputs.workbook_sheet == "Новые блоггеры"
    assert config.campaign.brand_name == "LD Latte"
    assert config.campaign.product_name == "товар из новой коллекции LD Latte"
    assert config.campaign.product_category == "женская одежда"
    assert config.campaign.barter_item == (
        "товар бренда в обмен на согласованный контент"
    )
    assert config.campaign.desired_content_format == "Reels или нативный обзор"
    assert config.campaign.language == "ru"
    assert config.campaign.geography is None


@pytest.fixture(scope="module")
def demo_result(tmp_path_factory: pytest.TempPathFactory):
    output_dir = tmp_path_factory.mktemp("phase-b-e2e")
    raw_workbook = ROOT / "data/raw/Блогеры.xlsx"
    before = _hash(raw_workbook)
    result = run_phase_b(DEMO_CONFIG, output_dir, expected_mode="demo")
    assert _hash(raw_workbook) == before
    return result


def test_credential_free_demo_generates_all_required_artifacts(demo_result) -> None:
    assert demo_result.manifest.status == "completed"
    assert demo_result.manifest.mode == "demo"
    assert demo_result.manifest.provider == "fixture"
    assert demo_result.manifest.run_id == "demo"
    assert demo_result.manifest.counts == {
        "generated_queries": 5,
        "raw_discovery_hits": 36,
        "source_exclusions": 6,
        "malformed_discovery_records": 2,
        "duplicate_discoveries": 2,
        "unique_candidates": 25,
        "enriched_candidates": 25,
        "eligible_candidates": 15,
        "ineligible_candidates": 10,
        "excluded_records_total": 21,
        "selected_candidates": 5,
    }
    assert {path.name for path in demo_result.generated_paths} == set(
        ARTIFACT_FILENAMES
    )
    assert all(path.is_file() for path in demo_result.generated_paths)
    assert set(demo_result.manifest.artifacts) == set(ARTIFACT_FILENAMES)


def test_demo_final_five_are_ranked_eligible_new_creators_with_grounded_drafts(
    demo_result,
) -> None:
    expected_ranking = [
        "reels.by.sonya",
        "mira_reels.ru",
        "beauty.offer.test",
        "beauty.and.city",
        "ugc_by_lena",
    ]
    assert [item.username for item in demo_result.selected_candidates] == (
        expected_ranking
    )
    assert all(
        left.score >= right.score
        for left, right in zip(
            demo_result.selected_candidates,
            demo_result.selected_candidates[1:],
        )
    )
    registry = ExclusionRegistry.from_files(
        ROOT / "data/raw/instagram_profiles.json",
        ROOT / "data/raw/manual_verification_audit.json",
    )
    for candidate in demo_result.selected_candidates:
        assert candidate.eligibility_status.value == "eligible"
        assert candidate.manual_verification_status.value == "pending"
        assert registry.match(candidate.username, candidate.profile_url) is None
        assert candidate.recent_post_url in candidate.barter_offer
        assert candidate.profile_url.startswith("https://www.instagram.com/")
        assert candidate.recent_post_url.startswith(
            "https://www.instagram.com/"
        )
        assert candidate.evidence
        assert all(
            item.source_field
            and item.source_reference
            and item.evidence_text
            and item.observation_type in {"direct", "derived"}
            for item in candidate.evidence
        )
        assert "[Черновик:" in candidate.barter_offer

    payload = json.loads(
        (demo_result.run_dir / "new_creators.json").read_text(encoding="utf-8")
    )
    assert [item["username"] for item in payload] == expected_ranking
    assert all(set(PHASE_B_COLUMNS) <= set(item) for item in payload)


def _subset_demo_config(
    tmp_path: Path, usernames: list[str]
) -> Path:
    config = json.loads(DEMO_CONFIG.read_text(encoding="utf-8"))
    config["inputs"] = {
        "ideal_creator_profile": str(
            (ROOT / "output/ideal_creator_profile.json").resolve()
        ),
        "source_analysis": str((ROOT / "output/source_analysis.csv").resolve()),
        "instagram_profiles": str(
            (ROOT / "data/raw/instagram_profiles.json").resolve()
        ),
        "manual_audit": str(
            (ROOT / "data/raw/manual_verification_audit.json").resolve()
        ),
        "workbook": str((ROOT / "data/raw/Блогеры.xlsx").resolve()),
    }
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    all_profiles = json.loads(
        (ROOT / "data/fixtures/phase_b/enriched_profiles.json").read_text(
            encoding="utf-8"
        )
    )["records"]
    profiles = [
        item for item in all_profiles if item.get("username") in set(usernames)
    ]
    assert len(profiles) == len(usernames)
    profile_by_username = {item["username"]: item for item in profiles}
    discoveries = []
    for index in range(30):
        username = usernames[index % len(usernames)]
        profile = profile_by_username[username]
        discoveries.append(
            {
                "platform": "instagram",
                "username": username,
                "profile_url": f"https://www.instagram.com/{username}/",
                "display_name": profile.get("full_name", username),
                "biography": profile.get("biography", ""),
                "followers": profile.get("followers"),
                "private": False,
                "accessible": True,
                "provider_identity_confidence": 0.9,
                "provider_id": f"subset-{index}",
                "provider_run_id": "subset-discovery",
                "query_ids": ["fashion_style_01", "reels_integrations_01"],
                "collected_at": config["analysis_as_of"],
            }
        )
    discovery_path = fixture_dir / "discovery.json"
    profiles_path = fixture_dir / "profiles.json"
    discovery_path.write_text(
        json.dumps({"records": discoveries}), encoding="utf-8"
    )
    profiles_path.write_text(
        json.dumps({"records": profiles}), encoding="utf-8"
    )
    config["provider"]["fixture"] = {
        "discovery_path": str(discovery_path.resolve()),
        "profiles_path": str(profiles_path.resolve()),
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "phase_b.demo.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return config_path


def test_pipeline_fails_instead_of_padding_when_fewer_than_three_qualify(
    tmp_path: Path,
) -> None:
    config_path = _subset_demo_config(
        tmp_path, ["reels.by.sonya", "mira_reels.ru"]
    )
    output_dir = tmp_path / "output"

    with pytest.raises(InsufficientCandidatePoolError) as raised:
        run_phase_b(config_path, output_dir, expected_mode="demo")
    assert raised.value.details["eligible_count"] == 2
    failed_manifest = json.loads(
        (output_dir / "demo/run_manifest.json").read_text(encoding="utf-8")
    )
    assert failed_manifest["status"] == "failed"
    assert failed_manifest["errors"][0]["category"] == (
        "insufficient_candidate_pool"
    )


def test_pipeline_returns_four_with_incomplete_target_warning(
    tmp_path: Path,
) -> None:
    usernames = [
        "reels.by.sonya",
        "mira_reels.ru",
        "beauty.offer.test",
        "beauty.and.city",
    ]
    config_path = _subset_demo_config(tmp_path, usernames)
    result = run_phase_b(
        config_path, tmp_path / "output", expected_mode="demo"
    )

    assert len(result.selected_candidates) == 4
    assert {item.username for item in result.selected_candidates} == set(
        usernames
    )
    assert any(
        "Incomplete target: selected 4" in warning
        for warning in result.manifest.warnings
    )


def test_cli_demo_and_validation_commands_succeed_without_env_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "phase-b",
                "validate-config",
                "--config",
                str(DEMO_CONFIG),
            ]
        )
        == 0
    )
    assert "configuration is valid" in capsys.readouterr().out

    assert (
        main(
            [
                "phase-b",
                "demo",
                "--config",
                str(DEMO_CONFIG),
                "--output-dir",
                str(tmp_path / "cli-output"),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Phase B complete" in output
    assert "Selected creators:" in output
    assert (tmp_path / "cli-output/demo/new_creators.json").is_file()


def test_cli_has_no_send_or_outreach_command() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["phase-b", "send"])
    assert raised.value.code == 2
