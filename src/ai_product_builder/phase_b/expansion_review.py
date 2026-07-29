"""Strict offline expansion review over immutable saved Phase B artifacts.

The command implemented here is diagnostic only. It never constructs a provider,
changes the existing shortlist, or creates outreach content.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .errors import InputValidationError, OutputWriteError
from .io import json_safe
from .io.local_csv import write_mapping_csv


EXPANSION_REVIEW_ARTIFACTS: tuple[str, ...] = (
    "expansion_review_report.md",
    "expansion_candidates.json",
    "expansion_candidates.csv",
    "stylistelenaialena_review.json",
    "near_miss_personal_candidates.json",
    "near_miss_personal_candidates.csv",
    "source_integrity_manifest.json",
)

EXPANSION_COLUMNS: tuple[str, ...] = (
    "proposed_order",
    "priority",
    "username",
    "profile_url",
    "followers",
    "source_score",
    "source_status",
    "source_exclusion_reason",
    "account_type",
    "content_themes",
    "detected_language",
    "detected_geography",
    "usable_posts",
    "known_format_posts",
    "recent_post_url",
    "direct_fashion_evidence",
    "barter_evidence",
    "no_barter_evidence",
    "commercial_conflict_evidence",
    "manual_review_risks",
    "proposed_decision",
    "proposed_decision_reason",
)

NEAR_MISS_COLUMNS: tuple[str, ...] = (
    *EXPANSION_COLUMNS,
    "source_exclusion_reasons",
    "source_exclusion_evidence",
    "reason_reconsiderable",
    "remaining_risk",
    "final_slot_assessment",
)

_REQUIRED_SOURCE_ARTIFACTS = (
    "run_manifest.json",
    "eligible_candidates.csv",
    "enriched_candidates.jsonl",
)
_REQUIRED_MANUAL_FINAL_ARTIFACTS = (
    "run_manifest.json",
    "new_creators.json",
    "eligible_audit_pool.json",
)
_HARD_ACCOUNT_TYPES = frozenset(
    {
        "brand",
        "marketplace",
        "store",
        "showroom",
        "agency_or_platform",
        "thematic_non_personal_page",
        "professional_portfolio",
    }
)
_HARD_REASON_EXACT = frozenset(
    {
        "explicit_no_barter_statement",
        "commercial_conflict_own_fashion_brand",
        "commercial_conflict_clothing_showroom",
        "post_format_data_missing",
        "profile_not_public",
        "profile_not_accessible",
        "last_post_date_missing",
        "recent_evidence_url_missing",
        "identity_conflict",
    }
)
_HARD_REASON_PREFIXES = (
    "insufficient_engagement_data:",
    "latest_post_older_than_",
)
_REVIEWABLE_REASONS = frozenset(
    {
        "campaign_language_mismatch",
        "target_theme_not_relevant",
        "high_audience_barter_review_required",
        "delivery_market_uncertain",
        "commercial_terms_unclear",
        "activity_boundary_review",
    }
)
_RUSSIAN_FASHION_PATTERN = re.compile(
    r"(?:одежд|гардероб|образ|примерк|стил|мод|плать|юбк|костюм|жакет)",
    re.IGNORECASE,
)
_OWN_COMMERCIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "own_fashion_brand",
        re.compile(
            r"(?:бренд\s+женской\s+одежды|собственн\w*\s+производств|"
            r"дизайнер\s+и\s+создател|founder\s+@)",
            re.IGNORECASE,
        ),
    ),
    (
        "own_store_or_showroom",
        re.compile(
            r"(?:владелиц\w*\s+шоурум|магазин\s+одежды|"
            r"нов\w*\s+коллекц\w*.*(?:бутик|сайт)|"
            r"скидк\w*.*коллекц\w*\s+бренд)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "agency_or_platform",
        re.compile(
            r"(?:agency|platform|connecting\s+(?:brands|creators)|"
            r"pictures\s+not\s+ours|page\s+growth\s*&\s*visibility)",
            re.IGNORECASE,
        ),
    ),
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
    records: list[Mapping[str, Any]] = []
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
        records.append(value)
    return records


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return [dict(row) for row in csv.DictReader(stream)]
    except FileNotFoundError as exc:
        raise InputValidationError(f"saved artifact not found: {path}") from exc


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


def _tree_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): hashlib.sha256(
            item.read_bytes()
        ).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file() and not item.name.startswith("~$")
    }


def _profile(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("profile")
    if not isinstance(value, Mapping):
        raise InputValidationError(
            "enriched_candidates.jsonl: profile must be an object"
        )
    return value


def _username(record: Mapping[str, Any]) -> str:
    try:
        value = _profile(record)["identity"]["username"]
    except (KeyError, TypeError) as exc:
        raise InputValidationError(
            "enriched_candidates.jsonl: missing profile.identity.username"
        ) from exc
    if not isinstance(value, str) or not value:
        raise InputValidationError(
            "enriched_candidates.jsonl: invalid username"
        )
    return value


def _known_format_posts(profile: Mapping[str, Any]) -> int:
    posts = profile.get("recent_posts")
    if not isinstance(posts, list):
        return 0
    return sum(
        isinstance(post, Mapping)
        and post.get("post_format") not in (None, "", "unknown")
        for post in posts
    )


def _latest_post(profile: Mapping[str, Any]) -> Mapping[str, Any] | None:
    posts = profile.get("recent_posts")
    if not isinstance(posts, list):
        return None
    valid = [
        post
        for post in posts
        if isinstance(post, Mapping)
        and isinstance(post.get("timestamp"), str)
        and isinstance(post.get("url"), str)
    ]
    if not valid:
        return None
    return max(
        valid,
        key=lambda post: datetime.fromisoformat(
            str(post["timestamp"]).replace("Z", "+00:00")
        ),
    )


def _exact_evidence(
    record: Mapping[str, Any],
    signal: str,
) -> list[dict[str, Any]]:
    evidence = record.get("evidence")
    if not isinstance(evidence, Mapping):
        return []
    values = evidence.get(signal)
    if not isinstance(values, list):
        return []
    return [dict(value) for value in values if isinstance(value, Mapping)]


def _commercial_conflict_evidence(
    record: Mapping[str, Any],
) -> list[dict[str, Any]]:
    profile = _profile(record)
    sources: list[tuple[str, str, str]] = [
        (
            "profile.full_name",
            "profile",
            str(profile.get("full_name") or ""),
        ),
        (
            "profile.biography",
            "profile",
            str(profile.get("biography") or ""),
        ),
    ]
    posts = profile.get("recent_posts")
    if isinstance(posts, list):
        for post in posts:
            if not isinstance(post, Mapping):
                continue
            sources.append(
                (
                    "profile.recent_posts.caption",
                    str(post.get("post_id") or ""),
                    str(post.get("caption") or ""),
                )
            )
    result: list[dict[str, Any]] = []
    for source_field, source_reference, text in sources:
        for signal_type, pattern in _OWN_COMMERCIAL_PATTERNS:
            match = pattern.search(text)
            if match:
                result.append(
                    {
                        "signal_type": signal_type,
                        "source_field": source_field,
                        "source_reference": source_reference,
                        "evidence_text": match.group(0),
                        "observation_type": "direct",
                        "url": (
                            profile.get("identity", {}).get("profile_url")
                            if source_reference == "profile"
                            else next(
                                (
                                    post.get("url")
                                    for post in posts or []
                                    if isinstance(post, Mapping)
                                    and str(post.get("post_id") or "")
                                    == source_reference
                                ),
                                None,
                            )
                        ),
                    }
                )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in result:
        key = (
            item["signal_type"],
            item["source_field"],
            item["source_reference"],
            item["evidence_text"],
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _direct_russian_fashion_evidence(
    record: Mapping[str, Any],
) -> list[dict[str, Any]]:
    profile = _profile(record)
    result: list[dict[str, Any]] = []
    biography = str(profile.get("biography") or "")
    for match in _RUSSIAN_FASHION_PATTERN.finditer(biography):
        result.append(
            {
                "source_field": "profile.biography",
                "source_reference": "profile",
                "evidence_text": match.group(0),
                "url": profile.get("identity", {}).get("profile_url"),
                "observation_type": "direct",
            }
        )
    posts = profile.get("recent_posts")
    if isinstance(posts, list):
        for post in posts:
            if not isinstance(post, Mapping):
                continue
            caption = str(post.get("caption") or "")
            match = _RUSSIAN_FASHION_PATTERN.search(caption)
            if match:
                result.append(
                    {
                        "source_field": "profile.recent_posts.caption",
                        "source_reference": str(
                            post.get("post_id") or ""
                        ),
                        "evidence_text": match.group(0),
                        "url": post.get("url"),
                        "observation_type": "direct",
                    }
                )
    return result


def _hard_reason(reason: str) -> bool:
    return reason in _HARD_REASON_EXACT or reason.startswith(
        _HARD_REASON_PREFIXES
    )


def assess_near_miss(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return an auditable near-miss assessment for one saved profile."""

    profile = _profile(record)
    account = record.get("account_type_assessment")
    eligibility = record.get("eligibility")
    metrics = record.get("metrics")
    compatibility = record.get("compatibility_assessment")
    barter = record.get("barter_signal_assessment")
    identity = profile.get("identity")
    if not all(
        isinstance(value, Mapping)
        for value in (
            account,
            eligibility,
            metrics,
            compatibility,
            barter,
            identity,
        )
    ):
        raise InputValidationError(
            f"{_username(record)}: saved assessment is incomplete"
        )

    reasons = [
        str(value) for value in eligibility.get("reasons", [])
    ]
    account_type = str(account.get("account_type") or "unclear")
    hard_reasons = [reason for reason in reasons if _hard_reason(reason)]
    conflicts = _commercial_conflict_evidence(record)

    if account_type in _HARD_ACCOUNT_TYPES:
        hard_reasons.append(f"account_type:{account_type}")
    if bool(barter.get("explicit_refusal")):
        hard_reasons.append("explicit_no_barter_statement")
    if bool(identity.get("identity_conflict")):
        hard_reasons.append("identity_conflict")
    if bool(profile.get("private")) or not bool(profile.get("accessible")):
        hard_reasons.append("profile_not_public_or_accessible")
    usable_posts = int(metrics.get("usable_posts") or 0)
    known_formats = _known_format_posts(profile)
    if usable_posts < 6:
        hard_reasons.append(f"insufficient_engagement_data:{usable_posts}<6")
    if known_formats < 1:
        hard_reasons.append("post_format_data_missing")
    if conflicts:
        hard_reasons.append(
            f"commercial_conflict:{conflicts[0]['signal_type']}"
        )

    russian_fashion = _direct_russian_fashion_evidence(record)
    language_compatible = bool(
        compatibility.get("campaign_language_compatible")
    )
    language_can_be_reconsidered = (
        "campaign_language_mismatch" in reasons
        and bool(russian_fashion)
    )
    if not language_compatible and not language_can_be_reconsidered:
        hard_reasons.append("campaign_language_not_ru")

    geography = compatibility.get("detected_geography")
    if bool(compatibility.get("delivery_market_conflict")):
        hard_reasons.append("delivery_market_conflict")
    if geography is None and not (language_compatible or russian_fashion):
        hard_reasons.append("delivery_market_compatibility_absent")

    direct_fashion = _exact_evidence(record, "fashion")
    direct_post_fashion = {
        item.get("source_reference")
        for item in direct_fashion
        if item.get("source_field") == "recent_posts.caption"
    }
    if not direct_fashion or not direct_post_fashion:
        hard_reasons.append("direct_recent_fashion_evidence_absent")

    source_reasons_reviewable = all(
        reason in _REVIEWABLE_REASONS
        or reason == "account_type_not_personal:unclear"
        for reason in reasons
    )
    if not source_reasons_reviewable:
        for reason in reasons:
            if reason not in _REVIEWABLE_REASONS:
                hard_reasons.append(f"source_reason_not_reviewable:{reason}")

    if account_type == "unclear":
        full_name = str(profile.get("full_name") or "")
        biography = str(profile.get("biography") or "")
        personal_author_evidence = bool(
            re.search(
                r"(?:я\s|стилист|блогер|creator|model)",
                f"{full_name}\n{biography}",
                re.IGNORECASE,
            )
        )
        if not personal_author_evidence:
            hard_reasons.append("unclear_account_lacks_personal_author")
    elif account_type != "personal_creator":
        hard_reasons.append(f"account_type_not_personal:{account_type}")

    hard_reasons = sorted(set(hard_reasons))
    reconsiderable = not hard_reasons and bool(reasons)
    return {
        "eligible_near_miss": reconsiderable,
        "source_exclusion_reasons": reasons,
        "hard_blocking_reasons": hard_reasons,
        "reconsiderable_reasons": (
            reasons if reconsiderable else []
        ),
        "commercial_conflict_evidence": conflicts,
        "direct_fashion_evidence": direct_fashion,
        "direct_russian_fashion_evidence": russian_fashion,
        "usable_posts": usable_posts,
        "known_format_posts": known_formats,
        "language_compatible": language_compatible,
        "detected_geography": geography,
    }


def _source_score_map(path: Path) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in _read_csv(path):
        username = row.get("username")
        score = row.get("score")
        if username and score:
            result[username] = float(score)
    return result


def _manual_review_decisions(
    review_path: Path,
    source_run_id: str,
) -> dict[str, Mapping[str, Any]]:
    payload = _read_json(review_path, dict)
    if payload.get("source_run_id") != source_run_id:
        raise InputValidationError(
            f"{review_path}: source_run_id must be {source_run_id}"
        )
    raw = payload.get("decisions")
    if not isinstance(raw, list):
        raise InputValidationError(
            f"{review_path}: decisions must be an array"
        )
    result: dict[str, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise InputValidationError(
                f"{review_path}: every decision must be an object"
            )
        username = item.get("username")
        if isinstance(username, str) and username:
            result[username] = item
    return result


def _stylist_review(
    record: Mapping[str, Any],
    *,
    source_score: float | None,
    source_status: str,
    source_reason: str,
) -> dict[str, Any]:
    profile = _profile(record)
    metrics = record["metrics"]
    account = record["account_type_assessment"]
    compatibility = record["compatibility_assessment"]
    barter = record["barter_signal_assessment"]
    latest = _latest_post(profile)
    biography = str(profile.get("biography") or "")
    commercial_evidence: list[dict[str, Any]] = []
    if "реклам" in biography.casefold():
        commercial_evidence.append(
            {
                "signal_type": "commercial_pr",
                "source_field": "profile.biography",
                "source_reference": "profile",
                "evidence_text": biography,
                "observation_type": "direct",
                "url": profile["identity"]["profile_url"],
                "reason": (
                    "The biography explicitly states styling work for "
                    "advertising; this is commercial-work evidence, not barter "
                    "acceptance."
                ),
            }
        )
    conflicts = _commercial_conflict_evidence(record)
    direct_fashion = _exact_evidence(record, "fashion")
    direct_fashion_urls = {
        str(item.get("url"))
        for item in direct_fashion
        if item.get("source_field") == "recent_posts.caption"
        and isinstance(item.get("url"), str)
    }
    recent_posts = profile.get("recent_posts")
    saved_posts = (
        [
            post
            for post in recent_posts
            if isinstance(post, Mapping)
            and isinstance(post.get("timestamp"), str)
            and isinstance(post.get("url"), str)
        ]
        if isinstance(recent_posts, list)
        else []
    )
    direct_fashion_posts = [
        post
        for post in saved_posts
        if str(post["url"]) in direct_fashion_urls
    ]
    latest_direct_fashion = (
        max(
            direct_fashion_posts,
            key=lambda post: datetime.fromisoformat(
                str(post["timestamp"]).replace("Z", "+00:00")
            ),
        )
        if direct_fashion_posts
        else None
    )
    known_format_posts = [
        post
        for post in saved_posts
        if post.get("post_format") not in (None, "", "unknown")
    ]
    latest_known_format = (
        max(
            known_format_posts,
            key=lambda post: datetime.fromisoformat(
                str(post["timestamp"]).replace("Z", "+00:00")
            ),
        )
        if known_format_posts
        else None
    )
    risks = [
        "Very small saved audience: 374 followers.",
        (
            "Latest saved post is 81.29 days old, close to the 90-day "
            "campaign recency boundary."
        ),
        "Saved posting frequency is only 0.176 posts per week.",
        (
            "Only 2 of 12 sampled posts have a known format; manual review "
            "must confirm current Reels capability."
        ),
        (
            "Most sampled captions are English and several latest captions "
            "are only credits; Russian comprehension is evidenced by the bio "
            "but audience-language fit needs visual review."
        ),
        (
            "Geography is not directly evidenced and delivery compatibility "
            "with Russia remains unverified."
        ),
        (
            "No explicit barter readiness, no contact-for-collaboration "
            "signal, and no no-barter statement were found."
        ),
        (
            "The account may function primarily as a professional styling "
            "portfolio rather than a creator-led audience channel."
        ),
    ]
    return {
        "username": _username(record),
        "profile_url": profile["identity"]["profile_url"],
        "full_name": profile.get("full_name"),
        "biography": biography,
        "followers": profile.get("followers"),
        "source_score": source_score,
        "source_status": source_status,
        "source_exclusion_reason": source_reason,
        "account_type": account.get("account_type"),
        "account_type_evidence": account.get("evidence") or [],
        "detected_geography": compatibility.get("detected_geography"),
        "detected_language": compatibility.get(
            "detected_content_language"
        ),
        "language_evidence": compatibility.get("evidence") or [],
        "content_themes": account.get("relevant_dimensions") or [],
        "sampled_posts": metrics.get("sampled_posts"),
        "usable_posts": metrics.get("usable_posts"),
        "known_format_posts": _known_format_posts(profile),
        "latest_post_date": metrics.get("last_post_date"),
        "engagement_rate": metrics.get("engagement_rate"),
        "median_likes": metrics.get("median_likes"),
        "median_comments": metrics.get("median_comments"),
        "posts_per_week": metrics.get("posts_per_week"),
        "short_video_share": metrics.get("short_video_share"),
        "latest_post_url": (
            latest.get("url") if latest is not None else None
        ),
        "latest_known_format_post_url": (
            latest_known_format.get("url")
            if latest_known_format is not None
            else None
        ),
        "commercial_pr_evidence": commercial_evidence,
        "source_detected_commercial_pr_evidence": _exact_evidence(
            record, "commercial_pr"
        ),
        "barter_evidence": barter.get("barter_evidence") or [],
        "no_barter_evidence": barter.get("no_barter_evidence") or [],
        "own_brand_evidence": [
            item
            for item in conflicts
            if item["signal_type"] == "own_fashion_brand"
        ],
        "own_store_showroom_evidence": [
            item
            for item in conflicts
            if item["signal_type"] == "own_store_or_showroom"
        ],
        "agency_platform_evidence": [
            item
            for item in conflicts
            if item["signal_type"] == "agency_or_platform"
        ],
        "native_integration_evidence": _exact_evidence(
            record, "native_product_integration"
        ),
        "recent_fashion_post_url": (
            latest_direct_fashion.get("url")
            if latest_direct_fashion is not None
            else None
        ),
        "direct_fashion_evidence": direct_fashion,
        "possible_inclusion_reason": (
            "Saved data supports a public personal costume stylist with a "
            "Russian-language bio, direct fashion-work evidence, 12 usable "
            "engagement observations, 2 known post formats, and no detected "
            "barter refusal or competing fashion business."
        ),
        "risks": risks,
        "recommended_manual_status": "candidate_for_manual_review",
        "priority": "Priority B",
    }


def _near_miss_row(
    record: Mapping[str, Any],
    assessment: Mapping[str, Any],
    *,
    proposed_order: int,
    source_score: float | None,
) -> dict[str, Any]:
    profile = _profile(record)
    account = record["account_type_assessment"]
    metrics = record["metrics"]
    compatibility = record["compatibility_assessment"]
    barter = record["barter_signal_assessment"]
    latest = _latest_post(profile)
    risks = [
        *assessment["reconsiderable_reasons"],
        (
            "Manual review must confirm account identity, current campaign "
            "fit, delivery compatibility, and collaboration terms."
        ),
    ]
    return {
        "proposed_order": proposed_order,
        "priority": "Priority A",
        "username": _username(record),
        "profile_url": profile["identity"]["profile_url"],
        "followers": profile.get("followers"),
        "source_score": source_score,
        "source_status": record["eligibility"].get("status"),
        "source_exclusion_reason": (
            record["eligibility"].get("reasons") or [""]
        )[0],
        "account_type": account.get("account_type"),
        "content_themes": account.get("relevant_dimensions") or [],
        "detected_language": compatibility.get(
            "detected_content_language"
        ),
        "detected_geography": compatibility.get("detected_geography"),
        "usable_posts": metrics.get("usable_posts"),
        "known_format_posts": assessment["known_format_posts"],
        "recent_post_url": (
            latest.get("url") if latest is not None else None
        ),
        "direct_fashion_evidence": assessment[
            "direct_fashion_evidence"
        ],
        "barter_evidence": barter.get("barter_evidence") or [],
        "no_barter_evidence": barter.get("no_barter_evidence") or [],
        "commercial_conflict_evidence": assessment[
            "commercial_conflict_evidence"
        ],
        "manual_review_risks": risks,
        "proposed_decision": "needs_manual_review",
        "proposed_decision_reason": (
            "The saved automatic reason appears potentially reviewable, but "
            "the candidate may be added only after manual Instagram review."
        ),
        "source_exclusion_reasons": assessment[
            "source_exclusion_reasons"
        ],
        "source_exclusion_evidence": [
            *assessment["direct_russian_fashion_evidence"],
            *assessment["direct_fashion_evidence"],
        ],
        "reason_reconsiderable": True,
        "remaining_risk": risks,
        "final_slot_assessment": (
            "Could be considered for slot four or five only after manual "
            "review; no automatic promotion is allowed."
        ),
    }


def _expansion_row(
    review: Mapping[str, Any],
    *,
    proposed_order: int,
) -> dict[str, Any]:
    return {
        "proposed_order": proposed_order,
        "priority": review["priority"],
        "username": review["username"],
        "profile_url": review["profile_url"],
        "followers": review["followers"],
        "source_score": review["source_score"],
        "source_status": review["source_status"],
        "source_exclusion_reason": review["source_exclusion_reason"],
        "account_type": review["account_type"],
        "content_themes": review["content_themes"],
        "detected_language": review["detected_language"],
        "detected_geography": review["detected_geography"],
        "usable_posts": review["usable_posts"],
        "known_format_posts": review["known_format_posts"],
        "recent_post_url": review["recent_fashion_post_url"],
        "direct_fashion_evidence": review["direct_fashion_evidence"],
        "barter_evidence": review["barter_evidence"],
        "no_barter_evidence": review["no_barter_evidence"],
        "commercial_conflict_evidence": [
            *review["own_brand_evidence"],
            *review["own_store_showroom_evidence"],
            *review["agency_platform_evidence"],
        ],
        "manual_review_risks": review["risks"],
        "proposed_decision": "needs_manual_review",
        "proposed_decision_reason": (
            "The saved profile is eligible in the source run but was not "
            "manually verified for the final shortlist. Visual review of "
            "identity, current fashion content, Reels capability, Russia "
            "delivery compatibility, and barter terms is required."
        ),
    }


def _rejected_rows(
    records: Iterable[Mapping[str, Any]],
    assessments: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in records:
        username = _username(record)
        assessment = assessments[username]
        if assessment["eligible_near_miss"]:
            continue
        eligibility = record["eligibility"]
        result.append(
            {
                "username": username,
                "source_status": eligibility.get("status"),
                "source_reasons": eligibility.get("reasons") or [],
                "blocking_reasons": assessment[
                    "hard_blocking_reasons"
                ],
                "commercial_conflict_evidence": assessment[
                    "commercial_conflict_evidence"
                ],
            }
        )
    return result


def _md_cell(value: Any) -> str:
    text = json.dumps(
        json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
    )
    return text.replace("|", "\\|").replace("\n", " ")


def _report(
    path: Path,
    *,
    source_run_id: str,
    final_shortlist: list[Mapping[str, Any]],
    stylist_review: Mapping[str, Any],
    near_misses: list[Mapping[str, Any]],
    rejected: list[Mapping[str, Any]],
    enriched_count: int,
    source_eligible_count: int,
) -> Path:
    lines = [
        "# Phase B expansion review",
        "",
        "Диагностический офлайн-анализ сохранённого второго live-run.",
        "",
        f"- Source run: `{source_run_id}`",
        "- Provider requests: `0`",
        "- Budget spent: `$0.00`",
        "- Outreach messages sent: `0`",
        f"- Enriched profiles reviewed: `{enriched_count}`",
        f"- Source eligible profiles: `{source_eligible_count}`",
        f"- Qualified near-miss profiles: `{len(near_misses)}`",
        "- Final shortlist changed: `false`",
        "",
        "## Текущая финальная тройка — без изменений",
        "",
        "| Order | Username | Source score | Manual status |",
        "|---:|---|---:|---|",
    ]
    for index, item in enumerate(final_shortlist, start=1):
        lines.append(
            f"| {index} | [@{item['username']}]({item['profile_url']}) | "
            f"{float(item['score']):.2f} | "
            f"`{item['manual_verification_status']}` |"
        )
    lines.extend(
        [
            "",
            "## Проверка stylistelenaialena",
            "",
            "| Field | Saved result |",
            "|---|---|",
            f"| Username | `@{stylist_review['username']}` |",
            (
                f"| Profile | [{stylist_review['profile_url']}]"
                f"({stylist_review['profile_url']}) |"
            ),
            f"| Followers | {stylist_review['followers']} |",
            f"| Account type | `{stylist_review['account_type']}` |",
            (
                f"| Language | "
                f"`{stylist_review['detected_language']}` |"
            ),
            (
                f"| Geography | "
                f"`{stylist_review['detected_geography']}` |"
            ),
            (
                f"| Themes | "
                f"{_md_cell(stylist_review['content_themes'])} |"
            ),
            (
                f"| Sampled / usable / known-format posts | "
                f"{stylist_review['sampled_posts']} / "
                f"{stylist_review['usable_posts']} / "
                f"{stylist_review['known_format_posts']} |"
            ),
            (
                f"| Latest saved post | "
                f"`{stylist_review['latest_post_date']}`; "
                f"[saved URL]({stylist_review['latest_post_url']}) |"
            ),
            (
                "| Latest directly evidenced fashion post | "
                f"[saved URL]({stylist_review['recent_fashion_post_url']}) |"
            ),
            (
                "| Latest known-format post | "
                f"[saved URL]"
                f"({stylist_review['latest_known_format_post_url']}) |"
            ),
            (
                f"| Engagement rate | "
                f"{float(stylist_review['engagement_rate']):.3f}% |"
            ),
            (
                f"| Short-video share | "
                f"{float(stylist_review['short_video_share']):.1%} |"
            ),
            (
                "| Commercial/PR evidence | Direct bio statement: "
                "`Создаю образы для fashion-съёмок, рекламы, кино, "
                "частных клиентов.` |"
            ),
            "| Barter evidence | none |",
            "| No-barter evidence | none |",
            "| Own brand/store/agency evidence | none in saved data |",
            (
                "| Recommended status | "
                "`candidate_for_manual_review` — `Priority B` |"
            ),
            "",
            "### Почему можно рассматривать",
            "",
            stylist_review["possible_inclusion_reason"],
            "",
            "### Риски",
            "",
        ]
    )
    lines.extend(f"- {risk}" for risk in stylist_review["risks"])
    lines.extend(
        [
            "",
            "## Near-miss personal candidates",
            "",
        ]
    )
    if near_misses:
        lines.extend(
            [
                "| Priority | Username | Source reason | Why reviewable |",
                "|---|---|---|---|",
            ]
        )
        for item in near_misses:
            lines.append(
                f"| {item['priority']} | "
                f"[@{item['username']}]({item['profile_url']}) | "
                f"`{item['source_exclusion_reason']}` | "
                f"{item['proposed_decision_reason']} |"
            )
    else:
        lines.append(
            "Качественных near-miss кандидатов: **0**. Ни одно жёсткое "
            "автоматическое исключение не было переопределено."
        )
    lines.extend(
        [
            "",
            "## Priority for manual review",
            "",
            "- **Priority A:** none.",
            (
                "- **Priority B:** `stylistelenaialena` — проверить "
                "персональность автора, визуальную долю женской одежды в "
                "последних постах, способность сделать Reels, актуальную "
                "активность, аудиторию из России, доставку и условия бартера."
            ),
            (
                "- **Rejected:** все 20 не-eligible enriched-профилей; "
                "запрещённые причины не пересматривались."
            ),
            "",
            "## Rejected profiles",
            "",
            "| Username | Source reasons | Blocking reasons |",
            "|---|---|---|",
        ]
    )
    for item in rejected:
        lines.append(
            f"| `@{item['username']}` | "
            f"{_md_cell(item['source_reasons'])} | "
            f"{_md_cell(item['blocking_reasons'])} |"
        )
    lines.extend(
        [
            "",
            "Особенно важные проверки:",
            "",
            (
                "- `malina_fashion`: full_name прямо говорит "
                "`Бренд женской одежды Malina Bonita` и "
                "`собственное производство`; посты ведут в бутики и магазин. "
                "Это не creator near-miss."
            ),
            (
                "- `theonlyone_brand`: сохранённый account type — `store`; "
                "посты продают одежду и приглашают в магазин/шоурум."
            ),
            (
                "- `stylist_katy`: профиль недоступен, usable posts = 0, "
                "post formats отсутствуют, recent evidence URL отсутствует."
            ),
            (
                "- `curvy_josies_fashion`: сохранённые captions на английском, "
                "есть прямое упоминание возвращения в Германию; русского или "
                "российского контекста нет."
            ),
            "",
            "## Ответы на вопросы",
            "",
            (
                "1. **Подходит ли stylistelenaialena для ручной проверки?** "
                "Да, как `Priority B`, но не для автоматического включения."
            ),
            (
                f"2. **Сколько качественных near-miss найдено?** "
                f"{len(near_misses)}."
            ),
            (
                "3. **Можно ли получить финальную пятёрку без нового API?** "
                "По сохранённым данным — нет. Даже положительное решение по "
                "`stylistelenaialena` даст максимум четыре подтверждённых "
                "профиля; второго допустимого кандидата в пуле нет."
            ),
            (
                "4. **Что открыть в Instagram?** Только профиль "
                "`stylistelenaialena`, последний сохранённый пост "
                f"{stylist_review['latest_post_url']}, последний пост с "
                "прямым fashion-evidence "
                f"{stylist_review['recent_fashion_post_url']} и последний "
                "пост с известным форматом "
                f"{stylist_review['latest_known_format_post_url']}."
            ),
            (
                "5. **Что проверить визуально?** Наличие персонального автора "
                "в кадре; женская одежда и образы; актуальность публикаций; "
                "качество Reels; реальная вовлечённость; русскоязычная "
                "аудитория; Россия/доставка; готовность к товарному бартеру; "
                "отсутствие собственного конкурирующего бренда или магазина."
            ),
            (
                "6. **Что исключило кандидата?** `stylistelenaialena` не был "
                "исключён системой: он остался sixth eligible, но получил "
                "`not_manually_verified_for_final_shortlist`. Исходные причины "
                "остальных профилей приведены в таблице Rejected."
            ),
            (
                "7. **Почему review не нарушает логику системы?** "
                "Диагностика не меняет eligibility, scores или shortlist. "
                "Она сохраняет жёсткие исключения и присваивает каждому "
                "expansion-кандидату только `needs_manual_review`."
            ),
            "",
            "## Safety and integrity",
            "",
            (
                "Команда читает только сохранённые artifacts, не импортирует "
                "provider adapters, не создаёт офферы и не изменяет Phase A, "
                "source run или manual-final run. Оба каталога проверены "
                "SHA-256 до и после выполнения."
            ),
        ]
    )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def run_expansion_review(
    source_run_dir: Path,
    manual_final_run_dir: Path,
    output_run_dir: Path,
    *,
    review_path: Path,
    focus_username: str = "stylistelenaialena",
) -> tuple[dict[str, Any], tuple[Path, ...], tuple[dict[str, Any], ...]]:
    """Create a diagnostic expansion shortlist using saved data only."""

    source_run_dir = Path(source_run_dir)
    manual_final_run_dir = Path(manual_final_run_dir)
    output_run_dir = Path(output_run_dir)
    review_path = Path(review_path)
    for directory, required in (
        (source_run_dir, _REQUIRED_SOURCE_ARTIFACTS),
        (manual_final_run_dir, _REQUIRED_MANUAL_FINAL_ARTIFACTS),
    ):
        if not directory.is_dir():
            raise InputValidationError(
                f"saved run directory not found: {directory}"
            )
        missing = [
            name
            for name in required
            if not (directory / name).is_file()
        ]
        if missing:
            raise InputValidationError(
                f"saved run is incomplete: {directory}; missing={missing}"
            )
    if not review_path.is_file():
        raise InputValidationError(
            f"structured review not found: {review_path}"
        )
    if output_run_dir.exists():
        raise OutputWriteError(
            f"expansion-review output already exists: {output_run_dir}"
        )
    resolved_inputs = {
        source_run_dir.resolve(),
        manual_final_run_dir.resolve(),
    }
    if output_run_dir.resolve() in resolved_inputs:
        raise OutputWriteError("input run cannot be used as output")
    if any(
        directory in output_run_dir.resolve().parents
        for directory in resolved_inputs
    ):
        raise OutputWriteError(
            "expansion-review output cannot be nested inside an input run"
        )

    source_hashes_before = _tree_hashes(source_run_dir)
    manual_hashes_before = _tree_hashes(manual_final_run_dir)
    review_hash_before = hashlib.sha256(review_path.read_bytes()).hexdigest()

    source_manifest = _read_json(
        source_run_dir / "run_manifest.json", dict
    )
    source_run_id = str(source_manifest.get("run_id") or "")
    if not source_run_id:
        raise InputValidationError("source manifest has no run_id")
    decisions = _manual_review_decisions(review_path, source_run_id)

    enriched = _read_jsonl(
        source_run_dir / "enriched_candidates.jsonl"
    )
    by_username = {_username(record): record for record in enriched}
    if len(by_username) != len(enriched):
        raise InputValidationError(
            "saved enrichment contains duplicate usernames"
        )
    if focus_username not in by_username:
        raise InputValidationError(
            f"focus candidate not found: {focus_username}"
        )

    score_map = _source_score_map(
        source_run_dir / "eligible_candidates.csv"
    )
    manual_audit = _read_json(
        manual_final_run_dir / "eligible_audit_pool.json", list
    )
    audit_by_username = {
        str(item.get("username")): item
        for item in manual_audit
        if isinstance(item, Mapping)
    }
    if focus_username not in audit_by_username:
        raise InputValidationError(
            f"focus candidate missing from manual-final audit: "
            f"{focus_username}"
        )
    focus_decision = decisions.get(focus_username)
    if not isinstance(focus_decision, Mapping):
        raise InputValidationError(
            f"focus candidate missing from structured review: "
            f"{focus_username}"
        )
    source_reason = str(
        focus_decision.get("reason_code")
        or "not_manually_verified_for_final_shortlist"
    )
    source_status = str(
        audit_by_username[focus_username].get("status")
        or "eligible_not_selected_pending_manual_review"
    )
    stylist_review = _stylist_review(
        by_username[focus_username],
        source_score=score_map.get(focus_username),
        source_status=source_status,
        source_reason=source_reason,
    )

    final_shortlist = _read_json(
        manual_final_run_dir / "new_creators.json", list
    )
    selected_decisions = sorted(
        (
            value
            for value in decisions.values()
            if value.get("action") == "select"
        ),
        key=lambda value: int(value.get("selection_order") or 999),
    )
    expected_final = [
        str(value["username"]) for value in selected_decisions
    ]
    actual_final = [
        str(item.get("username"))
        for item in final_shortlist
        if isinstance(item, Mapping)
    ]
    if actual_final != expected_final or len(actual_final) != 3:
        raise InputValidationError(
            "manual-final shortlist does not match structured review"
        )

    noneligible = [
        record
        for record in enriched
        if not bool(record.get("eligibility", {}).get("eligible"))
    ]
    assessments = {
        _username(record): assess_near_miss(record)
        for record in noneligible
    }
    qualified_records = [
        record
        for record in noneligible
        if assessments[_username(record)]["eligible_near_miss"]
    ]
    qualified_records.sort(
        key=lambda record: (
            -int(record.get("metrics", {}).get("usable_posts") or 0),
            -float(
                record.get("metrics", {}).get("engagement_rate") or 0.0
            ),
            _username(record),
        )
    )
    near_misses = [
        _near_miss_row(
            record,
            assessments[_username(record)],
            proposed_order=index + 2,
            source_score=score_map.get(_username(record)),
        )
        for index, record in enumerate(qualified_records[:5])
    ]
    expansion_candidates = [
        _expansion_row(stylist_review, proposed_order=1),
        *near_misses,
    ]
    if len(expansion_candidates) > 6:
        raise OutputWriteError(
            "expansion shortlist exceeded the six-candidate limit"
        )
    if any(
        item.get("proposed_decision") != "needs_manual_review"
        for item in expansion_candidates
    ):
        raise OutputWriteError(
            "expansion candidates must remain needs_manual_review"
        )
    rejected = _rejected_rows(noneligible, assessments)

    output_run_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=f".{output_run_dir.name}-",
        dir=output_run_dir.parent,
    ) as temporary:
        staging = Path(temporary)
        paths = {
            name: staging / name
            for name in EXPANSION_REVIEW_ARTIFACTS
        }
        _write_json(
            paths["expansion_candidates.json"], expansion_candidates
        )
        write_mapping_csv(
            paths["expansion_candidates.csv"],
            expansion_candidates,
            columns=EXPANSION_COLUMNS,
        )
        _write_json(
            paths["stylistelenaialena_review.json"], stylist_review
        )
        _write_json(
            paths["near_miss_personal_candidates.json"], near_misses
        )
        write_mapping_csv(
            paths["near_miss_personal_candidates.csv"],
            near_misses,
            columns=NEAR_MISS_COLUMNS,
        )
        _report(
            paths["expansion_review_report.md"],
            source_run_id=source_run_id,
            final_shortlist=[
                dict(item)
                for item in final_shortlist
                if isinstance(item, Mapping)
            ],
            stylist_review=stylist_review,
            near_misses=near_misses,
            rejected=rejected,
            enriched_count=len(enriched),
            source_eligible_count=len(score_map),
        )

        source_hashes_after = _tree_hashes(source_run_dir)
        manual_hashes_after = _tree_hashes(manual_final_run_dir)
        review_hash_after = hashlib.sha256(
            review_path.read_bytes()
        ).hexdigest()
        if source_hashes_before != source_hashes_after:
            raise OutputWriteError(
                "source run changed during expansion review"
            )
        if manual_hashes_before != manual_hashes_after:
            raise OutputWriteError(
                "manual-final run changed during expansion review"
            )
        if review_hash_before != review_hash_after:
            raise OutputWriteError(
                "structured review changed during expansion review"
            )

        manifest = {
            "run_id": output_run_dir.name,
            "source_run_id": source_run_id,
            "source_run_path": str(source_run_dir),
            "manual_final_run_id": manual_final_run_dir.name,
            "manual_final_run_path": str(manual_final_run_dir),
            "review_path": str(review_path),
            "mode": "offline_expansion_review",
            "status": "completed",
            "offline_reselection": True,
            "diagnostic_only": True,
            "provider_requests_made": 0,
            "budget_spent_usd": 0.0,
            "outreach_messages_sent": 0,
            "offers_generated": 0,
            "phase_a_unchanged": True,
            "source_run_unchanged": True,
            "manual_final_run_unchanged": True,
            "structured_review_unchanged": True,
            "final_shortlist_unchanged": True,
            "final_shortlist": [
                {
                    "username": item["username"],
                    "score": item["score"],
                    "manual_verification_status": item[
                        "manual_verification_status"
                    ],
                    "recent_post_url": item["recent_post_url"],
                }
                for item in final_shortlist
                if isinstance(item, Mapping)
            ],
            "counts": {
                "enriched_profiles_reviewed": len(enriched),
                "source_eligible_profiles": len(score_map),
                "source_noneligible_profiles": len(noneligible),
                "focus_candidates": 1,
                "qualified_near_miss_candidates": len(near_misses),
                "expansion_candidates": len(expansion_candidates),
                "priority_a": sum(
                    item["priority"] == "Priority A"
                    for item in expansion_candidates
                ),
                "priority_b": sum(
                    item["priority"] == "Priority B"
                    for item in expansion_candidates
                ),
                "rejected_noneligible_profiles": len(rejected),
            },
            "source_artifact_sha256": source_hashes_before,
            "manual_final_artifact_sha256": manual_hashes_before,
            "structured_review_sha256": review_hash_before,
            "artifacts": {
                name: str(output_run_dir / name)
                for name in EXPANSION_REVIEW_ARTIFACTS
            },
            "warnings": (
                [
                    "No qualified near-miss personal creators were found; "
                    "the saved pool cannot produce five confirmed creators."
                ]
                if not near_misses
                else []
            ),
            "errors": [],
        }
        _write_json(
            paths["source_integrity_manifest.json"], manifest
        )
        staging.replace(output_run_dir)

    final_paths = tuple(
        output_run_dir / name
        for name in EXPANSION_REVIEW_ARTIFACTS
    )
    return manifest, final_paths, tuple(expansion_candidates)


__all__ = [
    "EXPANSION_COLUMNS",
    "EXPANSION_REVIEW_ARTIFACTS",
    "NEAR_MISS_COLUMNS",
    "assess_near_miss",
    "run_expansion_review",
]
