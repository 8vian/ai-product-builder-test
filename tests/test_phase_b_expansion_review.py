from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_product_builder.cli import build_parser
from ai_product_builder.phase_b.errors import OutputWriteError
from ai_product_builder.phase_b.expansion_review import (
    EXPANSION_REVIEW_ARTIFACTS,
    assess_near_miss,
    run_expansion_review,
)
from ai_product_builder.phase_b.providers.apify import (
    ApifyInstagramProvider,
)


SOURCE_RUN = Path("output/phase_b/live-20260728T211907Z")
MANUAL_FINAL_RUN = Path(
    "output/phase_b/live-20260728T211907Z-manual-final"
)
REVIEW_FILE = Path(
    "data/reviews/phase_b/"
    "live-20260728T211907Z-manual-final.json"
)
FINAL_SHORTLIST = (
    {
        "username": "verkhovskaya_style",
        "score": 51.57,
        "manual_verification_status": "approved",
        "recent_post_url": (
            "https://www.instagram.com/p/DZE_YfrDegn/"
        ),
    },
    {
        "username": "olganemka_stylist",
        "score": 54.66,
        "manual_verification_status": "approved",
        "recent_post_url": (
            "https://www.instagram.com/p/DbSlmTzNZUg/"
        ),
    },
    {
        "username": "angelashegiryan",
        "score": 57.7,
        "manual_verification_status": "pending",
        "recent_post_url": (
            "https://www.instagram.com/p/DbTRVFYtEgD/"
        ),
    },
)


def _tree_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): hashlib.sha256(
            item.read_bytes()
        ).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file() and not item.name.startswith("~$")
    }


def _saved_records() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for line in (
        SOURCE_RUN / "enriched_candidates.jsonl"
    ).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        username = record["profile"]["identity"]["username"]
        result[username] = record
    return result


@pytest.fixture(scope="module")
def expansion_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, dict, tuple[dict, ...]]:
    output = tmp_path_factory.mktemp("expansion-review") / "run"
    source_before = _tree_hashes(SOURCE_RUN)
    manual_before = _tree_hashes(MANUAL_FINAL_RUN)
    review_before = hashlib.sha256(REVIEW_FILE.read_bytes()).hexdigest()

    def _provider_forbidden(*_args, **_kwargs):
        raise AssertionError(
            "provider construction or request is forbidden"
        )

    with (
        patch.object(
            ApifyInstagramProvider,
            "__init__",
            _provider_forbidden,
        ),
        patch.object(
            ApifyInstagramProvider,
            "discover",
            _provider_forbidden,
        ),
        patch.object(
            ApifyInstagramProvider,
            "enrich",
            _provider_forbidden,
        ),
    ):
        manifest, _, candidates = run_expansion_review(
            SOURCE_RUN,
            MANUAL_FINAL_RUN,
            output,
            review_path=REVIEW_FILE,
        )

    assert source_before == _tree_hashes(SOURCE_RUN)
    assert manual_before == _tree_hashes(MANUAL_FINAL_RUN)
    assert review_before == hashlib.sha256(
        REVIEW_FILE.read_bytes()
    ).hexdigest()
    return output, manifest, candidates


def test_expansion_review_cli_is_strictly_offline() -> None:
    args = build_parser().parse_args(
        [
            "phase-b",
            "expansion-review",
            "--source-run",
            str(SOURCE_RUN),
            "--manual-final-run",
            str(MANUAL_FINAL_RUN),
            "--review-file",
            str(REVIEW_FILE),
            "--output-dir",
            "output/phase_b/example-expansion-review",
        ]
    )

    assert args.phase_b_command == "expansion-review"
    assert not hasattr(args, "config")
    assert not hasattr(args, "mode")


def test_offline_run_writes_exactly_seven_diagnostic_artifacts(
    expansion_run: tuple[Path, dict, tuple[dict, ...]],
) -> None:
    output, manifest, _ = expansion_run

    assert {item.name for item in output.iterdir()} == set(
        EXPANSION_REVIEW_ARTIFACTS
    )
    assert manifest["provider_requests_made"] == 0
    assert manifest["budget_spent_usd"] == 0.0
    assert manifest["outreach_messages_sent"] == 0
    assert manifest["offers_generated"] == 0
    assert manifest["diagnostic_only"] is True
    assert manifest["final_shortlist_unchanged"] is True
    assert manifest["counts"] == {
        "enriched_profiles_reviewed": 26,
        "source_eligible_profiles": 6,
        "source_noneligible_profiles": 20,
        "focus_candidates": 1,
        "qualified_near_miss_candidates": 0,
        "expansion_candidates": 1,
        "priority_a": 0,
        "priority_b": 1,
        "rejected_noneligible_profiles": 20,
    }


def test_final_shortlist_is_preserved_byte_for_byte_in_meaning(
    expansion_run: tuple[Path, dict, tuple[dict, ...]],
) -> None:
    _, manifest, _ = expansion_run

    assert tuple(manifest["final_shortlist"]) == FINAL_SHORTLIST


def test_stylistelenaialena_has_complete_priority_b_card(
    expansion_run: tuple[Path, dict, tuple[dict, ...]],
) -> None:
    output, _, candidates = expansion_run
    card = json.loads(
        (output / "stylistelenaialena_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(candidates) == 1
    assert candidates[0]["username"] == "stylistelenaialena"
    assert candidates[0]["proposed_decision"] == "needs_manual_review"
    assert card["recommended_manual_status"] == (
        "candidate_for_manual_review"
    )
    assert card["priority"] == "Priority B"
    assert card["account_type"] == "personal_creator"
    assert card["followers"] == 374
    assert card["sampled_posts"] == 12
    assert card["usable_posts"] == 12
    assert card["known_format_posts"] == 2
    assert card["source_score"] == pytest.approx(41.22)
    assert card["engagement_rate"] == pytest.approx(
        7.48663101604278
    )
    assert card["barter_evidence"] == []
    assert card["no_barter_evidence"] == []
    assert card["own_brand_evidence"] == []
    assert card["own_store_showroom_evidence"] == []
    assert card["agency_platform_evidence"] == []
    assert card["latest_post_url"] == (
        "https://www.instagram.com/p/DYFK6HoCNl2/"
    )
    assert card["recent_fashion_post_url"] == (
        "https://www.instagram.com/p/DWEAlrPCOc9/"
    )
    assert card["latest_known_format_post_url"] == (
        "https://www.instagram.com/p/DWyM52OjDZi/"
    )
    assert card["commercial_pr_evidence"][0][
        "source_field"
    ] == "profile.biography"


def test_no_near_miss_survives_strict_saved_pool_review(
    expansion_run: tuple[Path, dict, tuple[dict, ...]],
) -> None:
    output, _, _ = expansion_run
    payload = json.loads(
        (output / "near_miss_personal_candidates.json").read_text(
            encoding="utf-8"
        )
    )
    with (
        output / "near_miss_personal_candidates.csv"
    ).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert payload == []
    assert rows == []


def test_expansion_candidates_have_no_offer_or_sendable_status(
    expansion_run: tuple[Path, dict, tuple[dict, ...]],
) -> None:
    output, _, _ = expansion_run
    candidates = json.loads(
        (output / "expansion_candidates.json").read_text(
            encoding="utf-8"
        )
    )

    assert all(
        item["proposed_decision"] == "needs_manual_review"
        for item in candidates
    )
    assert all(
        "offer" not in key.casefold()
        for item in candidates
        for key in item
    )
    assert "barter_offer_drafts.md" not in {
        path.name for path in output.iterdir()
    }


@pytest.mark.parametrize(
    ("username", "expected_blocker"),
    (
        ("malina_fashion", "commercial_conflict:own_fashion_brand"),
        ("theonlyone_brand", "account_type:store"),
        (
            "jeba_jeru_fashion",
            "account_type:thematic_non_personal_page",
        ),
        ("fashion_assistant", "post_format_data_missing"),
        ("stylist_katy", "insufficient_engagement_data:0<6"),
        ("ainazik.wildberries", "latest_post_older_than_90_days"),
        ("curvy_josies_fashion", "campaign_language_not_ru"),
    ),
)
def test_hard_blocks_cannot_be_reconsidered(
    username: str,
    expected_blocker: str,
) -> None:
    assessment = assess_near_miss(_saved_records()[username])

    assert assessment["eligible_near_miss"] is False
    assert expected_blocker in assessment["hard_blocking_reasons"]


def test_explicit_no_barter_can_never_be_a_near_miss() -> None:
    record = copy.deepcopy(_saved_records()["curvy_josies_fashion"])
    record["barter_signal_assessment"]["explicit_refusal"] = True
    record["barter_signal_assessment"]["no_barter_evidence"] = [
        {
            "signal_type": "no_barter",
            "source_field": "recent_posts.caption",
            "source_reference": "saved-post",
            "evidence_text": "Не работаю по бартеру",
            "observation_type": "direct",
        }
    ]

    assessment = assess_near_miss(record)

    assert assessment["eligible_near_miss"] is False
    assert "explicit_no_barter_statement" in (
        assessment["hard_blocking_reasons"]
    )


def test_report_explains_no_offline_path_to_five_and_review_targets(
    expansion_run: tuple[Path, dict, tuple[dict, ...]],
) -> None:
    output, _, _ = expansion_run
    report = (output / "expansion_review_report.md").read_text(
        encoding="utf-8"
    )

    assert "Qualified near-miss profiles: `0`" in report
    assert "Priority A" in report
    assert "Priority B" in report
    assert "Rejected profiles" in report
    assert "https://www.instagram.com/stylistelenaialena/" in report
    assert "https://www.instagram.com/p/DYFK6HoCNl2/" in report
    assert "https://www.instagram.com/p/DWEAlrPCOc9/" in report
    assert "https://www.instagram.com/p/DWyM52OjDZi/" in report
    assert "максимум четыре подтверждённых профиля" in report
    assert "`malina_fashion`" in report
    assert "`theonlyone_brand`" in report
    assert "`stylist_katy`" in report
    assert "`curvy_josies_fashion`" in report


def test_existing_or_nested_output_is_rejected(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "exists"
    existing.mkdir()

    with pytest.raises(OutputWriteError, match="already exists"):
        run_expansion_review(
            SOURCE_RUN,
            MANUAL_FINAL_RUN,
            existing,
            review_path=REVIEW_FILE,
        )

    with pytest.raises(OutputWriteError, match="cannot be nested"):
        run_expansion_review(
            SOURCE_RUN,
            MANUAL_FINAL_RUN,
            SOURCE_RUN / "forbidden-output",
            review_path=REVIEW_FILE,
        )
