from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .analysis import (
    AnalysisResult,
    MIN_SAMPLED_POSTS,
    MIN_USABLE_ENGAGEMENT_POSTS,
)
from .models import Profile, ProfileMetrics, ProfileStatus


def json_ready(value: Any) -> Any:
    if hasattr(value, "serializable"):
        return value.serializable()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=json_ready,
        )
        + "\n",
        encoding="utf-8",
    )


def fmt_number(value: float | int | None, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.{decimals}f}"


def fmt_share(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def flatten_signal(metrics: ProfileMetrics, name: str) -> str:
    signal = metrics.signals[name]
    return " | ".join(signal.evidence) if signal.evidence else ""


def csv_rows(result: AnalysisResult) -> tuple[list[str], list[dict[str, Any]]]:
    component_names = [
        "content_and_aesthetic_fit",
        "native_product_integration_potential",
        "short_video_consistency",
        "engagement",
        "barter_feasibility",
        "recent_activity",
    ]
    fields = [
        "source_index",
        "username",
        "input_url",
        "full_name",
        "status",
        "exclusion_reason",
        "followers",
        "posts_count",
        "sampled_posts",
        "usable_engagement_posts",
        "engagement_confidence",
        "median_likes",
        "median_comments",
        "engagement_rate_pct",
        "short_video_share",
        "latest_post_at",
        "posting_recency_days",
        "posts_per_week",
        "median_post_interval_days",
        "format_counts_json",
        "format_distribution_json",
        "commercial_pr_signal",
        "contact_signal",
        "fashion_signal",
        "beauty_signal",
        "lifestyle_signal",
        "ugc_signal",
        "marketplace_signal",
        "native_product_integration_signal",
        "signal_evidence_json",
        "total_score",
        "raw_engagement_score",
        "score_explanation",
    ]
    for name in component_names:
        fields.extend([f"{name}_score", f"{name}_maximum", f"{name}_explanation"])
    fields.append("validation_issues")

    rows: list[dict[str, Any]] = []
    for profile in result.profiles:
        metrics = result.metrics[profile.source_index]
        score = result.scores.get(profile.source_index)
        row: dict[str, Any] = {
            "source_index": profile.source_index,
            "username": profile.username or "",
            "input_url": profile.input_url or "",
            "full_name": profile.full_name,
            "status": profile.status.value if profile.status else "",
            "exclusion_reason": profile.exclusion_reason or "",
            "followers": metrics.followers,
            "posts_count": metrics.posts_count,
            "sampled_posts": metrics.sampled_posts,
            "usable_engagement_posts": metrics.usable_engagement_posts,
            "engagement_confidence": metrics.engagement_confidence,
            "median_likes": metrics.median_likes,
            "median_comments": metrics.median_comments,
            "engagement_rate_pct": metrics.engagement_rate_pct,
            "short_video_share": metrics.short_video_share,
            "latest_post_at": (
                metrics.latest_post_at.isoformat() if metrics.latest_post_at else ""
            ),
            "posting_recency_days": metrics.posting_recency_days,
            "posts_per_week": metrics.posts_per_week,
            "median_post_interval_days": metrics.median_post_interval_days,
            "format_counts_json": json.dumps(
                metrics.format_counts, ensure_ascii=False, sort_keys=True
            ),
            "format_distribution_json": json.dumps(
                metrics.format_distribution, ensure_ascii=False, sort_keys=True
            ),
            "signal_evidence_json": json.dumps(
                {
                    name: signal.serializable()
                    for name, signal in metrics.signals.items()
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "total_score": score.total if score else None,
            "raw_engagement_score": score.raw_engagement_score if score else None,
            "score_explanation": (
                score.explanation
                if score
                else f"Not scored: {profile.exclusion_reason or 'not quantitatively eligible'}"
            ),
            "validation_issues": " | ".join(profile.validation_issues),
        }
        for name in (
            "commercial_pr",
            "contact",
            "fashion",
            "beauty",
            "lifestyle",
            "ugc",
            "marketplace",
            "native_product_integration",
        ):
            row[f"{name}_signal"] = metrics.signals[name].detected
        components = (
            {component.name: component for component in score.components}
            if score
            else {}
        )
        for name in component_names:
            component = components.get(name)
            row[f"{name}_score"] = component.score if component else None
            row[f"{name}_maximum"] = component.maximum if component else None
            row[f"{name}_explanation"] = (
                component.explanation
                if component
                else "Not scored because the profile is outside the eligible creator cohort."
            )
        rows.append(row)
    return fields, rows


def write_source_csv(path: Path, result: AnalysisResult) -> None:
    fields, rows = csv_rows(result)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def manual_qa_payload(result: AnalysisResult) -> dict[str, Any]:
    recovery = result.manual_audit.get("manual_human_in_the_loop_recovery", {})
    provenance = recovery.get("raw_provenance", {})
    performed_by = recovery.get("performed_by")
    corrections = recovery.get("confirmed_corrections", [])
    recovery_count = len(corrections)
    rejected_false_leads = recovery.get("rejected_false_leads", [])
    if not rejected_false_leads and recovery.get("excluded_uncertain_match"):
        rejected_false_leads = [recovery["excluded_uncertain_match"]]
    return {
        "performed_by": performed_by,
        "excel_hyperlink_issue": {
            "issue_detected_by": provenance.get("issue_detected_by"),
            "issue": provenance.get("issue"),
            "finding": provenance.get("finding"),
            "subsequent_verification": provenance.get("subsequent_verification"),
            "raw_verified_mismatch_count": provenance.get("verified_mismatch_count"),
            "observed_display_target_mismatch_count": result.workbook_reconciliation[
                "display_target_mismatch_count"
            ],
            "observed_mismatches": result.workbook_reconciliation[
                "display_target_mismatches"
            ],
        },
        "manually_verified_corrections": corrections,
        "rejected_false_leads": rejected_false_leads,
        "remaining_unresolved": result.manual_audit.get("remaining_unresolved", []),
        "reliability_improvement": (
            f"{performed_by}'s manual review recovered {recovery_count} profiles using "
            "explicit verification "
            "evidence, including __aparina → nikaanow, while preserving the earlier "
            "rejection of the unsupported aparina_ false lead. It also exposed a systematic "
            "spreadsheet display-text parsing failure. This improves precision, preserves "
            "the QA history, and makes reconciliation auditable instead of silently "
            "substituting guessed accounts."
        ),
    }


def write_data_quality_report(path: Path, result: AnalysisResult) -> None:
    exclusions = [
        {
            "source_index": profile.source_index,
            "username": profile.username,
            "status": profile.status.value if profile.status else None,
            "reason": profile.exclusion_reason,
        }
        for profile in result.profiles
        if profile.status != ProfileStatus.CREATOR
    ]
    write_json(
        path,
        {
            "generated_at": result.generated_at.isoformat(),
            "analysis_as_of": result.analysis_as_of.isoformat(),
            "source_files": {
                "instagram_profiles_json": "data/raw/instagram_profiles.json",
                "manual_verification_audit_json": (
                    "data/raw/manual_verification_audit.json"
                ),
                "reference_workbook": "data/raw/Блогеры.xlsx",
            },
            "dataset_summary": result.dataset_summary,
            "validation": {
                "typed_normalization": True,
                "minimum_sampled_posts_for_quantitative_analysis": MIN_SAMPLED_POSTS,
                "minimum_posts_with_usable_likes_and_comments": (
                    MIN_USABLE_ENGAGEMENT_POSTS
                ),
                "validation_issue_count": len(result.validation_issues),
                "issues": result.validation_issues,
                "missing_values_policy": (
                    "Missing values remain null/blank and are never converted to zero. "
                    "Raw engagement uses only posts with both likes and comments; the "
                    "score is reduced by usable_engagement_posts / sampled_posts. No "
                    "follower, engagement, post, date, status, or identity is invented."
                ),
            },
            "workbook_reconciliation": result.workbook_reconciliation,
            "human_in_the_loop_qa": manual_qa_payload(result),
            "excluded_from_quantitative_analysis": exclusions,
            "brand_reference_policy": {
                "usernames": ["nike", "apple"],
                "policy": (
                    "Excluded from all creator cohort statistics and percentile ranks; "
                    "used only for documented visual/content-format alignment signals."
                ),
            },
        },
    )


def ranking(result: AnalysisResult) -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "rank": 0,
                "source_index": source_index,
                **score.serializable(),
            }
            for source_index, score in result.scores.items()
        ),
        key=lambda item: (-item["total"], item["username"].casefold()),
    )


def write_ideal_profile(path: Path, result: AnalysisResult) -> None:
    ranked = ranking(result)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    write_json(
        path,
        {
            "generated_at": result.generated_at.isoformat(),
            "analysis_as_of": result.analysis_as_of.isoformat(),
            "dataset_summary": result.dataset_summary,
            "ideal_creator_profile": result.ideal_creator_profile,
            "scoring_results": ranked,
            "brand_reference_signals": result.brand_reference_signals,
        },
    )


def md_escape(value: str | None) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def exclusion_table(result: AnalysisResult) -> str:
    lines = ["| Profile | Category | Why excluded |", "|---|---|---|"]
    for profile in result.profiles:
        if profile.status == ProfileStatus.CREATOR:
            continue
        lines.append(
            f"| `{md_escape(profile.username or '(missing username)')}` | "
            f"{profile.status.value if profile.status else 'unclassified'} | "
            f"{md_escape(profile.exclusion_reason)} |"
        )
    return "\n".join(lines)


def ranking_table(result: AnalysisResult) -> str:
    ranked = ranking(result)
    lines = [
        "| Rank | Creator | Total | Content | Integration | Short video | Engagement | Barter | Activity |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank_index, item in enumerate(ranked, start=1):
        components = {
            component["name"]: component["score"] for component in item["components"]
        }
        lines.append(
            f"| {rank_index} | `{md_escape(item['username'])}` | {item['total']:.2f} | "
            f"{components['content_and_aesthetic_fit']:.2f}/30 | "
            f"{components['native_product_integration_potential']:.2f}/20 | "
            f"{components['short_video_consistency']:.2f}/15 | "
            f"{components['engagement']:.2f}/15 | "
            f"{components['barter_feasibility']:.2f}/10 | "
            f"{components['recent_activity']:.2f}/10 |"
        )
    return "\n".join(lines)


def metric_line(label: str, block: dict[str, float | None], suffix: str = "") -> str:
    return (
        f"- {label}: Q1 {fmt_number(block.get('q1'))}{suffix}, "
        f"median {fmt_number(block.get('median'))}{suffix}, "
        f"Q3 {fmt_number(block.get('q3'))}{suffix}"
    )


def write_analysis_report(path: Path, result: AnalysisResult) -> None:
    summary = result.dataset_summary
    counts = summary["counts_by_status"]
    ideal = result.ideal_creator_profile
    qa = manual_qa_payload(result)
    top = ranking(result)[:5]
    top_lines = "\n".join(
        f"- `{item['username']}` — {item['total']:.2f}/100: {item['explanation']}"
        for item in top
    )
    correction_lines = "\n".join(
        f"- `{item['source_username']}` → `{item['verified_username']}`: "
        f"{item['verification']}"
        for item in qa["manually_verified_corrections"]
    )
    report = f"""# AI Product Builder — Phase A Analysis

Generated: {result.generated_at.isoformat()}  
Analysis reference time: {result.analysis_as_of.isoformat()}

## Executive summary

The input contains **{summary['total_records']}** records. The CLI classified them from
the source data rather than using fixed counts:

- Quantitatively analyzed creators: **{counts.get('creator', 0)}**
- Brand references (Nike and Apple): **{counts.get('brand_reference', 0)}**
- Private profiles: **{counts.get('private', 0)}**
- Unresolved/not-found profiles: **{counts.get('unresolved_not_found', 0)}**
- Other insufficient-data profiles: **{counts.get('insufficient_data', 0)}**

Nike and Apple are excluded from creator statistics, engagement percentiles, and the
ideal-profile distributions. Their sampled formats are used only for the explicitly
documented brand-format alignment signal.

## Ideal creator profile

The ideal is a robust synthesis, not a fabricated person. It uses cohort medians and
interquartile ranges so viral posts and very large accounts do not dominate.

{metric_line("Followers", ideal["audience"]["followers"])}
{metric_line("Median likes per creator", ideal["performance"]["median_likes"])}
{metric_line("Median comments per creator", ideal["performance"]["median_comments"])}
{metric_line("Median per-post engagement rate", ideal["performance"]["engagement_rate_pct"], "%")}
{metric_line("Engagement-data confidence", ideal["performance"]["engagement_confidence"])}
{metric_line("Posting recency", ideal["activity"]["posting_recency_days"], " days")}
{metric_line("Sampled posting frequency", ideal["activity"]["posts_per_week"], " posts/week")}
{metric_line("Short-video share", ideal["formats"]["short_video_share"])}

Recommended recurring signals: {", ".join(ideal["recommended_signals"]) or "none above the 35% prevalence threshold"}.

The practical target is an active creator near the cohort's robust audience and
engagement center, consistently using short video, with independently evidenced UGC
and commercial readiness plus native product-integration evidence. Barter feasibility
is strongest for smaller audiences with explicit UGC, PR, or marketplace experience.

## Highest-scoring source profiles

{top_lines}

## Full transparent scoring

{ranking_table(result)}

Every component explanation is included in `source_analysis.csv` and
`ideal_creator_profile.json`. The complete formula and thresholds are documented in
`scoring_methodology.md`.

## Human-in-the-loop QA

{qa["excel_hyperlink_issue"]["finding"]} This run found
**{qa["excel_hyperlink_issue"]["observed_display_target_mismatch_count"]}**
display/target mismatches and consistently preferred the target.

{qa["performed_by"]} manually recovered and verified
{len(qa["manually_verified_corrections"])} corrected profiles:

{correction_lines}

Before the verified `nikaanow` recovery, {qa["performed_by"]} rejected the uncertain `aparina_` match
for source `__aparina`, because there was insufficient evidence that it was the same
person. That rejected false lead remains in the QA history; `nikaanow` is now treated as
an accessible public creator with sampled post data.

This improved reliability by recovering only evidence-backed identities, preventing an
uncertain candidate from contaminating the creator cohort, and exposing a systematic
Excel parsing failure. The process improves precision while preserving the decision
history.

## Profiles excluded from quantitative analysis

{exclusion_table(result)}

All excluded records remain present in `source_analysis.csv` and the data-quality report.
Missing metrics are blank/null; they are not imputed.

## Data quality

- Workbook references: **{result.workbook_reconciliation['workbook_reference_count']}**
- JSON records: **{result.workbook_reconciliation['json_record_count']}**
- Workbook/JSON reconciliation after verified audit corrections:
  **{str(result.workbook_reconciliation['reconciled']).lower()}**
- Typed validation issues: **{summary['validation_issue_count']}**
- Duplicate normalized usernames: **{len(summary['duplicate_usernames'])}**

## Limitations

- The latest-post sample is a snapshot and may not represent long-term performance.
- Likes and comments can be hidden, missing, delayed, or affected by platform behavior.
- Engagement uses the median of sampled per-post rates; it does not measure reach,
  impressions, saves, shares, audience quality, or conversions.
- Engagement score is conservatively multiplied by the share of sampled posts with
  usable likes and comments; raw engagement metrics and the raw component are retained.
- Keyword signals are deterministic indicators, not semantic or visual ground truth.
- The available data cannot directly verify aesthetic quality, audience geography,
  brand safety, authenticity, commercial rates, or willingness to accept barter.
- Engagement percentile scores are relative to this eligible creator cohort.
- Posting frequency is estimated from the observed timestamp span, not the full account history.
- The score supports shortlisting; it does not replace a human visual and commercial review.

## Scope boundary

This phase performs no creator discovery, external enrichment, live Instagram access,
paid API calls, credential use, or outreach generation/sending.
"""
    path.write_text(report, encoding="utf-8")


def write_scoring_methodology(path: Path) -> None:
    content = """# Source-profile scoring methodology

## Purpose

The deterministic score ranks only public creator profiles with a username, positive
follower count, at least three sampled posts, and at least six posts with usable likes
and comments. Nike, Apple, private accounts, not-found accounts, and insufficient-data
records are preserved but not scored.

The total is the sum of six bounded components and therefore always falls between
0 and 100. Scores are rounded to two decimals. Each output row contains the component
value, maximum, and evidence-based explanation.

## 1. Content and aesthetic fit — 30 points

Topical keyword signals contribute up to 24 points:

- fashion: 7
- beauty: 5
- lifestyle: 4
- UGC: 5
- marketplace/product-review: 3

Format readiness contributes up to 5 points:

- any short video: 3
- any non-short video, carousel, or image: 1
- at least one non-empty sampled caption: 1

The final 1 point measures short-video-format alignment with the median Nike/Apple
short-video share: `1 - abs(creator_share - brand_reference_median_share)`.
This is the only way brand profiles influence creator scoring.

## 2. Native product integration potential — 20 points

- UGC positioning: 6
- marketplace/product-review signal: 4
- commercial/PR signal: 4
- native integration evidence in sampled posts: up to 4, calculated as
  `min(4, integration_post_share × 8)`
- contact signal: 2

An integration post requires its own post-level evidence: a structured brand/product
mention or tag, product-focused caption, review, try-on, unboxing, marketplace article,
promo code, product link, or comparable observable product language. A paid-partnership
flag alone does not create native-integration evidence.

## 3. Short-video consistency — 15 points

`short_video_share × 15`.

Short video is identified from normalized Instagram `productType` values such as
`clips`, `reels`, or `reel`. Unknown formats remain unknown.

## 4. Engagement — 15 points

For each creator:

1. Calculate each usable post rate as `(likes + comments) / followers × 100`.
2. Use the median post rate to limit the effect of viral posts.
3. Rank that median within the eligible creator cohort.
4. Calculate raw component `raw_engagement_component = average_tie_percentile × 15`.
5. Calculate `engagement_confidence = usable_engagement_posts / sampled_posts`.
6. Score `adjusted_engagement_component = raw_engagement_component × engagement_confidence`.

Missing likes, comments, or followers are not imputed and are never converted to zero.
The median rate and raw component are preserved separately from the adjustment. Fewer
than six posts with both likes and comments makes the profile `insufficient_data`.
Ties receive the average percentile. The score is cohort-relative and must not be
interpreted as an absolute industry benchmark.

## 5. Barter feasibility — 10 points

Audience-size points:

- ≤1,000 followers: 4
- 1,001–10,000: 5
- 10,001–50,000: 4
- 50,001–150,000: 3
- 150,001–300,000: 2
- >300,000: 1

Additional evidence:

- commercial/PR signal: 2
- UGC signal: 2
- marketplace/product-review signal: 1

The result is capped at 10. This is a feasibility proxy, not evidence that a creator
will accept a barter offer.

## 6. Recent activity — 10 points

Recency contributes up to 6 points:

- latest post ≤7 days: 6
- ≤14 days: 5
- ≤30 days: 4
- ≤60 days: 2
- ≤90 days: 1
- older or unavailable: 0

Frequency contributes up to 4:
`min(4, sampled_posts_per_week / 2 × 4)`.

Sampled posts per week is `(timestamped_posts - 1) / observed_span_days × 7`.
The default reference time is the newest valid post timestamp in the dataset, making
the offline demo reproducible. It can be overridden with `--as-of` or
`ANALYSIS_AS_OF`.

## Signal extraction

Signals come from case-insensitive, documented keyword matching across profile name,
bio, captions, and hashtags, plus structured fields. Every signal stores typed
provenance: signal type, source field/post, evidence reason, and whether the observation
is direct or derived. No detected signal is used to create another signal.

The five potentially overlapping dimensions are deliberately separate:

- Contact accessibility: email, Telegram, WhatsApp, manager, “для связи”, contact
  wording, or an external contact/link URL. It awards only dedicated contact points.
- Commercial experience: explicit PR, advertising, collaboration/cooperation,
  partnership, commercial, ambassador, previous brand-work language, or a directly
  observed paid-partnership flag. Generic contact information is insufficient.
- UGC capability: explicit UGC/content-creator positioning; it does not imply
  commercial experience or native integration.
- Native integration: independent post-level product/brand mentions or tags,
  product-focused captions, reviews, try-ons, unboxings, promo codes, product links, or
  marketplace/product language. Paid-partnership alone is insufficient.
- Marketplace experience: explicit marketplace, review, unpacking, article, or
  shopping language.

A post may support both commercial experience and native integration only when
separate source observations support each dimension. Evidence is an indicator, not
proof of quality, intent, identity, or commercial terms.

## Missing-data and exclusion policy

- Never replace missing values with zero unless zero is explicitly present.
- Missing derived metrics remain null/blank.
- All source records appear in the CSV and data-quality report.
- Excluded records receive a reason rather than a score.
- Brand references never enter creator medians, quartiles, or engagement percentiles.
"""
    path.write_text(content, encoding="utf-8")


def generate_outputs(output_dir: Path, result: AnalysisResult) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        output_dir / "source_analysis.csv",
        output_dir / "ideal_creator_profile.json",
        output_dir / "data_quality_report.json",
        output_dir / "analysis_report.md",
        output_dir / "scoring_methodology.md",
    ]
    write_source_csv(paths[0], result)
    write_ideal_profile(paths[1], result)
    write_data_quality_report(paths[2], result)
    write_analysis_report(paths[3], result)
    write_scoring_methodology(paths[4])
    return paths
