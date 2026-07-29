from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request

import pytest

from ai_product_builder.phase_b.recovery import (
    ApifyExistingRunReader,
    recover_completed_apify_runs,
)


ROOT = Path(__file__).resolve().parents[1]
SEARCH_RUN_ID = "Search123"
PROFILE_RUN_ID = "Profile456"


class _Response:
    def __init__(self, raw: bytes):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.raw


class _ReadOnlyOpener:
    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads
        self.requests: list[Request] = []

    def __call__(self, request: Request, *, timeout: int):
        assert timeout > 0
        self.requests.append(request)
        url = request.full_url
        if url not in self.payloads:
            raise AssertionError(f"Unexpected recovery URL: {url}")
        return _Response(self.payloads[url])


def _metadata(
    run_id: str, dataset_id: str, *, usage: float
) -> bytes:
    return json.dumps(
        {
            "data": {
                "id": run_id,
                "status": "SUCCEEDED",
                "defaultDatasetId": dataset_id,
                "startedAt": "2026-07-28T23:00:00Z",
                "finishedAt": "2026-07-28T23:01:00Z",
                "usageTotalUsd": usage,
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _payloads(
    *,
    search_dataset: bytes = b"[]\n",
    profile_dataset: bytes = b"[]\n",
) -> dict[str, bytes]:
    base = "https://api.apify.com/v2"
    return {
        f"{base}/actor-runs/{SEARCH_RUN_ID}": _metadata(
            SEARCH_RUN_ID, "SearchDataset", usage=0.08
        ),
        (
            f"{base}/datasets/SearchDataset/items?"
            "format=json&clean=false"
        ): search_dataset,
        f"{base}/actor-runs/{PROFILE_RUN_ID}": _metadata(
            PROFILE_RUN_ID, "ProfileDataset", usage=0.04
        ),
        (
            f"{base}/datasets/ProfileDataset/items?"
            "format=json&clean=false"
        ): profile_dataset,
    }


def _recovery_config(tmp_path: Path) -> Path:
    config = json.loads(
        (ROOT / "config/phase_b.live.example.json").read_text(
            encoding="utf-8"
        )
    )
    config["env_file"] = str((tmp_path / ".env").resolve())
    config["inputs"] = {
        "ideal_creator_profile": str(
            (ROOT / "output/ideal_creator_profile.json").resolve()
        ),
        "source_analysis": str(
            (ROOT / "output/source_analysis.csv").resolve()
        ),
        "instagram_profiles": str(
            (ROOT / "data/raw/instagram_profiles.json").resolve()
        ),
        "manual_audit": str(
            (ROOT / "data/raw/manual_verification_audit.json").resolve()
        ),
        "workbook": str((ROOT / "data/raw/Блогеры.xlsx").resolve()),
    }
    config["campaign"]["geography"] = "Россия"
    config["campaign"]["target_content_languages"] = ["ru"]
    config["campaign"]["delivery_markets"] = ["Россия"]
    config["eligibility"].update(
        {
            "minimum_recent_fashion_posts": 3,
            "minimum_short_video_posts": 1,
            "preferred_followers_min": 1_000,
            "preferred_followers_max": 100_000,
        }
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "recovery.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "APIFY_TOKEN=test-token\n", encoding="utf-8"
    )
    return config_path


def _failed_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "live-failed"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "provider_run_ids": [SEARCH_RUN_ID, PROFILE_RUN_ID],
                "review_summary": {
                    "provider_charges": [
                        {
                            "run_id": SEARCH_RUN_ID,
                            "charge_usd": 0.08,
                        },
                        {
                            "run_id": PROFILE_RUN_ID,
                            "charge_usd": 0.04,
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_existing_run_reader_uses_only_get_for_exact_existing_ids() -> None:
    opener = _ReadOnlyOpener(_payloads())
    reader = ApifyExistingRunReader(
        api_base_url="https://api.apify.com/v2",
        token="test-token",
        timeout_seconds=30,
        max_retries=0,
        opener=opener,
    )

    snapshot = reader.read_existing_run(SEARCH_RUN_ID)

    assert snapshot.run_id == SEARCH_RUN_ID
    assert snapshot.raw_dataset == b"[]\n"
    assert reader.new_actor_runs_started == 0
    assert len(opener.requests) == 2
    assert all(request.get_method() == "GET" for request in opener.requests)
    assert "/acts/" not in " ".join(
        request.full_url for request in opener.requests
    )


@pytest.fixture()
def recovered_empty_pool(tmp_path: Path):
    source_run = _failed_run(tmp_path)
    source_before = (source_run / "run_manifest.json").read_bytes()
    opener = _ReadOnlyOpener(_payloads())
    output_dir = tmp_path / "recovered"
    result = recover_completed_apify_runs(
        config_path=_recovery_config(tmp_path),
        source_failed_run=source_run,
        search_run_id=SEARCH_RUN_ID,
        profile_run_id=PROFILE_RUN_ID,
        output_dir=output_dir,
        opener=opener,
        clock=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    return result, opener, source_run, source_before


def test_recovery_does_not_apply_minimum_final_count(
    recovered_empty_pool,
) -> None:
    result, _, _, _ = recovered_empty_pool
    assert result.manifest["counts"]["strict_eligible"] == 0
    assert result.manifest["minimum_final_count_applied"] is False
    assert result.manifest["final_shortlist_created"] is False


def test_recovery_starts_no_actors_and_sends_no_outreach(
    recovered_empty_pool,
) -> None:
    result, opener, _, _ = recovered_empty_pool
    assert result.manifest["new_actor_runs_started"] == 0
    assert result.manifest["search_actor_runs_started"] == 0
    assert result.manifest["profile_actor_runs_started"] == 0
    assert result.manifest["outreach_messages_sent"] == 0
    assert len(opener.requests) == 4
    assert all(request.get_method() == "GET" for request in opener.requests)


def test_recovery_preserves_raw_responses_exactly(
    recovered_empty_pool,
) -> None:
    result, _, _, _ = recovered_empty_pool
    assert (
        result.output_dir / "search_dataset_raw.json"
    ).read_bytes() == b"[]\n"
    assert (
        result.output_dir / "profile_dataset_raw.json"
    ).read_bytes() == b"[]\n"
    assert (
        result.output_dir / "search_run_metadata.json"
    ).read_bytes() == _metadata(
        SEARCH_RUN_ID, "SearchDataset", usage=0.08
    )


def test_recovery_leaves_source_run_and_phase_a_unchanged(
    recovered_empty_pool,
) -> None:
    result, _, source_run, source_before = recovered_empty_pool
    assert (source_run / "run_manifest.json").read_bytes() == source_before
    assert result.manifest["previous_run_directories_unchanged"] is True
    assert result.manifest["phase_a_unchanged"] is True
    assert (
        result.manifest["phase_a_sha256_before"]
        == result.manifest["phase_a_sha256_after"]
    )


def test_recovery_writes_complete_zero_pool_audit(
    recovered_empty_pool,
) -> None:
    result, _, _, _ = recovered_empty_pool
    expected = {
        "discovery_pool.jsonl",
        "deduplication_report.json",
        "enriched_candidates.jsonl",
        "eligible_candidates.csv",
        "excluded_candidates.csv",
        "near_miss_candidates.json",
        "near_miss_candidates.csv",
        "recovery_report.md",
        "recovery_manifest.json",
    }
    assert expected <= {path.name for path in result.generated_paths}
    assert result.manifest["additional_actor_charges_usd"] == 0.0
    near_miss_header = (
        result.output_dir / "near_miss_candidates.csv"
    ).read_text(encoding="utf-8-sig").splitlines()[0]
    assert near_miss_header.startswith("username,profile_url,followers,score")
