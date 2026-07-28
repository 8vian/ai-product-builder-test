# Source-profile scoring methodology

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
