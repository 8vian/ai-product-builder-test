# AI Product Builder — Phase A Analysis

Generated: 2026-07-28T11:07:43.903279+00:00  
Analysis reference time: 2026-07-27T16:24:16+00:00

## Executive summary

The input contains **34** records. The CLI classified them from
the source data rather than using fixed counts:

- Quantitatively analyzed creators: **27**
- Brand references (Nike and Apple): **2**
- Private profiles: **1**
- Unresolved/not-found profiles: **3**
- Other insufficient-data profiles: **1**

Nike and Apple are excluded from creator statistics, engagement percentiles, and the
ideal-profile distributions. Their sampled formats are used only for the explicitly
documented brand-format alignment signal.

## Ideal creator profile

The ideal is a robust synthesis, not a fabricated person. It uses cohort medians and
interquartile ranges so viral posts and very large accounts do not dominate.

- Followers: Q1 9,781.00, median 48,505.00, Q3 82,754.50
- Median likes per creator: Q1 92.75, median 251.00, Q3 444.25
- Median comments per creator: Q1 3.75, median 7.50, Q3 18.00
- Median per-post engagement rate: Q1 0.36%, median 0.66%, Q3 2.59%
- Engagement-data confidence: Q1 1.00, median 1.00, Q3 1.00
- Posting recency: Q1 0.13 days, median 1.78 days, Q3 11.64 days
- Sampled posting frequency: Q1 0.22 posts/week, median 0.67 posts/week, Q3 2.13 posts/week
- Short-video share: Q1 0.29, median 0.58, Q3 0.92

Recommended recurring signals: commercial_pr, contact, fashion, beauty, lifestyle, ugc, marketplace, native_product_integration.

The practical target is an active creator near the cohort's robust audience and
engagement center, consistently using short video, with independently evidenced UGC
and commercial readiness plus native product-integration evidence. Barter feasibility
is strongest for smaller audiences with explicit UGC, PR, or marketplace experience.

## Highest-scoring source profiles

- `kotova.live` — 85.56/100: Total 85.56/100. Strongest relative component: recent_activity (10.00/10); lowest relative component: engagement (6.35/15).
- `yunglolaa` — 80.14/100: Total 80.14/100. Strongest relative component: native_product_integration_potential (20.00/20); lowest relative component: engagement (2.60/15).
- `kristi_naxodka` — 77.19/100: Total 77.19/100. Strongest relative component: native_product_integration_potential (20.00/20); lowest relative component: engagement (1.73/15).
- `martini.a13` — 75.69/100: Total 75.69/100. Strongest relative component: engagement (13.85/15); lowest relative component: content_and_aesthetic_fit (14.62/30).
- `v.m.beauty_blog` — 73.04/100: Total 73.04/100. Strongest relative component: recent_activity (10.00/10); lowest relative component: engagement (0.00/15).

## Full transparent scoring

| Rank | Creator | Total | Content | Integration | Short video | Engagement | Barter | Activity |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `kotova.live` | 85.56 | 29.71/30 | 18.00/20 | 12.50/15 | 6.35/15 | 9.00/10 | 10.00/10 |
| 2 | `yunglolaa` | 80.14 | 24.54/30 | 20.00/20 | 15.00/15 | 2.60/15 | 8.00/10 | 10.00/10 |
| 3 | `kristi_naxodka` | 77.19 | 29.96/30 | 20.00/20 | 7.50/15 | 1.73/15 | 8.00/10 | 10.00/10 |
| 4 | `martini.a13` | 75.69 | 14.62/30 | 16.00/20 | 13.75/15 | 13.85/15 | 9.00/10 | 8.47/10 |
| 5 | `v.m.beauty_blog` | 73.04 | 29.62/30 | 14.67/20 | 13.75/15 | 0.00/15 | 5.00/10 | 10.00/10 |
| 6 | `masha_obzor.wb` | 69.32 | 24.96/30 | 18.00/20 | 8.75/15 | 5.19/15 | 8.00/10 | 4.42/10 |
| 7 | `zari.ishikhovaa` | 67.48 | 24.71/30 | 10.00/20 | 12.50/15 | 10.96/15 | 7.00/10 | 2.31/10 |
| 8 | `nikaanow` | 66.54 | 14.79/30 | 16.00/20 | 11.25/15 | 7.50/15 | 7.00/10 | 10.00/10 |
| 9 | `armlilitka` | 66.05 | 11.54/30 | 14.00/20 | 15.00/15 | 13.27/15 | 6.00/10 | 6.24/10 |
| 10 | `llaurraiiam` | 65.55 | 26.54/30 | 10.00/20 | 1.25/15 | 12.12/15 | 6.00/10 | 9.64/10 |
| 11 | `dddinaaaaaa` | 64.77 | 26.54/30 | 10.00/20 | 1.25/15 | 14.42/15 | 7.00/10 | 5.56/10 |
| 12 | `_kate_bruni` | 64.53 | 24.79/30 | 14.00/20 | 11.25/15 | 4.04/15 | 4.00/10 | 6.45/10 |
| 13 | `aida.mixx` | 61.56 | 16.54/30 | 14.00/20 | 15.00/15 | 2.31/15 | 7.00/10 | 6.71/10 |
| 14 | `ninooochka2.0` | 60.24 | 24.79/30 | 8.00/20 | 5.00/15 | 9.81/15 | 7.00/10 | 5.64/10 |
| 15 | `jd_cosm` | 57.94 | 17.96/30 | 8.00/20 | 8.75/15 | 9.23/15 | 4.00/10 | 10.00/10 |
| 16 | `irina.titovaaaa` | 57.82 | 14.71/30 | 10.67/20 | 3.75/15 | 12.69/15 | 6.00/10 | 10.00/10 |
| 17 | `anetboss_` | 56.98 | 11.54/30 | 8.67/20 | 15.00/15 | 5.77/15 | 6.00/10 | 10.00/10 |
| 18 | `juliar_r` | 53.07 | 21.88/30 | 6.67/20 | 6.25/15 | 6.92/15 | 4.00/10 | 7.35/10 |
| 19 | `ksiushabakher` | 52.56 | 21.62/30 | 3.33/20 | 13.75/15 | 4.62/15 | 3.00/10 | 6.24/10 |
| 20 | `mishandkatya` | 52.36 | 11.54/30 | 8.67/20 | 15.00/15 | 1.15/15 | 6.00/10 | 10.00/10 |
| 21 | `bazhenova_alenaa` | 49.60 | 15.96/30 | 8.67/20 | 8.75/15 | 2.88/15 | 6.00/10 | 7.34/10 |
| 22 | `daria_grogulenko` | 46.67 | 14.54/30 | 10.00/20 | 15.00/15 | 0.58/15 | 6.00/10 | 0.55/10 |
| 23 | `janestetsiura` | 41.16 | 12.71/30 | 2.67/20 | 3.75/15 | 8.08/15 | 4.00/10 | 9.95/10 |
| 24 | `krrazalia` | 39.21 | 14.46/30 | 8.00/20 | 0.00/15 | 8.65/15 | 8.00/10 | 0.10/10 |
| 25 | `lv_yana_vl` | 37.25 | 9.96/30 | 0.67/20 | 8.75/15 | 11.54/15 | 5.00/10 | 1.33/10 |
| 26 | `_crazy___unicorn_` | 27.30 | 2.46/30 | 0.67/20 | 0.00/15 | 15.00/15 | 4.00/10 | 5.17/10 |
| 27 | `mari_vls` | 23.05 | 2.46/30 | 0.00/20 | 0.00/15 | 10.38/15 | 4.00/10 | 6.21/10 |

Every component explanation is included in `source_analysis.csv` and
`ideal_creator_profile.json`. The complete formula and thresholds are documented in
`scoring_methodology.md`.

## Human-in-the-loop QA

Pavel detected that visible Excel cell text could contain truncated or stale Instagram usernames while the embedded hyperlink target retained the intended URL. This run found
**6**
display/target mismatches and consistently preferred the target.

Pavel manually recovered and verified
4 corrected profiles:

- `_crazy__unicorn__` → `_crazy___unicorn_`: Manually found and visually confirmed in Instagram by Pavel
- `irinatitovaaa_` → `irina.titovaaaa`: Manually found and visually confirmed in Instagram by Pavel
- `lv_yana_lv` → `lv_yana_vl`: Manually found and visually confirmed in Instagram by Pavel
- `__aparina` → `nikaanow`: Manually found by Pavel and confirmed through matching PR contact @pr.aparina, Telegram link t.me/aparinanika, Tula location, UGC/model/blogger positioning, and successful Apify extraction.

Before the verified `nikaanow` recovery, Pavel rejected the uncertain `aparina_` match
for source `__aparina`, because there was insufficient evidence that it was the same
person. That rejected false lead remains in the QA history; `nikaanow` is now treated as
an accessible public creator with sampled post data.

This improved reliability by recovering only evidence-backed identities, preventing an
uncertain candidate from contaminating the creator cohort, and exposing a systematic
Excel parsing failure. The process improves precision while preserving the decision
history.

## Profiles excluded from quantitative analysis

| Profile | Category | Why excluded |
|---|---|---|
| `nike` | brand_reference | Nike/Apple brand reference: used only for content-format alignment signals. |
| `shalafaeva.al` | private | Private profile: latest-post metrics are unavailable and were not inferred. |
| `nev_pollyy` | unresolved_not_found | Source status is not_found; missing values were preserved. |
| `mademoiselle._.marie` | insufficient_data | Insufficient public data for robust quantitative scoring (requires username, positive followers, 3+ sampled posts, and 6+ posts with usable likes and comments; found 0 usable engagement posts). |
| `apple` | brand_reference | Nike/Apple brand reference: used only for content-format alignment signals. |
| `19.voron` | unresolved_not_found | Source status is not_found; missing values were preserved. |
| `miysta_fatt_` | unresolved_not_found | Source status is not_found; missing values were preserved. |

All excluded records remain present in `source_analysis.csv` and the data-quality report.
Missing metrics are blank/null; they are not imputed.

## Data quality

- Workbook references: **34**
- JSON records: **34**
- Workbook/JSON reconciliation after verified audit corrections:
  **true**
- Typed validation issues: **15**
- Duplicate normalized usernames: **0**

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
