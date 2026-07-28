"""End-to-end orchestration for the Phase B Instagram MVP."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .config import (
    PhaseBConfig,
    load_env_secrets,
    load_phase_b_config,
    require_secret,
)
from .eligibility import evaluate_candidate_eligibility
from .enrichment import calculate_candidate_metrics
from .errors import (
    ConfigurationError,
    EvidenceValidationError,
    InputValidationError,
    OutputWriteError,
    PhaseBError,
)
from .evidence import collect_signal_evidence, flatten_evidence
from .exclusions import ExclusionMatch, ExclusionRegistry
from .io.artifacts import (
    ARTIFACT_FILENAMES,
    generate_phase_b_artifacts,
    run_directory,
)
from .io.google_sheets import (
    GoogleSheetsAdapter,
    GoogleSheetsWriteReceipt,
    build_google_sheets_client,
)
from .models import (
    CandidateIdentity,
    CandidateResult,
    CreatorProfile,
    DiscoveryHit,
    EligibilityDecision,
    PhaseBRunManifest,
    QuerySpec,
    SignalEvidence,
    parse_datetime,
)
from .normalization import canonicalize_profile_url, normalize_username
from .offers import generate_deterministic_offer
from .providers.apify import ApifyInstagramProvider
from .providers.base import InstagramProvider
from .providers.fixtures import FixtureInstagramProvider
from .queries import generate_queries
from .scoring import (
    calculate_discovery_confidence,
    load_phase_a_reference,
    score_candidate,
)
from .selection import (
    rank_candidates,
    select_top_candidates,
    selection_explanation,
)


@dataclass(frozen=True, slots=True)
class DeduplicationOutcome:
    identities: tuple[CandidateIdentity, ...]
    report: Mapping[str, Any]
    excluded_records: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PhaseBRunResult:
    manifest: PhaseBRunManifest
    run_dir: Path
    generated_paths: tuple[Path, ...]
    selected_candidates: tuple[CandidateResult, ...]
    eligible_candidates: tuple[CandidateResult, ...]
    google_sheets_receipt: GoogleSheetsWriteReceipt | None = None


def validate_phase_b_config(
    config_path: Path,
    *,
    expected_mode: str | None = None,
) -> tuple[PhaseBConfig, tuple[str, ...]]:
    """Load configuration and validate all non-secret local dependencies."""

    config_path = Path(config_path)
    config = load_phase_b_config(config_path)
    if expected_mode is not None and config.mode != expected_mode:
        raise ConfigurationError(
            f"Command mode '{expected_mode}' does not match config mode "
            f"'{config.mode}'."
        )
    if not 3 <= config.discovery.final_count <= 5:
        raise ConfigurationError(
            "discovery.final_count must be between 3 and 5 for the MVP"
        )
    if config.discovery.minimum_final_count != 3:
        raise ConfigurationError(
            "discovery.minimum_final_count must be 3 for the MVP"
        )
    if config.discovery.minimum_unique_pool <= config.discovery.final_count:
        raise ConfigurationError(
            "discovery.minimum_unique_pool must be larger than final_count"
        )
    if config.eligibility.minimum_usable_posts < 6:
        raise ConfigurationError(
            "eligibility.minimum_usable_posts cannot be lower than 6"
        )
    if config.eligibility.maximum_recency_days > 90:
        raise ConfigurationError(
            "eligibility.maximum_recency_days cannot exceed 90"
        )
    if config.outputs.csv_encoding.casefold() != "utf-8-sig":
        raise ConfigurationError(
            "outputs.csv_encoding must be 'utf-8-sig' for the MVP"
        )
    if config.outputs.workbook_sheet != "Новые блоггеры":
        raise ConfigurationError(
            "outputs.workbook_sheet must be exactly 'Новые блоггеры'"
        )

    required_files = {
        "ideal_creator_profile": config.inputs.ideal_creator_profile,
        "source_analysis": config.inputs.source_analysis,
        "instagram_profiles": config.inputs.instagram_profiles,
        "manual_audit": config.inputs.manual_audit,
        "workbook": config.inputs.workbook,
    }
    if config.provider.type == "fixture":
        if config.provider.fixture is None:
            raise ConfigurationError("fixture provider configuration is missing")
        required_files.update(
            {
                "fixture.discovery_path": config.provider.fixture.discovery_path,
                "fixture.profiles_path": config.provider.fixture.profiles_path,
            }
        )
    missing = [
        f"{name}: {path}"
        for name, path in required_files.items()
        if not Path(path).is_file()
    ]
    if missing:
        raise InputValidationError(
            "Required Phase B input file(s) are missing.",
            details={"missing": missing},
        )

    ideal = _read_json_object(
        config.inputs.ideal_creator_profile, "ideal_creator_profile"
    )
    # Query construction is also a schema validation pass.
    generate_queries(ideal, config.campaign)
    _validate_source_analysis(config.inputs.source_analysis)
    ExclusionRegistry.from_files(
        config.inputs.instagram_profiles, config.inputs.manual_audit
    )
    _validate_workbook(config.inputs.workbook)

    if config.analysis_as_of is not None and parse_datetime(config.analysis_as_of) is None:
        raise ConfigurationError("analysis_as_of must be a valid ISO-8601 timestamp")

    warnings: list[str] = []
    if config.provider.type == "fixture":
        fixture = config.provider.fixture
        assert fixture is not None
        provider = FixtureInstagramProvider(
            fixture.discovery_path, fixture.profiles_path
        )
        fixture_hits = provider.discover(())
        if len(fixture_hits) < 30:
            raise InputValidationError(
                "Demo discovery fixture must contain at least 30 records.",
                details={"record_count": len(fixture_hits)},
            )
    else:
        apify = config.provider.apify
        if apify is None:
            raise ConfigurationError("Apify provider configuration is missing")
        # Construction validates required mappings without making a request.
        ApifyInstagramProvider(apify, "configuration-validation-only")
        secrets = load_env_secrets(config.env_file)
        if not secrets.get("APIFY_TOKEN"):
            warnings.append(
                f"Live execution requires APIFY_TOKEN in {config.env_file}."
            )
        if (
            apify.discovery_actor_id.startswith("REPLACE_WITH_")
            or apify.enrichment_actor_id.startswith("REPLACE_WITH_")
        ):
            warnings.append(
                "Live actor IDs are placeholders and must be replaced before "
                "the first Apify smoke test."
            )

    if config.google_sheets.enabled:
        if not config.google_sheets.spreadsheet_id:
            raise ConfigurationError(
                "google_sheets.spreadsheet_id is required when enabled"
            )
        secrets = load_env_secrets(config.env_file)
        if not secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
            warnings.append(
                "Google Sheets is enabled, but GOOGLE_SERVICE_ACCOUNT_JSON is "
                f"missing from {config.env_file}."
            )
    return config, tuple(warnings)


def deduplicate_discovery_hits(
    hits: Sequence[DiscoveryHit],
    exclusion_registry: ExclusionRegistry,
    *,
    known_query_ids: Iterable[str] = (),
) -> DeduplicationOutcome:
    """Exclude frozen identities and merge discoveries by username/profile URL."""

    allowed_query_ids = frozenset(known_query_ids)
    prepared: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            # Stable first-seen roots keep fixture runs byte-for-byte predictable.
            parent[right_root] = left_root

    for index, hit in enumerate(hits):
        username_key = normalize_username(hit.username)
        url_key = normalize_username(hit.profile_url)
        if hit.platform.casefold() != "instagram":
            excluded.append(
                _excluded_hit(hit, "unsupported_platform", hit.validation_issues)
            )
            continue
        if username_key is None and url_key is None:
            excluded.append(
                _excluded_hit(
                    hit,
                    "malformed_discovery_record",
                    hit.validation_issues
                    or ("missing or malformed Instagram identity",),
                )
            )
            continue
        exclusion = exclusion_registry.match(hit.username, hit.profile_url)
        if exclusion is not None:
            excluded.append(
                _excluded_hit(
                    hit,
                    "source_exclusion",
                    exclusion.reasons,
                    exclusion_match=exclusion,
                )
            )
            continue

        tokens = tuple(dict.fromkeys(value for value in (username_key, url_key) if value))
        for token in tokens:
            find(token)
        if len(tokens) == 2:
            union(tokens[0], tokens[1])
        query_ids = tuple(
            dict.fromkeys(
                query_id
                for query_id in hit.query_ids
                if not allowed_query_ids or query_id in allowed_query_ids
            )
        )
        prepared.append(
            {
                "index": index,
                "hit": hit,
                "tokens": tokens,
                "query_ids": query_ids,
                "identity_conflict": len(tokens) > 1,
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in prepared:
        root = find(item["tokens"][0])
        grouped.setdefault(root, []).append(item)

    identities: list[CandidateIdentity] = []
    groups_report: list[dict[str, Any]] = []
    duplicate_count = 0
    for group in grouped.values():
        group.sort(key=lambda item: item["index"])
        first_hit: DiscoveryHit = group[0]["hit"]
        all_tokens = tuple(
            dict.fromkeys(token for item in group for token in item["tokens"])
        )
        normalized = (
            normalize_username(first_hit.username)
            or normalize_username(first_hit.profile_url)
            or all_tokens[0]
        )
        canonical = canonicalize_profile_url(normalized)
        if canonical is None:  # Defensive: prepared records always normalized.
            excluded.append(
                _excluded_hit(
                    first_hit,
                    "malformed_discovery_record",
                    ("could not construct a canonical profile URL",),
                )
            )
            continue
        username = (
            first_hit.username.strip()
            if normalize_username(first_hit.username) == normalized
            else normalized
        )
        query_ids = tuple(
            dict.fromkeys(
                query_id for item in group for query_id in item["query_ids"]
            )
        )
        provider_ids = tuple(
            dict.fromkeys(
                value
                for item in group
                for value in (
                    item["hit"].provider_id,
                    item["hit"].provider_run_id,
                )
                if value
            )
        )
        identity_conflict = (
            len(all_tokens) > 1
            or any(item["identity_conflict"] for item in group)
        )
        identity = CandidateIdentity(
            platform="instagram",
            username=username,
            normalized_username=normalized,
            profile_url=canonical,
            canonical_profile_url=canonical,
            query_ids=query_ids,
            provider_ids=provider_ids,
            identity_conflict=identity_conflict,
        )
        identities.append(identity)
        duplicate_count += max(0, len(group) - 1)
        for duplicate in group[1:]:
            excluded.append(
                _excluded_hit(
                    duplicate["hit"],
                    "duplicate_merged",
                    (f"merged_into:{normalized}",),
                )
            )
        groups_report.append(
            {
                "normalized_username": normalized,
                "canonical_profile_url": canonical,
                "merged_hit_count": len(group),
                "query_ids": list(query_ids),
                "provider_ids": list(provider_ids),
                "identity_conflict": identity_conflict,
            }
        )

    reason_counts = Counter(str(item["exclusion_reason"]) for item in excluded)
    report = {
        "raw_hit_count": len(hits),
        "processable_hit_count": len(prepared),
        "unique_candidate_count": len(identities),
        "duplicate_hit_count": duplicate_count,
        "excluded_record_count": len(excluded),
        "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "groups": groups_report,
    }
    return DeduplicationOutcome(
        identities=tuple(identities),
        report=report,
        excluded_records=tuple(excluded),
    )


def run_phase_b(
    config_path: Path,
    output_dir: Path,
    *,
    expected_mode: str | None = None,
    sheets_client: Any | None = None,
) -> PhaseBRunResult:
    """Run Phase B without any outreach or message-sending side effects."""

    config_path = Path(config_path)
    config, validation_warnings = validate_phase_b_config(
        config_path, expected_mode=expected_mode
    )
    config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    run_id = (
        "demo"
        if config.mode == "demo"
        else datetime.now(timezone.utc).strftime("live-%Y%m%dT%H%M%SZ")
    )
    manifest = PhaseBRunManifest.started(
        run_id=run_id,
        mode=config.mode,
        provider=config.provider.type,
        config_digest=config_digest,
    )
    manifest.warnings.extend(validation_warnings)
    run_dir = run_directory(Path(output_dir), manifest)
    run_dir.mkdir(parents=True, exist_ok=True)
    resolved_sheets_client = sheets_client
    if config.google_sheets.enabled and resolved_sheets_client is None:
        google_secret = load_env_secrets(config.env_file).get(
            "GOOGLE_SERVICE_ACCOUNT_JSON"
        )
        if google_secret:
            try:
                resolved_sheets_client = build_google_sheets_client(
                    google_secret
                )
            except (OSError, RuntimeError, ValueError) as exc:
                wrapped = ConfigurationError(
                    f"Could not configure Google Sheets: {exc}"
                )
                _record_failed_manifest(run_dir, manifest, wrapped)
                raise wrapped from exc

    try:
        ideal = _read_json_object(
            config.inputs.ideal_creator_profile, "ideal_creator_profile"
        )
        queries = generate_queries(ideal, config.campaign)
        exclusion_registry = ExclusionRegistry.from_files(
            config.inputs.instagram_profiles, config.inputs.manual_audit
        )
        provider = _build_provider(config)
        discovery_hits = provider.discover(queries)
        deduplicated = deduplicate_discovery_hits(
            discovery_hits,
            exclusion_registry,
            known_query_ids=(query.query_id for query in queries),
        )
        if (
            len(deduplicated.identities)
            < config.discovery.minimum_unique_pool
        ):
            manifest.warnings.append(
                "Unique discovery pool is below the configured target: "
                f"{len(deduplicated.identities)} < "
                f"{config.discovery.minimum_unique_pool}."
            )

        profiles = provider.enrich(deduplicated.identities)
        as_of = (
            parse_datetime(config.analysis_as_of)
            if config.analysis_as_of is not None
            else None
        )
        reference = load_phase_a_reference(
            config.inputs.source_analysis,
            config.inputs.ideal_creator_profile,
        )

        excluded_records = list(deduplicated.excluded_records)
        enriched_records: list[dict[str, Any]] = []
        eligible_results: list[CandidateResult] = []
        provider_run_ids = {
            value
            for hit in discovery_hits
            for value in (hit.provider_run_id,)
            if value
        }
        for profile in profiles:
            provider_run_ids.update(profile.provider_run_ids)
            metrics = calculate_candidate_metrics(profile, as_of)
            evidence = collect_signal_evidence(profile)
            decision = evaluate_candidate_eligibility(
                profile,
                metrics,
                evidence,
                as_of=as_of,
                max_recency_days=config.eligibility.maximum_recency_days,
                minimum_usable_posts=config.eligibility.minimum_usable_posts,
            )
            offer = None
            if decision.eligible:
                try:
                    offer = generate_deterministic_offer(
                        profile,
                        config.campaign,
                        evidence,
                        as_of=as_of,
                        max_age_days=config.eligibility.maximum_recency_days,
                    )
                except EvidenceValidationError as exc:
                    decision = EligibilityDecision(
                        eligible=False,
                        status="ineligible",
                        reasons=(
                            "offer_grounding_evidence_missing",
                            str(exc),
                        ),
                    )

            enriched_records.append(
                {
                    "profile": profile.to_dict(),
                    "metrics": metrics.to_dict(),
                    "evidence": {
                        name: [item.to_dict() for item in items]
                        for name, items in evidence.items()
                    },
                    "eligibility": decision.to_dict(),
                }
            )
            if not decision.eligible or offer is None:
                excluded_records.append(
                    _excluded_profile(profile, decision)
                )
                continue

            score = score_candidate(profile, metrics, evidence, reference)
            discovery_confidence = calculate_discovery_confidence(
                profile, evidence
            )
            confidence_evidence = SignalEvidence(
                signal_type="discovery_confidence",
                source_field="provider_and_discovery",
                source_reference=profile.identity.username,
                evidence_text=discovery_confidence.explanation,
                observation_type="derived",
                url=profile.identity.profile_url,
            )
            all_evidence = _unique_evidence(
                (
                    *flatten_evidence(evidence),
                    confidence_evidence,
                    *offer.evidence,
                )
            )
            collected_at = profile.collected_at or manifest.started_at
            eligible_results.append(
                CandidateResult(
                    platform=profile.identity.platform,
                    username=profile.identity.username,
                    profile_url=profile.identity.canonical_profile_url,
                    followers=metrics.followers,
                    median_likes=metrics.median_likes,
                    median_comments=metrics.median_comments,
                    engagement_rate=metrics.engagement_rate,
                    usable_posts=metrics.usable_posts,
                    sampled_posts=metrics.sampled_posts,
                    data_completeness=metrics.data_completeness,
                    short_video_share=metrics.short_video_share,
                    last_post_date=metrics.last_post_date,
                    score=score.score,
                    score_components=score.components,
                    selection_explanation=score.explanation,
                    evidence=all_evidence,
                    recent_post_url=offer.recent_post_url,
                    barter_offer=offer.text,
                    manual_verification_status=offer.manual_verification_status,
                    verification_notes="",
                    discovery_confidence=discovery_confidence.score,
                    eligibility_status=decision.status,
                    eligibility_reasons=decision.reasons,
                    query_ids=profile.query_ids,
                    provider=profile.provider,
                    collected_at=collected_at,
                    offer_generation_mode=offer.generation_mode,
                    source_exclusion_check=(
                        "clear: no Phase A username, URL, historical alias, "
                        "replacement, rejected false lead, Nike, or Apple match"
                    ),
                )
            )

        ranked = rank_candidates(eligible_results)
        ranked = [
            replace(
                candidate,
                selection_explanation=selection_explanation(
                    candidate, rank, len(ranked)
                ),
            )
            for rank, candidate in enumerate(ranked, start=1)
        ]
        selection = select_top_candidates(
            ranked,
            final_count=config.discovery.final_count,
            minimum_count=config.discovery.minimum_final_count,
        )
        if selection.warning:
            manifest.warnings.append(selection.warning)

        manifest.provider_run_ids = sorted(provider_run_ids)
        source_exclusion_count = sum(
            item.get("exclusion_reason") == "source_exclusion"
            for item in deduplicated.excluded_records
        )
        malformed_count = sum(
            item.get("exclusion_reason") == "malformed_discovery_record"
            for item in deduplicated.excluded_records
        )
        manifest.counts = {
            "generated_queries": len(queries),
            "raw_discovery_hits": len(discovery_hits),
            "source_exclusions": source_exclusion_count,
            "malformed_discovery_records": malformed_count,
            "duplicate_discoveries": int(
                deduplicated.report["duplicate_hit_count"]
            ),
            "unique_candidates": len(deduplicated.identities),
            "enriched_candidates": len(profiles),
            "eligible_candidates": len(ranked),
            "ineligible_candidates": len(profiles) - len(ranked),
            "excluded_records_total": len(excluded_records),
            "selected_candidates": len(selection.selected),
        }
        # Persistent provider-response caching is intentionally outside this
        # bounded MVP; report that explicitly instead of inventing cache misses.
        manifest.cache = {"enabled": 0, "hits": 0, "misses": 0}
        if config.google_sheets.enabled and resolved_sheets_client is None:
            manifest.warnings.append(
                "Google Sheets output was configured but no authenticated "
                "client was injected; local artifacts were generated."
            )
        manifest.status = "completed"
        manifest.completed_at = datetime.now(timezone.utc)
        manifest.artifacts = {
            name: str(run_dir / name) for name in ARTIFACT_FILENAMES
        }

        discovery_payload = [
            hit.to_dict(include_raw=True) for hit in discovery_hits
        ]
        try:
            generated_paths = generate_phase_b_artifacts(
                run_dir,
                manifest=manifest,
                queries=queries,
                discovery_pool=discovery_payload,
                deduplication_report=deduplicated.report,
                excluded_candidates=excluded_records,
                enriched_candidates=enriched_records,
                eligible_candidates=ranked,
                selected_candidates=selection.selected,
                source_workbook=config.inputs.workbook,
                workbook_sheet=config.outputs.workbook_sheet,
            )
        except (OSError, ValueError, KeyError) as exc:
            raise OutputWriteError(
                f"Could not generate Phase B artifacts: {exc}"
            ) from exc

        sheets_receipt: GoogleSheetsWriteReceipt | None = None
        if config.google_sheets.enabled:
            if resolved_sheets_client is not None:
                try:
                    adapter = GoogleSheetsAdapter(
                        resolved_sheets_client,
                        config.google_sheets.spreadsheet_id or "",
                        worksheet_name=config.google_sheets.worksheet_name,
                    )
                    sheets_receipt = adapter.upsert(selection.selected)
                except Exception as exc:
                    raise OutputWriteError(
                        f"Google Sheets upsert failed: {exc}"
                    ) from exc
                manifest.artifacts["google_sheets"] = (
                    f"{sheets_receipt.spreadsheet_id}#"
                    f"{sheets_receipt.worksheet_name}"
                )
                _write_manifest(run_dir / "run_manifest.json", manifest)

        return PhaseBRunResult(
            manifest=manifest,
            run_dir=run_dir,
            generated_paths=tuple(generated_paths),
            selected_candidates=selection.selected,
            eligible_candidates=tuple(ranked),
            google_sheets_receipt=sheets_receipt,
        )
    except PhaseBError as exc:
        _record_failed_manifest(run_dir, manifest, exc)
        raise
    except (OSError, ValueError, KeyError, csv.Error, json.JSONDecodeError) as exc:
        wrapped = InputValidationError(f"Phase B pipeline failed: {exc}")
        _record_failed_manifest(run_dir, manifest, wrapped)
        raise wrapped from exc


def _build_provider(config: PhaseBConfig) -> InstagramProvider:
    if config.provider.type == "fixture":
        fixture = config.provider.fixture
        if fixture is None:
            raise ConfigurationError("fixture provider configuration is missing")
        return FixtureInstagramProvider(
            fixture.discovery_path, fixture.profiles_path
        )
    apify = config.provider.apify
    if apify is None:
        raise ConfigurationError("Apify provider configuration is missing")
    secrets = load_env_secrets(config.env_file)
    return ApifyInstagramProvider(
        apify,
        require_secret(secrets, "APIFY_TOKEN"),
    )


def _excluded_hit(
    hit: DiscoveryHit,
    reason: str,
    reasons: Iterable[str],
    *,
    exclusion_match: ExclusionMatch | None = None,
) -> dict[str, Any]:
    normalized = normalize_username(hit.username) or normalize_username(
        hit.profile_url
    )
    result: dict[str, Any] = {
        "platform": hit.platform,
        "username": hit.username,
        "profile_url": hit.profile_url,
        "normalized_username": normalized or "",
        "exclusion_reason": reason,
        "exclusion_reasons": list(reasons),
        "query_ids": list(hit.query_ids),
        "provider": hit.provider,
    }
    if exclusion_match is not None:
        result["exclusion_registry_match"] = exclusion_match.to_dict()
    return result


def _excluded_profile(
    profile: CreatorProfile, decision: EligibilityDecision
) -> dict[str, Any]:
    return {
        "platform": profile.identity.platform,
        "username": profile.identity.username,
        "profile_url": profile.identity.canonical_profile_url,
        "normalized_username": profile.identity.normalized_username,
        "exclusion_reason": decision.status.value,
        "exclusion_reasons": list(decision.reasons),
        "query_ids": list(profile.query_ids),
        "provider": profile.provider,
    }


def _unique_evidence(
    evidence: Iterable[SignalEvidence],
) -> tuple[SignalEvidence, ...]:
    unique: list[SignalEvidence] = []
    seen: set[tuple[str, str, str, str, str | None]] = set()
    for item in evidence:
        key = (
            item.signal_type,
            item.source_field,
            item.source_reference,
            item.evidence_text,
            item.url,
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputValidationError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputValidationError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, Mapping):
        raise InputValidationError(f"{path}: expected a JSON object")
    return value


def _validate_source_analysis(path: Path) -> None:
    required = {"username", "status", "engagement_rate_pct"}
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            missing = required - set(reader.fieldnames or ())
            if missing:
                raise InputValidationError(
                    f"{path}: missing columns: {', '.join(sorted(missing))}"
                )
            rows = list(reader)
    except FileNotFoundError as exc:
        raise InputValidationError(f"source analysis not found: {path}") from exc
    except csv.Error as exc:
        raise InputValidationError(f"{path}: malformed CSV: {exc}") from exc
    if not any(
        row.get("status") == "creator"
        and str(row.get("engagement_rate_pct") or "").strip()
        for row in rows
    ):
        raise InputValidationError(
            f"{path}: no scored Phase A creator engagement reference values"
        )


def _validate_workbook(path: Path) -> None:
    try:
        workbook = load_workbook(path, read_only=True, data_only=False)
        workbook.close()
    except (OSError, ValueError) as exc:
        raise InputValidationError(f"{path}: invalid XLSX workbook: {exc}") from exc


def _record_failed_manifest(
    run_dir: Path, manifest: PhaseBRunManifest, error: PhaseBError
) -> None:
    manifest.status = "failed"
    manifest.completed_at = datetime.now(timezone.utc)
    manifest.errors.append(
        {
            "category": error.category,
            "message": str(error),
            "details": error.details,
        }
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        _write_manifest(run_dir / "run_manifest.json", manifest)
    except OSError:
        # Preserve the original, more actionable pipeline error.
        pass


def _write_manifest(path: Path, manifest: PhaseBRunManifest) -> None:
    path.write_text(
        json.dumps(
            manifest.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


__all__ = [
    "DeduplicationOutcome",
    "PhaseBRunResult",
    "deduplicate_discovery_hits",
    "run_phase_b",
    "validate_phase_b_config",
]
