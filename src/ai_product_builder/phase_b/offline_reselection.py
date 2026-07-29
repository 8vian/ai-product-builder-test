"""Offline reselection from an immutable, previously saved provider run."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .account_types import (
    AccountTypeAssessment,
    assess_account_type,
    with_account_theme_evidence,
)
from .eligibility import evaluate_candidate_eligibility
from .enrichment import calculate_candidate_metrics, known_format_posts
from .errors import (
    EvidenceValidationError,
    InputValidationError,
    OutputWriteError,
)
from .evidence import collect_signal_evidence, flatten_evidence
from .exclusions import ExclusionRegistry
from .io.artifacts import ARTIFACT_FILENAMES, generate_phase_b_artifacts
from .models import (
    CandidateResult,
    CreatorProfile,
    EligibilityDecision,
    ManualVerificationStatus,
    PhaseBRunManifest,
    SignalEvidence,
    parse_datetime,
)
from .offers import generate_deterministic_offer
from .pipeline import PhaseBRunResult, validate_phase_b_config
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

_REQUIRED_SOURCE_ARTIFACTS = (
    "run_manifest.json",
    "generated_queries.json",
    "discovery_pool.jsonl",
    "deduplication_report.json",
    "eligible_candidates.csv",
    "enriched_candidates.jsonl",
)


def _read_json(path: Path, *, expected: type) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputValidationError(f"saved artifact not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputValidationError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, expected):
        raise InputValidationError(
            f"{path}: expected {expected.__name__}, got {type(value).__name__}"
        )
    return value


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise InputValidationError(f"saved artifact not found: {path}") from exc
    result: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputValidationError(
                f"{path}:{line_number}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(value, Mapping):
            raise InputValidationError(
                f"{path}:{line_number}: expected a JSON object"
            )
        result.append(value)
    return result


def _read_previous_eligible_usernames(path: Path) -> frozenset[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if "username" not in (reader.fieldnames or ()):
                raise InputValidationError(
                    f"{path}: missing username column"
                )
            return frozenset(
                str(row.get("username") or "")
                for row in reader
                if str(row.get("username") or "")
            )
    except FileNotFoundError as exc:
        raise InputValidationError(f"saved artifact not found: {path}") from exc
    except csv.Error as exc:
        raise InputValidationError(f"{path}: malformed CSV: {exc}") from exc


def _load_manual_review(
    path: Path,
    *,
    source_run_id: str,
    available_usernames: frozenset[str],
) -> dict[str, Mapping[str, Any]]:
    payload = _read_json(path, expected=dict)
    if payload.get("source_run_id") != source_run_id:
        raise InputValidationError(
            f"{path}: source_run_id must be exactly {source_run_id}"
        )
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise InputValidationError(f"{path}: decisions must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, decision in enumerate(decisions):
        if not isinstance(decision, Mapping):
            raise InputValidationError(
                f"{path}: decisions[{index}] must be an object"
            )
        username = decision.get("username")
        if not isinstance(username, str) or not username:
            raise InputValidationError(
                f"{path}: decisions[{index}].username is required"
            )
        if username not in available_usernames:
            raise InputValidationError(
                f"{path}: review username is not in saved artifacts: {username}"
            )
        if username in result:
            raise InputValidationError(
                f"{path}: duplicate review decision for {username}"
            )
        action = decision.get("action")
        if action not in {"exclude", "review"}:
            raise InputValidationError(
                f"{path}: decisions[{index}].action must be exclude or review"
            )
        reason = decision.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise InputValidationError(
                f"{path}: decisions[{index}].reason is required"
            )
        result[username] = decision
    return result


def _phase_a_extreme_audience_threshold(
    ideal_profile_path: Path,
) -> tuple[float, float, float]:
    payload = _read_json(ideal_profile_path, expected=dict)
    try:
        follower_stats = payload["ideal_creator_profile"]["audience"][
            "followers"
        ]
        q1 = float(follower_stats["q1"])
        q3 = float(follower_stats["q3"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InputValidationError(
            f"{ideal_profile_path}: missing Phase A follower q1/q3"
        ) from exc
    if q1 < 0 or q3 < q1:
        raise InputValidationError(
            f"{ideal_profile_path}: invalid Phase A follower quartiles"
        )
    threshold = q3 + 3.0 * (q3 - q1)
    return q1, q3, threshold


def _unique_evidence(
    evidence: Iterable[SignalEvidence],
) -> tuple[SignalEvidence, ...]:
    result: list[SignalEvidence] = []
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
            result.append(item)
    return tuple(result)


def _manual_status(
    decision: Mapping[str, Any] | None,
) -> ManualVerificationStatus:
    if decision is None:
        return ManualVerificationStatus.PENDING
    raw = decision.get("manual_verification_status")
    if raw is None:
        return ManualVerificationStatus.PENDING
    try:
        return ManualVerificationStatus(str(raw))
    except ValueError as exc:
        raise InputValidationError(
            "manual_verification_status must be pending, approved, rejected, "
            "or needs_review"
        ) from exc


def _manual_override(
    decision: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if decision is None:
        return None
    return {
        key: decision[key]
        for key in ("account_type", "theme_relevant", "reason")
        if key in decision
    }


def _excluded_profile(
    profile: CreatorProfile,
    decision: EligibilityDecision,
    assessment: AccountTypeAssessment,
    *,
    manual_decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "platform": profile.identity.platform,
        "username": profile.identity.username,
        "profile_url": profile.identity.canonical_profile_url,
        "normalized_username": profile.identity.normalized_username,
        "exclusion_reason": decision.status.value,
        "exclusion_reasons": list(decision.reasons),
        "query_ids": list(profile.query_ids),
        "provider": profile.provider,
        "account_type": assessment.account_type.value,
        "account_type_explanation": assessment.explanation,
        "theme_relevant": assessment.theme_relevant,
        "relevant_dimensions": list(assessment.relevant_dimensions),
        "negative_topics": list(assessment.negative_topics),
        "account_type_evidence": [
            item.to_dict() for item in assessment.evidence
        ],
    }
    if manual_decision is not None:
        result["manual_review_action"] = manual_decision.get("action")
        result["manual_review_reason"] = manual_decision.get("reason")
    return result


def reselect_saved_run(
    source_run_dir: Path,
    output_run_dir: Path,
    *,
    config_path: Path,
    review_path: Path,
) -> PhaseBRunResult:
    """Re-evaluate saved discovery/enrichment artifacts with zero provider calls."""

    source_run_dir = Path(source_run_dir)
    output_run_dir = Path(output_run_dir)
    if not source_run_dir.is_dir():
        raise InputValidationError(
            f"saved source run directory not found: {source_run_dir}"
        )
    missing = [
        name
        for name in _REQUIRED_SOURCE_ARTIFACTS
        if not (source_run_dir / name).is_file()
    ]
    if missing:
        raise InputValidationError(
            "saved source run is incomplete; missing: " + ", ".join(missing)
        )
    if source_run_dir.resolve() == output_run_dir.resolve():
        raise OutputWriteError(
            "offline reselection output must not overwrite the source run"
        )
    if output_run_dir.exists() and any(output_run_dir.iterdir()):
        raise OutputWriteError(
            f"offline reselection output directory is not empty: "
            f"{output_run_dir}"
        )

    config, validation_warnings = validate_phase_b_config(
        config_path, expected_mode="live"
    )
    if config.google_sheets.enabled:
        raise InputValidationError(
            "offline reselection requires google_sheets.enabled=false"
        )
    source_manifest = _read_json(
        source_run_dir / "run_manifest.json", expected=dict
    )
    if source_manifest.get("status") != "completed":
        raise InputValidationError(
            "saved source run manifest must have status=completed"
        )
    source_run_id = str(source_manifest.get("run_id") or "")
    if not source_run_id:
        raise InputValidationError("saved source run manifest has no run_id")

    generated_queries = _read_json(
        source_run_dir / "generated_queries.json", expected=list
    )
    discovery_pool = _read_jsonl(source_run_dir / "discovery_pool.jsonl")
    deduplication_report = _read_json(
        source_run_dir / "deduplication_report.json", expected=dict
    )
    saved_enriched = _read_jsonl(
        source_run_dir / "enriched_candidates.jsonl"
    )
    profiles: list[CreatorProfile] = []
    for index, record in enumerate(saved_enriched):
        profile_raw = record.get("profile")
        if not isinstance(profile_raw, Mapping):
            raise InputValidationError(
                f"enriched_candidates.jsonl:{index + 1}: profile is missing"
            )
        try:
            profiles.append(CreatorProfile.from_dict(profile_raw))
        except (TypeError, ValueError) as exc:
            raise InputValidationError(
                f"enriched_candidates.jsonl:{index + 1}: {exc}"
            ) from exc

    expected_discovery = source_manifest.get("counts", {}).get(
        "raw_discovery_hits"
    )
    expected_enriched = source_manifest.get("counts", {}).get(
        "enriched_candidates"
    )
    if expected_discovery != len(discovery_pool):
        raise InputValidationError(
            "saved discovery_pool count does not match source manifest"
        )
    if expected_enriched != len(profiles):
        raise InputValidationError(
            "saved enriched profile count does not match source manifest"
        )
    usernames = [profile.identity.username for profile in profiles]
    if len(set(usernames)) != len(usernames):
        raise InputValidationError(
            "saved enriched profiles contain duplicate exact usernames"
        )
    manual_reviews = _load_manual_review(
        review_path,
        source_run_id=source_run_id,
        available_usernames=frozenset(usernames),
    )
    previous_eligible = _read_previous_eligible_usernames(
        source_run_dir / "eligible_candidates.csv"
    )

    exclusion_registry = ExclusionRegistry.from_files(
        config.inputs.instagram_profiles, config.inputs.manual_audit
    )
    reference = load_phase_a_reference(
        config.inputs.source_analysis,
        config.inputs.ideal_creator_profile,
    )
    q1, q3, extreme_threshold = _phase_a_extreme_audience_threshold(
        config.inputs.ideal_creator_profile
    )
    as_of = (
        parse_datetime(config.analysis_as_of)
        if config.analysis_as_of is not None
        else None
    )

    digest = hashlib.sha256()
    for path in (
        Path(config_path),
        Path(review_path),
        source_run_dir / "run_manifest.json",
        source_run_dir / "enriched_candidates.jsonl",
    ):
        digest.update(path.read_bytes())
    manifest = PhaseBRunManifest.started(
        run_id=output_run_dir.name,
        mode="offline_reselection",
        provider="saved_provider_artifacts",
        config_digest=digest.hexdigest(),
    )
    manifest.warnings.extend(validation_warnings)
    manifest.offline_reselection = True
    manifest.source_run_id = source_run_id
    manifest.source_run_path = str(source_run_dir)
    manifest.provider_run_ids = list(
        source_manifest.get("provider_run_ids") or []
    )
    manifest.provider_requests_made = 0
    manifest.budget_spent_usd = 0.0
    manifest.cache = {"enabled": 0, "hits": 0, "misses": 0}

    excluded_records: list[dict[str, Any]] = []
    enriched_records: list[dict[str, Any]] = []
    eligible_results: list[CandidateResult] = []
    assessments: dict[str, AccountTypeAssessment] = {}

    for profile in profiles:
        username = profile.identity.username
        manual_decision = manual_reviews.get(username)
        assessment = assess_account_type(
            profile,
            manual_override=_manual_override(manual_decision),
        )
        assessments[username] = assessment
        metrics = calculate_candidate_metrics(profile, as_of)
        evidence = with_account_theme_evidence(
            collect_signal_evidence(profile),
            assessment,
        )
        registry_match = exclusion_registry.match(
            username, profile.identity.canonical_profile_url
        )
        decision = evaluate_candidate_eligibility(
            profile,
            metrics,
            evidence,
            excluded_reason=(
                ",".join(registry_match.reasons)
                if registry_match is not None
                else None
            ),
            as_of=as_of,
            max_recency_days=config.eligibility.maximum_recency_days,
            minimum_usable_posts=config.eligibility.minimum_usable_posts,
            account_assessment=assessment,
            # Saved Apify results may omit the provider-specific format field.
            # Missing format earns no short-video points but is not allowed to
            # erase otherwise usable engagement/content observations.
            require_known_post_format=False,
        )
        if (
            manual_decision is not None
            and manual_decision.get("action") == "exclude"
        ):
            decision = EligibilityDecision(
                eligible=False,
                status="ineligible",
                reasons=(
                    *decision.reasons,
                    "manual_exclusion:"
                    f"{manual_decision.get('reason')}",
                ),
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
                    reasons=("offer_grounding_evidence_missing", str(exc)),
                )

        enriched_records.append(
            {
                "profile": profile.to_dict(),
                "metrics": metrics.to_dict(),
                "evidence": {
                    name: [item.to_dict() for item in items]
                    for name, items in evidence.items()
                },
                "account_type_assessment": assessment.to_dict(),
                "eligibility": decision.to_dict(),
                "offline_reselection": {
                    "source_run_id": source_run_id,
                    "manual_review": (
                        dict(manual_decision)
                        if manual_decision is not None
                        else None
                    ),
                },
            }
        )
        if not decision.eligible or offer is None:
            excluded_records.append(
                _excluded_profile(
                    profile,
                    decision,
                    assessment,
                    manual_decision=manual_decision,
                )
            )
            continue

        score = score_candidate(profile, metrics, evidence, reference)
        confidence = calculate_discovery_confidence(profile, evidence)
        confidence_evidence = SignalEvidence(
            signal_type="discovery_confidence",
            source_field="provider_and_discovery",
            source_reference=username,
            evidence_text=confidence.explanation,
            observation_type="derived",
            url=profile.identity.canonical_profile_url,
        )
        barter_review_required = bool(
            metrics.followers is not None
            and metrics.followers > extreme_threshold
        )
        barter_explanation = (
            "Audience requires manual barter review: "
            f"{metrics.followers:,} followers exceed the frozen Phase A "
            f"outer fence q3 + 3×IQR = {q3:,.1f} + 3×"
            f"({q3:,.1f} - {q1:,.1f}) = {extreme_threshold:,.1f}."
            if barter_review_required and metrics.followers is not None
            else (
                "Audience does not exceed the frozen Phase A upper outer "
                f"fence of {extreme_threshold:,.1f} followers."
            )
        )
        barter_evidence = SignalEvidence(
            signal_type="barter_feasibility_review",
            source_field="followers",
            source_reference=username,
            evidence_text=barter_explanation,
            observation_type="derived",
            url=profile.identity.canonical_profile_url,
        )
        manual_status = _manual_status(manual_decision)
        verification_notes = (
            str(manual_decision.get("verification_notes") or "").strip()
            if manual_decision is not None
            else ""
        )
        if barter_review_required:
            verification_notes = " ".join(
                part
                for part in (verification_notes, barter_explanation)
                if part
            )
        known_formats = known_format_posts(profile)
        format_explanation = (
            f"Saved post-format metadata is usable for {known_formats}/"
            f"{metrics.sampled_posts} sampled posts; unknown formats are not "
            "counted as short video."
        )
        if known_formats < metrics.sampled_posts:
            verification_notes = " ".join(
                part
                for part in (verification_notes, format_explanation)
                if part
            )
        format_evidence = SignalEvidence(
            signal_type="post_format_completeness",
            source_field="recent_posts.post_format",
            source_reference=username,
            evidence_text=format_explanation,
            observation_type="derived",
            url=profile.identity.canonical_profile_url,
        )
        all_evidence = _unique_evidence(
            (
                *flatten_evidence(evidence),
                *assessment.evidence,
                confidence_evidence,
                barter_evidence,
                format_evidence,
                *offer.evidence,
            )
        )
        eligible_results.append(
            CandidateResult(
                platform=profile.identity.platform,
                username=username,
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
                manual_verification_status=manual_status,
                verification_notes=verification_notes,
                discovery_confidence=confidence.score,
                eligibility_status=decision.status,
                eligibility_reasons=decision.reasons,
                query_ids=profile.query_ids,
                provider=profile.provider,
                collected_at=profile.collected_at or manifest.started_at,
                offer_generation_mode=offer.generation_mode,
                source_exclusion_check=(
                    "clear: rechecked against frozen Phase A usernames, URLs, "
                    "historical aliases, replacements, rejected false leads, "
                    "Nike, and Apple"
                ),
                account_type=assessment.account_type.value,
                account_type_explanation=assessment.explanation,
                barter_feasibility_review_required=(
                    barter_review_required
                ),
                barter_feasibility_explanation=barter_explanation,
            )
        )

    ranked = rank_candidates(eligible_results)
    ranked = [
        replace(
            candidate,
            selection_explanation=(
                selection_explanation(candidate, rank, len(ranked))
                + f" Account type: {candidate.account_type}."
                + (
                    " Manual barter-feasibility review is required."
                    if candidate.barter_feasibility_review_required
                    else " Audience remains within the Phase A upper outer fence."
                )
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

    old_rejected = previous_eligible - {
        candidate.username for candidate in ranked
    }
    old_rejected_categories = Counter(
        (
            assessments[username].account_type.value
            if assessments[username].account_type.value != "personal_creator"
            else "irrelevant_personal_theme"
        )
        for username in old_rejected
    )
    manual_exclusions = {
        username: str(decision["reason"])
        for username, decision in manual_reviews.items()
        if decision.get("action") == "exclude"
    }
    account_counts = Counter(
        item.account_type.value for item in assessments.values()
    )
    manifest.counts = {
        "generated_queries": len(generated_queries),
        "raw_discovery_hits": len(discovery_pool),
        "unique_candidates": len(profiles),
        "enriched_candidates": len(profiles),
        "previously_eligible_candidates": len(previous_eligible),
        "previously_eligible_rejected_by_account_or_theme": len(
            old_rejected
        ),
        "eligible_candidates": len(ranked),
        "ineligible_candidates": len(profiles) - len(ranked),
        "excluded_records_total": len(excluded_records),
        "manual_exclusions": len(manual_exclusions),
        "barter_feasibility_reviews_required": sum(
            candidate.barter_feasibility_review_required
            for candidate in ranked
        ),
        "selected_candidates": len(selection.selected),
    }
    manifest.review_summary = {
        "account_type_counts": dict(sorted(account_counts.items())),
        "previously_eligible_rejected_count": len(old_rejected),
        "previously_eligible_rejected_categories": dict(
            sorted(old_rejected_categories.items())
        ),
        "manual_exclusions": manual_exclusions,
        "barter_audience_rule": {
            "phase_a_q1": q1,
            "phase_a_q3": q3,
            "formula": "q3 + 3 * (q3 - q1)",
            "threshold": extreme_threshold,
        },
        "barter_review_usernames": [
            candidate.username
            for candidate in ranked
            if candidate.barter_feasibility_review_required
        ],
        "selected_usernames": [
            candidate.username for candidate in selection.selected
        ],
        "network_statement": (
            "Provider discovery and enrichment were not called; all records "
            "were loaded from the saved source run."
        ),
    }
    manifest.status = "completed"
    manifest.completed_at = datetime.now(timezone.utc)
    manifest.artifacts = {
        name: str(output_run_dir / name) for name in ARTIFACT_FILENAMES
    }
    output_run_dir.mkdir(parents=True, exist_ok=True)
    try:
        paths = generate_phase_b_artifacts(
            output_run_dir,
            manifest=manifest,
            queries=generated_queries,
            discovery_pool=discovery_pool,
            deduplication_report=deduplication_report,
            excluded_candidates=excluded_records,
            enriched_candidates=enriched_records,
            eligible_candidates=ranked,
            selected_candidates=selection.selected,
            source_workbook=config.inputs.workbook,
            workbook_sheet=config.outputs.workbook_sheet,
        )
    except (OSError, ValueError, KeyError) as exc:
        raise OutputWriteError(
            f"Could not generate offline reselection artifacts: {exc}"
        ) from exc
    return PhaseBRunResult(
        manifest=manifest,
        run_dir=output_run_dir,
        generated_paths=tuple(paths),
        selected_candidates=selection.selected,
        eligible_candidates=tuple(ranked),
        google_sheets_receipt=None,
    )


__all__ = ["reselect_saved_run"]
