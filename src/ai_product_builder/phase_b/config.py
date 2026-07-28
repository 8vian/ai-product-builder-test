"""Phase B non-secret configuration and explicit .env secret loading."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigurationError, MissingSecretError
from .models import CampaignBrief

ALLOWED_SECRET_NAMES = frozenset(
    {
        "APIFY_TOKEN",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "LLM_API_KEY",
        "LLM_PROVIDER",
        "LLM_MODEL",
    }
)


@dataclass(frozen=True, slots=True)
class InputConfig:
    ideal_creator_profile: Path
    source_analysis: Path
    instagram_profiles: Path
    manual_audit: Path
    workbook: Path


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    target_pool_size: int = 30
    minimum_unique_pool: int = 20
    final_count: int = 5
    minimum_final_count: int = 3


@dataclass(frozen=True, slots=True)
class EligibilityConfig:
    minimum_usable_posts: int = 6
    maximum_recency_days: float = 90.0


@dataclass(frozen=True, slots=True)
class FixtureProviderConfig:
    discovery_path: Path
    profiles_path: Path


@dataclass(frozen=True, slots=True)
class ApifyProviderConfig:
    discovery_actor_id: str
    enrichment_actor_id: str
    discovery_input_template: Mapping[str, Any]
    enrichment_input_template: Mapping[str, Any]
    discovery_field_mapping: Mapping[str, str]
    profile_field_mapping: Mapping[str, str]
    post_field_mapping: Mapping[str, str]
    maximum_items: int = 100
    timeout_seconds: int = 180
    max_retries: int = 3
    api_base_url: str = "https://api.apify.com/v2"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    type: str
    fixture: FixtureProviderConfig | None = None
    apify: ApifyProviderConfig | None = None


@dataclass(frozen=True, slots=True)
class OutputConfig:
    workbook_sheet: str = "Новые блоггеры"
    csv_encoding: str = "utf-8-sig"


@dataclass(frozen=True, slots=True)
class GoogleSheetsConfig:
    enabled: bool = False
    spreadsheet_id: str | None = None
    worksheet_name: str = "Новые блоггеры"


@dataclass(frozen=True, slots=True)
class PhaseBConfig:
    mode: str
    campaign: CampaignBrief
    inputs: InputConfig
    discovery: DiscoveryConfig
    eligibility: EligibilityConfig
    provider: ProviderConfig
    outputs: OutputConfig = field(default_factory=OutputConfig)
    google_sheets: GoogleSheetsConfig = field(default_factory=GoogleSheetsConfig)
    env_file: Path = Path(".env")
    analysis_as_of: str | None = None

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any], *, base_dir: Path = Path(".")
    ) -> "PhaseBConfig":
        if not isinstance(raw, Mapping):
            raise ConfigurationError("Phase B configuration must be a JSON object")
        _reject_secret_config(raw)
        mode = str(raw.get("mode") or "").casefold()
        if mode not in {"demo", "live"}:
            raise ConfigurationError("mode must be 'demo' or 'live'")
        try:
            campaign = CampaignBrief.from_dict(_mapping(raw, "campaign"))
            inputs_raw = _mapping(raw, "inputs")
            inputs = InputConfig(
                ideal_creator_profile=_path(
                    base_dir, inputs_raw, "ideal_creator_profile"
                ),
                source_analysis=_path(base_dir, inputs_raw, "source_analysis"),
                instagram_profiles=_path(base_dir, inputs_raw, "instagram_profiles"),
                manual_audit=_path(base_dir, inputs_raw, "manual_audit"),
                workbook=_path(base_dir, inputs_raw, "workbook"),
            )
            discovery_raw = _mapping(raw, "discovery")
            discovery = DiscoveryConfig(
                target_pool_size=_positive_int(
                    discovery_raw, "target_pool_size", default=30
                ),
                minimum_unique_pool=_positive_int(
                    discovery_raw, "minimum_unique_pool", default=20
                ),
                final_count=_positive_int(discovery_raw, "final_count", default=5),
                minimum_final_count=_positive_int(
                    discovery_raw, "minimum_final_count", default=3
                ),
            )
            eligibility_raw = _mapping(raw, "eligibility", required=False)
            eligibility = EligibilityConfig(
                minimum_usable_posts=_positive_int(
                    eligibility_raw, "minimum_usable_posts", default=6
                ),
                maximum_recency_days=_positive_float(
                    eligibility_raw, "maximum_recency_days", default=90.0
                ),
            )
            provider = _provider_config(_mapping(raw, "provider"), base_dir)
            outputs_raw = _mapping(raw, "outputs", required=False)
            outputs = OutputConfig(
                workbook_sheet=_non_empty_string(
                    outputs_raw, "workbook_sheet", default="Новые блоггеры"
                ),
                csv_encoding=_non_empty_string(
                    outputs_raw, "csv_encoding", default="utf-8-sig"
                ),
            )
            sheets_raw = _mapping(raw, "google_sheets", required=False)
            sheets = GoogleSheetsConfig(
                enabled=bool(sheets_raw.get("enabled", False)),
                spreadsheet_id=_optional_string(sheets_raw.get("spreadsheet_id")),
                worksheet_name=_non_empty_string(
                    sheets_raw, "worksheet_name", default="Новые блоггеры"
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(str(exc)) from exc
        if mode == "demo" and provider.type != "fixture":
            raise ConfigurationError("demo mode requires provider.type='fixture'")
        if mode == "live" and provider.type != "apify":
            raise ConfigurationError("live mode requires provider.type='apify'")
        if discovery.minimum_final_count > discovery.final_count:
            raise ConfigurationError(
                "discovery.minimum_final_count cannot exceed final_count"
            )
        if discovery.final_count > discovery.target_pool_size:
            raise ConfigurationError(
                "discovery.final_count cannot exceed target_pool_size"
            )
        env_value = raw.get("env_file", ".env")
        if not isinstance(env_value, str) or not env_value.strip():
            raise ConfigurationError("env_file must be a non-empty path")
        return cls(
            mode=mode,
            campaign=campaign,
            inputs=inputs,
            discovery=discovery,
            eligibility=eligibility,
            provider=provider,
            outputs=outputs,
            google_sheets=sheets,
            env_file=_resolve(base_dir, env_value),
            analysis_as_of=_optional_string(raw.get("analysis_as_of")),
        )


def load_phase_b_config(path: Path) -> PhaseBConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Phase B config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    return PhaseBConfig.from_dict(raw, base_dir=path.resolve().parent.parent)


def load_env_secrets(path: Path) -> dict[str, str]:
    """Read the allowed secrets only from the configured .env file."""

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(f"{path}:{line_number}: expected NAME=value")
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in ALLOWED_SECRET_NAMES:
            continue
        cleaned = value.strip()
        if (
            len(cleaned) >= 2
            and cleaned[0] == cleaned[-1]
            and cleaned[0] in {"'", '"'}
        ):
            cleaned = cleaned[1:-1]
        result[name] = cleaned
    return result


def require_secret(secrets: Mapping[str, str], name: str) -> str:
    value = secrets.get(name)
    if not value:
        raise MissingSecretError(
            f"{name} is required in .env for this operation",
            details={"secret": name},
        )
    return value


def _provider_config(raw: Mapping[str, Any], base_dir: Path) -> ProviderConfig:
    provider_type = str(raw.get("type") or "").casefold()
    if provider_type not in {"fixture", "apify"}:
        raise ValueError("provider.type must be 'fixture' or 'apify'")
    fixture: FixtureProviderConfig | None = None
    apify: ApifyProviderConfig | None = None
    if provider_type == "fixture":
        fixture_raw = _mapping(raw, "fixture")
        fixture = FixtureProviderConfig(
            discovery_path=_path(base_dir, fixture_raw, "discovery_path"),
            profiles_path=_path(base_dir, fixture_raw, "profiles_path"),
        )
    else:
        apify_raw = _mapping(raw, "apify")
        mappings = _mapping(apify_raw, "field_mappings")
        maximum_items = _positive_int(apify_raw, "maximum_items", default=100)
        timeout_seconds = _positive_int(
            apify_raw, "timeout_seconds", default=180
        )
        max_retries = _non_negative_int(apify_raw, "max_retries", default=3)
        if max_retries > 3:
            raise ValueError("provider.apify.max_retries cannot exceed 3")
        apify = ApifyProviderConfig(
            discovery_actor_id=_non_empty_string(
                apify_raw, "discovery_actor_id"
            ),
            enrichment_actor_id=_non_empty_string(
                apify_raw, "enrichment_actor_id"
            ),
            discovery_input_template=_mapping(
                apify_raw, "discovery_input_template"
            ),
            enrichment_input_template=_mapping(
                apify_raw, "enrichment_input_template"
            ),
            discovery_field_mapping=_string_mapping(
                _mapping(mappings, "discovery")
            ),
            profile_field_mapping=_string_mapping(_mapping(mappings, "profile")),
            post_field_mapping=_string_mapping(_mapping(mappings, "post")),
            maximum_items=maximum_items,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            api_base_url=_non_empty_string(
                apify_raw,
                "api_base_url",
                default="https://api.apify.com/v2",
            ).rstrip("/"),
        )
    return ProviderConfig(type=provider_type, fixture=fixture, apify=apify)


def _mapping(
    raw: Mapping[str, Any], key: str, *, required: bool = True
) -> Mapping[str, Any]:
    value = raw.get(key)
    if value is None and not required:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return value


def _string_mapping(raw: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            raise ValueError("field mappings must contain non-empty string paths")
        result[key] = value.strip()
    return result


def _path(base_dir: Path, raw: Mapping[str, Any], key: str) -> Path:
    value = _non_empty_string(raw, key)
    return _resolve(base_dir, value)


def _resolve(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base_dir / path).resolve()


def _non_empty_string(
    raw: Mapping[str, Any], key: str, *, default: str | None = None
) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional string value must be a string or null")
    return value.strip() or None


def _positive_int(
    raw: Mapping[str, Any], key: str, *, default: int
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _non_negative_int(
    raw: Mapping[str, Any], key: str, *, default: int
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _positive_float(
    raw: Mapping[str, Any], key: str, *, default: float
) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{key} must be a positive number")
    return float(value)


def _reject_secret_config(value: Any, path: str = "config") -> None:
    """Reject secret material in JSON so it can only originate from .env."""

    forbidden_names = {
        "apify_token",
        "google_service_account_json",
        "google_oauth_token_json",
        "llm_api_key",
        "api_key",
        "access_token",
        "authorization",
        "password",
        "client_secret",
    }
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            folded = key.casefold()
            if (
                folded in forbidden_names
                or folded.endswith(("_token", "_api_key", "_password", "_secret"))
                or "credential" in folded
            ):
                raise ConfigurationError(
                    f"{path}.{key}: secrets are forbidden in config JSON; use .env"
                )
            _reject_secret_config(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_config(item, f"{path}[{index}]")
    elif isinstance(value, str):
        folded_value = value.strip().casefold()
        if folded_value in {name.casefold() for name in ALLOWED_SECRET_NAMES}:
            raise ConfigurationError(
                f"{path}: secret environment-variable names must not be config values"
            )
