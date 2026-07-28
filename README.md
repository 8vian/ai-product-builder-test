# AI Product Builder — Phase A and Phase B MVP

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

## Phase A deliberate non-scope

This phase does not discover new creators, query Instagram, enrich profiles from external
services, generate outreach, or send messages.

## Phase B MVP

Phase B is a separate package layered on the frozen Phase A outputs. It discovers a
large Instagram candidate pool, excludes every known Phase A identity, enriches recent
posts, applies transparent eligibility rules, scores the complete eligible pool, and
only then selects up to five candidates. It never sends messages. Every barter offer is
a draft with `manual_verification_status = pending`.

### Credential-free demo

Validate the configuration:

```powershell
ai-product-builder phase-b validate-config --config config/phase_b.demo.json
```

Run the complete deterministic demo:

```powershell
ai-product-builder phase-b demo `
  --config config/phase_b.demo.json `
  --output-dir output/phase_b
```

The stable demo run ID is `demo`, so rerunning writes
`output/phase_b/demo/Блогеры_phase_b.xlsx` and preserves manually edited status and
notes by normalized username. The fixtures deliberately contain more than 30 discovery
records, duplicates, Phase A sources and aliases, brand references, malformed data,
private/inaccessible accounts, incomplete engagement, stale activity, identity
conflicts, and more than five eligible controls. Fixture profiles and links are
deterministic test data; only live mode is intended to return current real accounts.

### Data flow and eligibility

Phase B performs these stages in order:

1. Read `output/ideal_creator_profile.json` and deterministically generate five query
   families: fashion/style, beauty/lifestyle, UGC, marketplace/reviews, and
   Reels/product integration.
2. Discover a pool materially larger than five through the configured provider.
3. Normalize and deduplicate by case-insensitive username and canonical profile URL
   while preserving every dot and underscore.
4. Exclude all Phase A usernames and URLs, historical aliases, verified replacements,
   rejected false leads, Nike, and Apple with a traceable reason.
5. Enrich remaining profiles and recent posts, keeping missing and negative sentinel
   engagement values missing.
6. Apply eligibility rules, score the entire eligible pool, use deterministic
   tie-breaking, and select at most five without padding.
7. Generate evidence-grounded barter drafts for candidates that have a recent,
   attributable Instagram post and caption.
8. Write local audit artifacts and, when an authenticated client is explicitly
   supplied, optionally upsert the same schema to Google Sheets.

A candidate is eligible only when it is a public, accessible Instagram profile with a
canonical URL, positive follower count, at least six posts with both usable likes and
comments, a latest usable engagement post no older than 90 days, directly observed target-content evidence,
known post-format data, a recent Instagram evidence URL, no identity conflict, and no
source-exclusion match. Fewer than three eligible candidates fails with
`insufficient_candidate_pool`; three or four are returned with an incomplete-target
warning.

### Scoring and confidence

The 0–100 creator score reuses the audited Phase A weights:

- content and aesthetic fit: 30
- native product integration potential: 20
- short-video consistency: 15
- engagement: 15
- barter feasibility: 10
- recent activity: 10

Engagement percentiles are calculated only against the frozen eligible Phase A
reference cohort in `output/source_analysis.csv`; discovery-pool composition cannot
change them. Engagement retains the audited completeness adjustment:
`raw engagement component × usable engagement posts / sampled posts`. Completeness is
not applied again as a global score multiplier.

Contact accessibility, commercial/PR experience, UGC capability, marketplace
experience, and native integration are independently evidenced. A contact does not
create commercial evidence; generic UGC does not create commercial evidence; and a
paid-partnership flag does not create native-integration evidence.

Discovery confidence remains separate from the 0–100 score:

```text
0.40 × provider identity confidence
+ 0.20 × multi-query support
+ 0.20 × profile/post link verification
+ 0.20 × query-relevance evidence
```

Each term is bounded to 0–1 and explained in candidate evidence. Final ordering is
score, discovery confidence, data completeness, engagement rate, then normalized
username.

### Outputs

Each successful run writes `output/phase_b/<run_id>/`:

- `run_manifest.json`
- `generated_queries.json`
- `discovery_pool.jsonl`
- `deduplication_report.json`
- `excluded_candidates.csv`
- `enriched_candidates.jsonl`
- `eligible_candidates.csv`
- `new_creators.json`
- `new_creators.csv`
- `barter_offer_drafts.md`
- `discovery_report.md`
- `Блогеры_phase_b.xlsx`

CSV files use UTF-8 with BOM, and nested score/evidence fields contain JSON. The new
workbook preserves every source sheet, writes the exact `Новые блоггеры` schema, uses
real hyperlinks, and never overwrites `data/raw/Блогеры.xlsx`.

### Live Apify mode

Copy `config/phase_b.live.example.json` to `config/phase_b.live.json`, replace both
placeholder actor IDs, and verify each configured field mapping against those actors.
Put `APIFY_TOKEN` in `.env`; secrets in JSON config are rejected, and no credential CLI
flags exist.

```powershell
ai-product-builder phase-b validate-config --config config/phase_b.live.json

ai-product-builder phase-b run `
  --mode live `
  --config config/phase_b.live.json `
  --output-dir output/phase_b
```

The Apify adapter uses configurable actor IDs, templates, field mappings, maximum-item
budget and timeout. It preserves run IDs, honors `Retry-After`, uses bounded exponential
backoff with no more than three retries, and never falls back to fixtures after a live
failure. A live operator must manually verify that selected profile/post links still
work before approving any draft. The MVP does not persist provider-response caches;
repeat live runs therefore consume the configured Apify item budget again.

### Google Sheets

`GoogleSheetsAdapter` reads the existing `Новые блоггеры` range and batch-upserts by
normalized username without deleting unrelated ranges. Existing manual status and
notes win over generated defaults. Credentials remain in `.env` as
`GOOGLE_SERVICE_ACCOUNT_JSON`; when Sheets is enabled, the CLI can build the official
client from JSON content or a credential-file path. Programmatic callers may inject an
authenticated client directly. The credential-free CLI continues with local artifacts
when no credential is available. Install `python -m pip install -e ".[sheets]"` before
an authenticated Google Sheets run.

### Offer safety

The deterministic template cites one actual recent post URL and a safe caption-derived
topic, then uses only the configured campaign product, barter item, and requested
format. Validation rejects drafts that lack provenance or claim that a creator already
likes the brand, accepts barter, used the product, has an unsupported audience profile,
or guarantees performance. There is deliberately no send, DM, publish, or outreach
command.
