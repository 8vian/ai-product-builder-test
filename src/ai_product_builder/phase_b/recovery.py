"""Read-only recovery of completed Apify runs and offline Phase B analysis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .account_types import assess_account_type, with_account_theme_evidence
from .barter_signals import assess_barter_signals
from .compatibility import assess_compatibility
from .config import (
    PhaseBConfig,
    load_env_secrets,
    require_secret,
)
from .eligibility import evaluate_candidate_eligibility
from .enrichment import (
    calculate_candidate_metrics,
    known_format_posts,
)
from .errors import (
    InputValidationError,
    OutputWriteError,
    ProviderActorError,
    ProviderAuthError,
    ProviderSchemaError,
)
from .evidence import collect_signal_evidence, flatten_evidence
from .exclusions import ExclusionRegistry
from .io import json_safe
from .io.local_csv import (
    write_candidate_csv,
    write_excluded_candidates_csv,
    write_mapping_csv,
)
from .models import (
    CandidateResult,
    EligibilityDecision,
    ManualVerificationStatus,
    SignalEvidence,
    parse_datetime,
)
from .near_miss import NEAR_MISS_COLUMNS, build_near_miss_candidates
from .pipeline import (
    _apply_live_campaign_guards,
    _excluded_profile,
    _live_campaign_assessment,
    _phase_a_follower_outer_fence,
    _read_json_object,
    _unique_evidence,
    _with_run_level_exclusions,
    deduplicate_discovery_hits,
    validate_phase_b_config,
)
from .providers.apify import (
    normalize_apify_discovery_records,
    normalize_apify_profile_records,
)
from .queries import generate_queries
from .scoring import (
    calculate_discovery_confidence,
    load_phase_a_reference,
    score_candidate,
)
from .selection import rank_candidates, selection_explanation


_TERMINAL_RUN_STATES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


@dataclass(frozen=True, slots=True)
class ExistingRunSnapshot:
    run_id: str
    metadata: Mapping[str, Any]
    dataset_items: tuple[Any, ...]
    raw_metadata: bytes
    raw_dataset: bytes


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    output_dir: Path
    manifest: Mapping[str, Any]
    generated_paths: tuple[Path, ...]
    eligible_candidates: tuple[CandidateResult, ...]
    near_miss_candidates: tuple[Mapping[str, Any], ...]


class ApifyExistingRunReader:
    """A GET-only client with no Actor start/call/run method."""

    def __init__(
        self,
        *,
        api_base_url: str,
        token: str,
        timeout_seconds: int,
        max_retries: int,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        if api_base_url.rstrip("/") != "https://api.apify.com/v2":
            raise ProviderSchemaError(
                "Recovery requires the official https://api.apify.com/v2 host"
            )
        if not token:
            raise ProviderAuthError("APIFY_TOKEN is required for run recovery")
        self.api_base_url = api_base_url.rstrip("/")
        self._token = token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._opener = opener
        self.request_audit: list[dict[str, str]] = []

    @property
    def new_actor_runs_started(self) -> int:
        return 0

    def read_existing_run(self, run_id: str) -> ExistingRunSnapshot:
        if not run_id or not run_id.isalnum():
            raise InputValidationError(f"Invalid existing Apify run ID: {run_id}")
        metadata_url = (
            f"{self.api_base_url}/actor-runs/{quote(run_id, safe='')}"
        )
        raw_metadata, metadata_envelope = self._get_json(metadata_url)
        metadata = (
            metadata_envelope.get("data")
            if isinstance(metadata_envelope, Mapping)
            else None
        )
        if not isinstance(metadata, Mapping):
            raise ProviderSchemaError(
                f"Existing run {run_id} metadata is missing data"
            )
        if str(metadata.get("id") or "") != run_id:
            raise ProviderSchemaError(
                f"Existing run metadata identity mismatch for {run_id}"
            )
        status = str(metadata.get("status") or "")
        if status not in _TERMINAL_RUN_STATES:
            raise ProviderActorError(
                f"Existing run {run_id} is not terminal: {status}",
                details={"run_id": run_id, "status": status},
            )
        dataset_id = str(metadata.get("defaultDatasetId") or "")
        if not dataset_id:
            raise ProviderSchemaError(
                f"Existing run {run_id} has no defaultDatasetId"
            )
        dataset_query = urlencode({"format": "json", "clean": "false"})
        dataset_url = (
            f"{self.api_base_url}/datasets/{quote(dataset_id, safe='')}/items?"
            f"{dataset_query}"
        )
        raw_dataset, dataset_items = self._get_json(dataset_url)
        if not isinstance(dataset_items, list):
            raise ProviderSchemaError(
                f"Existing dataset {dataset_id} response is not a JSON array"
            )
        return ExistingRunSnapshot(
            run_id=run_id,
            metadata=dict(metadata),
            dataset_items=tuple(dataset_items),
            raw_metadata=raw_metadata,
            raw_dataset=raw_dataset,
        )

    def _get_json(self, url: str) -> tuple[bytes, Any]:
        request = Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": "ai-product-builder-phase-b-recovery/0.1",
            },
        )
        if request.get_method() != "GET":
            raise AssertionError("recovery request must be GET-only")
        for attempt in range(self.max_retries + 1):
            try:
                self.request_audit.append(
                    {"method": "GET", "url": _redacted_url(url)}
                )
                with self._opener(
                    request, timeout=self.timeout_seconds
                ) as response:
                    raw = response.read()
                try:
                    return raw, json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProviderSchemaError(
                        "Apify recovery endpoint returned invalid JSON"
                    ) from exc
            except HTTPError as exc:
                if exc.code in {401, 403}:
                    raise ProviderAuthError(
                        f"Apify recovery authentication failed with HTTP "
                        f"{exc.code}"
                    ) from exc
                if attempt >= self.max_retries or exc.code not in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }:
                    raise ProviderActorError(
                        f"Apify recovery GET failed with HTTP {exc.code}",
                        details={"url": _redacted_url(url)},
                    ) from exc
            except (TimeoutError, URLError) as exc:
                if attempt >= self.max_retries:
                    raise ProviderActorError(
                        "Apify recovery GET timed out",
                        details={"url": _redacted_url(url)},
                    ) from exc
        raise AssertionError("GET retry loop must return or raise")


def _redacted_url(url: str) -> str:
    return url.split("?", 1)[0]


def _write_json(path: Path, value: Any) -> Path:
    path.write_text(
        json.dumps(
            json_safe(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> Path:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(
                json.dumps(
                    json_safe(value),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(
        (entry for entry in path.rglob("*") if entry.is_file()),
        key=lambda entry: entry.as_posix(),
    ):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _phase_a_digest(output_root: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(
        (entry for entry in output_root.iterdir() if entry.is_file()),
        key=lambda entry: entry.name,
    ):
        digest.update(item.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _usage_total_usd(metadata: Mapping[str, Any]) -> float:
    value = metadata.get("usageTotalUsd")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, parsed)


def _source_charge_by_run(
    source_manifest: Mapping[str, Any],
) -> dict[str, float]:
    review = source_manifest.get("review_summary")
    charges = (
        review.get("provider_charges")
        if isinstance(review, Mapping)
        else None
    )
    result: dict[str, float] = {}
    if not isinstance(charges, list):
        return result
    for item in charges:
        if not isinstance(item, Mapping):
            continue
        run_id = item.get("run_id")
        try:
            charge = float(item.get("charge_usd"))
        except (TypeError, ValueError):
            continue
        if isinstance(run_id, str):
            result[run_id] = charge
    return result


def _candidate_result(
    *,
    profile: Any,
    metrics: Any,
    evidence: Mapping[str, Any],
    account: Any,
    barter: Any,
    compatibility: Any,
    live: Any,
    decision: EligibilityDecision,
    reference: Any,
    manifest_time: datetime,
    preferred_followers_max: int,
    barter_review_threshold: float,
) -> CandidateResult:
    score = score_candidate(profile, metrics, evidence, reference)
    confidence = calculate_discovery_confidence(profile, evidence)
    confidence_evidence = SignalEvidence(
        signal_type="discovery_confidence",
        source_field="provider_and_discovery",
        source_reference=profile.identity.username,
        evidence_text=confidence.explanation,
        observation_type="derived",
        url=profile.identity.canonical_profile_url,
    )
    all_evidence = _unique_evidence(
        (
            *flatten_evidence(evidence),
            *account.evidence,
            *barter.barter_evidence,
            *barter.no_barter_evidence,
            *compatibility.evidence,
            confidence_evidence,
        )
    )
    audience_review = bool(live.audience_review_required)
    recent_post_url = (
        live.recent_fashion_post_urls[0]
        if live.recent_fashion_post_urls
        else ""
    )
    return CandidateResult(
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
        selection_explanation=(
            f"{score.explanation} Strict recovery eligibility passed; the "
            "candidate was not automatically selected."
        ),
        evidence=all_evidence,
        recent_post_url=recent_post_url,
        barter_offer="",
        manual_verification_status=ManualVerificationStatus.PENDING,
        verification_notes=(
            "Recovered offline from completed Actor datasets. Manual review "
            "is mandatory; no offer was generated or sent."
        ),
        discovery_confidence=confidence.score,
        eligibility_status=decision.status,
        eligibility_reasons=decision.reasons,
        query_ids=profile.query_ids,
        provider=profile.provider,
        collected_at=profile.collected_at or manifest_time,
        offer_generation_mode="not_generated_recovery",
        source_exclusion_check=(
            "clear: no Phase A identity, historical alias, or prior-live "
            "run-level exclusion match"
        ),
        account_type=account.account_type.value,
        account_type_explanation=account.explanation,
        barter_feasibility_review_required=audience_review,
        barter_feasibility_explanation=(
            (
                "Audience exceeds the configured preferred automatic barter "
                f"range ceiling {preferred_followers_max:,}; manual review is "
                "required."
            )
            if audience_review
            else (
                "Audience does not exceed the configured preferred automatic "
                "barter review ceiling and remains below the frozen Phase A "
                f"outer-fence threshold {barter_review_threshold:,.1f}."
            )
        ),
        content_themes=account.relevant_dimensions,
        known_format_posts=known_format_posts(profile),
        detected_content_language=compatibility.detected_content_language,
        campaign_language_compatible=(
            compatibility.campaign_language_compatible
        ),
        detected_geography=compatibility.detected_geography,
        delivery_market_review_required=(
            compatibility.delivery_market_review_required
        ),
        compatibility_explanation=compatibility.explanation,
        barter_evidence=barter.barter_evidence,
        no_barter_evidence=barter.no_barter_evidence,
        campaign_bucket=(
            "needs_manual_review" if audience_review else "barter_ready"
        ),
        campaign_status_reasons=(
            ("preferred_audience_range_review_required",)
            if audience_review
            else ()
        ),
    )


def _analyze_recovered_profiles(
    *,
    profiles: Sequence[Any],
    config: PhaseBConfig,
    ideal: Mapping[str, Any],
    manifest_time: datetime,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[CandidateResult],
]:
    reference = load_phase_a_reference(
        config.inputs.source_analysis,
        config.inputs.ideal_creator_profile,
    )
    phase_a_q1, phase_a_q3, barter_review_threshold = (
        _phase_a_follower_outer_fence(ideal)
    )
    as_of = (
        parse_datetime(config.analysis_as_of)
        if config.analysis_as_of is not None
        else None
    )
    enriched: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    eligible: list[CandidateResult] = []
    for profile in profiles:
        metrics = calculate_candidate_metrics(profile, as_of)
        account = assess_account_type(profile)
        evidence = with_account_theme_evidence(
            collect_signal_evidence(profile), account
        )
        barter = assess_barter_signals(profile)
        compatibility = assess_compatibility(profile, config.campaign)
        live = _live_campaign_assessment(
            profile,
            evidence,
            account,
            language_compatible=(
                compatibility.campaign_language_compatible
            ),
            detected_geography=compatibility.detected_geography,
            delivery_market_review_required=(
                compatibility.delivery_market_review_required
            ),
            delivery_market_conflict=compatibility.delivery_market_conflict,
            explicit_no_barter=barter.explicit_refusal,
            as_of=as_of,
            maximum_recency_days=config.eligibility.maximum_recency_days,
            minimum_recent_fashion_posts=(
                config.eligibility.minimum_recent_fashion_posts
            ),
            minimum_short_video_posts=(
                config.eligibility.minimum_short_video_posts
            ),
            preferred_followers_min=(
                config.eligibility.preferred_followers_min
            ),
            preferred_followers_max=(
                config.eligibility.preferred_followers_max
            ),
        )
        decision = evaluate_candidate_eligibility(
            profile,
            metrics,
            evidence,
            as_of=as_of,
            max_recency_days=config.eligibility.maximum_recency_days,
            minimum_usable_posts=config.eligibility.minimum_usable_posts,
            account_assessment=account,
        )
        high_audience = bool(
            metrics.followers is not None
            and metrics.followers > barter_review_threshold
        )
        decision = _apply_live_campaign_guards(
            decision,
            explicit_barter_refusal=barter.explicit_refusal,
            language_compatible=(
                compatibility.campaign_language_compatible
            ),
            delivery_market_conflict=compatibility.delivery_market_conflict,
            high_audience=high_audience,
            geography_compatible=live.geography_compatible,
            commercial_conflict=live.commercial_conflict,
            own_fashion_brand=live.own_fashion_brand,
            own_clothing_store_or_showroom=(
                live.own_clothing_store_or_showroom
            ),
            blogger_content_sufficient=live.blogger_content_sufficient,
            short_video_ready=live.short_video_ready,
            recent_fashion_posts=live.recent_fashion_posts,
            minimum_recent_fashion_posts=(
                config.eligibility.minimum_recent_fashion_posts
            ),
        )
        confidence = calculate_discovery_confidence(profile, evidence)
        score_preview = (
            score_candidate(profile, metrics, evidence, reference)
            if metrics.usable_posts >= 6 and metrics.sampled_posts > 0
            else None
        )
        record = {
            "profile": profile.to_dict(),
            "metrics": metrics.to_dict(),
            "evidence": {
                name: [item.to_dict() for item in items]
                for name, items in evidence.items()
            },
            "account_type_assessment": account.to_dict(),
            "barter_signal_assessment": barter.to_dict(),
            "compatibility_assessment": compatibility.to_dict(),
            "live_campaign_assessment": live.to_dict(),
            "barter_review_threshold": {
                "q1": phase_a_q1,
                "q3": phase_a_q3,
                "formula": "q3 + 3 * (q3 - q1)",
                "threshold": barter_review_threshold,
                "review_required": high_audience,
            },
            "discovery_confidence": confidence.score,
            "score_preview": (
                score_preview.to_dict() if score_preview is not None else None
            ),
            "eligibility": decision.to_dict(),
        }
        enriched.append(record)
        if not decision.eligible:
            excluded.append(_excluded_profile(profile, decision))
            continue
        eligible.append(
            _candidate_result(
                profile=profile,
                metrics=metrics,
                evidence=evidence,
                account=account,
                barter=barter,
                compatibility=compatibility,
                live=live,
                decision=decision,
                reference=reference,
                manifest_time=manifest_time,
                preferred_followers_max=(
                    config.eligibility.preferred_followers_max
                ),
                barter_review_threshold=barter_review_threshold,
            )
        )
    ranked = rank_candidates(eligible)
    ranked = [
        replace(
            candidate,
            selection_explanation=selection_explanation(
                candidate, rank, len(ranked)
            )
            + " Recovery analysis did not select a final shortlist.",
        )
        for rank, candidate in enumerate(ranked, start=1)
    ]
    return enriched, excluded, ranked


def _recovery_report(
    *,
    manifest: Mapping[str, Any],
    near_misses: Sequence[Mapping[str, Any]],
) -> str:
    counts = manifest["counts"]
    near_lines = []
    manual_urls: list[str] = []
    for rank, item in enumerate(near_misses[:5], start=1):
        reasons = ", ".join(
            str(value) for value in item.get("reviewable_reasons", ())
        )
        near_lines.append(
            f"{rank}. **@{item.get('username')}** — score "
            f"{item.get('score', 'n/a')}; excluded for: {reasons}. "
            f"Profile: {item.get('profile_url')}; evidence: "
            f"{item.get('recent_fashion_post_url') or 'not available'}."
        )
        for url in (
            item.get("profile_url"),
            item.get("recent_fashion_post_url"),
        ):
            if isinstance(url, str) and url and url not in manual_urls:
                manual_urls.append(url)
    return f"""# Phase B read-only recovery report

Source failed run: `{manifest['source_failed_run']}`
Search Actor run: `{manifest['search_run_id']}`
Profile Actor run: `{manifest['profile_run_id']}`
Recovered at: {manifest['recovery_timestamp']}

## Recovery counts

- Search dataset items: **{counts['recovered_search_items']}**
- Profile dataset items: **{counts['recovered_profile_items']}**
- Profiles after discovery exclusions/deduplication: **{counts['profiles_after_exclusions']}**
- Enriched profiles: **{counts['enriched_profiles']}**
- Strict eligible: **{counts['strict_eligible']}**
- Near-miss candidates: **{counts['near_miss']}**
- Automatically selected: **0**

## Near-miss review

No candidate below is automatically selected or approved.

{chr(10).join(near_lines) if near_lines else "No conservative near-miss candidates were found."}

## Profiles/evidence to open manually

{chr(10).join(f"- {url}" for url in manual_urls) if manual_urls else "- None identified by the conservative near-miss rules."}

## Safety and billing audit

- New Actor runs started: **0**
- Search Actor runs started: **0**
- Profile Actor runs started: **0**
- HTTP methods used for recovery: **GET only**
- Additional Actor charges reflected by run metadata: **${manifest['additional_actor_charges_usd']:.6f}**
- Outreach messages sent: **0**
- Final shortlist created: **no**
- Previous run directories unchanged: **{str(manifest['previous_run_directories_unchanged']).lower()}**
- Phase A unchanged: **{str(manifest['phase_a_unchanged']).lower()}**
"""


def recover_completed_apify_runs(
    *,
    config_path: Path,
    source_failed_run: Path,
    search_run_id: str,
    profile_run_id: str,
    output_dir: Path,
    opener: Callable[..., Any] = urlopen,
    clock: Callable[[], datetime] | None = None,
) -> RecoveryResult:
    """Recover two existing run datasets and analyze them completely offline."""

    source_failed_run = Path(source_failed_run).resolve()
    output_dir = Path(output_dir).resolve()
    if not source_failed_run.is_dir():
        raise InputValidationError(
            f"Source failed run directory not found: {source_failed_run}"
        )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise OutputWriteError(
            f"Recovery output directory must be new or empty: {output_dir}"
        )
    source_manifest_path = source_failed_run / "run_manifest.json"
    source_manifest = _read_json_object(
        source_manifest_path, "source failed run manifest"
    )
    if source_manifest.get("status") != "failed":
        raise InputValidationError("Recovery source manifest must be failed")
    recorded_ids = {
        str(value) for value in source_manifest.get("provider_run_ids", ())
    }
    if {search_run_id, profile_run_id} - recorded_ids:
        raise InputValidationError(
            "Requested recovery run IDs are not both recorded in the source "
            "failed run manifest"
        )

    config, validation_warnings = validate_phase_b_config(
        Path(config_path), expected_mode="live"
    )
    apify = config.provider.apify
    if apify is None:
        raise InputValidationError("Recovery config has no Apify provider")
    token = require_secret(
        load_env_secrets(config.env_file), "APIFY_TOKEN"
    )

    source_digest_before = _tree_digest(source_failed_run)
    phase_a_root = config.inputs.ideal_creator_profile.parent
    phase_a_digest_before = _phase_a_digest(phase_a_root)
    reader = ApifyExistingRunReader(
        api_base_url=apify.api_base_url,
        token=token,
        timeout_seconds=apify.timeout_seconds,
        max_retries=apify.max_retries,
        opener=opener,
    )
    search = reader.read_existing_run(search_run_id)
    profile = reader.read_existing_run(profile_run_id)
    if reader.new_actor_runs_started != 0:
        raise AssertionError("read-only recovery cannot start Actor runs")

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_paths = {
        "search_run_metadata.json": search.raw_metadata,
        "search_dataset_raw.json": search.raw_dataset,
        "profile_run_metadata.json": profile.raw_metadata,
        "profile_dataset_raw.json": profile.raw_dataset,
    }
    for name, raw in raw_paths.items():
        (output_dir / name).write_bytes(raw)

    ideal = _read_json_object(
        config.inputs.ideal_creator_profile, "ideal_creator_profile"
    )
    queries = generate_queries(
        ideal,
        config.campaign,
        query_texts=config.discovery.query_texts,
    )
    search_collected_at = (
        parse_datetime(search.metadata.get("finishedAt"))
        or parse_datetime(search.metadata.get("startedAt"))
        or datetime.now(timezone.utc)
    )
    profile_collected_at = (
        parse_datetime(profile.metadata.get("finishedAt"))
        or parse_datetime(profile.metadata.get("startedAt"))
        or search_collected_at
    )
    hits = normalize_apify_discovery_records(
        search.dataset_items,
        config=apify,
        queries=queries,
        run_id=search_run_id,
        collected_at=search_collected_at,
    )
    exclusion_registry = _with_run_level_exclusions(
        ExclusionRegistry.from_files(
            config.inputs.instagram_profiles,
            config.inputs.manual_audit,
        ),
        config,
    )
    deduplicated = deduplicate_discovery_hits(
        hits,
        exclusion_registry,
        known_query_ids=(query.query_id for query in queries),
    )
    profiles = normalize_apify_profile_records(
        profile.dataset_items,
        config=apify,
        identities=deduplicated.identities,
        run_id=profile_run_id,
        collected_at=profile_collected_at,
    )
    recovery_time = (clock or (lambda: datetime.now(timezone.utc)))()
    if recovery_time.tzinfo is None:
        recovery_time = recovery_time.replace(tzinfo=timezone.utc)
    enriched, profile_exclusions, eligible = _analyze_recovered_profiles(
        profiles=profiles,
        config=config,
        ideal=ideal,
        manifest_time=recovery_time.astimezone(timezone.utc),
    )
    exclusions = [
        *deduplicated.excluded_records,
        *profile_exclusions,
    ]
    near_misses = build_near_miss_candidates(enriched)

    discovery_payload = [
        hit.to_dict(include_raw=True) for hit in hits
    ]
    generated_paths = [
        _write_jsonl(
            output_dir / "discovery_pool.jsonl", discovery_payload
        ),
        _write_json(
            output_dir / "deduplication_report.json",
            deduplicated.report,
        ),
        _write_jsonl(
            output_dir / "enriched_candidates.jsonl", enriched
        ),
        write_candidate_csv(
            output_dir / "eligible_candidates.csv", eligible
        ),
        write_excluded_candidates_csv(
            output_dir / "excluded_candidates.csv", exclusions
        ),
        _write_json(
            output_dir / "near_miss_candidates.json", near_misses
        ),
        write_mapping_csv(
            output_dir / "near_miss_candidates.csv",
            near_misses,
            columns=NEAR_MISS_COLUMNS,
        ),
    ]

    source_charges = _source_charge_by_run(source_manifest)
    current_usage = {
        search_run_id: _usage_total_usd(search.metadata),
        profile_run_id: _usage_total_usd(profile.metadata),
    }
    additional_actor_charges = round(
        sum(
            max(0.0, charge - source_charges.get(run_id, charge))
            for run_id, charge in current_usage.items()
        ),
        6,
    )
    source_digest_after = _tree_digest(source_failed_run)
    phase_a_digest_after = _phase_a_digest(phase_a_root)
    counts = {
        "recovered_search_items": len(search.dataset_items),
        "recovered_profile_items": len(profile.dataset_items),
        "profiles_after_exclusions": len(deduplicated.identities),
        "enriched_profiles": len(profiles),
        "strict_eligible": len(eligible),
        "near_miss": len(near_misses),
        "excluded_records_total": len(exclusions),
        "selected_candidates": 0,
    }
    manifest: dict[str, Any] = {
        "source_failed_run": source_failed_run.name,
        "source_failed_run_path": str(source_failed_run),
        "search_run_id": search_run_id,
        "profile_run_id": profile_run_id,
        "search_default_dataset_id": search.metadata.get(
            "defaultDatasetId"
        ),
        "profile_default_dataset_id": profile.metadata.get(
            "defaultDatasetId"
        ),
        "new_actor_runs_started": 0,
        "search_actor_runs_started": 0,
        "profile_actor_runs_started": 0,
        "outreach_messages_sent": 0,
        "recovered_search_items": len(search.dataset_items),
        "recovered_profile_items": len(profile.dataset_items),
        "recovery_timestamp": recovery_time.astimezone(
            timezone.utc
        ).isoformat(),
        "read_only_http_requests": reader.request_audit,
        "http_methods_used": sorted(
            {item["method"] for item in reader.request_audit}
        ),
        "additional_actor_charges_usd": additional_actor_charges,
        "recovered_run_usage_total_usd": current_usage,
        "counts": counts,
        "minimum_final_count_applied": False,
        "final_shortlist_created": False,
        "offline_analysis_only": True,
        "previous_run_directories_unchanged": (
            source_digest_before == source_digest_after
        ),
        "source_failed_run_sha256_before": source_digest_before,
        "source_failed_run_sha256_after": source_digest_after,
        "phase_a_unchanged": phase_a_digest_before == phase_a_digest_after,
        "phase_a_sha256_before": phase_a_digest_before,
        "phase_a_sha256_after": phase_a_digest_after,
        "validation_warnings": list(validation_warnings),
        "artifacts": {},
    }
    report_path = output_dir / "recovery_report.md"
    report_path.write_text(
        _recovery_report(manifest=manifest, near_misses=near_misses),
        encoding="utf-8",
    )
    generated_paths.append(report_path)
    manifest_path = output_dir / "recovery_manifest.json"
    manifest["artifacts"] = {
        path.name: str(path)
        for path in (
            *[output_dir / name for name in raw_paths],
            *generated_paths,
            manifest_path,
        )
    }
    _write_json(manifest_path, manifest)
    generated_paths = [
        *[output_dir / name for name in raw_paths],
        manifest_path,
        *generated_paths,
    ]
    return RecoveryResult(
        output_dir=output_dir,
        manifest=manifest,
        generated_paths=tuple(generated_paths),
        eligible_candidates=tuple(eligible),
        near_miss_candidates=tuple(near_misses),
    )


__all__ = [
    "ApifyExistingRunReader",
    "ExistingRunSnapshot",
    "RecoveryResult",
    "recover_completed_apify_runs",
]
