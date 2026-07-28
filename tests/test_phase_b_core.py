from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_product_builder.analysis import average_tie_percentile
from ai_product_builder.phase_b.eligibility import evaluate_candidate_eligibility
from ai_product_builder.phase_b.enrichment import calculate_candidate_metrics
from ai_product_builder.phase_b.evidence import (
    collect_signal_evidence,
    signal_is_present,
)
from ai_product_builder.phase_b.exclusions import ExclusionRegistry
from ai_product_builder.phase_b.models import (
    CampaignBrief,
    CandidateIdentity,
    CandidateMetrics,
    CandidateResult,
    CreatorProfile,
    OfferDraft,
    RecentPost,
    ScoreComponent,
    SignalEvidence,
    parse_datetime,
)
from ai_product_builder.phase_b.normalization import (
    canonicalize_profile_url,
    normalize_username,
)
from ai_product_builder.phase_b.offers import (
    generate_deterministic_offer,
    validate_offer_draft,
)
from ai_product_builder.phase_b.queries import generate_queries
from ai_product_builder.phase_b.scoring import (
    COMPONENT_MAXIMUMS,
    PhaseAReferenceCohort,
    calculate_discovery_confidence,
    load_phase_a_reference,
    percentile_against_reference,
    score_candidate,
)
from ai_product_builder.phase_b.selection import rank_candidates


ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 1, 31, tzinfo=timezone.utc)


def _campaign(*, language: str = "en") -> CampaignBrief:
    return CampaignBrief(
        brand_name="LD Latte",
        product_name="new collection dress",
        product_category="women's clothing",
        barter_item="a product in exchange for agreed content",
        desired_content_format="Reels or a native review",
        language=language,
        tone="friendly and pressure-free",
    )


def _post(
    index: int,
    *,
    caption: str = "Fashion dress review and try-on",
    likes: int | None = 100,
    comments: int | None = 10,
    age_days: float = 10,
    post_format: str = "short_video",
    url: str | None = None,
    paid_partnership: bool = False,
    mentions: tuple[str, ...] = (),
) -> RecentPost:
    return RecentPost(
        post_id=f"post-{index}",
        url=url
        if url is not None
        else f"https://www.instagram.com/p/test{index}/",
        caption=caption,
        likes=likes,
        comments=comments,
        timestamp=AS_OF - timedelta(days=age_days),
        post_format=post_format,
        mentions=mentions,
        paid_partnership=paid_partnership,
    )


def _profile(
    username: str = "new.creator__",
    *,
    posts: tuple[RecentPost, ...] | None = None,
    biography: str = "fashion creator",
    followers: int | None = 10_000,
    private: bool | None = False,
    accessible: bool | None = True,
    query_ids: tuple[str, ...] = ("q1",),
    identity_conflict: bool = False,
    identity_confidence: float = 0.9,
) -> CreatorProfile:
    normalized = normalize_username(username)
    assert normalized is not None
    url = canonicalize_profile_url(username)
    assert url is not None
    identity = CandidateIdentity(
        platform="instagram",
        username=username,
        normalized_username=normalized,
        profile_url=url,
        canonical_profile_url=url,
        query_ids=query_ids,
        identity_conflict=identity_conflict,
    )
    return CreatorProfile(
        identity=identity,
        full_name="New Creator",
        biography=biography,
        followers=followers,
        posts_count=120,
        private=private,
        accessible=accessible,
        recent_posts=posts
        if posts is not None
        else tuple(_post(index) for index in range(6)),
        provider="fixture",
        provider_identity_confidence=identity_confidence,
        query_ids=query_ids,
        collected_at=AS_OF,
    )


def _direct(
    signal_type: str,
    *,
    reference: str = "post-0",
    field: str = "recent_posts.caption",
) -> SignalEvidence:
    return SignalEvidence(
        signal_type=signal_type,
        source_field=field,
        source_reference=reference,
        evidence_text=f"Observed {signal_type}",
        observation_type="direct",
        url="https://www.instagram.com/p/test0/",
    )


def _result(
    username: str,
    *,
    score: float = 50.0,
    confidence: float = 0.8,
    completeness: float = 1.0,
    engagement: float | None = 2.0,
    eligibility_status: str = "eligible",
) -> CandidateResult:
    component = ScoreComponent(
        name="test_total",
        score=score,
        max_score=100.0,
        explanation="test fixture",
    )
    return CandidateResult(
        platform="instagram",
        username=username,
        profile_url=canonicalize_profile_url(username) or "",
        followers=10_000,
        median_likes=190.0,
        median_comments=10.0,
        engagement_rate=engagement,
        usable_posts=6,
        sampled_posts=6,
        data_completeness=completeness,
        short_video_share=0.5,
        last_post_date=AS_OF - timedelta(days=5),
        score=score,
        score_components=(component,),
        selection_explanation="fixture",
        evidence=(),
        recent_post_url="https://www.instagram.com/p/test0/",
        barter_offer="Draft",
        manual_verification_status="pending",
        verification_notes="",
        discovery_confidence=confidence,
        eligibility_status=eligibility_status,
        eligibility_reasons=(),
        query_ids=("q1",),
        provider="fixture",
        collected_at=AS_OF,
        offer_generation_mode="deterministic_template",
        source_exclusion_check="clear",
    )


def test_query_generation_is_deterministic_and_never_uses_source_usernames() -> None:
    document = json.loads(
        (ROOT / "output" / "ideal_creator_profile.json").read_text(encoding="utf-8")
    )
    first = generate_queries(document, _campaign())
    second = generate_queries(document, _campaign())

    assert first == second
    assert [query.query_id for query in first] == [
        "fashion_style_01",
        "beauty_lifestyle_01",
        "ugc_creator_01",
        "marketplace_reviews_01",
        "reels_integrations_01",
    ]
    assert all(query.audience_min == 9781 for query in first)
    assert all(query.audience_max == 82754 for query in first)

    source_usernames = {
        normalize_username(item.get("username"))
        for item in json.loads(
            (ROOT / "data" / "raw" / "instagram_profiles.json").read_text(
                encoding="utf-8"
            )
        )
    }
    generated_terms = {
        normalize_username(term)
        for query in first
        for term in (*query.search_terms, *query.hashtags)
    }
    assert not ({item for item in source_usernames if item} & generated_terms)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  @_crazy___unicorn_  ", "_crazy___unicorn_"),
        (
            "https://www.instagram.com/irina.titovaaaa/?hl=ru#bio",
            "irina.titovaaaa",
        ),
        ("https://instagram.com/mademoiselle._.marie/", "mademoiselle._.marie"),
        ("LV_YANA_VL", "lv_yana_vl"),
        ("__aparina", "__aparina"),
    ],
)
def test_username_normalization_preserves_dots_and_underscores(
    raw: str, expected: str
) -> None:
    assert normalize_username(raw) == expected
    assert canonicalize_profile_url(raw) == (
        f"https://www.instagram.com/{expected}/"
    )


def test_profile_and_post_urls_are_not_confused_during_normalization() -> None:
    assert normalize_username("https://www.instagram.com/p/abc/") is None
    assert normalize_username("https://example.com/_creator_/") is None
    assert normalize_username("@bad-name") is None


def test_exclusion_registry_covers_sources_aliases_replacements_and_false_lead() -> None:
    registry = ExclusionRegistry.from_files(
        ROOT / "data" / "raw" / "instagram_profiles.json",
        ROOT / "data" / "raw" / "manual_verification_audit.json",
    )

    expected = {
        "_crazy__unicorn__": "historical_alias",
        "_crazy___unicorn_": "verified_replacement",
        "irinatitovaaa_": "historical_alias",
        "irina.titovaaaa": "verified_replacement",
        "lv_yana_lv": "historical_alias",
        "lv_yana_vl": "verified_replacement",
        "__aparina": "historical_alias",
        "nikaanow": "verified_replacement",
        "aparina_": "rejected_false_lead",
        "nev_pollyy": "phase_a_unresolved",
        "19.voron": "phase_a_unresolved",
        "miysta_fatt_": "phase_a_unresolved",
        "nike": "brand_reference",
        "apple": "brand_reference",
    }
    for username, reason in expected.items():
        match = registry.match(username=username)
        assert match is not None, username
        assert reason in match.reasons
        assert match.normalized_username == normalize_username(username)

    assert registry.match(username="genuinely.new_creator") is None


def test_negative_like_sentinel_stays_missing_and_does_not_enter_engagement() -> None:
    raw_posts = [
        {
            "id": f"p{index}",
            "url": f"https://www.instagram.com/p/p{index}/",
            "caption": "fashion review",
            "likes": -1 if index == 6 else 100,
            "comments": 5,
            "timestamp": (AS_OF - timedelta(days=index + 1)).isoformat(),
            "post_format": "reel",
        }
        for index in range(7)
    ]
    posts = tuple(
        RecentPost.from_dict(item, index=index)
        for index, item in enumerate(raw_posts)
    )
    assert posts[-1].likes is None
    assert "negative sentinel" in posts[-1].validation_issues[0]

    metrics = calculate_candidate_metrics(_profile(posts=posts), as_of=AS_OF)
    assert metrics.sampled_posts == 7
    assert metrics.usable_posts == 6
    assert metrics.data_completeness == pytest.approx(6 / 7)
    assert metrics.median_likes == 100
    assert metrics.engagement_rate == pytest.approx(1.05)


def test_partial_missing_engagement_can_only_reduce_adjusted_component() -> None:
    complete_posts = tuple(
        _post(index, likes=100, comments=0, age_days=index + 1)
        for index in range(7)
    )
    partial_posts = (*complete_posts[:6], _post(6, likes=None, comments=0, age_days=7))
    complete_profile = _profile(posts=complete_posts)
    partial_profile = _profile(posts=partial_posts)
    reference = PhaseAReferenceCohort((0.5, 1.0, 1.5), None, "fixture", 3)

    complete_metrics = calculate_candidate_metrics(complete_profile, as_of=AS_OF)
    partial_metrics = calculate_candidate_metrics(partial_profile, as_of=AS_OF)
    complete_score = score_candidate(
        complete_profile,
        complete_metrics,
        collect_signal_evidence(complete_profile),
        reference,
    )
    partial_score = score_candidate(
        partial_profile,
        partial_metrics,
        collect_signal_evidence(partial_profile),
        reference,
    )
    complete_engagement = next(
        item.score for item in complete_score.components if item.name == "engagement"
    )
    partial_engagement = next(
        item.score for item in partial_score.components if item.name == "engagement"
    )

    assert complete_metrics.engagement_rate == partial_metrics.engagement_rate
    assert complete_engagement == 7.5
    assert partial_engagement == pytest.approx(round(7.5 * 6 / 7, 2))
    assert partial_engagement < complete_engagement


def test_eligibility_accepts_six_usable_posts_and_90_day_boundary() -> None:
    posts = tuple(_post(index, age_days=90) for index in range(6))
    profile = _profile(posts=posts)
    metrics = calculate_candidate_metrics(profile, as_of=AS_OF)
    evidence = collect_signal_evidence(profile)

    decision = evaluate_candidate_eligibility(
        profile, metrics, evidence, as_of=AS_OF
    )
    assert decision.eligible is True
    assert decision.status.value == "eligible"


@pytest.mark.parametrize(
    ("posts", "expected_status", "reason_fragment"),
    [
        (
            tuple(_post(index, age_days=10) for index in range(5)),
            "insufficient_data",
            "insufficient_engagement_data:5<6",
        ),
        (
            tuple(_post(index, age_days=90 + 1 / 86_400) for index in range(6)),
            "ineligible",
            "latest_post_older_than_90_days",
        ),
    ],
)
def test_eligibility_rejects_below_six_and_beyond_recency_boundary(
    posts: tuple[RecentPost, ...], expected_status: str, reason_fragment: str
) -> None:
    profile = _profile(posts=posts)
    metrics = calculate_candidate_metrics(profile, as_of=AS_OF)
    decision = evaluate_candidate_eligibility(
        profile,
        metrics,
        collect_signal_evidence(profile),
        as_of=AS_OF,
    )

    assert decision.eligible is False
    assert decision.status.value == expected_status
    assert any(reason_fragment in reason for reason in decision.reasons)


def test_recent_unusable_post_cannot_hide_stale_usable_engagement() -> None:
    posts = (
        _post(0, age_days=1, likes=None, comments=5),
        *tuple(
            _post(index, age_days=100 + index)
            for index in range(1, 7)
        ),
    )
    profile = _profile(posts=posts)
    metrics = calculate_candidate_metrics(profile, as_of=AS_OF)
    decision = evaluate_candidate_eligibility(
        profile,
        metrics,
        collect_signal_evidence(profile),
        as_of=AS_OF,
    )

    assert metrics.usable_posts == 6
    assert decision.eligible is False
    assert any(
        "older_than_90_days" in reason
        for reason in decision.reasons
    )


def test_contact_commercial_ugc_marketplace_and_native_are_independent() -> None:
    contact_only = _profile(
        biography="Contact: creator@example.com, Telegram t.me/example",
        posts=tuple(_post(i, caption="daily thoughts") for i in range(6)),
    )
    contact_evidence = collect_signal_evidence(contact_only)
    assert signal_is_present(contact_evidence, "contact")
    for signal in ("commercial_pr", "ugc", "marketplace", "native_product_integration"):
        assert not signal_is_present(contact_evidence, signal)

    paid_only = _profile(
        biography="personal diary",
        posts=(
            _post(0, caption="A quiet day", paid_partnership=True),
            *tuple(_post(i, caption="A quiet day") for i in range(1, 6)),
        ),
    )
    paid_evidence = collect_signal_evidence(paid_only)
    assert signal_is_present(paid_evidence, "commercial_pr")
    assert not signal_is_present(paid_evidence, "native_product_integration")

    native_only = _profile(
        biography="personal diary",
        posts=tuple(_post(i, caption="Unboxing and try-on review") for i in range(6)),
    )
    native_evidence = collect_signal_evidence(native_only)
    assert signal_is_present(native_evidence, "native_product_integration")
    assert not signal_is_present(native_evidence, "commercial_pr")


def test_explicit_pr_text_is_independent_commercial_evidence() -> None:
    profile = _profile(
        biography="PR portfolio",
        posts=tuple(_post(i, caption="daily thoughts") for i in range(6)),
    )
    evidence = collect_signal_evidence(profile)

    assert signal_is_present(evidence, "commercial_pr")
    assert any(
        item.source_field == "biography"
        and "pr" in item.evidence_text.casefold()
        for item in evidence["commercial_pr"]
    )
    assert not signal_is_present(evidence, "contact")
    assert not signal_is_present(evidence, "native_product_integration")


def test_every_observed_signal_has_field_reference_reason_and_provenance() -> None:
    profile = _profile(
        biography="UGC creator; advertising collaboration; creator@example.com",
        posts=tuple(
            _post(
                i,
                caption="Fashion Wildberries unboxing and product review",
                paid_partnership=i == 0,
                mentions=("@brand",),
            )
            for i in range(6)
        ),
    )
    evidence = collect_signal_evidence(profile)

    for signal in (
        "contact",
        "commercial_pr",
        "ugc",
        "marketplace",
        "native_product_integration",
    ):
        assert evidence[signal], signal
        assert all(item.signal_type == signal for item in evidence[signal])
        assert all(item.source_field for item in evidence[signal])
        assert all(item.source_reference for item in evidence[signal])
        assert all(item.evidence_text for item in evidence[signal])
        assert all(item.observation_type == "direct" for item in evidence[signal])


def test_phase_b_scoring_components_exactly_reuse_phase_a_definitions() -> None:
    posts = (
        _post(0, post_format="short_video"),
        _post(1, post_format="image"),
    )
    profile = _profile(posts=posts, followers=5_000)
    metrics = CandidateMetrics(
        followers=5_000,
        median_likes=90,
        median_comments=10,
        engagement_rate=2.0,
        usable_posts=6,
        sampled_posts=12,
        data_completeness=0.5,
        short_video_share=0.5,
        last_post_date=AS_OF - timedelta(days=7),
        posts_per_week=1.0,
        recency_days=7.0,
    )
    evidence = {
        "fashion": (_direct("fashion"),),
        "beauty": (),
        "lifestyle": (),
        "ugc": (_direct("ugc"),),
        "marketplace": (_direct("marketplace"),),
        "commercial_pr": (_direct("commercial_pr"),),
        "contact": (_direct("contact", reference="profile", field="biography"),),
        "native_product_integration": tuple(
            _direct("native_product_integration", reference=f"post-{index}")
            for index in range(6)
        ),
    }
    reference = PhaseAReferenceCohort(
        engagement_rates=(1.0, 2.0, 3.0),
        brand_short_video_reference=0.5,
        source_csv="frozen.csv",
        eligible_creator_count=3,
    )

    result = score_candidate(profile, metrics, evidence, reference)
    components = {item.name: item.score for item in result.components}

    assert COMPONENT_MAXIMUMS == {
        "content_and_aesthetic_fit": 30.0,
        "native_product_integration_potential": 20.0,
        "short_video_consistency": 15.0,
        "engagement": 15.0,
        "barter_feasibility": 10.0,
        "recent_activity": 10.0,
    }
    assert components == {
        "content_and_aesthetic_fit": 21.0,
        "native_product_integration_potential": 20.0,
        "short_video_consistency": 7.5,
        "engagement": 3.75,
        "barter_feasibility": 10.0,
        "recent_activity": 8.0,
    }
    assert result.score == 70.25
    assert result.score == sum(components.values())


def test_frozen_phase_a_percentiles_match_phase_a_tie_rule_and_ignore_new_pool() -> None:
    reference = load_phase_a_reference(
        ROOT / "output" / "source_analysis.csv",
        ROOT / "output" / "ideal_creator_profile.json",
    )
    indexed = dict(enumerate(reference.engagement_rates))
    phase_a = average_tie_percentile(indexed)

    for index, value in indexed.items():
        assert percentile_against_reference(
            value, reference.engagement_rates
        ) == pytest.approx(phase_a[index])

    frozen_value = percentile_against_reference(2.0, (1.0, 3.0))
    assert frozen_value == 0.5
    assert percentile_against_reference(2.0, (1.0, 3.0)) == frozen_value


def test_phase_b_score_matches_every_frozen_phase_a_eligible_creator() -> None:
    raw_profiles = json.loads(
        (ROOT / "data/raw/instagram_profiles.json").read_text(encoding="utf-8")
    )
    ideal = json.loads(
        (ROOT / "output/ideal_creator_profile.json").read_text(encoding="utf-8")
    )
    as_of = parse_datetime(ideal["analysis_as_of"])
    assert as_of is not None
    with (ROOT / "output/source_analysis.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        phase_a_rows = {
            row["username"]: row
            for row in csv.DictReader(handle)
            if row["status"] == "creator"
        }
    reference = load_phase_a_reference(
        ROOT / "output/source_analysis.csv",
        ROOT / "output/ideal_creator_profile.json",
    )

    compared = 0
    for raw in raw_profiles:
        username = raw.get("username")
        if username not in phase_a_rows:
            continue
        normalized = normalize_username(username)
        profile_url = canonicalize_profile_url(username)
        assert normalized is not None and profile_url is not None
        posts = tuple(
            RecentPost.from_dict(item, index=index)
            for index, item in enumerate(raw.get("latestPosts") or [])
        )
        external_urls = tuple(
            item["url"]
            for item in (raw.get("externalUrls") or [])
            if isinstance(item, dict) and item.get("url")
        )
        profile = CreatorProfile(
            identity=CandidateIdentity(
                platform="instagram",
                username=username,
                normalized_username=normalized,
                profile_url=profile_url,
                canonical_profile_url=profile_url,
            ),
            full_name=raw.get("fullName") or "",
            biography=raw.get("biography") or "",
            followers=raw.get("followersCount"),
            posts_count=raw.get("postsCount"),
            private=raw.get("private"),
            accessible=True,
            recent_posts=posts,
            external_urls=external_urls,
            provider="phase_a_parity_fixture",
            collected_at=as_of,
        )
        metrics = calculate_candidate_metrics(profile, as_of=as_of)
        score = score_candidate(
            profile,
            metrics,
            collect_signal_evidence(profile),
            reference,
        )
        phase_a = phase_a_rows[username]
        expected_components = {
            "content_and_aesthetic_fit": float(
                phase_a["content_and_aesthetic_fit_score"]
            ),
            "native_product_integration_potential": float(
                phase_a["native_product_integration_potential_score"]
            ),
            "short_video_consistency": float(
                phase_a["short_video_consistency_score"]
            ),
            "engagement": float(phase_a["engagement_score"]),
            "barter_feasibility": float(phase_a["barter_feasibility_score"]),
            "recent_activity": float(phase_a["recent_activity_score"]),
        }
        assert {
            item.name: item.score for item in score.components
        } == expected_components, username
        assert score.score == float(phase_a["total_score"]), username
        compared += 1

    assert compared == len(phase_a_rows) == reference.eligible_creator_count


def test_percentile_small_cohorts_and_ties_are_deterministic() -> None:
    assert percentile_against_reference(4, (5,)) == 0
    assert percentile_against_reference(5, (5,)) == 1
    assert percentile_against_reference(6, (5,)) == 1
    assert percentile_against_reference(2, (1, 2, 2, 4)) == pytest.approx(0.5)
    assert percentile_against_reference(3, (1, 2, 2, 4)) == pytest.approx(0.75)


def test_discovery_confidence_uses_only_its_four_documented_terms() -> None:
    profile = _profile(query_ids=("q1", "q2", "q3"), identity_confidence=0.8)
    evidence = {
        "fashion": (_direct("fashion"),),
        "beauty": (_direct("beauty"),),
        "lifestyle": (_direct("lifestyle"),),
    }
    confidence = calculate_discovery_confidence(profile, evidence)

    assert confidence.components == {
        "provider_identity_confidence": 0.8,
        "multi_query_support": 1.0,
        "profile_and_post_link_verification": 1.0,
        "query_relevance_evidence": 1.0,
    }
    assert confidence.score == pytest.approx(0.92)


def test_deterministic_ranking_uses_all_tie_breakers_in_order() -> None:
    candidates = [
        _result("zeta", score=51),
        _result("gamma", confidence=0.9),
        _result("beta", completeness=1.0, engagement=2.0),
        _result("delta", completeness=0.9, engagement=3.0),
        _result("alpha", completeness=0.9, engagement=1.0),
        _result("_alpha", completeness=0.9, engagement=1.0),
        _result("excluded", score=99, eligibility_status="ineligible"),
    ]

    assert [item.username for item in rank_candidates(candidates)] == [
        "zeta",
        "gamma",
        "beta",
        "delta",
        "_alpha",
        "alpha",
    ]


def test_offer_is_grounded_in_actual_recent_post_and_starts_pending() -> None:
    profile = _profile(
        posts=tuple(
            _post(i, caption="A linen dress try-on for summer") for i in range(6)
        )
    )
    evidence = collect_signal_evidence(profile)
    offer = generate_deterministic_offer(
        profile, _campaign(), evidence, as_of=AS_OF
    )

    assert offer.recent_post_url == profile.recent_posts[0].url
    assert offer.recent_post_url in offer.text
    assert "A linen dress try-on for summer" in offer.text
    assert "new collection dress" in offer.text
    assert offer.manual_verification_status.value == "pending"
    assert any(
        item.signal_type == "offer_personalization"
        and item.observation_type == "direct"
        and item.url == offer.recent_post_url
        for item in offer.evidence
    )
    assert validate_offer_draft(
        offer, profile=profile, campaign=_campaign()
    ) == ()


def test_offer_validator_rejects_unsupported_claims_and_missing_provenance() -> None:
    profile = _profile()
    campaign = _campaign()
    post_url = profile.recent_posts[0].url
    assert post_url is not None
    text = (
        f"Hello, you already love our brand and your audience guarantees results. "
        f"{post_url} {campaign.brand_name} {campaign.product_name} "
        f"{campaign.barter_item} {campaign.desired_content_format}"
    )
    draft = OfferDraft(
        text=text,
        generation_mode="deterministic_template",
        recent_post_url=post_url,
        evidence=(),
        manual_verification_status="pending",
    )

    errors = validate_offer_draft(draft, profile=profile, campaign=campaign)
    assert "personalized_claim_has_no_direct_evidence" in errors
    assert any(error.startswith("unsupported_claim:") for error in errors)


@pytest.mark.parametrize("status", ["sent", "drafted", ""])
def test_manual_status_enum_rejects_unapproved_states(status: str) -> None:
    with pytest.raises(ValueError):
        OfferDraft(
            text="draft",
            generation_mode="deterministic_template",
            recent_post_url="https://www.instagram.com/p/test/",
            manual_verification_status=status,
        )
