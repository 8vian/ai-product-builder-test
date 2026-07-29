# Phase B discovery report

Run ID: `live-20260728T191026Z`
Mode: `live`
Provider: `apify`
Provider run IDs: `EVhxBZch8YLwRaRoS`, `XPOnPowTZs96qIZVF`
Generated: 2026-07-28T19:10:26.307341+00:00

## Pipeline counts

- Generated queries: **5**
- Raw discovery hits: **40**
- Excluded candidates: **10**
- Enriched candidates: **40**
- Eligible candidates: **30**
- Selected candidates: **5**
- Unique candidates after discovery-stage filtering: **40**
- Duplicate discoveries merged: **0**



## Discovery-stage exclusion audit

- No discovery-stage exclusions.

## Selected creators

| Rank | Creator | Score | Discovery confidence | Data completeness | Evidence |
|---:|---|---:|---:|---:|---|
| 1 | [`bainur_beauty`](https://www.instagram.com/bainur_beauty/) | 70.62 | 0.866667 | 1.0 | [recent post](https://www.instagram.com/p/DbAnTyEFNyl/) |
| 2 | [`creatorbrand.hub`](https://www.instagram.com/creatorbrand.hub/) | 64.83 | 0.8 | 1.0 | [recent post](https://www.instagram.com/p/DapTLh1lmDy/) |
| 3 | [`by.wildberries`](https://www.instagram.com/by.wildberries/) | 61.93 | 0.866667 | 1.0 | [recent post](https://www.instagram.com/p/DbTVg9EsZM-/) |
| 4 | [`beautynthebean`](https://www.instagram.com/beautynthebean/) | 61.06 | 0.866667 | 1.0 | [recent post](https://www.instagram.com/p/DbWBLr9p0Xr/) |
| 5 | [`kz.wildberries`](https://www.instagram.com/kz.wildberries/) | 58.87 | 0.866667 | 0.9166666666666666 | [recent post](https://www.instagram.com/p/DbVkk1-s9vq/) |

## First selected score example

The creator score uses the frozen Phase A reference cohort. Discovery confidence is
reported separately and is used only as a deterministic tie-break after score.

- `content_and_aesthetic_fit`: 21.88/30.0 — 17.00/24 topical + 4.00/5 format readiness + 0.88/1 Nike/Apple format alignment.
- `native_product_integration_potential`: 12.67/20.0 — UGC=True, marketplace=False, commercial/PR=True, contact=True; 1/12 posts have independently observed native-integration evidence.
- `short_video_consistency`: 10.0/15.0 — Short video share 0.667 × 15 = 10.00.
- `engagement`: 11.07/15.0 — Frozen Phase A percentile 0.737732 × 15 = 11.07 raw; × completeness 12/12 (1.000000) = 11.07 adjusted.
- `barter_feasibility`: 5.0/10.0 — 1.0/5 for very large audience (>300k); commercial/PR=True (+2), UGC=True (+2), marketplace=False (+1).
- `recent_activity`: 10.0/10.0 — 6.00/6 recency + 4.00/4 posting frequency.

## Safety boundary

Every barter offer is a draft with a manual-verification state. This pipeline contains
no message-sending functionality. Candidate evidence and links must be manually
reviewed before any external action. Demo-mode links are deterministic fixtures; live
mode is required for current real-profile discovery.
