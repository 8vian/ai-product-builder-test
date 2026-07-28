# Phase B discovery report

Run ID: `demo`
Mode: `demo`
Provider: `fixture`
Provider run IDs: `fixture-discovery-v1`, `fixture-enrich-v1`
Generated: 2026-07-28T13:00:37.843302+00:00

## Pipeline counts

- Generated queries: **5**
- Raw discovery hits: **36**
- Excluded candidates: **21**
- Enriched candidates: **25**
- Eligible candidates: **15**
- Selected candidates: **5**
- Unique candidates after discovery-stage filtering: **25**
- Duplicate discoveries merged: **2**



## Discovery-stage exclusion audit

- `duplicate_merged`: **2**
- `malformed_discovery_record`: **2**
- `source_exclusion`: **6**
- `unsupported_platform`: **1**

## Selected creators

| Rank | Creator | Score | Discovery confidence | Data completeness | Evidence |
|---:|---|---:|---:|---:|---|
| 1 | [`reels.by.sonya`](https://www.instagram.com/reels.by.sonya/) | 86.04 | 0.921333 | 1.0 | [recent post](https://www.instagram.com/p/reelsbysonya01/) |
| 2 | [`mira_reels.ru`](https://www.instagram.com/mira_reels.ru/) | 74.83 | 0.921333 | 1.0 | [recent post](https://www.instagram.com/p/mirareelsru01/) |
| 3 | [`beauty.offer.test`](https://www.instagram.com/beauty.offer.test/) | 73.97 | 0.917333 | 1.0 | [recent post](https://www.instagram.com/p/beautyoffertest01/) |
| 4 | [`beauty.and.city`](https://www.instagram.com/beauty.and.city/) | 71.36 | 0.913333 | 1.0 | [recent post](https://www.instagram.com/p/beautyandcity01/) |
| 5 | [`ugc_by_lena`](https://www.instagram.com/ugc_by_lena/) | 70.77 | 0.996 | 1.0 | [recent post](https://www.instagram.com/p/ugcbylena01/) |

## First selected score example

The creator score uses the frozen Phase A reference cohort. Discovery confidence is
reported separately and is used only as a deterministic tie-break after score.

- `content_and_aesthetic_fit`: 24.72/30.0 — 19.00/24 topical + 5.00/5 format readiness + 0.72/1 Nike/Apple format alignment.
- `native_product_integration_potential`: 18.0/20.0 — UGC=True, marketplace=True, commercial/PR=True, contact=False; 11/11 posts have independently observed native-integration evidence.
- `short_video_consistency`: 12.27/15.0 — Short video share 0.818 × 15 = 12.27.
- `engagement`: 12.55/15.0 — Frozen Phase A percentile 0.836884 × 15 = 12.55 raw; × completeness 11/11 (1.000000) = 12.55 adjusted.
- `barter_feasibility`: 9.0/10.0 — 4.0/5 for micro creator audience (10k–50k); commercial/PR=True (+2), UGC=True (+2), marketplace=True (+1).
- `recent_activity`: 9.5/10.0 — 6.00/6 recency + 3.50/4 posting frequency.

## Safety boundary

Every barter offer is a draft with a manual-verification state. This pipeline contains
no message-sending functionality. Candidate evidence and links must be manually
reviewed before any external action. Demo-mode links are deterministic fixtures; live
mode is required for current real-profile discovery.
