# Phase B discovery report

Run ID: `live-20260728T191026Z-reviewed`
Mode: `offline_reselection`
Provider: `saved_provider_artifacts`
Provider run IDs: `EVhxBZch8YLwRaRoS`, `XPOnPowTZs96qIZVF`
Generated: 2026-07-28T19:52:52.957037+00:00

## Pipeline counts

- Generated queries: **5**
- Raw discovery hits: **40**
- Excluded candidates: **35**
- Enriched candidates: **40**
- Eligible candidates: **5**
- Selected candidates: **5**
- Unique candidates after discovery-stage filtering: **40**
- Duplicate discoveries merged: **0**




## Offline reselection provenance

- Source run ID: `live-20260728T191026Z`
- Source run path: `output\phase_b\live-20260728T191026Z`
- Offline reselection: **true**
- Provider discovery/enrichment requests made: **0**
- Provider budget spent during reselection: **$0.00**
- Previously eligible candidates reclassified by account type or theme: **26**
- Network statement: Provider discovery and enrichment were not called; all records were loaded from the saved source run.

### Account-type audit

- `agency_or_platform`: **2**
- `brand`: **2**
- `marketplace`: **5**
- `personal_creator`: **16**
- `store`: **1**
- `thematic_non_personal_page`: **11**
- `unclear`: **3**

### Manual exclusions

- **@beautynthebean** — Manual verification identified pet/cat-only creator content that is not relevant to women's clothing, fashion, beauty, or compatible lifestyle.
- **@by.wildberries** — Manual verification identified the official regional Wildberries Belarus brand account.
- **@creatorbrand.hub** — Manual verification identified a B2B creator platform connecting brands and creators, not an independent personal creator.
- **@kz.wildberries** — Manual verification identified the official regional Wildberries Kazakhstan brand account.

### Barter-feasibility audience rule

The review threshold is derived only from the frozen Phase A audience
distribution: `q3 + 3 * (q3 - q1)` = **301675.0**
followers (Q1 **9781.0**, Q3
**82754.5**). Exceeding it triggers manual
review; it does not prove that barter is impossible and does not approve or
send an offer.


## Discovery-stage exclusion audit

- No discovery-stage exclusions.

## Selected creators

| Rank | Creator | Score | Discovery confidence | Data completeness | Account type | Manual status | Evidence |
|---:|---|---:|---:|---:|---|---|---|
| 1 | [`bainur_beauty`](https://www.instagram.com/bainur_beauty/) | 70.62 | 0.866667 | 1.0 | personal_creator | needs_review | [recent post](https://www.instagram.com/p/DbAnTyEFNyl/) |
| 2 | [`ugc_creator_sayani`](https://www.instagram.com/ugc_creator_sayani/) | 55.11 | 0.866667 | 1.0 | personal_creator | pending | [recent post](https://www.instagram.com/p/DbV3unITdlT/) |
| 3 | [`bong_beauty_vlog`](https://www.instagram.com/bong_beauty_vlog/) | 47.78 | 0.8 | 1.0 | personal_creator | pending | [recent post](https://www.instagram.com/p/DbTfFWKSCV2/) |
| 4 | [`beauty_newnew`](https://www.instagram.com/beauty_newnew/) | 41.21 | 0.866667 | 1.0 | personal_creator | pending | [recent post](https://www.instagram.com/p/DayyT0zjDDR/) |
| 5 | [`marwadi._.reels_29`](https://www.instagram.com/marwadi._.reels_29/) | 40.79 | 0.8 | 1.0 | personal_creator | pending | [recent post](https://www.instagram.com/p/DbWKPWYDEhT/) |

### Selection explanations

1. **@bainur_beauty** — Rank 1 of 5 eligible candidates: score 70.62/100, discovery confidence 0.867, data completeness 1.000, engagement rate 2.511%. Account type: personal_creator. Manual barter-feasibility review is required. Barter feasibility: Audience requires manual barter review: 777,795 followers exceed the frozen Phase A outer fence q3 + 3×IQR = 82,754.5 + 3×(82,754.5 - 9,781.0) = 301,675.0.
2. **@ugc_creator_sayani** — Rank 2 of 5 eligible candidates: score 55.11/100, discovery confidence 0.867, data completeness 1.000, engagement rate 0.052%. Account type: personal_creator. Audience remains within the Phase A upper outer fence. Barter feasibility: Audience does not exceed the frozen Phase A upper outer fence of 301,675.0 followers.
3. **@bong_beauty_vlog** — Rank 3 of 5 eligible candidates: score 47.78/100, discovery confidence 0.800, data completeness 1.000, engagement rate 0.052%. Account type: personal_creator. Audience remains within the Phase A upper outer fence. Barter feasibility: Audience does not exceed the frozen Phase A upper outer fence of 301,675.0 followers.
4. **@beauty_newnew** — Rank 4 of 5 eligible candidates: score 41.21/100, discovery confidence 0.867, data completeness 1.000, engagement rate 0.630%. Account type: personal_creator. Manual barter-feasibility review is required. Barter feasibility: Audience requires manual barter review: 769,642 followers exceed the frozen Phase A outer fence q3 + 3×IQR = 82,754.5 + 3×(82,754.5 - 9,781.0) = 301,675.0.
5. **@marwadi._.reels_29** — Rank 5 of 5 eligible candidates: score 40.79/100, discovery confidence 0.800, data completeness 1.000, engagement rate 1.232%. Account type: personal_creator. Audience remains within the Phase A upper outer fence. Barter feasibility: Audience does not exceed the frozen Phase A upper outer fence of 301,675.0 followers.

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
