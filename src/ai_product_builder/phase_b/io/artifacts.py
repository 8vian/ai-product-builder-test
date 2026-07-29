"""Generation of the complete auditable Phase B artifact set."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import as_mapping, candidate_mapping, json_safe, normalized_identity
from .local_csv import (
    write_candidate_csv,
    write_excluded_candidates_csv,
    write_mapping_csv,
)
from .local_xlsx import write_candidates_workbook
from ..near_miss import NEAR_MISS_COLUMNS


ARTIFACT_FILENAMES: tuple[str, ...] = (
    "run_manifest.json",
    "generated_queries.json",
    "discovery_pool.jsonl",
    "deduplication_report.json",
    "excluded_candidates.csv",
    "enriched_candidates.jsonl",
    "eligible_candidates.csv",
    "new_creators.json",
    "new_creators.csv",
    "barter_offer_drafts.md",
    "discovery_report.md",
    "Блогеры_phase_b.xlsx",
)


def _materialize(values: Iterable[Any]) -> list[Any]:
    return list(values)


def _write_json(path: Path, value: Any) -> Path:
    path.write_text(
        json.dumps(json_safe(value), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_jsonl(path: Path, values: Iterable[Any]) -> Path:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(
                json.dumps(
                    json_safe(as_mapping(value)),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    return path


def _manifest_value(manifest: Any, field: str, default: Any = None) -> Any:
    value = as_mapping(manifest)
    return value.get(field, default)


def run_directory(base_output_dir: Path, manifest: Any) -> Path:
    """Return ``base_output_dir/run_id`` with a safe, non-empty run identifier."""

    run_id = str(_manifest_value(manifest, "run_id", "")).strip()
    if not run_id:
        raise ValueError("Phase B manifest must contain a non-empty run_id")
    if Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("Phase B run_id must be a single safe path component")
    return Path(base_output_dir) / run_id


def write_offer_drafts(path: Path, candidates: Iterable[Any]) -> Path:
    lines = [
        "# Phase B barter-offer drafts",
        "",
        "> Drafts only. Every offer requires manual approval; no message was sent.",
        "",
    ]
    for index, candidate in enumerate(candidates, start=1):
        item = candidate_mapping(candidate)
        username = str(item.get("username") or "(missing username)")
        profile_url = str(item.get("profile_url") or "")
        recent_post_url = str(item.get("recent_post_url") or "")
        status = str(item.get("manual_verification_status") or "pending")
        offer = str(item.get("barter_offer") or "")
        lines.extend(
            [
                f"## {index}. @{username}",
                "",
                f"- Profile: {profile_url or 'unavailable'}",
                f"- Referenced post: {recent_post_url or 'unavailable'}",
                f"- Manual verification: `{status}`",
                "",
                offer,
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_discovery_report(
    path: Path,
    *,
    manifest: Any,
    queries: list[Any],
    discovery_pool: list[Any],
    excluded_candidates: list[Any],
    enriched_candidates: list[Any],
    eligible_candidates: list[Any],
    selected_candidates: list[Any],
    deduplication_report: Any,
    near_miss_candidates: list[Any] | None = None,
) -> Path:
    manifest_item = as_mapping(manifest)
    warning = (
        "Fewer than five eligible candidates were available; the result was not padded."
        if 3 <= len(selected_candidates) < 5
        else ""
    )
    selected_lines = []
    for rank, candidate in enumerate(selected_candidates, start=1):
        item = candidate_mapping(candidate)
        score = item.get("score")
        score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else "n/a"
        username = str(item.get("username") or "")
        profile_url = str(item.get("profile_url") or "")
        recent_post_url = str(item.get("recent_post_url") or "")
        creator_cell = (
            f"[`{username}`]({profile_url})" if profile_url else f"`{username}`"
        )
        evidence_cell = (
            f"[recent post]({recent_post_url})"
            if recent_post_url
            else "unavailable"
        )
        selected_lines.append(
            f"| {rank} | {creator_cell} | {score_text} | "
            f"{item.get('discovery_confidence', '')} | "
            f"{item.get('data_completeness', '')} | "
            f"{item.get('account_type', '')} | "
            f"{item.get('manual_verification_status', '')} | "
            f"{evidence_cell} |"
        )
    if not selected_lines:
        selected_lines.append("| — | — | — | — | — | — | — | — |")
    selected_explanations = []
    for rank, candidate in enumerate(selected_candidates, start=1):
        item = candidate_mapping(candidate)
        selected_explanations.append(
            f"{rank}. **@{item.get('username', '')}** — "
            f"{item.get('selection_explanation', '')} "
            "Barter feasibility: "
            f"{item.get('barter_feasibility_explanation', 'not reported')}"
        )

    dedupe = json_safe(deduplication_report)
    dedupe_counts = (
        dedupe.get("exclusion_reason_counts", {})
        if isinstance(dedupe, Mapping)
        else {}
    )
    dedupe_lines = (
        "\n".join(
            f"- `{reason}`: **{count}**"
            for reason, count in sorted(dedupe_counts.items())
        )
        if isinstance(dedupe_counts, Mapping) and dedupe_counts
        else "- No discovery-stage exclusions."
    )
    first_candidate = (
        candidate_mapping(selected_candidates[0])
        if selected_candidates
        else None
    )
    component_lines: list[str] = []
    if first_candidate is not None:
        for component in first_candidate.get("score_components") or []:
            component_item = as_mapping(component)
            component_lines.append(
                f"- `{component_item.get('name', '')}`: "
                f"{component_item.get('score', '')}/"
                f"{component_item.get('max_score', '')} — "
                f"{component_item.get('explanation', '')}"
            )
    score_example = (
        "\n".join(component_lines)
        if component_lines
        else "- No selected score example is available."
    )
    provider_run_ids = manifest_item.get("provider_run_ids") or []
    provider_runs_text = (
        ", ".join(f"`{value}`" for value in provider_run_ids)
        if provider_run_ids
        else "none reported"
    )
    review_summary = manifest_item.get("review_summary") or {}
    provider_charges = (
        review_summary.get("provider_charges", [])
        if isinstance(review_summary, Mapping)
        else []
    )
    provider_charge_lines = (
        "\n".join(
            "- `{stage}` Actor `{actor}` run `{run}`: "
            "**${charge:.6f}** (server cap **${limit:.2f}**)"
            .format(
                stage=item.get("stage", "unknown"),
                actor=item.get("actor_id", "unknown"),
                run=item.get("run_id", "unknown"),
                charge=float(item.get("charge_usd", 0.0)),
                limit=float(item.get("charge_limit_usd", 0.0)),
            )
            for item in provider_charges
            if isinstance(item, Mapping)
        )
        if isinstance(provider_charges, list) and provider_charges
        else "- No billable Actor runs were recorded."
    )
    account_counts = (
        review_summary.get("account_type_counts", {})
        if isinstance(review_summary, Mapping)
        else {}
    )
    account_count_lines = (
        "\n".join(
            f"- `{name}`: **{count}**"
            for name, count in sorted(account_counts.items())
        )
        if isinstance(account_counts, Mapping) and account_counts
        else "- No account-type summary is available."
    )
    manual_exclusions = (
        review_summary.get("manual_exclusions", {})
        if isinstance(review_summary, Mapping)
        else {}
    )
    manual_exclusion_lines = (
        "\n".join(
            f"- **@{username}** — {reason}"
            for username, reason in sorted(manual_exclusions.items())
        )
        if isinstance(manual_exclusions, Mapping) and manual_exclusions
        else "- No manual exclusions were supplied."
    )
    near_miss_items = near_miss_candidates or []
    near_miss_lines = []
    for rank, candidate in enumerate(near_miss_items[:5], start=1):
        item = as_mapping(candidate)
        reasons = ", ".join(
            str(value) for value in item.get("reviewable_reasons", ())
        )
        near_miss_lines.append(
            f"{rank}. **@{item.get('username', '')}** — score "
            f"{item.get('score', 'n/a')}; review: {reasons or 'not reported'}."
        )
    near_miss_section = f"""
## Near-miss manual review

No near-miss candidate is automatically selected or approved.

{chr(10).join(near_miss_lines) if near_miss_lines else "No conservative near-miss candidates were found."}
"""
    barter_rule = (
        review_summary.get("barter_audience_rule", {})
        if isinstance(review_summary, Mapping)
        else {}
    )
    offline_section = ""
    if manifest_item.get("offline_reselection"):
        offline_section = f"""
## Offline reselection provenance

- Source run ID: `{manifest_item.get("source_run_id", "unknown")}`
- Source run path: `{manifest_item.get("source_run_path", "unknown")}`
- Offline reselection: **true**
- Provider discovery/enrichment requests made: **{manifest_item.get("provider_requests_made", 0)}**
- Provider budget spent during reselection: **${float(manifest_item.get("budget_spent_usd", 0.0)):.2f}**
- Previously eligible candidates reclassified by account type or theme: **{review_summary.get("previously_eligible_rejected_count", "n/a") if isinstance(review_summary, Mapping) else "n/a"}**
- Network statement: {review_summary.get("network_statement", "not reported") if isinstance(review_summary, Mapping) else "not reported"}

### Account-type audit

{account_count_lines}

### Manual exclusions

{manual_exclusion_lines}

### Barter-feasibility audience rule

The review threshold is derived only from the frozen Phase A audience
distribution: `{barter_rule.get("formula", "not reported")}` = **{barter_rule.get("threshold", "n/a")}**
followers (Q1 **{barter_rule.get("phase_a_q1", "n/a")}**, Q3
**{barter_rule.get("phase_a_q3", "n/a")}**). Exceeding it triggers manual
review; it does not prove that barter is impossible and does not approve or
send an offer.
"""
    report = f"""# Phase B discovery report

Run ID: `{manifest_item.get("run_id", "unknown")}`
Mode: `{manifest_item.get("mode", "unknown")}`
Provider: `{manifest_item.get("provider", "unknown")}`
Provider run IDs: {provider_runs_text}
Generated: {manifest_item.get("generated_at") or manifest_item.get("started_at") or datetime.now(timezone.utc).isoformat()}

## Pipeline counts

- Generated queries: **{len(queries)}**
- Raw discovery hits: **{len(discovery_pool)}**
- Excluded candidates: **{len(excluded_candidates)}**
- Enriched candidates: **{len(enriched_candidates)}**
- Eligible candidates: **{len(eligible_candidates)}**
- Selected candidates: **{len(selected_candidates)}**
- Unique candidates after discovery-stage filtering: **{dedupe.get("unique_candidate_count", "n/a") if isinstance(dedupe, Mapping) else "n/a"}**
- Duplicate discoveries merged: **{dedupe.get("duplicate_hit_count", "n/a") if isinstance(dedupe, Mapping) else "n/a"}**
- Billable Actor runs started: **{manifest_item.get("provider_requests_made", 0)}**
- Actual provider spend: **${float(manifest_item.get("budget_spent_usd", 0.0)):.6f}**

### Provider charge audit

{provider_charge_lines}

{f"**Warning:** {warning}" if warning else ""}

{offline_section}

## Discovery-stage exclusion audit

{dedupe_lines}

## Selected creators

| Rank | Creator | Score | Discovery confidence | Data completeness | Account type | Manual status | Evidence |
|---:|---|---:|---:|---:|---|---|---|
{chr(10).join(selected_lines)}

### Selection explanations

{chr(10).join(selected_explanations) if selected_explanations else "No creators were selected."}

## First selected score example

The creator score uses the frozen Phase A reference cohort. Discovery confidence is
reported separately and is used only as a deterministic tie-break after score.

{score_example}

{near_miss_section}

## Safety boundary

Every barter offer is a draft with a manual-verification state. This pipeline contains
no message-sending functionality. Candidate evidence and links must be manually
reviewed before any external action. Demo-mode links are deterministic fixtures; live
mode is required for current real-profile discovery.
"""
    path.write_text(report, encoding="utf-8")
    return path


def generate_phase_b_artifacts(
    output_dir: Path,
    *,
    manifest: Any,
    queries: Iterable[Any],
    discovery_pool: Iterable[Any],
    deduplication_report: Any,
    excluded_candidates: Iterable[Any],
    enriched_candidates: Iterable[Any],
    eligible_candidates: Iterable[Any],
    selected_candidates: Iterable[Any],
    source_workbook: Path,
    workbook_sheet: str = "Новые блоггеры",
) -> list[Path]:
    """Generate all twelve required artifacts in an existing run directory."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    query_items = _materialize(queries)
    discovery_items = _materialize(discovery_pool)
    excluded_items = _materialize(excluded_candidates)
    enriched_items = _materialize(enriched_candidates)
    eligible_items = _materialize(eligible_candidates)
    selected_items = _materialize(selected_candidates)

    paths = {name: output_dir / name for name in ARTIFACT_FILENAMES}
    _write_json(paths["run_manifest.json"], as_mapping(manifest))
    _write_json(
        paths["generated_queries.json"],
        [as_mapping(item) for item in query_items],
    )
    _write_jsonl(paths["discovery_pool.jsonl"], discovery_items)
    _write_json(paths["deduplication_report.json"], deduplication_report)
    write_excluded_candidates_csv(
        paths["excluded_candidates.csv"], excluded_items
    )
    _write_jsonl(paths["enriched_candidates.jsonl"], enriched_items)
    write_candidate_csv(paths["eligible_candidates.csv"], eligible_items)
    _write_json(
        paths["new_creators.json"],
        [candidate_mapping(item) for item in selected_items],
    )
    write_candidate_csv(paths["new_creators.csv"], selected_items)
    write_offer_drafts(paths["barter_offer_drafts.md"], selected_items)
    write_discovery_report(
        paths["discovery_report.md"],
        manifest=manifest,
        queries=query_items,
        discovery_pool=discovery_items,
        excluded_candidates=excluded_items,
        enriched_candidates=enriched_items,
        eligible_candidates=eligible_items,
        selected_candidates=selected_items,
        deduplication_report=deduplication_report,
    )
    write_candidates_workbook(
        source_workbook,
        paths["Блогеры_phase_b.xlsx"],
        selected_items,
        sheet_name=workbook_sheet,
    )
    return [paths[name] for name in ARTIFACT_FILENAMES]


def generate_phase_b_failure_artifacts(
    output_dir: Path,
    *,
    manifest: Any,
    queries: Iterable[Any],
    discovery_pool: Iterable[Any],
    deduplication_report: Any,
    excluded_candidates: Iterable[Any],
    enriched_candidates: Iterable[Any],
    eligible_candidates: Iterable[Any],
    near_miss_candidates: Iterable[Any],
) -> list[Path]:
    """Persist audit artifacts before an insufficient-pool error is returned."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    query_items = _materialize(queries)
    discovery_items = _materialize(discovery_pool)
    excluded_items = _materialize(excluded_candidates)
    enriched_items = _materialize(enriched_candidates)
    eligible_items = _materialize(eligible_candidates)
    near_miss_items = _materialize(near_miss_candidates)
    paths = {
        name: output_dir / name
        for name in (
            "run_manifest.json",
            "generated_queries.json",
            "discovery_pool.jsonl",
            "deduplication_report.json",
            "excluded_candidates.csv",
            "enriched_candidates.jsonl",
            "eligible_candidates.csv",
            "near_miss_candidates.json",
            "near_miss_candidates.csv",
            "discovery_report.md",
        )
    }
    _write_json(paths["run_manifest.json"], as_mapping(manifest))
    _write_json(
        paths["generated_queries.json"],
        [as_mapping(item) for item in query_items],
    )
    _write_jsonl(paths["discovery_pool.jsonl"], discovery_items)
    _write_json(paths["deduplication_report.json"], deduplication_report)
    write_excluded_candidates_csv(
        paths["excluded_candidates.csv"], excluded_items
    )
    _write_jsonl(paths["enriched_candidates.jsonl"], enriched_items)
    write_candidate_csv(paths["eligible_candidates.csv"], eligible_items)
    _write_json(paths["near_miss_candidates.json"], near_miss_items)
    write_mapping_csv(
        paths["near_miss_candidates.csv"],
        near_miss_items,
        columns=NEAR_MISS_COLUMNS,
    )
    write_discovery_report(
        paths["discovery_report.md"],
        manifest=manifest,
        queries=query_items,
        discovery_pool=discovery_items,
        excluded_candidates=excluded_items,
        enriched_candidates=enriched_items,
        eligible_candidates=eligible_items,
        selected_candidates=[],
        deduplication_report=deduplication_report,
        near_miss_candidates=near_miss_items,
    )
    return list(paths.values())


def selected_username_set(candidates: Iterable[Any]) -> frozenset[str]:
    """Small audit helper used by pipeline/tests to compare outputs."""

    return frozenset(
        key
        for candidate in candidates
        if (key := normalized_identity(candidate))
    )


__all__ = [
    "ARTIFACT_FILENAMES",
    "generate_phase_b_failure_artifacts",
    "generate_phase_b_artifacts",
    "run_directory",
    "selected_username_set",
    "write_discovery_report",
    "write_offer_drafts",
]
