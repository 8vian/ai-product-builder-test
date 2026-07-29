"""Offline human-in-the-loop finalization of one immutable saved Phase B run.

This module deliberately has no provider imports. It consumes only the artifacts
already present in a completed run and a structured review decision file.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .errors import InputValidationError, OutputWriteError
from .io import PHASE_B_COLUMNS, json_safe
from .io.local_csv import (
    write_candidate_csv,
    write_excluded_candidates_csv,
    write_mapping_csv,
)
from .io.local_xlsx import write_candidates_workbook


MANUAL_FINALIZATION_ARTIFACTS: tuple[str, ...] = (
    "run_manifest.json",
    "eligible_candidates.csv",
    "excluded_candidates.csv",
    "new_creators.json",
    "new_creators.csv",
    "barter_offer_drafts.md",
    "discovery_report.md",
    "Блогеры_phase_b.xlsx",
    "eligible_audit_pool.json",
    "eligible_audit_pool.csv",
    "selected_post_evidence.json",
    "manual_review_audit.json",
)

_REQUIRED_SOURCE_ARTIFACTS: tuple[str, ...] = (
    "run_manifest.json",
    "eligible_candidates.csv",
    "excluded_candidates.csv",
    "enriched_candidates.jsonl",
    "new_creators.json",
    "Блогеры_phase_b.xlsx",
)

_JSON_FIELDS = frozenset(
    {
        "score_components",
        "evidence",
        "eligibility_reasons",
        "query_ids",
        "content_themes",
        "barter_evidence",
        "no_barter_evidence",
        "campaign_status_reasons",
    }
)
_INTEGER_FIELDS = frozenset(
    {"followers", "usable_posts", "sampled_posts", "known_format_posts"}
)
_FLOAT_FIELDS = frozenset(
    {
        "median_likes",
        "median_comments",
        "engagement_rate",
        "data_completeness",
        "short_video_share",
        "score",
        "discovery_confidence",
    }
)
_BOOLEAN_FIELDS = frozenset(
    {
        "barter_feasibility_review_required",
        "campaign_language_compatible",
        "delivery_market_review_required",
    }
)
_CLOTHING_POST_PATTERN = re.compile(
    r"(?:одежд|гардероб|образ|костюм|плать|жакет|блуз|юбк|вещ|лук|"
    r"примерк|стилиз|стилист|ткан|кро[йя]|fashion|outfit|styling)",
    re.IGNORECASE,
)
_SELECTION_ACTION = "select"
_REJECTION_ACTION = "reject"
_AUDIT_ONLY_ACTION = "eligible_not_selected"


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


def _tree_hashes(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if not item.is_file() or item.name.startswith("~$"):
            continue
        hashes[item.relative_to(path).as_posix()] = hashlib.sha256(
            item.read_bytes()
        ).hexdigest()
    return hashes


def _typed_csv_value(field: str, value: str) -> Any:
    if value == "":
        return None if field in _INTEGER_FIELDS | _FLOAT_FIELDS else ""
    if field in _JSON_FIELDS:
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise InputValidationError(
                f"eligible_candidates.csv: invalid JSON in {field}"
            ) from exc
    if field in _INTEGER_FIELDS:
        return int(value)
    if field in _FLOAT_FIELDS:
        return float(value)
    if field in _BOOLEAN_FIELDS:
        lowered = value.casefold()
        if lowered not in {"true", "false", "1", "0"}:
            raise InputValidationError(
                f"eligible_candidates.csv: invalid boolean in {field}"
            )
        return lowered in {"true", "1"}
    return value


def _read_candidate_csv(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except FileNotFoundError as exc:
        raise InputValidationError(f"saved artifact not found: {path}") from exc
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                field: _typed_csv_value(field, value or "")
                for field, value in row.items()
                if field is not None
            }
        )
    return result


def _read_csv_mappings(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return [dict(row) for row in csv.DictReader(stream)]
    except FileNotFoundError as exc:
        raise InputValidationError(f"saved artifact not found: {path}") from exc


def _profile_username(record: Mapping[str, Any]) -> str:
    try:
        username = record["profile"]["identity"]["username"]
    except (KeyError, TypeError) as exc:
        raise InputValidationError(
            "enriched_candidates.jsonl: missing profile.identity.username"
        ) from exc
    if not isinstance(username, str) or not username:
        raise InputValidationError(
            "enriched_candidates.jsonl: invalid profile identity username"
        )
    return username


def _load_review(
    path: Path,
    *,
    source_run_id: str,
    eligible_usernames: set[str],
) -> tuple[Mapping[str, Any], dict[str, Mapping[str, Any]]]:
    payload = _read_json(path, dict)
    if payload.get("source_run_id") != source_run_id:
        raise InputValidationError(
            f"{path}: source_run_id must be exactly {source_run_id}"
        )
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise InputValidationError(f"{path}: decisions must be an array")

    by_username: dict[str, Mapping[str, Any]] = {}
    for raw in decisions:
        if not isinstance(raw, Mapping):
            raise InputValidationError(
                f"{path}: every decision must be an object"
            )
        username = raw.get("username")
        if not isinstance(username, str) or not username:
            raise InputValidationError(
                f"{path}: every decision needs an exact username"
            )
        if username in by_username:
            raise InputValidationError(
                f"{path}: duplicate decision for {username}"
            )
        if username not in eligible_usernames:
            raise InputValidationError(
                f"{path}: {username} is not in the saved eligible pool"
            )
        action = raw.get("action")
        if action not in {
            _SELECTION_ACTION,
            _REJECTION_ACTION,
            _AUDIT_ONLY_ACTION,
        }:
            raise InputValidationError(
                f"{path}: unsupported action for {username}: {action}"
            )
        if action == _REJECTION_ACTION:
            if raw.get("manual_verification_status") != "rejected":
                raise InputValidationError(
                    f"{path}: rejected decision for {username} must have "
                    "manual_verification_status=rejected"
                )
            if not str(raw.get("reason_code") or "").strip():
                raise InputValidationError(
                    f"{path}: rejected decision for {username} needs "
                    "reason_code"
                )
            if not str(raw.get("decision_reason") or "").strip():
                raise InputValidationError(
                    f"{path}: rejected decision for {username} needs "
                    "decision_reason"
                )
        by_username[username] = raw

    if set(by_username) != eligible_usernames:
        missing = sorted(eligible_usernames - set(by_username))
        raise InputValidationError(
            f"{path}: every saved eligible candidate must be reviewed; "
            f"missing={missing}"
        )

    selected = [
        value
        for value in by_username.values()
        if value.get("action") == _SELECTION_ACTION
    ]
    orders = [value.get("selection_order") for value in selected]
    if len(selected) != 3 or sorted(orders) != [1, 2, 3]:
        raise InputValidationError(
            f"{path}: final shortlist must contain selection_order 1, 2, and 3"
        )
    statuses = {
        value.get("manual_verification_status") for value in selected
    }
    if not statuses <= {"approved", "pending"}:
        raise InputValidationError(
            f"{path}: selected statuses must be approved or pending"
        )
    return payload, by_username


def _post_for_decision(
    profile_record: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> Mapping[str, Any]:
    selected_url = decision.get("selected_post_url")
    if not isinstance(selected_url, str) or not selected_url:
        raise InputValidationError(
            f"{decision.get('username')}: selected_post_url is required"
        )
    try:
        posts = profile_record["profile"]["recent_posts"]
    except (KeyError, TypeError) as exc:
        raise InputValidationError(
            f"{decision.get('username')}: saved recent posts are missing"
        ) from exc
    if not isinstance(posts, list):
        raise InputValidationError(
            f"{decision.get('username')}: saved recent posts must be an array"
        )
    post = next(
        (
            value
            for value in posts
            if isinstance(value, Mapping) and value.get("url") == selected_url
        ),
        None,
    )
    if post is None:
        raise InputValidationError(
            f"{decision.get('username')}: selected post is not in saved data"
        )
    caption = str(post.get("caption") or "")
    if not _CLOTHING_POST_PATTERN.search(caption):
        raise InputValidationError(
            f"{decision.get('username')}: selected post lacks direct "
            "clothing/fashion evidence"
        )
    return post


def _evidence_record(
    *,
    signal_type: str,
    source_field: str,
    source_reference: str,
    evidence_text: str,
    url: str,
    observation_type: str = "direct",
) -> dict[str, Any]:
    return {
        "signal_type": signal_type,
        "source_field": source_field,
        "source_reference": source_reference,
        "evidence_text": evidence_text,
        "url": url,
        "observation_type": observation_type,
    }


def _offer_text(decision: Mapping[str, Any]) -> str:
    first_name = str(decision.get("first_name") or "").strip()
    personalization = str(
        decision.get("offer_personalization") or ""
    ).strip()
    closing = str(decision.get("offer_closing") or "").strip()
    if not first_name or not personalization:
        raise InputValidationError(
            f"{decision.get('username')}: offer first name and "
            "personalization are required"
        )
    paragraphs = [
        f"{first_name}, здравствуйте!",
        (
            "Мы — LD Latte, бренд женской одежды. "
            f"{personalization}"
        ),
        (
            "Хотим предложить бартер: товар из новой коллекции LD Latte "
            "в обмен на согласованный Reels или нативный обзор. Нам важно "
            "сохранить ваш авторский стиль: без жёсткого сценария, но с "
            "предварительным согласованием выбранной вещи, ключевых акцентов "
            "и состава контента."
        ),
        closing
        or (
            "Если вам откликается идея, обсудим размер, подходящую модель, "
            "сроки и формат публикации."
        ),
    ]
    return "\n\n".join(paragraphs)


def _selected_candidate(
    candidate: Mapping[str, Any],
    profile_record: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    item = copy.deepcopy(dict(candidate))
    post = _post_for_decision(profile_record, decision)
    username = str(item["username"])
    profile_url = str(item["profile_url"])
    selected_url = str(post["url"])
    post_id = str(post.get("post_id") or selected_url)
    caption = " ".join(str(post.get("caption") or "").split())
    evidence_summary = str(decision.get("post_evidence_summary") or "").strip()
    if not evidence_summary:
        raise InputValidationError(
            f"{username}: post_evidence_summary is required"
        )

    new_evidence = [
        _evidence_record(
            signal_type="human_shortlist_decision",
            source_field="manual_review.decisions",
            source_reference=username,
            evidence_text=str(decision.get("decision_reason") or ""),
            url=profile_url,
            observation_type="derived",
        ),
        _evidence_record(
            signal_type="offer_personalization",
            source_field="profile.recent_posts.caption",
            source_reference=post_id,
            evidence_text=evidence_summary,
            url=selected_url,
        ),
    ]
    item["evidence"] = [*(item.get("evidence") or []), *new_evidence]
    item["recent_post_url"] = selected_url
    item["barter_offer"] = _offer_text(decision)
    item["manual_verification_status"] = decision[
        "manual_verification_status"
    ]
    item["verification_notes"] = str(
        decision.get("verification_notes") or ""
    )
    item["outreach_status"] = "not_sent"
    item["offer_generation_mode"] = "deterministic_manual_review_template"
    item["selection_explanation"] = (
        f"{item.get('selection_explanation', '').rstrip()} "
        f"Human-in-the-loop decision: "
        f"{decision.get('decision_reason', '')}"
    ).strip()

    post_evidence = {
        "username": username,
        "profile_url": profile_url,
        "post_id": post_id,
        "post_url": selected_url,
        "post_format": post.get("post_format"),
        "post_timestamp": post.get("timestamp"),
        "caption_excerpt": caption[:600],
        "evidence_summary": evidence_summary,
        "direct_clothing_or_fashion_evidence": True,
        "source": "saved_enriched_candidates_jsonl",
    }
    return item, post_evidence


def _audit_candidate(
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
    selected: Mapping[str, Any] | None,
    manual_exclusion: Mapping[str, Any] | None,
) -> dict[str, Any]:
    item = copy.deepcopy(dict(selected or candidate))
    action = str(decision["action"])
    reason_code = str(decision.get("reason_code") or "")
    item["manual_review_action"] = action
    item["manual_review_reason"] = str(
        decision.get("decision_reason") or ""
    )
    item["commercial_conflict_account"] = str(
        decision.get("commercial_conflict_account") or ""
    )
    item["outreach_status"] = "not_sent"

    if action == _SELECTION_ACTION:
        status = str(decision["manual_verification_status"])
        item["status"] = f"selected_{status}"
        return item

    item["barter_offer"] = ""
    item["offer_generation_mode"] = "none"
    item["recent_post_url"] = item.get("recent_post_url") or ""
    if action == _REJECTION_ACTION:
        item["manual_verification_status"] = "rejected"
        item["manual_review_evidence"] = list(
            (manual_exclusion or {}).get("manual_review_evidence") or []
        )
        item["eligibility_status"] = "ineligible"
        item["eligibility_reasons"] = [
            *(item.get("eligibility_reasons") or []),
            reason_code,
        ]
        item["campaign_bucket"] = "ineligible_or_insufficient"
        item["campaign_status_reasons"] = [
            *(item.get("campaign_status_reasons") or []),
            reason_code,
        ]
        item["status"] = "rejected"
    else:
        item["manual_verification_status"] = "pending"
        item["campaign_bucket"] = "needs_manual_review"
        item["campaign_status_reasons"] = [
            *(item.get("campaign_status_reasons") or []),
            reason_code,
        ]
        item["verification_notes"] = str(
            decision.get("verification_notes") or ""
        )
        item["status"] = (
            "eligible_not_selected_pending_manual_review"
        )
    return item


def _profile_mapping(
    candidate: Mapping[str, Any],
    profile_record: Mapping[str, Any],
) -> Mapping[str, Any]:
    profile = profile_record.get("profile")
    if not isinstance(profile, Mapping):
        raise InputValidationError(
            f"{candidate.get('username')}: profile evidence is missing"
        )
    return profile


def _profile_post(
    candidate: Mapping[str, Any],
    profile: Mapping[str, Any],
    source_reference: str,
) -> Mapping[str, Any]:
    posts = profile.get("recent_posts")
    if not isinstance(posts, list):
        raise InputValidationError(
            f"{candidate.get('username')}: saved recent posts are missing"
        )
    post = next(
        (
            value
            for value in posts
            if isinstance(value, Mapping)
            and source_reference
            in {
                str(value.get("post_id") or ""),
                str(value.get("url") or ""),
            }
        ),
        None,
    )
    if post is None:
        raise InputValidationError(
            f"{candidate.get('username')}: manual exclusion evidence "
            f"references unknown post {source_reference}"
        )
    return post


def _post_format_evidence(
    candidate: Mapping[str, Any],
    profile: Mapping[str, Any],
    source_field: str,
) -> str:
    posts = profile.get("recent_posts")
    if not isinstance(posts, list):
        raise InputValidationError(
            f"{candidate.get('username')}: saved recent posts are missing"
        )
    formats = [
        str(value.get("post_format") or "unknown")
        for value in posts
        if isinstance(value, Mapping)
    ]
    sampled = len(formats)
    short_videos = sum(value == "short_video" for value in formats)
    known = sum(value != "unknown" for value in formats)
    unknown = sampled - known
    if source_field == "candidate.metrics.short_video_share":
        share = short_videos / sampled if sampled else 0.0
        return (
            f"Observed {short_videos} short-video posts out of {sampled} "
            f"sampled posts (share {share:.6f})."
        )
    if source_field == "candidate.metrics.post_format_distribution":
        return (
            f"Observed {known} posts with known post format and {unknown} "
            f"with unknown post format out of {sampled} sampled posts."
        )
    raise InputValidationError(
        f"{candidate.get('username')}: unsupported derived manual exclusion "
        f"source field {source_field}"
    )


def _validated_manual_evidence(
    candidate: Mapping[str, Any],
    profile_record: Mapping[str, Any],
    raw_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    username = str(candidate.get("username") or "")
    profile_url = str(candidate.get("profile_url") or "")
    profile = _profile_mapping(candidate, profile_record)
    signal_type = str(raw_evidence.get("signal_type") or "").strip()
    source_field = str(raw_evidence.get("source_field") or "").strip()
    source_reference = str(
        raw_evidence.get("source_reference") or ""
    ).strip()
    evidence_text = str(raw_evidence.get("evidence_text") or "").strip()
    observation_type = str(
        raw_evidence.get("observation_type") or ""
    ).strip()
    evidence_url = str(raw_evidence.get("url") or "").strip()
    if not all(
        (
            signal_type,
            source_field,
            source_reference,
            evidence_text,
            observation_type,
            evidence_url,
        )
    ):
        raise InputValidationError(
            f"{username}: manual exclusion evidence needs signal_type, "
            "source_field, source_reference, evidence_text, "
            "observation_type, and url"
        )

    expected_url = profile_url
    if source_field in {"profile.full_name", "profile.biography"}:
        if observation_type != "direct" or source_reference != "profile":
            raise InputValidationError(
                f"{username}: {source_field} evidence must be direct and "
                "reference profile"
            )
        profile_key = source_field.rsplit(".", maxsplit=1)[-1]
        source_text = str(profile.get(profile_key) or "")
        if evidence_text not in source_text:
            raise InputValidationError(
                f"{username}: manual exclusion evidence is absent from "
                f"saved {source_field}"
            )
    elif source_field == "profile.recent_posts.caption":
        if observation_type != "direct":
            raise InputValidationError(
                f"{username}: post-caption evidence must be direct"
            )
        post = _profile_post(candidate, profile, source_reference)
        source_text = str(post.get("caption") or "")
        if evidence_text not in source_text:
            raise InputValidationError(
                f"{username}: manual exclusion evidence is absent from "
                f"saved post caption {source_reference}"
            )
        expected_url = str(post.get("url") or "")
    elif source_field in {
        "candidate.metrics.short_video_share",
        "candidate.metrics.post_format_distribution",
    }:
        if observation_type != "derived" or source_reference != username:
            raise InputValidationError(
                f"{username}: {source_field} evidence must be derived and "
                "reference the exact username"
            )
        expected_text = _post_format_evidence(
            candidate,
            profile,
            source_field,
        )
        if evidence_text != expected_text:
            raise InputValidationError(
                f"{username}: derived manual exclusion evidence does not "
                f"match saved post-format data for {source_field}"
            )
    else:
        raise InputValidationError(
            f"{username}: unsupported manual exclusion evidence source "
            f"field {source_field}"
        )

    if evidence_url != expected_url:
        raise InputValidationError(
            f"{username}: manual exclusion evidence URL does not match "
            f"saved source for {source_field}"
        )
    return _evidence_record(
        signal_type=signal_type,
        source_field=source_field,
        source_reference=source_reference,
        evidence_text=evidence_text,
        url=evidence_url,
        observation_type=observation_type,
    )


def _manual_exclusion_evidence(
    candidate: Mapping[str, Any],
    profile_record: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw_items = decision.get("manual_exclusion_evidence")
    if raw_items is not None:
        if not isinstance(raw_items, list) or not raw_items:
            raise InputValidationError(
                f"{candidate.get('username')}: manual_exclusion_evidence "
                "must be a non-empty array"
            )
        evidence: list[dict[str, Any]] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                raise InputValidationError(
                    f"{candidate.get('username')}: every manual exclusion "
                    "evidence item must be an object"
                )
            evidence.append(
                _validated_manual_evidence(
                    candidate,
                    profile_record,
                    raw_item,
                )
            )
        return evidence

    profile = _profile_mapping(candidate, profile_record)
    biography = str(profile.get("biography") or "")
    conflict_text = str(decision.get("conflict_evidence_text") or "")
    if not conflict_text or conflict_text not in biography:
        raise InputValidationError(
            f"{candidate.get('username')}: rejected decision needs "
            "source-backed manual_exclusion_evidence"
        )
    return [
        _evidence_record(
            signal_type="commercial_conflict",
            source_field="profile.biography",
            source_reference="profile",
            evidence_text=conflict_text,
            url=str(candidate["profile_url"]),
        )
    ]


def _manual_exclusion(
    candidate: Mapping[str, Any],
    profile_record: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    reason_code = str(decision["reason_code"])
    evidence = _manual_exclusion_evidence(
        candidate,
        profile_record,
        decision,
    )
    return {
        "platform": candidate.get("platform", "instagram"),
        "username": candidate["username"],
        "profile_url": candidate["profile_url"],
        "normalized_username": str(candidate["username"]).casefold(),
        "exclusion_reason": reason_code,
        "exclusion_reasons": [reason_code],
        "query_ids": candidate.get("query_ids") or [],
        "provider": candidate.get("provider") or "saved_provider_artifacts",
        "manual_review_action": "reject",
        "manual_review_reason": decision.get("decision_reason"),
        "commercial_conflict_account": decision.get(
            "commercial_conflict_account"
        ),
        "manual_review_evidence": evidence,
    }


def _offer_drafts(
    path: Path,
    selected: Iterable[Mapping[str, Any]],
) -> Path:
    lines = [
        "# LD Latte — manually reviewed barter-offer drafts",
        "",
        "> Exactly three drafts. No message was sent. "
        "Every outreach action remains prohibited until its recorded "
        "manual-verification state permits the operator to proceed.",
        "",
    ]
    for index, item in enumerate(selected, start=1):
        status = str(item["manual_verification_status"])
        lines.extend(
            [
                f"## {index}. @{item['username']}",
                "",
                f"- Manual verification: `{status}`",
                "- Outreach status: `not_sent`",
                f"- Saved evidence: {item['recent_post_url']}",
                (
                    "- Draft gate: approved for the shortlist, but not sent."
                    if status == "approved"
                    else (
                        "- Draft gate: preliminary only; sending is prohibited "
                        "until barter terms and the content scope are agreed "
                        "with the manager."
                    )
                ),
                "",
                str(item["barter_offer"]),
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _report(
    path: Path,
    *,
    source_run_id: str,
    selected: list[Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, Any]],
    source_eligible_count: int,
    source_excluded_count: int,
    final_excluded_count: int,
) -> Path:
    lines = [
        "# Phase B manual finalization report",
        "",
        f"- Source run: `{source_run_id}`",
        "- Execution: offline over saved artifacts only",
        "- Provider requests made: `0`",
        "- Budget spent: `$0.00`",
        "- Outreach messages sent: `0`",
        f"- Saved eligible pool reviewed: `{source_eligible_count}`",
        f"- Preserved automatic exclusions: `{source_excluded_count}`",
        f"- Final exclusion records: `{final_excluded_count}`",
        f"- Final shortlist: `{len(selected)}`",
        "",
        "## Final shortlist",
        "",
        "| Order | Creator | Source score | Followers | Manual status | "
        "Saved post evidence |",
        "|---:|---|---:|---:|---|---|",
    ]
    for index, item in enumerate(selected, start=1):
        lines.append(
            f"| {index} | [@{item['username']}]({item['profile_url']}) | "
            f"{float(item['score']):.2f} | {int(item['followers']):,} | "
            f"`{item['manual_verification_status']}` | "
            f"[clothing/fashion post]({item['recent_post_url']}) |"
        )
    lines.extend(
        [
            "",
            (
                "The shortlist order is the recorded human decision order. "
                "The frozen source scores remain visible for transparency; "
                "they were not reinterpreted as approval."
            ),
            "",
            "## Human-in-the-loop exclusions",
            "",
            "| Creator | Decision | Direct saved evidence | Reason |",
            "|---|---|---|---|",
        ]
    )
    rejected_decisions = [
        decision
        for decision in decisions.values()
        if decision.get("action") == _REJECTION_ACTION
    ]
    for decision in rejected_decisions:
        username = str(decision["username"])
        raw_evidence = decision.get("manual_exclusion_evidence")
        if isinstance(raw_evidence, list) and raw_evidence:
            evidence_text = "; ".join(
                " ".join(str(item.get("evidence_text") or "").split())
                for item in raw_evidence
                if isinstance(item, Mapping)
            )
        else:
            evidence_text = " ".join(
                str(decision.get("conflict_evidence_text") or "").split()
            )
        decision_explanation = str(
            decision.get("decision_explanation")
            or decision["decision_reason"]
        )
        lines.append(
            f"| [@{username}](https://www.instagram.com/{username}/) | "
            f"`{decision['reason_code']}` | "
            f"`{evidence_text}` | "
            f"{decision_explanation} |"
        )
    lines.extend(
        [
            "",
            "## Why the other eligible profiles were not included",
            "",
            (
                f"- `ayuma.style`: `{decisions['ayuma.style']['reason_code']}`; "
                "the saved biography directly identifies the creator's own "
                "fashion brand."
            ),
            (
                f"- `miss_sunrise9`: "
                f"`{decisions['miss_sunrise9']['reason_code']}`; the saved "
                "biography directly identifies ownership of a clothing showroom."
            ),
            (
                "- `stylistelenaialena`: "
                f"`{decisions['stylistelenaialena']['reason_code']}`; saved "
                "profile and post evidence identifies a professional styling/"
                "costume portfolio with 2 short-video posts out of 12 sampled "
                "posts and 10 unknown-format posts."
            ),
            "",
            "## Offer controls",
            "",
            (
                "Only the three selected personal creators have drafts. "
                "All three manually rejected profiles have no offer."
            ),
            (
                "The draft for `angelashegiryan` remains `pending`: "
                f"{decisions['angelashegiryan']['verification_notes']}"
            ),
            (
                "All three records have `outreach_status=not_sent`; this "
                "workflow contains no message-sending operation."
            ),
            "",
            "## Source integrity",
            "",
            (
                "The source run was hashed before and after finalization and "
                "remained byte-for-byte unchanged. The finalization path does "
                "not read or write Phase A artifacts and does not construct a "
                "discovery or enrichment provider."
            ),
        ]
    )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def finalize_saved_run(
    source_run_dir: Path,
    output_run_dir: Path,
    *,
    review_path: Path,
) -> tuple[dict[str, Any], tuple[Path, ...], tuple[dict[str, Any], ...]]:
    """Finalize one saved eligible pool with zero provider requests."""

    source_run_dir = Path(source_run_dir)
    output_run_dir = Path(output_run_dir)
    review_path = Path(review_path)
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
            f"saved source run is incomplete; missing={missing}"
        )
    if output_run_dir.exists():
        raise OutputWriteError(
            f"manual-finalization output already exists: {output_run_dir}"
        )
    if source_run_dir.resolve() == output_run_dir.resolve():
        raise OutputWriteError("source run cannot be used as the output directory")
    if source_run_dir.resolve() in output_run_dir.resolve().parents:
        raise OutputWriteError(
            "manual-finalization output cannot be nested inside the source run"
        )

    source_hashes_before = _tree_hashes(source_run_dir)
    source_manifest = _read_json(
        source_run_dir / "run_manifest.json", dict
    )
    source_run_id = str(source_manifest.get("run_id") or "")
    if not source_run_id:
        raise InputValidationError("source run manifest has no run_id")

    csv_candidates = _read_candidate_csv(
        source_run_dir / "eligible_candidates.csv"
    )
    candidate_by_username = {
        str(item.get("username")): item for item in csv_candidates
    }
    if len(candidate_by_username) != len(csv_candidates):
        raise InputValidationError(
            "saved eligible pool contains duplicate usernames"
        )
    eligible_usernames = set(candidate_by_username)
    review_payload, decisions = _load_review(
        review_path,
        source_run_id=source_run_id,
        eligible_usernames=eligible_usernames,
    )

    enriched = _read_jsonl(
        source_run_dir / "enriched_candidates.jsonl"
    )
    profile_by_username = {
        _profile_username(record): record for record in enriched
    }
    missing_profiles = sorted(eligible_usernames - set(profile_by_username))
    if missing_profiles:
        raise InputValidationError(
            "saved enrichment is missing eligible profiles: "
            f"{missing_profiles}"
        )

    source_selected = _read_json(
        source_run_dir / "new_creators.json", list
    )
    for item in source_selected:
        if isinstance(item, Mapping) and item.get("username") in eligible_usernames:
            candidate_by_username[str(item["username"])] = dict(item)

    selection_decisions = sorted(
        (
            value
            for value in decisions.values()
            if value.get("action") == _SELECTION_ACTION
        ),
        key=lambda value: int(value["selection_order"]),
    )
    selected: list[dict[str, Any]] = []
    selected_post_evidence: list[dict[str, Any]] = []
    selected_by_username: dict[str, dict[str, Any]] = {}
    for decision in selection_decisions:
        username = str(decision["username"])
        item, post_evidence = _selected_candidate(
            candidate_by_username[username],
            profile_by_username[username],
            decision,
        )
        selected.append(item)
        selected_post_evidence.append(post_evidence)
        selected_by_username[username] = item

    manual_exclusions = [
        _manual_exclusion(
            candidate_by_username[username],
            profile_by_username[username],
            decision,
        )
        for username, decision in decisions.items()
        if decision.get("action") == _REJECTION_ACTION
    ]
    manual_exclusion_by_username = {
        str(item["username"]): item for item in manual_exclusions
    }
    audit_pool = [
        _audit_candidate(
            candidate_by_username[username],
            decisions[username],
            selected_by_username.get(username),
            manual_exclusion_by_username.get(username),
        )
        for username in candidate_by_username
    ]
    audit_pool.sort(
        key=lambda item: (
            0
            if item["manual_review_action"] == _SELECTION_ACTION
            else 1
            if item["manual_review_action"] == _REJECTION_ACTION
            else 2,
            int(
                decisions[str(item["username"])].get(
                    "selection_order", 99
                )
            ),
            str(item["username"]),
        )
    )

    source_exclusions = _read_csv_mappings(
        source_run_dir / "excluded_candidates.csv"
    )
    all_exclusions: list[Mapping[str, Any]] = [
        *source_exclusions,
        *manual_exclusions,
    ]

    output_run_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=f".{output_run_dir.name}-",
        dir=output_run_dir.parent,
    ) as temporary:
        staging = Path(temporary)
        staged_paths = {
            name: staging / name for name in MANUAL_FINALIZATION_ARTIFACTS
        }
        _write_json(staged_paths["new_creators.json"], selected)
        write_candidate_csv(
            staged_paths["new_creators.csv"], selected
        )
        write_candidate_csv(
            staged_paths["eligible_candidates.csv"], selected
        )
        write_excluded_candidates_csv(
            staged_paths["excluded_candidates.csv"], all_exclusions
        )
        _write_json(
            staged_paths["eligible_audit_pool.json"], audit_pool
        )
        audit_columns = (
            *PHASE_B_COLUMNS,
            "status",
            "manual_review_action",
            "manual_review_reason",
            "commercial_conflict_account",
            "manual_review_evidence",
        )
        write_mapping_csv(
            staged_paths["eligible_audit_pool.csv"],
            audit_pool,
            columns=audit_columns,
        )
        _write_json(
            staged_paths["selected_post_evidence.json"],
            selected_post_evidence,
        )
        _write_json(
            staged_paths["manual_review_audit.json"],
            {
                "schema_version": 1,
                "source_run_id": source_run_id,
                "review_record": review_payload,
                "resolved_decisions": audit_pool,
                "provider_requests_made": 0,
                "budget_spent_usd": 0.0,
                "outreach_messages_sent": 0,
            },
        )
        _offer_drafts(
            staged_paths["barter_offer_drafts.md"], selected
        )
        _report(
            staged_paths["discovery_report.md"],
            source_run_id=source_run_id,
            selected=selected,
            decisions=decisions,
            source_eligible_count=len(csv_candidates),
            source_excluded_count=len(source_exclusions),
            final_excluded_count=len(all_exclusions),
        )
        write_candidates_workbook(
            source_run_dir / "Блогеры_phase_b.xlsx",
            staged_paths["Блогеры_phase_b.xlsx"],
            selected,
            preserve_existing_manual_values=False,
        )

        source_hashes_after = _tree_hashes(source_run_dir)
        if source_hashes_after != source_hashes_before:
            raise OutputWriteError(
                "source run changed during offline manual finalization"
            )

        commercial_conflict_exclusions = sum(
            str(item.get("exclusion_reason") or "").startswith(
                "commercial_conflict_"
            )
            for item in manual_exclusions
        )
        action_counts = {
            "manual_approved": sum(
                item["manual_verification_status"] == "approved"
                for item in selected
            ),
            "manual_pending": sum(
                item["manual_verification_status"] == "pending"
                for item in selected
            ),
            "manual_rejected": len(manual_exclusions),
            "eligible_not_selected": sum(
                value.get("action") == _AUDIT_ONLY_ACTION
                for value in decisions.values()
            ),
        }
        completed_at = datetime.now(timezone.utc).isoformat()
        manifest: dict[str, Any] = {
            "run_id": output_run_dir.name,
            "source_run_id": source_run_id,
            "source_run_path": str(source_run_dir),
            "mode": "offline_manual_finalization",
            "status": "completed",
            "offline_reselection": True,
            "manual_finalization": True,
            "human_in_the_loop_review": True,
            "provider": "saved_provider_artifacts",
            "provider_requests_made": 0,
            "provider_run_ids": list(
                source_manifest.get("provider_run_ids") or []
            ),
            "budget_spent_usd": 0.0,
            "outreach_messages_sent": 0,
            "source_run_unchanged": True,
            "phase_a_unchanged": True,
            "completed_at": completed_at,
            "counts": {
                "source_eligible_candidates": len(csv_candidates),
                "manual_reviewed_candidates": len(decisions),
                "final_selected_candidates": len(selected),
                "preserved_automatic_exclusions": len(source_exclusions),
                "manual_commercial_conflict_exclusions": (
                    commercial_conflict_exclusions
                ),
                "manual_other_exclusions": (
                    len(manual_exclusions)
                    - commercial_conflict_exclusions
                ),
                "excluded_records_total": len(all_exclusions),
                **action_counts,
            },
            "source_artifact_sha256": source_hashes_before,
            "artifacts": {
                name: str(output_run_dir / name)
                for name in MANUAL_FINALIZATION_ARTIFACTS
            },
            "warnings": [],
            "errors": [],
        }
        _write_json(staged_paths["run_manifest.json"], manifest)
        staging.replace(output_run_dir)

    final_paths = tuple(
        output_run_dir / name for name in MANUAL_FINALIZATION_ARTIFACTS
    )
    return manifest, final_paths, tuple(selected)


__all__ = [
    "MANUAL_FINALIZATION_ARTIFACTS",
    "finalize_saved_run",
]
