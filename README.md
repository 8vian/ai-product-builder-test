# AI Product Builder — Phase A

An offline Python 3.12 CLI that validates and analyzes the supplied verified Instagram
reference dataset, keeps brand and unavailable records separate, calculates robust
creator metrics, and generates a transparent ideal-creator profile.

## What it produces

- `output/source_analysis.csv` — one row per source record, including classifications,
  metrics, signal evidence, component scores, explanations, and exclusions.
- `output/ideal_creator_profile.json` — robust ideal-profile synthesis, full ranking,
  component explanations, and separate Nike/Apple reference signals.
- `output/data_quality_report.json` — validation, source reconciliation, exclusions,
  missing-data policy, and human-in-the-loop QA.
- `output/analysis_report.md` — stakeholder-readable findings and limitations.
- `output/scoring_methodology.md` — deterministic formulas, thresholds, and caveats.

## Requirements

- Python 3.12+
- No network access, API key, paid API, or Instagram credential

## Install

```powershell
python -m pip install -e ".[dev]"
```

## One-command demo

Run from the repository root:

```powershell
ai-product-builder demo
```

The equivalent module command is:

```powershell
python -m ai_product_builder.cli demo
```

The command reads:

- `data/raw/instagram_profiles.json`
- `data/raw/manual_verification_audit.json`
- `data/raw/Блогеры.xlsx`

and writes all required artifacts under `output/`.

## Configurable run

```powershell
ai-product-builder analyze --input-dir data/raw --output-dir output
```

By default, posting recency is measured against the newest valid sampled-post timestamp
in the dataset. This makes the offline demo reproducible. Override it when needed:

```powershell
ai-product-builder analyze --as-of 2026-07-27T00:00:00Z
```

The same value may be supplied through `ANALYSIS_AS_OF`; see `.env.example`.

## Test

```powershell
python -m pytest
```

## Analysis rules

- JSON fields and sampled posts are normalized into typed dataclasses.
- Every record receives exactly one classification.
- Nike and Apple never enter creator statistics or engagement percentiles.
- Private, unresolved, not-found, and insufficient-data records remain in the outputs.
- Missing values remain missing; they are not guessed or converted to artificial zeros.
- Robust scoring requires at least three sampled posts and at least six posts with both
  usable likes and comments; profiles below the engagement threshold remain reported as
  insufficient data.
- Median post metrics and median per-post engagement reduce outlier influence.
- Engagement confidence is `usable_engagement_posts / sampled_posts`; the engagement
  component is its raw percentile score multiplied by that confidence. Missing likes
  remain missing and are never converted to zero.
- Contact accessibility, commercial/PR experience, UGC capability, native product
  integration, and marketplace experience are independent dimensions. Contact details
  do not create commercial/PR evidence; a paid-partnership flag creates commercial
  evidence but does not create native integration without separate post-level product
  evidence.
- Every signal includes typed provenance identifying the source field or post,
  evidence reason, and whether it was directly observed or derived.
- The Excel reader prefers embedded hyperlink targets over potentially truncated display
  text and reports every mismatch.
- Pavel's four verified username corrections—including `__aparina` → `nikaanow`—and
  the previously rejected `aparina_` false lead are documented as human-in-the-loop QA.

## Score

Eligible public creators are scored from 0 to 100:

- content and aesthetic fit: 30
- native product integration potential: 20
- short-video consistency: 15
- engagement: 15
- barter feasibility: 10
- recent activity: 10

Every component includes its score, maximum, and human-readable explanation. See the
generated `output/scoring_methodology.md` for exact rules.

## Deliberate non-scope

This phase does not discover new creators, query Instagram, enrich profiles from external
services, generate outreach, or send messages.
