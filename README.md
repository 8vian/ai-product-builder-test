# AI Product Builder — Phase A and Phase B MVP

A Python 3.12 CLI with offline Phase A and deterministic Phase B demo workflows, plus
an explicitly configured live Phase B path for guarded Instagram discovery through
official Apify Actors.

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
- Phase A and demo mode require no network access, API key, paid API, or Instagram
  credential
- Live Phase B requires an `APIFY_TOKEN` read only from `.env`

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
a draft; its manual state is `pending` unless a human review explicitly marks it
`needs_review`, `approved`, or `rejected`.

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
6. Classify account type and require direct fashion, beauty, or compatible personal
   lifestyle evidence before applying the remaining eligibility rules.
7. Score the entire eligible pool, use deterministic
   tie-breaking, and select at most five without padding.
8. Generate evidence-grounded barter drafts for candidates that have a recent,
   attributable Instagram post and caption.
9. Write local audit artifacts and, when an authenticated client is explicitly
   supplied, optionally upsert the same schema to Google Sheets.

A candidate is eligible only when it is a public, accessible Instagram profile with a
canonical URL, positive follower count, account type `personal_creator`, at least six
posts with both usable likes and comments, a latest usable engagement post no older
than 90 days, directly observed fashion/beauty/compatible-personal-lifestyle evidence,
a recent Instagram evidence URL, no identity conflict, and no source-exclusion match.
The account types are `personal_creator`, `brand`, `marketplace`, `store`,
`agency_or_platform`, `thematic_non_personal_page`, and `unclear`. Official/brand
accounts, marketplaces, stores, agencies/platforms, aggregators, non-personal theme
pages, and dominant pet, gaming, fishing, electronics/STEM, B2B education,
media/clip, political/religious, or incompatible food/garden themes are ineligible.
A lone generic word such as “beauty”, “style”, “fashion”, “lifestyle”, or “creator”
does not establish topical fit. A personal author is not rejected merely for a link,
course, or own product.

Normal discovery runs also require known post-format data. Offline reselection of a
saved provider run may retain a creator whose format field is missing when all other
requirements are satisfied: missing format earns no short-video points and is
reported, rather than being invented or used as positive evidence. Fewer than three
eligible candidates fails with
`insufficient_candidate_pool`; three or four are returned with an incomplete-target
warning.

The campaign-specific `final-review` command below is intentionally stricter than
the generic reselection path: a personal candidate with no known post format is
classified as `insufficient_data` with `post_format_data_missing` and cannot enter a
barter shortlist.

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

Account-type, topical-fit, and manual-review observations are preserved as separate
evidence records with source field/post, evidence text, URL where available, and
direct/derived provenance.

Discovery confidence remains separate from the 0–100 score:

```text
0.40 × provider identity confidence
+ 0.20 × multi-query support
+ 0.20 × profile/post link verification
+ 0.20 × query-relevance evidence
```

Each term is bounded to 0–1 and explained in candidate evidence. Final ordering is
score, discovery confidence, data completeness, engagement rate, then normalized
username. In official Apify mode, identity confidence is derived from exact agreement
between the returned username and direct Instagram profile URL; it is not read from a
nonexistent Actor field.

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

Copy `config/phase_b.live.example.json` to `config/phase_b.live.json`. The example is
preconfigured for the official `apify/instagram-search-scraper` discovery Actor and
`apify/instagram-profile-scraper` enrichment Actor. Put `APIFY_TOKEN` in `.env`;
secrets in JSON config are rejected, and no credential CLI flags exist.

```powershell
ai-product-builder phase-b validate-config --config config/phase_b.live.json

ai-product-builder phase-b run `
  --mode live `
  --config config/phase_b.live.json `
  --output-dir output/phase_b
```

`validate-config` is a local-only preflight and does not call Apify. Discovery retains
the five original deterministic texts in the legacy `query_texts` context. For
`query_text_csv`, it replaces only characters forbidden by the official Search Actor
input schema (for example `#` and `-`) with spaces, collapses whitespace, and sends the
five resulting terms as one comma-separated `search` value. Each returned `searchTerm`
is matched back to exactly one `QuerySpec.query_id` against both the original and
Actor-safe forms using case-insensitive, whitespace-normalized comparison; unknown or
ambiguous terms retain an empty `query_ids` value.

The Apify adapter uses configurable templates and field mappings, preserves run IDs,
honors `Retry-After`, and never falls back to fixtures after a live failure. The smoke
configuration limits each Actor run with `maxItems=50`,
`maxTotalChargeUsd=1.0`, a 180-second client/polling deadline, and at most two bounded
retries. The Actor-start request clamps `waitForFinish` to Apify's documented 60-second
server maximum; time spent in that request is deducted from the same 180-second
deadline before bounded client polling continues. Every poll, retry/backoff, and final
dataset read is likewise capped by the remaining overall deadline.
An ambiguously failed Actor-start POST is not retried, avoiding accidental duplicate
paid runs. The charge cap applies separately to discovery and enrichment, so the
pipeline-wide worst-case cap is approximately USD 2.00, not USD 1.00.

Because the official profile Actor does not return `accessible`, the adapter derives it
from a valid matching username/profile URL, absent error fields, an unrestricted usable
record, valid core profile fields, and an array of recent posts. `private` remains an
independent field and private profiles still fail eligibility. Structured
`externalUrls` and `taggedUsers` are normalized without stringifying objects;
`likesCount=-1` remains missing, and paid-partnership evidence is never inferred from
text, tags, or mentions.

A live operator must manually verify that selected profile/post links still work before
approving any draft. The MVP does not persist provider-response caches; repeat live
runs therefore consume the configured Apify item budget again.

If an already completed live run reaches `insufficient_candidate_pool`, the pipeline
now writes the discovery pool, deduplication audit, enriched profiles, exclusions,
eligible rows, conservative near-miss files, discovery report, and failed manifest
before returning a nonzero exit code. It never pads the result.

An older failed run whose Actor datasets already exist can be recovered with the
dedicated GET-only command below. `recover` reads only
`GET /v2/actor-runs/:runId` and
`GET /v2/datasets/:datasetId/items`; it has no Actor-start/call method, never invokes
the discovery/enrichment provider methods, does not apply `minimum_final_count`, and
does not create a shortlist or offer drafts:

```powershell
ai-product-builder phase-b recover `
  --config config/phase_b.live.json `
  --source-failed-run output/phase_b/live-YYYYMMDDTHHMMSSZ `
  --search-run-id EXISTING_SEARCH_RUN_ID `
  --profile-run-id EXISTING_PROFILE_RUN_ID `
  --output-dir output/phase_b/live-YYYYMMDDTHHMMSSZ-recovered
```

Recovery preserves the four raw HTTP JSON bodies, records the exact existing run and
dataset IDs, writes normalized discovery/enrichment and exclusion artifacts, and
reports strict eligibility plus manual-review-only near misses. A zero-eligible or
zero-near-miss result is a valid completed recovery analysis.

For the final Russian LD Latte search, the local ignored configuration is
`config/phase_b.live.ru.json`. It sets campaign geography and delivery market to
`Россия`, targets directly evidenced Russian-language bio/captions, retains the
frozen Phase A follower IQR and `Q3 + 3 × IQR` barter-review fence, and contains
run-level username/profile-URL exclusions from the previous live run. A matching
discovery result is recorded as `previous_live_run_exclusion` and is removed before
enrichment. The Search Actor contract has no configured provider-side blacklist, so
an excluded identity may still consume a discovery result if the Actor returns it.

`validate-config` prints the exact generated query texts and run-level exclusion
count without exposing the token value or making a provider request:

```powershell
ai-product-builder phase-b validate-config `
  --config config/phase_b.live.ru.json
```

For a live campaign, generic eligibility is followed by campaign guards: Russian
language must be evidenced lexically in saved bio/captions, an explicit barter
refusal is ineligible, directly observed foreign geography conflicting with the
Russian delivery market is ineligible, and audience above the frozen Phase A outer
fence is `needs_review` rather than automatically selectable.

### Offline reselection of a saved live run

Use `reselect` after human QA when discovery and enrichment are already saved. This
path reads the existing `discovery_pool.jsonl` and `enriched_candidates.jsonl`; it
does not call `provider.discover`, `provider.enrich`, Apify HTTP endpoints, Google
Sheets, or an LLM:

```powershell
ai-product-builder phase-b reselect `
  --source-run output/phase_b/live-20260728T191026Z `
  --config config/phase_b.live.example.json `
  --review-file data/reviews/phase_b/live-20260728T191026Z.json `
  --output-dir output/phase_b/live-20260728T191026Z-reviewed
```

The output directory must be new and empty; the source run is never overwritten. The
reviewed manifest records `offline_reselection=true`, the source run ID/path, zero
provider requests, and zero provider budget spent. Manual exclusions and their exact
reasons are data in the review JSON, not hidden username rules.

“Substantially above the Phase A reference range” uses Tukey’s conservative upper
outer fence derived from the frozen Phase A follower quartiles:

```text
barter review threshold = Q3 + 3 × IQR = Q3 + 3 × (Q3 − Q1)
```

Exceeding the threshold sets `barter_feasibility_review_required=true`; it neither
changes the creator score nor asserts that barter is impossible. The generated status
stays `pending` unless an explicit human review decision sets `needs_review`.

### Offline final barter-campaign review

Use `final-review` to apply the stricter campaign rules to the identical saved
discovery/enrichment records from the original and reviewed runs:

```powershell
ai-product-builder phase-b final-review `
  --source-run output/phase_b/live-20260728T191026Z `
  --reviewed-run output/phase_b/live-20260728T191026Z-reviewed `
  --config config/phase_b.live.example.json `
  --review-file data/reviews/phase_b/live-20260728T191026Z.json `
  --output-dir output/phase_b/live-20260728T191026Z-final-review
```

The command does not construct a provider and does not run discovery or enrichment.
It verifies that both input runs contain identical saved profiles, requires a new
empty output directory, and records zero provider requests and zero spend.

Every candidate is placed in exactly one campaign bucket:

- `barter_ready`: personal, topically relevant, at least six usable engagement
  observations, recent evidence, at least one known post format, no explicit barter
  refusal, within the frozen Phase A audience fence, and no unresolved language,
  delivery, or collaboration-term review;
- `needs_manual_review`: otherwise eligible but requiring review for a large
  audience, missing campaign-language evidence, an unconfirmed delivery market, or
  unclear barter terms;
- `ineligible_or_insufficient`: non-personal or irrelevant accounts, explicit
  barter refusals, and candidates missing mandatory data.

Direct statements equivalent to “не работаю по бартеру”, “бартер не рассматриваю”,
or “только платное сотрудничество” exclude a creator only from this barter campaign
with reason `explicit_no_barter_statement`; the report may retain a note that a
separate paid campaign remains possible. A price list, manager, contact address, or
commercial terms alone are not treated as a refusal. No offer is generated for an
ineligible or insufficient-data record. A `needs_manual_review` draft is visibly
prefixed `DO NOT SEND BEFORE MANUAL APPROVAL`, remains `pending`, and cannot be sent
by this project.

Compatibility review uses only biography, caption, explicit-location, and language
evidence. It stores `detected_content_language`,
`campaign_language_compatible`, `detected_geography`,
`delivery_market_review_required`, and a human-readable explanation. Geography is
never inferred from a username. With campaign geography left `null`, delivery-market
confirmation is required rather than guessed.

In addition to the standard Phase B artifacts, final review writes separate
`barter_ready`, `needs_manual_review`, `ineligible_or_insufficient`,
`personal_candidate_classification`, and `top_10_personal_candidates` JSON/CSV
artifacts. The top ten is diagnostic: campaign status overrides score, so an
ineligible or review-only high-scoring creator is never silently promoted into an
approved barter selection.

### Offline manual finalization of one saved run

`manual-finalize` is the reusable last-mile path for a complete structured human
review of one immutable run. It reads only the saved eligible CSV, enriched JSONL,
source exclusions, source workbook, manifest, and review JSON. It has no config,
provider, discovery, enrichment, LLM, or sending step:

```powershell
ai-product-builder phase-b manual-finalize `
  --source-run output/phase_b/live-20260728T211907Z `
  --review-file data/reviews/phase_b/live-20260728T211907Z-manual-final.json `
  --output-dir output/phase_b/live-20260728T211907Z-manual-final
```

The review file must cover every username in the saved eligible pool. Each decision
is either `select`, `reject`, or `eligible_not_selected`; selected records require a
human first name, explicit status, deterministic order, and an actual saved
clothing/fashion post URL. A generic lifestyle post cannot ground an offer.
Rejected records require `manual_verification_status=rejected`, a stable reason
code, and source-backed evidence. Evidence may reference an exact saved profile
field or post caption, or a deterministic metric derived from saved posts. The
finalizer validates the source field, reference, text, observation type, and URL;
unsupported or invented evidence fails closed. Legacy commercial-conflict
decisions remain supported and their quoted evidence must occur verbatim in the
saved biography.

The final `eligible_candidates.csv`, `new_creators.json`, `new_creators.csv`, offer
draft file, and workbook contain only the three selected creators. Rejected and
unverified candidates remain traceable in `eligible_audit_pool.json`/CSV; rejected
commercial conflicts are appended to `excluded_candidates.csv` without removing
automatic exclusions. The manifest records the source hashes, zero provider
requests, zero spend, `outreach_messages_sent=0`, and confirms that the source run
remained unchanged.

The canonical Part 1 result is
`output/phase_b/live-20260728T211907Z-manual-final`:

| Order | Creator | Manual status | Outreach status |
|---:|---|---|---|
| 1 | `verkhovskaya_style` | `approved` | `not_sent` |
| 2 | `olganemka_stylist` | `approved` | `not_sent` |
| 3 | `angelashegiryan` | `pending` | `not_sent` |

The result intentionally contains three creators rather than padding to five.
Two otherwise eligible profiles were manually rejected for direct fashion
commercial conflicts. `stylistelenaialena` was manually rejected as
`professional_portfolio_not_influencer_creator`: saved profile/post evidence
shows a professional styling and costume portfolio, while only 2 of 12 sampled
posts have short-video format and 10 formats are unknown. None of the three
rejected profiles has an offer.

### Offline expansion review of a frozen shortlist

`expansion-review` is a diagnostic-only command for inspecting one saved eligible
profile and reconsidering only the saved noneligible enrichment pool. It does not
load a Phase B config, construct a provider, run discovery/enrichment, generate
offers, send messages, or change the frozen manual-final shortlist:

```powershell
ai-product-builder phase-b expansion-review `
  --source-run output/phase_b/live-20260728T211907Z `
  --manual-final-run output/phase_b/live-20260728T211907Z-manual-final `
  --review-file data/reviews/phase_b/live-20260728T211907Z-manual-final.json `
  --output-dir output/phase_b/live-20260728T211907Z-expansion-review
```

The output directory must be new and cannot be inside either input run. The command
hashes the source run, manual-final run, and structured review before and after
processing. It retains hard exclusions for explicit barter refusal, identity or
commercial conflicts, nonpersonal accounts, unavailable/private profiles, fewer
than six usable engagement posts, missing post-format data, stale/missing recent
evidence, incompatible campaign language, and absent direct recent fashion
evidence. A candidate that survives can receive only
`proposed_decision=needs_manual_review`; no automatic approval or offer is possible.

The seven generated files are an expansion report, a detailed focus-candidate card,
JSON/CSV expansion and near-miss lists, and a source-integrity manifest. They are
diagnostic artifacts, not a replacement `new_creators` list or campaign workbook.

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
