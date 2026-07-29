from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_product_builder.phase_b.models import (
    CandidateIdentity,
    CreatorProfile,
    RecentPost,
)
from ai_product_builder.phase_b.offline_reselection import reselect_saved_run
from ai_product_builder.phase_b.providers.apify import ApifyInstagramProvider


def _profile(
    username: str,
    *,
    followers: int,
    biography: str,
) -> CreatorProfile:
    collected_at = datetime(2026, 7, 28, 19, tzinfo=timezone.utc)
    posts = tuple(
        RecentPost(
            post_id=f"{username}-{index}",
            url=(
                "https://www.instagram.com/p/"
                f"{username.replace('.', '').replace('_', '')}{index}/"
            ),
            caption=f"Outfit try-on and clothing review number {index}",
            likes=100 + index,
            comments=10 + index,
            timestamp=collected_at - timedelta(days=index),
            post_format="short_video",
        )
        for index in range(1, 7)
    )
    return CreatorProfile(
        identity=CandidateIdentity(
            platform="instagram",
            username=username,
            normalized_username=username.casefold(),
            profile_url=f"https://www.instagram.com/{username}/",
            canonical_profile_url=f"https://www.instagram.com/{username}/",
            query_ids=("fashion_test",),
            provider_ids=(f"id-{username}",),
        ),
        full_name=f"Creator {username}",
        biography=biography,
        followers=followers,
        posts_count=50,
        private=False,
        accessible=True,
        recent_posts=posts,
        provider="apify",
        provider_run_ids=("saved-enrichment-run",),
        provider_identity_confidence=1.0,
        query_ids=("fashion_test",),
        collected_at=collected_at,
    )


def _write_saved_source_run(path: Path) -> tuple[str, ...]:
    path.mkdir()
    usernames = (
        "fashion.one_",
        "fashion.two",
        "fashion_three",
        "fashion.four_",
        "fashion_five",
    )
    profiles = [
        _profile(
            username,
            followers=777_795 if index == 0 else 10_000 + index * 1_000,
            biography="Content creator with outfit styling and clothing try-ons.",
        )
        for index, username in enumerate(usernames)
    ]
    manifest = {
        "run_id": "saved-live-test",
        "status": "completed",
        "provider_run_ids": ["saved-discovery-run", "saved-enrichment-run"],
        "counts": {
            "raw_discovery_hits": len(profiles),
            "enriched_candidates": len(profiles),
        },
    }
    (path / "run_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (path / "generated_queries.json").write_text("[]", encoding="utf-8")
    with (path / "discovery_pool.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as stream:
        for profile in profiles:
            stream.write(
                json.dumps(
                    {
                        "username": profile.identity.username,
                        "profile_url": profile.identity.profile_url,
                    }
                )
                + "\n"
            )
    (path / "deduplication_report.json").write_text(
        json.dumps(
            {
                "unique_candidate_count": len(profiles),
                "duplicate_hit_count": 0,
                "exclusion_reason_counts": {},
            }
        ),
        encoding="utf-8",
    )
    with (path / "enriched_candidates.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as stream:
        for profile in profiles:
            stream.write(
                json.dumps(
                    {"profile": profile.to_dict()},
                    ensure_ascii=False,
                )
                + "\n"
            )
    with (path / "eligible_candidates.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=["username"])
        writer.writeheader()
        writer.writerows({"username": username} for username in usernames)
    return usernames


def test_offline_reselection_never_calls_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "saved-run"
    output = tmp_path / "reviewed-run"
    usernames = _write_saved_source_run(source)
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_run_id": "saved-live-test",
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )
    source_hashes = {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in source.iterdir()
        if item.is_file()
    }

    def _network_forbidden(*_args, **_kwargs):
        raise AssertionError("provider network method must not be called")

    monkeypatch.setattr(ApifyInstagramProvider, "discover", _network_forbidden)
    monkeypatch.setattr(ApifyInstagramProvider, "enrich", _network_forbidden)

    result = reselect_saved_run(
        source,
        output,
        config_path=Path("config/phase_b.live.example.json"),
        review_path=review,
    )

    assert result.manifest.offline_reselection is True
    assert result.manifest.source_run_id == "saved-live-test"
    assert result.manifest.provider_requests_made == 0
    assert result.manifest.budget_spent_usd == 0.0
    assert result.manifest.counts["selected_candidates"] == 5
    assert {item.username for item in result.selected_candidates} == set(
        usernames
    )
    assert all(
        item.manual_verification_status.value == "pending"
        for item in result.selected_candidates
    )
    extreme = next(
        item
        for item in result.selected_candidates
        if item.username == "fashion.one_"
    )
    assert extreme.barter_feasibility_review_required is True
    assert "q3 + 3×IQR" in extreme.barter_feasibility_explanation
    assert all((output / name).is_file() for name in result.manifest.artifacts)
    assert source_hashes == {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in source.iterdir()
        if item.is_file()
    }
