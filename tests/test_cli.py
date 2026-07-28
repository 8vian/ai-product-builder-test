from __future__ import annotations

import csv
import json
from pathlib import Path

from ai_product_builder.cli import main


ROOT = Path(__file__).resolve().parents[1]


def test_demo_generates_required_outputs(tmp_path: Path) -> None:
    output = tmp_path / "output"
    exit_code = main(
        [
            "demo",
            "--input-dir",
            str(ROOT / "data" / "raw"),
            "--output-dir",
            str(output),
        ]
    )
    assert exit_code == 0
    expected = {
        "source_analysis.csv",
        "ideal_creator_profile.json",
        "data_quality_report.json",
        "analysis_report.md",
        "scoring_methodology.md",
    }
    assert {path.name for path in output.iterdir()} == expected
    quality = json.loads(
        (output / "data_quality_report.json").read_text(encoding="utf-8")
    )
    with (output / "source_analysis.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == quality["dataset_summary"]["total_records"]
    assert sum(
        quality["dataset_summary"]["counts_by_status"].values()
    ) == quality["dataset_summary"]["total_records"]
    assert all(row["score_explanation"] for row in rows)
    raw_profiles = json.loads(
        (ROOT / "data" / "raw" / "instagram_profiles.json").read_text(encoding="utf-8")
    )
    assert [row["username"] for row in rows] == [
        profile.get("username") or "" for profile in raw_profiles
    ]
    exact_verified_usernames = {
        "_crazy___unicorn_",
        "lv_yana_vl",
        "irina.titovaaaa",
        "nikaanow",
    }
    assert exact_verified_usernames <= {row["username"] for row in rows}
    ideal = json.loads(
        (output / "ideal_creator_profile.json").read_text(encoding="utf-8")
    )
    assert exact_verified_usernames <= {
        item["username"] for item in ideal["scoring_results"]
    }

    quality_qa = quality["human_in_the_loop_qa"]
    assert len(quality_qa["manually_verified_corrections"]) == 4
    assert quality_qa["remaining_unresolved"] == [
        "nev_pollyy",
        "19.voron",
        "miysta_fatt_",
    ]
    assert any(
        item["candidate_username"] == "aparina_"
        for item in quality_qa["rejected_false_leads"]
    )
