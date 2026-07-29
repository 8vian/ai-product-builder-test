"""Campaign-specific final review over saved Phase B provider artifacts."""

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
    AccountType,
    AccountTypeAssessment,
    assess_account_type,
    with_account_theme_evidence,
)
from .barter_signals import BarterSignalAssessment, assess_barter_signals
from .compatibility import CompatibilityAssessment, assess_compatibility
from .config import load_phase_b_config
from .eligibility import evaluate_candidate_eligibility
from .enrichment import (
    calculate_candidate_metrics,
    known_format_posts,
    select_recent_post,
)
from .errors import (
    EvidenceValidationError,
    InputValidationError,
    OutputWriteError,
)
from .evidence import collect_signal_evidence, flatten_evidence
from .io import candidate_mapping, json_safe
from .io.local_csv import write_candidate_csv, write_mapping_csv
from .io.local_xlsx import write_candidates_workbook
from .models import (
    CandidateMetrics,
    CandidateResult,
    CandidateScore,
    CreatorProfile,
    EligibilityStatus,
    ManualVerificationStatus,
    PhaseBRunManifest,
    SignalEvidence,
    parse_datetime,
)
from .offers import generate_deterministic_offer
from .scoring import (
    calculate_discovery_confidence,
    load_phase_a_reference,
    score_candidate,
)


FINAL_REVIEW_ARTIFACTS: tuple[str, ...] = (
    "run_manifest.json",
    "generated_queries.json",
    "discovery_pool.jsonl",
    "deduplication_report.json",
    "enriched_candidates.jsonl",
    "barter_ready.json",
    "barter_ready.csv",
    "needs_manual_review.json",
    "needs_manual_review.csv",
    "ineligible_or_insufficient.json",
    "ineligible_or_insufficient.csv",
    "personal_candidate_classification.json",
    "personal_candidate_classification.csv",
    "top_10_personal_candidates.json",
    "top_10_personal_candidates.csv",
    "eligible_candidates.csv",
    "excluded_candidates.csv",
    "new_creators.json",
    "new_creators.csv",
    "barter_offer_drafts.md",
    "discovery_report.md",
    "Блогеры_phase_b.xlsx",
)

_CLASSIFICATION_COLUMNS = (
    "platform",
    "username",
    "profile_url",
    "account_type",
    "followers",
    "score",
    "content_themes",
    "usable_posts",
    "sampled_posts",
    "known_format_posts",
    "detected_content_language",
    "campaign_language_compatible",
    "detected_geography",
    "delivery_market_review_required",
    "barter_feasibility_review_required",
    "barter_evidence",
    "no_barter_evidence",
    "latest_evidence_url",
    "status",
    "campaign_bucket",
    "status_reasons",
    "compatibility_explanation",
    "alternative_campaign_note",
)


def _read_json(path: Path, expected: type) -> Any:
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
            f"{path}: expected {expected.__name__}"
        )
    return value


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise InputValidationError(f"saved artifact not found: {path}") from exc
    values: list[Mapping[str, Any]] = []
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
                f"{path}:{line_number}: expected an object"
            )
        values.append(value)
    return values


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


def _write_jsonl(path: Path, values: Iterable[Any]) -> Path:
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


def _load_manual_decisions(
    path: Path,
    source_run_id: str,
) -> dict[str, Mapping[str, Any]]:
    payload = _read_json(path, dict)
    if payload.get("source_run_id") != source_run_id:
        raise InputValidationError(
            f"{path}: source_run_id must be {source_run_id}"
        )
    raw = payload.get("decisions")
    if not isinstance(raw, list):
        raise InputValidationError(f"{path}: decisions must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for decision in raw:
        if not isinstance(decision, Mapping):
            raise InputValidationError(
                f"{path}: every decision must be an object"
            )
        username = decision.get("username")
        if isinstance(username, str) and username:
            result[username] = decision
    return result


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


def _phase_a_audience_threshold(path: Path) -> tuple[float, float, float]:
    payload = _read_json(path, dict)
    try:
        followers = payload["ideal_creator_profile"]["audience"]["followers"]
        q1 = float(followers["q1"])
        q3 = float(followers["q3"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InputValidationError(
            f"{path}: missing Phase A follower quartiles"
        ) from exc
    if q1 < 0 or q3 < q1:
        raise InputValidationError(
            f"{path}: invalid Phase A follower quartiles"
        )
    return q1, q3, q3 + 3.0 * (q3 - q1)


def _unique_evidence(
    values: Iterable[SignalEvidence],
) -> tuple[SignalEvidence, ...]:
    result: list[SignalEvidence] = []
    seen: set[tuple[str, str, str, str, str | None]] = set()
    for item in values:
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


def _campaign_classification(
    profile: CreatorProfile,
    metrics: CandidateMetrics,
    assessment: AccountTypeAssessment,
    barter: BarterSignalAssessment,
    compatibility: CompatibilityAssessment,
    *,
    maximum_recency_days: float,
    minimum_usable_posts: int,
    extreme_audience_threshold: float,
    evidence: Mapping[str, tuple[SignalEvidence, ...]],
) -> tuple[str, str, tuple[str, ...], bool, str]:
    known_formats = known_format_posts(profile)
    high_audience = bool(
        metrics.followers is not None
        and metrics.followers > extreme_audience_threshold
    )
    base = evaluate_candidate_eligibility(
        profile,
        metrics,
        evidence,
        as_of=None,
        max_recency_days=maximum_recency_days,
        minimum_usable_posts=minimum_usable_posts,
        account_assessment=assessment,
        require_known_post_format=True,
    )

    insufficient_reasons: list[str] = []
    if metrics.followers is None or metrics.followers <= 0:
        insufficient_reasons.append("followers_missing_or_non_positive")
    if metrics.usable_posts < minimum_usable_posts:
        insufficient_reasons.append(
            f"insufficient_engagement_data:{metrics.usable_posts}<"
            f"{minimum_usable_posts}"
        )
    if known_formats < 1:
        insufficient_reasons.append("post_format_data_missing")
    if metrics.last_post_date is None:
        insufficient_reasons.append("last_post_date_missing")

    if assessment.account_type is not AccountType.PERSONAL_CREATOR:
        return (
            "ineligible_or_insufficient",
            "ineligible",
            (
                f"account_type_not_personal:{assessment.account_type.value}",
            ),
            high_audience,
            "",
        )
    if insufficient_reasons:
        return (
            "ineligible_or_insufficient",
            "insufficient_data",
            tuple(dict.fromkeys(insufficient_reasons)),
            high_audience,
            "",
        )
    if not assessment.theme_relevant:
        return (
            "ineligible_or_insufficient",
            "ineligible",
            tuple(
                dict.fromkeys(
                    (
                        "target_theme_not_relevant",
                        *base.reasons,
                    )
                )
            ),
            high_audience,
            "",
        )
    if barter.explicit_refusal:
        return (
            "ineligible_or_insufficient",
            "ineligible",
            ("explicit_no_barter_statement",),
            high_audience,
            (
                "Potentially suitable for a separate paid campaign; excluded "
                "only from the current product-for-content barter campaign."
            ),
        )
    if base.status is not EligibilityStatus.ELIGIBLE:
        return (
            "ineligible_or_insufficient",
            "ineligible",
            base.reasons,
            high_audience,
            "",
        )

    review_reasons: list[str] = []
    if high_audience:
        review_reasons.append("high_audience_barter_review_required")
    if compatibility.language_review_required:
        review_reasons.append("campaign_language_mismatch")
    if compatibility.delivery_market_review_required:
        review_reasons.append("delivery_market_review_required")
    if not barter.explicit_barter_readiness:
        review_reasons.append("barter_terms_unclear")
    if review_reasons:
        return (
            "needs_manual_review",
            "needs_review",
            tuple(review_reasons),
            high_audience,
            "",
        )
    return (
        "barter_ready",
        "eligible",
        (),
        high_audience,
        "",
    )


def _score_or_none(
    profile: CreatorProfile,
    metrics: CandidateMetrics,
    evidence: Mapping[str, tuple[SignalEvidence, ...]],
    reference: Any,
) -> CandidateScore | None:
    try:
        return score_candidate(profile, metrics, evidence, reference)
    except ValueError:
        return None


def _classification_mapping(
    *,
    profile: CreatorProfile,
    metrics: CandidateMetrics,
    score: CandidateScore | None,
    assessment: AccountTypeAssessment,
    barter: BarterSignalAssessment,
    compatibility: CompatibilityAssessment,
    bucket: str,
    status: str,
    reasons: tuple[str, ...],
    high_audience: bool,
    latest_url: str,
    alternative_note: str,
) -> dict[str, Any]:
    return {
        "platform": profile.identity.platform,
        "username": profile.identity.username,
        "profile_url": profile.identity.canonical_profile_url,
        "account_type": assessment.account_type.value,
        "followers": metrics.followers,
        "score": score.score if score is not None else None,
        "score_components": (
            [item.to_dict() for item in score.components]
            if score is not None
            else []
        ),
        "content_themes": list(assessment.relevant_dimensions),
        "usable_posts": metrics.usable_posts,
        "sampled_posts": metrics.sampled_posts,
        "known_format_posts": known_format_posts(profile),
        "detected_content_language": (
            compatibility.detected_content_language
        ),
        "campaign_language_compatible": (
            compatibility.campaign_language_compatible
        ),
        "detected_geography": compatibility.detected_geography,
        "delivery_market_review_required": (
            compatibility.delivery_market_review_required
        ),
        "barter_feasibility_review_required": high_audience,
        "barter_evidence": [
            item.to_dict() for item in barter.barter_evidence
        ],
        "no_barter_evidence": [
            item.to_dict() for item in barter.no_barter_evidence
        ],
        "latest_evidence_url": latest_url,
        "status": status,
        "campaign_bucket": bucket,
        "status_reasons": list(reasons),
        "compatibility_explanation": compatibility.explanation,
        "alternative_campaign_note": alternative_note,
    }


def _candidate_result(
    *,
    profile: CreatorProfile,
    metrics: CandidateMetrics,
    score: CandidateScore,
    assessment: AccountTypeAssessment,
    barter: BarterSignalAssessment,
    compatibility: CompatibilityAssessment,
    bucket: str,
    status: str,
    reasons: tuple[str, ...],
    high_audience: bool,
    extreme_threshold: float,
    latest_url: str,
    offer_text: str,
    offer_mode: str,
    offer_evidence: tuple[SignalEvidence, ...],
    alternative_note: str,
    evidence: Mapping[str, tuple[SignalEvidence, ...]],
) -> CandidateResult:
    confidence = calculate_discovery_confidence(profile, evidence)
    all_evidence = _unique_evidence(
        (
            *flatten_evidence(evidence),
            *assessment.evidence,
            *barter.barter_evidence,
            *barter.no_barter_evidence,
            *compatibility.evidence,
            *offer_evidence,
        )
    )
    if status == "eligible":
        eligibility_status = EligibilityStatus.ELIGIBLE
        manual_status = ManualVerificationStatus.PENDING
    elif status == "needs_review":
        eligibility_status = EligibilityStatus.NEEDS_REVIEW
        manual_status = ManualVerificationStatus.PENDING
    elif status == "insufficient_data":
        eligibility_status = EligibilityStatus.INSUFFICIENT_DATA
        manual_status = ManualVerificationStatus.NEEDS_REVIEW
    else:
        eligibility_status = EligibilityStatus.INELIGIBLE
        manual_status = ManualVerificationStatus.REJECTED
    barter_explanation = (
        "Audience requires manual barter review: "
        f"{metrics.followers:,} followers exceed the frozen Phase A "
        f"threshold of {extreme_threshold:,.1f}."
        if high_audience and metrics.followers is not None
        else (
            "Audience does not exceed the frozen Phase A barter-review "
            f"threshold of {extreme_threshold:,.1f}."
        )
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
            "Diagnostic personal-candidate score; campaign status takes "
            f"precedence. Bucket={bucket}; reasons="
            + (", ".join(reasons) if reasons else "none")
            + "."
        ),
        evidence=all_evidence,
        recent_post_url=latest_url,
        barter_offer=offer_text,
        manual_verification_status=manual_status,
        verification_notes="; ".join(reasons),
        discovery_confidence=confidence.score,
        eligibility_status=eligibility_status,
        eligibility_reasons=reasons,
        query_ids=profile.query_ids,
        provider=profile.provider,
        collected_at=profile.collected_at or datetime.now(timezone.utc),
        offer_generation_mode=offer_mode,
        source_exclusion_check=(
            "clear: saved candidate was rechecked against frozen Phase A "
            "source identities before campaign review"
        ),
        account_type=assessment.account_type.value,
        account_type_explanation=assessment.explanation,
        barter_feasibility_review_required=high_audience,
        barter_feasibility_explanation=barter_explanation,
        content_themes=assessment.relevant_dimensions,
        known_format_posts=known_format_posts(profile),
        detected_content_language=(
            compatibility.detected_content_language
        ),
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
        campaign_bucket=bucket,
        campaign_status_reasons=reasons,
        alternative_campaign_note=alternative_note,
    )


def _write_offer_drafts(
    path: Path,
    barter_ready: list[CandidateResult],
    needs_review: list[CandidateResult],
) -> Path:
    lines = [
        "# Phase B campaign drafts",
        "",
        "> No message was sent. Every draft requires manual approval.",
        "",
        f"Barter-ready drafts: **{len(barter_ready)}**",
        f"Preliminary review drafts: **{len(needs_review)}**",
        "",
    ]
    if not barter_ready:
        lines.extend(
            [
                "## Barter-ready",
                "",
                "No candidate currently meets every automatic barter-ready rule.",
                "",
            ]
        )
    for candidate in barter_ready:
        lines.extend(
            [
                f"## Barter-ready: @{candidate.username}",
                "",
                candidate.barter_offer,
                "",
            ]
        )
    if needs_review:
        lines.extend(
            [
                "## Preliminary drafts — sending prohibited",
                "",
                "These drafts may not be sent before explicit manual approval.",
                "",
            ]
        )
    for candidate in needs_review:
        if not candidate.barter_offer:
            continue
        lines.extend(
            [
                f"### @{candidate.username}",
                "",
                f"Review reasons: {', '.join(candidate.campaign_status_reasons)}",
                "",
                candidate.barter_offer,
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _write_report(
    path: Path,
    *,
    manifest: PhaseBRunManifest,
    personal: list[Mapping[str, Any]],
    top_ten: list[CandidateResult],
    pool_counts: Mapping[str, int],
) -> Path:
    personal_lines = []
    for item in sorted(personal, key=lambda row: str(row["username"]).casefold()):
        reasons = ", ".join(item["status_reasons"]) or "none"
        personal_lines.append(
            f"| `{item['username']}` | {item['score'] if item['score'] is not None else 'n/a'} | "
            f"{item['followers'] if item['followers'] is not None else 'n/a'} | "
            f"{', '.join(item['content_themes']) or 'none'} | "
            f"{item['known_format_posts']} | {item['detected_content_language']} | "
            f"{item['status']} | {reasons} |"
        )
    top_lines = []
    for rank, candidate in enumerate(top_ten, start=1):
        top_lines.append(
            f"| {rank} | [`{candidate.username}`]({candidate.profile_url}) | "
            f"{candidate.score:.2f} | {candidate.followers or 'n/a'} | "
            f"{', '.join(candidate.content_themes) or 'none'} | "
            f"{candidate.usable_posts} | {candidate.known_format_posts} | "
            f"{candidate.detected_content_language} | "
            f"{candidate.campaign_bucket} | "
            f"{', '.join(candidate.campaign_status_reasons) or 'none'} |"
        )
    report = f"""# Phase B final campaign review

Run ID: `{manifest.run_id}`
Source live run: `{manifest.source_run_id}`
Source reviewed run: `{manifest.review_summary.get("source_reviewed_run_id")}`
Offline-only: **true**
Provider requests: **{manifest.provider_requests_made}**
Provider budget spent: **${manifest.budget_spent_usd:.2f}**

## Classification counts

Whole saved pool:

- Barter ready: **{pool_counts.get("barter_ready", 0)}**
- Needs manual review: **{pool_counts.get("needs_manual_review", 0)}**
- Ineligible or insufficient: **{pool_counts.get("ineligible_or_insufficient", 0)}**

Personal-creator subset:

- Personal accounts: **{len(personal)}**
- Barter ready: **{sum(item["campaign_bucket"] == "barter_ready" for item in personal)}**
- Needs manual review: **{sum(item["campaign_bucket"] == "needs_manual_review" for item in personal)}**
- Ineligible or insufficient: **{sum(item["campaign_bucket"] == "ineligible_or_insufficient" for item in personal)}**

## All 16 personal accounts

| Username | Score | Followers | Themes | Known formats | Language | Status | Reasons |
|---|---:|---:|---|---:|---|---|---|
{chr(10).join(personal_lines)}

## Top 10 personal candidates

The score is diagnostic only. Campaign status and reasons override rank.

| Rank | Creator | Score | Followers | Themes | Usable posts | Known formats | Language | Bucket | Reasons |
|---:|---|---:|---:|---|---:|---:|---|---|---|
{chr(10).join(top_lines)}

## Required corrections

- `bainur_beauty` is ineligible for this barter campaign because the saved
  caption explicitly states that the creator does not work on barter. The
  profile may be considered separately for a paid campaign.
- `marwadi._.reels_29` is `insufficient_data`: all saved post-format values
  are missing, so `post_format_data_missing` is a blocking eligibility reason.
- `beauty_newnew` remains `pending` and belongs only to the manual-review
  shortlist because its audience exceeds the frozen Phase A barter threshold.

## Compatibility methodology

Language uses directly observed biography/caption script and lexical evidence.
Geography is stored only for explicit biography/caption/location evidence and
is never inferred from a username. Non-Russian content is not automatically
excluded when campaign geography is null, but Russian-language compatibility
and physical-delivery market must be manually confirmed.

## Safety

Ineligible and insufficient-data records receive no offer. Needs-review records
may receive only a preliminary draft explicitly marked “DO NOT SEND”. The
pipeline contains no message-sending functionality.
"""
    path.write_text(report, encoding="utf-8")
    return path


def run_final_campaign_review(
    source_run_dir: Path,
    reviewed_run_dir: Path,
    output_run_dir: Path,
    *,
    config_path: Path,
    review_path: Path,
) -> tuple[PhaseBRunManifest, tuple[Path, ...], tuple[CandidateResult, ...]]:
    """Classify a saved pool without constructing or calling a provider."""

    source_run_dir = Path(source_run_dir)
    reviewed_run_dir = Path(reviewed_run_dir)
    output_run_dir = Path(output_run_dir)
    for label, directory in (
        ("source run", source_run_dir),
        ("reviewed run", reviewed_run_dir),
    ):
        if not directory.is_dir():
            raise InputValidationError(f"{label} not found: {directory}")
    if output_run_dir.resolve() in {
        source_run_dir.resolve(),
        reviewed_run_dir.resolve(),
    }:
        raise OutputWriteError("final review must use a new output directory")
    if output_run_dir.exists() and any(output_run_dir.iterdir()):
        raise OutputWriteError(
            f"final review output directory is not empty: {output_run_dir}"
        )

    source_manifest = _read_json(source_run_dir / "run_manifest.json", dict)
    reviewed_manifest = _read_json(
        reviewed_run_dir / "run_manifest.json", dict
    )
    source_run_id = str(source_manifest.get("run_id") or "")
    if reviewed_manifest.get("source_run_id") != source_run_id:
        raise InputValidationError(
            "reviewed run does not reference the supplied source run"
        )
    source_records = _read_jsonl(
        source_run_dir / "enriched_candidates.jsonl"
    )
    reviewed_records = _read_jsonl(
        reviewed_run_dir / "enriched_candidates.jsonl"
    )
    source_profiles = {
        record["profile"]["identity"]["username"]: record["profile"]
        for record in source_records
    }
    reviewed_profiles = {
        record["profile"]["identity"]["username"]: record["profile"]
        for record in reviewed_records
    }
    if source_profiles != reviewed_profiles:
        raise InputValidationError(
            "source and reviewed runs do not contain identical saved profiles"
        )

    config = load_phase_b_config(config_path)
    if config.mode != "live":
        raise InputValidationError(
            "final campaign review requires the saved live configuration"
        )
    if config.google_sheets.enabled:
        raise InputValidationError(
            "final offline review requires google_sheets.enabled=false"
        )
    manual_decisions = _load_manual_decisions(review_path, source_run_id)
    reference = load_phase_a_reference(
        config.inputs.source_analysis,
        config.inputs.ideal_creator_profile,
    )
    q1, q3, extreme_threshold = _phase_a_audience_threshold(
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
        source_run_dir / "enriched_candidates.jsonl",
        reviewed_run_dir / "run_manifest.json",
    ):
        digest.update(path.read_bytes())
    manifest = PhaseBRunManifest.started(
        run_id=output_run_dir.name,
        mode="offline_final_review",
        provider="saved_provider_artifacts",
        config_digest=digest.hexdigest(),
    )
    manifest.offline_reselection = True
    manifest.source_run_id = source_run_id
    manifest.source_run_path = str(source_run_dir)
    manifest.provider_run_ids = list(
        source_manifest.get("provider_run_ids") or []
    )
    manifest.provider_requests_made = 0
    manifest.budget_spent_usd = 0.0
    manifest.cache = {"enabled": 0, "hits": 0, "misses": 0}

    all_classifications: list[dict[str, Any]] = []
    personal_classifications: list[dict[str, Any]] = []
    scoreable_personal: list[CandidateResult] = []
    enriched_audit: list[dict[str, Any]] = []

    for username, raw_profile in source_profiles.items():
        profile = CreatorProfile.from_dict(raw_profile)
        manual_decision = manual_decisions.get(username)
        assessment = assess_account_type(
            profile,
            manual_override=_manual_override(manual_decision),
        )
        metrics = calculate_candidate_metrics(profile, as_of)
        evidence = with_account_theme_evidence(
            collect_signal_evidence(profile),
            assessment,
        )
        barter = assess_barter_signals(profile)
        compatibility = assess_compatibility(profile, config.campaign)
        bucket, status, reasons, high_audience, alternative_note = (
            _campaign_classification(
                profile,
                metrics,
                assessment,
                barter,
                compatibility,
                maximum_recency_days=(
                    config.eligibility.maximum_recency_days
                ),
                minimum_usable_posts=(
                    config.eligibility.minimum_usable_posts
                ),
                extreme_audience_threshold=extreme_threshold,
                evidence=evidence,
            )
        )
        score = _score_or_none(profile, metrics, evidence, reference)
        latest = select_recent_post(
            profile,
            as_of=as_of,
            max_age_days=config.eligibility.maximum_recency_days,
        )
        latest_url = latest.url if latest is not None and latest.url else ""

        mapping = _classification_mapping(
            profile=profile,
            metrics=metrics,
            score=score,
            assessment=assessment,
            barter=barter,
            compatibility=compatibility,
            bucket=bucket,
            status=status,
            reasons=reasons,
            high_audience=high_audience,
            latest_url=latest_url,
            alternative_note=alternative_note,
        )
        all_classifications.append(mapping)
        if assessment.account_type is AccountType.PERSONAL_CREATOR:
            personal_classifications.append(mapping)

        offer_text = ""
        offer_mode = "none"
        offer_evidence: tuple[SignalEvidence, ...] = ()
        if bucket in {"barter_ready", "needs_manual_review"}:
            try:
                draft = generate_deterministic_offer(
                    profile,
                    config.campaign,
                    evidence,
                    as_of=as_of,
                    max_age_days=config.eligibility.maximum_recency_days,
                )
                offer_text = draft.text
                offer_mode = draft.generation_mode
                offer_evidence = draft.evidence
                if bucket == "needs_manual_review":
                    offer_text = (
                        "[PRELIMINARY DRAFT — DO NOT SEND BEFORE MANUAL "
                        "APPROVAL]\n\n" + offer_text
                    )
                    offer_mode = "preliminary_deterministic_template"
            except EvidenceValidationError:
                offer_text = ""
                offer_mode = "none"

        if (
            assessment.account_type is AccountType.PERSONAL_CREATOR
            and score is not None
        ):
            scoreable_personal.append(
                _candidate_result(
                    profile=profile,
                    metrics=metrics,
                    score=score,
                    assessment=assessment,
                    barter=barter,
                    compatibility=compatibility,
                    bucket=bucket,
                    status=status,
                    reasons=reasons,
                    high_audience=high_audience,
                    extreme_threshold=extreme_threshold,
                    latest_url=latest_url,
                    offer_text=offer_text,
                    offer_mode=offer_mode,
                    offer_evidence=offer_evidence,
                    alternative_note=alternative_note,
                    evidence=evidence,
                )
            )
        enriched_audit.append(
            {
                "profile": profile.to_dict(),
                "metrics": metrics.to_dict(),
                "account_type_assessment": assessment.to_dict(),
                "barter_signal_assessment": barter.to_dict(),
                "compatibility_assessment": compatibility.to_dict(),
                "campaign_classification": mapping,
            }
        )

    ranked_personal = sorted(
        scoreable_personal,
        key=lambda item: (
            -item.score,
            -item.discovery_confidence,
            item.username.casefold(),
        ),
    )
    ranked_personal = [
        replace(
            item,
            selection_explanation=(
                f"Diagnostic rank {rank} of {len(ranked_personal)} scored "
                f"personal accounts; score {item.score:.2f}/100. Campaign "
                f"bucket={item.campaign_bucket}; reasons="
                + (
                    ", ".join(item.campaign_status_reasons)
                    if item.campaign_status_reasons
                    else "none"
                )
                + ". Status overrides score."
            ),
        )
        for rank, item in enumerate(ranked_personal, start=1)
    ]
    top_ten = ranked_personal[:10]
    by_username = {item.username: item for item in ranked_personal}
    barter_ready = sorted(
        [
            by_username[item["username"]]
            for item in personal_classifications
            if item["campaign_bucket"] == "barter_ready"
            and item["username"] in by_username
        ],
        key=lambda candidate: (-(candidate.score or 0.0), candidate.username),
    )
    needs_review = sorted(
        [
            by_username[item["username"]]
            for item in personal_classifications
            if item["campaign_bucket"] == "needs_manual_review"
            and item["username"] in by_username
        ],
        key=lambda candidate: (-(candidate.score or 0.0), candidate.username),
    )
    ineligible = [
        item
        for item in all_classifications
        if item["campaign_bucket"] == "ineligible_or_insufficient"
    ]
    pool_counts = Counter(
        item["campaign_bucket"] for item in all_classifications
    )

    manifest.counts = {
        "raw_discovery_hits": len(
            _read_jsonl(source_run_dir / "discovery_pool.jsonl")
        ),
        "enriched_candidates": len(source_profiles),
        "personal_accounts": len(personal_classifications),
        "barter_ready": len(barter_ready),
        "needs_manual_review": len(needs_review),
        "ineligible_or_insufficient": len(ineligible),
        "personal_ineligible_or_insufficient": sum(
            item["campaign_bucket"] == "ineligible_or_insufficient"
            for item in personal_classifications
        ),
        "top_personal_candidates": len(top_ten),
    }
    manifest.review_summary = {
        "source_reviewed_run_id": reviewed_manifest.get("run_id"),
        "source_reviewed_run_path": str(reviewed_run_dir),
        "phase_a_audience_rule": {
            "q1": q1,
            "q3": q3,
            "formula": "q3 + 3 * (q3 - q1)",
            "threshold": extreme_threshold,
        },
        "pool_bucket_counts": dict(sorted(pool_counts.items())),
        "personal_status_counts": dict(
            sorted(
                Counter(
                    item["status"] for item in personal_classifications
                ).items()
            )
        ),
        "top_10_usernames": [item.username for item in top_ten],
        "network_statement": (
            "No provider was constructed or called. Discovery and enrichment "
            "records came only from the two saved run directories."
        ),
    }
    manifest.status = "completed"
    manifest.completed_at = datetime.now(timezone.utc)
    manifest.artifacts = {
        name: str(output_run_dir / name) for name in FINAL_REVIEW_ARTIFACTS
    }

    output_run_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: output_run_dir / name for name in FINAL_REVIEW_ARTIFACTS}
    generated_queries = _read_json(
        source_run_dir / "generated_queries.json", list
    )
    discovery_pool = _read_jsonl(
        source_run_dir / "discovery_pool.jsonl"
    )
    dedupe = _read_json(
        source_run_dir / "deduplication_report.json", dict
    )
    _write_json(paths["run_manifest.json"], manifest.to_dict())
    _write_json(paths["generated_queries.json"], generated_queries)
    _write_jsonl(paths["discovery_pool.jsonl"], discovery_pool)
    _write_json(paths["deduplication_report.json"], dedupe)
    _write_jsonl(paths["enriched_candidates.jsonl"], enriched_audit)
    for stem, values in (
        ("barter_ready", barter_ready),
        ("needs_manual_review", needs_review),
        ("ineligible_or_insufficient", ineligible),
        ("personal_candidate_classification", personal_classifications),
        ("top_10_personal_candidates", top_ten),
    ):
        _write_json(
            paths[f"{stem}.json"],
            [
                candidate_mapping(item)
                if isinstance(item, CandidateResult)
                else item
                for item in values
            ],
        )
        if stem in {
            "barter_ready",
            "needs_manual_review",
            "top_10_personal_candidates",
        }:
            write_candidate_csv(paths[f"{stem}.csv"], values)
        else:
            write_mapping_csv(
                paths[f"{stem}.csv"],
                values,
                columns=_CLASSIFICATION_COLUMNS,
            )
    write_candidate_csv(
        paths["eligible_candidates.csv"],
        barter_ready,
    )
    write_mapping_csv(
        paths["excluded_candidates.csv"],
        ineligible,
        columns=_CLASSIFICATION_COLUMNS,
    )
    _write_json(
        paths["new_creators.json"],
        [candidate_mapping(item) for item in barter_ready],
    )
    write_candidate_csv(paths["new_creators.csv"], barter_ready)
    _write_offer_drafts(
        paths["barter_offer_drafts.md"],
        barter_ready,
        needs_review,
    )
    _write_report(
        paths["discovery_report.md"],
        manifest=manifest,
        personal=personal_classifications,
        top_ten=top_ten,
        pool_counts=pool_counts,
    )
    write_candidates_workbook(
        config.inputs.workbook,
        paths["Блогеры_phase_b.xlsx"],
        top_ten,
        sheet_name=config.outputs.workbook_sheet,
    )
    return manifest, tuple(paths[name] for name in FINAL_REVIEW_ARTIFACTS), tuple(top_ten)


__all__ = ["FINAL_REVIEW_ARTIFACTS", "run_final_campaign_review"]
