from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_product_builder.cli import main
from ai_product_builder.phase_b.config import (
    load_env_secrets,
    load_phase_b_config,
)
from ai_product_builder.phase_b.errors import (
    ConfigurationError,
    InputValidationError,
    MissingSecretError,
)
from ai_product_builder.phase_b.pipeline import (
    run_phase_b,
    validate_phase_b_config,
)


ROOT = Path(__file__).resolve().parents[1]
DEMO_CONFIG = ROOT / "config/phase_b.demo.json"
LIVE_CONFIG = ROOT / "config/phase_b.live.example.json"


def _absolute_config(source: Path) -> dict[str, object]:
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw["inputs"] = {
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
    if raw["provider"]["type"] == "fixture":  # type: ignore[index]
        raw["provider"]["fixture"] = {  # type: ignore[index]
            "discovery_path": str(
                (
                    ROOT / "data/fixtures/phase_b/discovery_results.json"
                ).resolve()
            ),
            "profiles_path": str(
                (
                    ROOT / "data/fixtures/phase_b/enriched_profiles.json"
                ).resolve()
            ),
        }
    return raw


def test_malformed_and_missing_config_files_raise_typed_errors(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"mode": "demo",', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="invalid JSON"):
        load_phase_b_config(malformed)

    with pytest.raises(ConfigurationError, match="not found"):
        load_phase_b_config(tmp_path / "missing.json")


def test_validation_reports_missing_input_before_any_provider_work(
    tmp_path: Path,
) -> None:
    raw = _absolute_config(DEMO_CONFIG)
    missing = tmp_path / "does-not-exist.csv"
    raw["inputs"]["source_analysis"] = str(missing)  # type: ignore[index]
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "demo.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(InputValidationError) as raised:
        validate_phase_b_config(path, expected_mode="demo")
    assert str(missing) in raised.value.details["missing"][0]


def test_env_loader_reads_only_allowlisted_values_from_configured_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "must-not-be-read-from-process-env")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APIFY_TOKEN='from-file'",
                "GOOGLE_SERVICE_ACCOUNT_JSON='{\"type\":\"service_account\"}'",
                "LLM_PROVIDER=future-provider",
                "UNRELATED_SECRET=must-be-ignored",
            ]
        ),
        encoding="utf-8",
    )

    secrets = load_env_secrets(env_file)
    assert secrets == {
        "APIFY_TOKEN": "from-file",
        "GOOGLE_SERVICE_ACCOUNT_JSON": '{"type":"service_account"}',
        "LLM_PROVIDER": "future-provider",
    }
    assert load_env_secrets(tmp_path / "absent.env") == {}


@pytest.mark.parametrize(
    "secret_key",
    ["APIFY_TOKEN", "GOOGLE_SERVICE_ACCOUNT_JSON", "LLM_API_KEY"],
)
def test_json_config_rejects_secret_bearing_keys(
    tmp_path: Path, secret_key: str
) -> None:
    raw = _absolute_config(DEMO_CONFIG)
    raw[secret_key] = "must-live-in-dotenv"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "demo.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=secret_key):
        load_phase_b_config(path)


def test_live_run_ignores_process_environment_token_and_requires_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _absolute_config(LIVE_CONFIG)
    absent_env = tmp_path / "deliberately-absent.env"
    raw["env_file"] = str(absent_env)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "live.json"
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    monkeypatch.setenv("APIFY_TOKEN", "process-env-token-is-forbidden")

    with pytest.raises(MissingSecretError, match="required in .env"):
        run_phase_b(
            path,
            tmp_path / "output",
            expected_mode="live",
        )


def test_cli_does_not_accept_secret_flags() -> None:
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "phase-b",
                "run",
                "--mode",
                "live",
                "--config",
                str(LIVE_CONFIG),
                "--apify-token",
                "forbidden",
            ]
        )
    assert raised.value.code == 2


@pytest.mark.parametrize(
    ("field_path", "value", "message"),
    [
        (("discovery", "final_count"), 6, "between 3 and 5"),
        (
            ("eligibility", "minimum_usable_posts"),
            5,
            "cannot be lower than 6",
        ),
        (
            ("eligibility", "maximum_recency_days"),
            91,
            "cannot exceed 90",
        ),
    ],
)
def test_mvp_safety_bounds_are_validated(
    tmp_path: Path,
    field_path: tuple[str, str],
    value: int,
    message: str,
) -> None:
    raw = _absolute_config(DEMO_CONFIG)
    raw[field_path[0]][field_path[1]] = value  # type: ignore[index]
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "demo.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        validate_phase_b_config(path, expected_mode="demo")
