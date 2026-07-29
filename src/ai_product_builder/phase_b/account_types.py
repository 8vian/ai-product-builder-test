"""Auditable account-type and topic assessment for Phase B candidates."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .models import CreatorProfile, SignalEvidence


class AccountType(StrEnum):
    PERSONAL_CREATOR = "personal_creator"
    BRAND = "brand"
    MARKETPLACE = "marketplace"
    STORE = "store"
    SHOWROOM = "showroom"
    AGENCY_OR_PLATFORM = "agency_or_platform"
    THEMATIC_NON_PERSONAL_PAGE = "thematic_non_personal_page"
    PROFESSIONAL_PORTFOLIO = "professional_portfolio"
    UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class AccountTypeAssessment:
    account_type: AccountType
    theme_relevant: bool
    explanation: str
    evidence: tuple[SignalEvidence, ...]
    relevant_dimensions: tuple[str, ...] = ()
    negative_topics: tuple[str, ...] = ()
    manual_override: bool = False
    commercial_conflict: bool = False
    own_fashion_brand: bool = False
    own_clothing_store_or_showroom: bool = False
    professional_portfolio: bool = False

    @property
    def eligible_account(self) -> bool:
        return (
            self.account_type is AccountType.PERSONAL_CREATOR
            and self.theme_relevant
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_type": self.account_type.value,
            "eligible_account": self.eligible_account,
            "theme_relevant": self.theme_relevant,
            "relevant_dimensions": list(self.relevant_dimensions),
            "negative_topics": list(self.negative_topics),
            "manual_override": self.manual_override,
            "commercial_conflict": self.commercial_conflict,
            "own_fashion_brand": self.own_fashion_brand,
            "own_clothing_store_or_showroom": (
                self.own_clothing_store_or_showroom
            ),
            "professional_portfolio": self.professional_portfolio,
            "explanation": self.explanation,
            "evidence": [item.to_dict() for item in self.evidence],
        }


_OFFICIAL_ACCOUNT_PHRASES = (
    "official account",
    "official brand account",
    "официальный аккаунт",
    "официальная страница",
    "ресми аккаунты",
)
_MARKETPLACE_PHRASES = (
    "marketplace",
    "маркетплейс",
    "wildberries marketplace",
)
_STORE_PHRASES = (
    "online store",
    "shop now",
    "магазин",
    "товар/услуга",
    "товары и услуги",
    "order now",
    "buy now",
    "all product links",
    "product links",
    "артикулы",
    "обзоры и видео",
    "скидки на товары",
)
_SHOWROOM_PHRASES = (
    "шоурум",
    "showroom",
)
_OWN_FASHION_BRAND_PHRASES = (
    "бренд женской одежды",
    "собственный бренд одежды",
    "свой бренд одежды",
    "мой бренд одежды",
    "основатель бренда одежды",
    "создатель бренда одежды",
    "дизайнер и создатель @",
    "основатель @",
    "собственное производство",
    "founder of clothing brand",
    "founder of fashion brand",
)
_OWN_CLOTHING_STORE_PHRASES = (
    "мой магазин одежды",
    "собственный магазин одежды",
    "владелец магазина одежды",
    "владелица магазина одежды",
    "мой шоурум",
    "собственный шоурум",
    "владелец шоурума",
    "владелица шоурума",
    "owner of clothing store",
    "owner of showroom",
)
_PROFESSIONAL_PORTFOLIO_PHRASES = (
    "профессиональное портфолио",
    "портфолио стилиста",
    "художник по костюму",
    "fashion-съём",
    "fashion-съем",
    "стилист для съём",
    "стилист для съем",
    "stylist portfolio",
    "commercial stylist",
    "costume designer",
)
_PORTFOLIO_CREDIT_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:fashion|stylist|photograph(?:y|er)?|model|"
    r"makeup|mua|designer)\s*[:@]",
    re.IGNORECASE,
)
_AGENCY_PLATFORM_PHRASES = (
    "creator platform",
    "connecting brands",
    "connecting creators",
    "influencer platform",
    "creator marketplace",
    "creator agency",
    "influencer agency",
    "help creators grow",
    "help creators monetize",
)
_PERSONAL_PHRASES = (
    "content creator",
    "blogger",
    "makeup artist",
    "beauty expert",
    "vlogger",
    "my life",
    "personally",
    "personnellement",
    "лично от меня",
    "обо мне",
    "визажист",
    "блогер",
    "мама",
    "mom",
    "mother",
    "nurse",
)

_GENERIC_TARGET_TERMS = {
    "fashion": ("fashion", "style", "styling", "мода", "стиль"),
    "beauty": ("beauty", "красота"),
    "lifestyle": ("lifestyle", "лайфстайл"),
}
_SPECIFIC_TARGET_TERMS = {
    "fashion": (
        "clothing",
        "clothes",
        "outfit",
        "lookbook",
        "try-on",
        "try on",
        "haul",
        "wardrobe",
        "dress",
        "dresses",
        "shirt",
        "blouse",
        "skirt",
        "trousers",
        "jeans",
        "myntrafashion",
        "myntradresses",
        "rajasthani_style",
        "одежд",
        "образ",
        "примерк",
        "гардероб",
        "плать",
        "рубаш",
        "джинс",
        "каблук",
    ),
    "beauty": (
        "makeup",
        "skin care",
        "skincare",
        "cosmetic",
        "beauty routine",
        "glam",
        "swatch",
        "braids",
        "hair care",
        "hairstyle",
        "макияж",
        "уход",
        "косметик",
        "визажист",
        "бьюти",
    ),
    "lifestyle": (
        "personal lifestyle",
        "daily life",
        "mom life",
        "family lifestyle",
        "wellness journey",
        "wellness",
        "travel diary",
        "soft life",
    ),
}
_NEGATIVE_TOPIC_TERMS = {
    "pet": (
        "cat",
        "kitten",
        "pet",
        "animal",
        "meow",
        "catlover",
        "кошк",
        "котик",
    ),
    "gaming": (
        "gaming",
        "gamer",
        "free fire",
        "gameplay",
        "stylish mode",
    ),
    "fishing": ("fishing", "рыбал", "рыбач"),
    "electronics_or_stem": (
        "electronics",
        "gadget",
        "mechanical engineer",
        "engineering",
        "stem",
        "iphone unboxing",
    ),
    "b2b_education": (
        "help creators grow",
        "help creators monetize",
        "creator platform",
        "connecting brands",
        "connecting creators",
        "seller education",
        "внутренней рекламы на wildberries",
        "менеджер на wildberries",
    ),
    "media_or_clips": (
        "daily family guy content",
        "lyrics reels",
        "lyrics song",
        "cinema",
        "movie clips",
        "professional beauty media",
        "美妝媒體",
        "cinemas",
        "режиссер",
        "сценарист",
        "тв-програм",
        "новые медиа",
    ),
    "politics_history_religion": (
        "unofficial page of former chief minister",
        "history teacher",
        "daily history",
        "jesus",
        "biblical",
        "oraciones",
        "mahadev",
        "ganesh",
        "jaishreeram",
    ),
    "food_or_gardening": (
        "cooking",
        "recipe",
        "receita",
        "chocolate",
        "frutas",
        "horta",
        "comida",
        "garden",
    ),
}


def _fold(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _contains(text: str, term: str) -> bool:
    folded = _fold(text)
    needle = _fold(term)
    if not needle:
        return False
    if re.fullmatch(r"[a-z0-9_]+", needle):
        return bool(
            re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", folded)
        )
    return needle in folded


def _sources(
    profile: CreatorProfile,
) -> list[tuple[str, str, str, str | None]]:
    result = [
        (
            "biography",
            "profile",
            profile.biography,
            profile.identity.canonical_profile_url,
        ),
        (
            "full_name",
            "profile",
            profile.full_name,
            profile.identity.canonical_profile_url,
        ),
    ]
    for index, post in enumerate(profile.recent_posts):
        result.append(
            (
                "recent_posts.caption",
                post.post_id or post.url or f"index:{index}",
                post.caption,
                post.url,
            )
        )
    return result


def _match_evidence(
    signal_type: str,
    phrases: tuple[str, ...],
    sources: list[tuple[str, str, str, str | None]],
    *,
    reason_prefix: str,
) -> list[SignalEvidence]:
    matches: list[SignalEvidence] = []
    seen: set[tuple[str, str, str]] = set()
    for source_field, source_reference, text, url in sources:
        for phrase in phrases:
            if not _contains(text, phrase):
                continue
            key = (source_field, source_reference, phrase.casefold())
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                SignalEvidence(
                    signal_type=signal_type,
                    source_field=source_field,
                    source_reference=source_reference,
                    evidence_text=f"{reason_prefix}: {phrase}",
                    observation_type="direct",
                    url=url,
                )
            )
    return matches


def _looks_like_personal_creator(
    profile: CreatorProfile,
    sources: list[tuple[str, str, str, str | None]],
) -> tuple[bool, list[SignalEvidence]]:
    evidence = _match_evidence(
        "account_type_personal",
        _PERSONAL_PHRASES,
        sources[:2],
        reason_prefix="Observed personal-author phrase",
    )
    bio = _fold(profile.biography)
    if re.search(r"(^|[.!?]\s+)(i|i'm|i am|my)\b", bio):
        evidence.append(
            SignalEvidence(
                signal_type="account_type_personal",
                source_field="biography",
                source_reference="profile",
                evidence_text="Observed first-person biography wording.",
                observation_type="direct",
                url=profile.identity.canonical_profile_url,
            )
        )
    full_name_words = [
        word
        for word in re.findall(r"[^\W\d_]+", profile.full_name, re.UNICODE)
        if len(word) > 1
    ]
    institutional_name = any(
        _contains(profile.full_name, phrase)
        for phrase in (
            "official",
            "marketplace",
            "store",
            "shop",
            "agency",
            "platform",
            "cinema",
            "media",
            "wildberries",
            "cinemas",
            "обзоры",
            "находки",
            "артикулы",
            "скидки",
            "акции",
        )
    )
    if len(full_name_words) >= 2 and not institutional_name:
        evidence.append(
            SignalEvidence(
                signal_type="account_type_personal",
                source_field="full_name",
                source_reference="profile",
                evidence_text=(
                    f"Observed person-like profile name: {profile.full_name}"
                ),
                observation_type="derived",
                url=profile.identity.canonical_profile_url,
            )
        )
    return bool(evidence), evidence


def _target_theme_evidence(
    sources: list[tuple[str, str, str, str | None]],
) -> tuple[list[SignalEvidence], tuple[str, ...]]:
    evidence: list[SignalEvidence] = []
    dimensions: set[str] = set()
    for source_field, source_reference, text, url in sources:
        generic_dimensions = {
            dimension
            for dimension, terms in _GENERIC_TARGET_TERMS.items()
            if any(_contains(text, term) for term in terms)
        }
        for dimension, terms in _SPECIFIC_TARGET_TERMS.items():
            matched = [term for term in terms if _contains(text, term)]
            if not matched:
                continue
            dimensions.add(dimension)
            evidence.append(
                SignalEvidence(
                    signal_type=dimension,
                    source_field=source_field,
                    source_reference=source_reference,
                    evidence_text=(
                        f"Observed direct {dimension} evidence: "
                        + ", ".join(matched[:5])
                    ),
                    observation_type="direct",
                    url=url,
                )
            )
        # A single generic word such as "beauty", "style", or "creator" is
        # intentionally insufficient. Two distinct target dimensions in the
        # same source form a direct, auditable positioning statement.
        if len(generic_dimensions) >= 2:
            dimensions.update(generic_dimensions)
            for dimension in sorted(generic_dimensions):
                evidence.append(
                    SignalEvidence(
                        signal_type=dimension,
                        source_field=source_field,
                        source_reference=source_reference,
                        evidence_text=(
                            "Observed multi-dimensional target positioning: "
                            + ", ".join(sorted(generic_dimensions))
                        ),
                        observation_type="direct",
                        url=url,
                    )
                )
    return evidence, tuple(sorted(dimensions))


def _negative_topic_evidence(
    sources: list[tuple[str, str, str, str | None]],
) -> tuple[list[SignalEvidence], tuple[str, ...]]:
    evidence: list[SignalEvidence] = []
    dominant: list[str] = []
    for topic, terms in _NEGATIVE_TOPIC_TERMS.items():
        matches = _match_evidence(
            "target_theme_negative",
            terms,
            sources,
            reason_prefix=f"Observed {topic} topic phrase",
        )
        bio_or_name_match = any(
            item.source_field in {"biography", "full_name"} for item in matches
        )
        post_match_count = len(
            {
                item.source_reference
                for item in matches
                if item.source_field == "recent_posts.caption"
            }
        )
        threshold = 3
        if bio_or_name_match or post_match_count >= threshold:
            dominant.append(topic)
            evidence.extend(matches)
    return evidence, tuple(sorted(dominant))


def _professional_portfolio_evidence(
    profile: CreatorProfile,
    sources: list[tuple[str, str, str, str | None]],
) -> list[SignalEvidence]:
    positioning = _match_evidence(
        "professional_portfolio",
        _PROFESSIONAL_PORTFOLIO_PHRASES,
        sources[:2],
        reason_prefix="Observed professional-portfolio positioning",
    )
    credit_posts = [
        post
        for post in profile.recent_posts
        if len(_PORTFOLIO_CREDIT_PATTERN.findall(post.caption)) >= 2
    ]
    explicit_portfolio = any(
        "портфолио" in item.evidence_text.casefold()
        or "portfolio" in item.evidence_text.casefold()
        for item in positioning
    )
    if not positioning or (len(credit_posts) < 3 and not explicit_portfolio):
        return []
    result = list(positioning)
    result.append(
        SignalEvidence(
            signal_type="professional_portfolio",
            source_field="recent_posts.caption",
            source_reference="portfolio_credit_sample",
            evidence_text=(
                "Observed a commercial-production credit pattern in "
                f"{len(credit_posts)} sampled posts; this supports portfolio "
                "classification rather than regular creator-led blogging."
            ),
            observation_type="derived",
            url=(
                credit_posts[0].url
                if credit_posts
                else profile.identity.canonical_profile_url
            ),
        )
    )
    return result


def assess_account_type(
    profile: CreatorProfile,
    *,
    manual_override: Mapping[str, Any] | None = None,
) -> AccountTypeAssessment:
    """Classify account type and topical fit without deriving one from another."""

    sources = _sources(profile)
    personal, personal_evidence = _looks_like_personal_creator(profile, sources)
    theme_evidence, relevant_dimensions = _target_theme_evidence(sources)
    negative_evidence, negative_topics = _negative_topic_evidence(sources)

    official = _match_evidence(
        "account_type_negative",
        _OFFICIAL_ACCOUNT_PHRASES,
        sources[:2],
        reason_prefix="Observed official-account phrase",
    )
    marketplace = _match_evidence(
        "account_type_negative",
        _MARKETPLACE_PHRASES,
        sources[:2],
        reason_prefix="Observed marketplace phrase",
    )
    store = _match_evidence(
        "account_type_negative",
        _STORE_PHRASES,
        sources[:2],
        reason_prefix="Observed store/shop phrase",
    )
    showroom = _match_evidence(
        "account_type_negative",
        _SHOWROOM_PHRASES,
        sources[:2],
        reason_prefix="Observed showroom phrase",
    )
    own_brand = _match_evidence(
        "commercial_conflict",
        _OWN_FASHION_BRAND_PHRASES,
        sources[:2],
        reason_prefix="Observed own-fashion-brand evidence",
    )
    own_store = _match_evidence(
        "commercial_conflict",
        _OWN_CLOTHING_STORE_PHRASES,
        sources[:2],
        reason_prefix="Observed own clothing store/showroom evidence",
    )
    agency = _match_evidence(
        "account_type_negative",
        _AGENCY_PLATFORM_PHRASES,
        sources[:2],
        reason_prefix="Observed agency/platform phrase",
    )
    portfolio = _professional_portfolio_evidence(profile, sources)

    combined_profile_text = " ".join(
        (profile.identity.username, profile.full_name, profile.biography)
    )
    wildberries_regional = (
        _contains(combined_profile_text, "wildberries")
        and any(
            _contains(combined_profile_text, marker)
            for marker in (
                "belarus",
                "kazakhstan",
                "беларус",
                "қазақстан",
                "казахстан",
                "региональный аккаунт бренда",
            )
        )
    )
    if wildberries_regional:
        marketplace.append(
            SignalEvidence(
                signal_type="account_type_negative",
                source_field="biography",
                source_reference="profile",
                evidence_text=(
                    "Observed Wildberries linked to Belarus/Kazakhstan or a "
                    "regional brand-account description."
                ),
                observation_type="direct",
                url=profile.identity.canonical_profile_url,
            )
        )

    username_key = profile.identity.username.casefold()
    wildberries_listing_account = (
        "wildberries" in username_key
        and (
            username_key
            in {
                "wildberriesru",
                "uz_wildberries",
                "by.wildberries",
                "kz.wildberries",
            }
            or any(
                _contains(combined_profile_text, marker)
                for marker in (
                    "артикулы",
                    "обзоры",
                    "скидки на товары",
                    "promokod",
                    "buyurtma",
                    "заказ",
                    "товары",
                )
            )
        )
    )
    if wildberries_listing_account:
        marketplace.append(
            SignalEvidence(
                signal_type="account_type_negative",
                source_field="username_and_profile",
                source_reference="profile",
                evidence_text=(
                    "Observed a Wildberries-branded marketplace, regional, "
                    "review, promotion, or product-listing account."
                ),
                observation_type="derived",
                url=profile.identity.canonical_profile_url,
            )
        )

    media_page = any(
        topic in negative_topics
        for topic in (
            "pet",
            "gaming",
            "media_or_clips",
            "politics_history_religion",
        )
    )
    direct_personal = any(
        item.observation_type == "direct" for item in personal_evidence
    )
    if agency:
        account_type = AccountType.AGENCY_OR_PLATFORM
        type_evidence = agency
        type_reason = "The profile describes an agency, B2B service, or creator platform."
    elif official:
        account_type = AccountType.BRAND
        type_evidence = official
        type_reason = "The profile explicitly identifies itself as an official brand account."
    elif marketplace:
        account_type = AccountType.MARKETPLACE
        type_evidence = marketplace
        type_reason = "The profile describes a marketplace or regional marketplace account."
    elif own_brand:
        account_type = AccountType.BRAND
        type_evidence = own_brand
        type_reason = (
            "The profile directly describes its own clothing/fashion brand "
            "or production business."
        )
    elif showroom or own_store:
        account_type = AccountType.SHOWROOM
        type_evidence = [*showroom, *own_store]
        type_reason = (
            "The profile directly describes a clothing showroom or ownership "
            "of a clothing retail business."
        )
    elif store and not direct_personal:
        account_type = AccountType.STORE
        type_evidence = store
        type_reason = "The profile is presented as a store/shop rather than an individual author."
    elif portfolio:
        account_type = AccountType.PROFESSIONAL_PORTFOLIO
        type_evidence = portfolio
        type_reason = (
            "The saved bio and recurring production-credit captions describe "
            "a professional portfolio, not regular creator-led blogging."
        )
    elif media_page:
        account_type = AccountType.THEMATIC_NON_PERSONAL_PAGE
        type_evidence = negative_evidence
        type_reason = "The sampled account is a dominant thematic/media page, not a suitable personal creator."
    elif personal:
        account_type = AccountType.PERSONAL_CREATOR
        type_evidence = personal_evidence
        type_reason = "The profile contains auditable personal-author evidence."
    elif theme_evidence:
        account_type = AccountType.THEMATIC_NON_PERSONAL_PAGE
        type_evidence = theme_evidence
        type_reason = "Relevant topical words exist, but no suitable personal author is evidenced."
    else:
        account_type = AccountType.UNCLEAR
        type_evidence = ()
        type_reason = "The saved profile does not provide enough evidence to identify a personal creator."

    theme_relevant = bool(theme_evidence) and not negative_topics
    all_evidence = [
        *type_evidence,
        *theme_evidence,
        *negative_evidence,
        *own_brand,
        *own_store,
        *portfolio,
    ]
    manual = False
    if manual_override is not None:
        raw_type = manual_override.get("account_type")
        if raw_type:
            account_type = AccountType(str(raw_type))
        if "theme_relevant" in manual_override:
            theme_relevant = bool(manual_override["theme_relevant"])
        reason = str(manual_override.get("reason") or "").strip()
        if reason:
            all_evidence.append(
                SignalEvidence(
                    signal_type="manual_review",
                    source_field="manual_review",
                    source_reference=profile.identity.username,
                    evidence_text=reason,
                    observation_type="direct",
                    url=profile.identity.canonical_profile_url,
                )
            )
            type_reason = f"Manual review override: {reason}"
        manual = True

    if account_type is not AccountType.PERSONAL_CREATOR:
        theme_relevant = False
    elif not theme_relevant:
        type_reason += (
            " Direct recent/bio evidence for fashion, beauty, or compatible "
            "personal lifestyle is absent or outweighed by an irrelevant theme."
        )

    unique: list[SignalEvidence] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in all_evidence:
        key = (
            item.signal_type,
            item.source_field,
            item.source_reference,
            item.evidence_text,
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return AccountTypeAssessment(
        account_type=account_type,
        theme_relevant=theme_relevant,
        explanation=type_reason,
        evidence=tuple(unique),
        relevant_dimensions=relevant_dimensions,
        negative_topics=negative_topics,
        manual_override=manual,
        commercial_conflict=bool(own_brand or own_store),
        own_fashion_brand=bool(own_brand),
        own_clothing_store_or_showroom=bool(own_store),
        professional_portfolio=bool(portfolio),
    )


def account_type_counts(
    assessments: list[AccountTypeAssessment],
) -> dict[str, int]:
    return dict(
        sorted(
            Counter(item.account_type.value for item in assessments).items()
        )
    )


def with_account_theme_evidence(
    evidence: Mapping[str, tuple[SignalEvidence, ...]],
    assessment: AccountTypeAssessment,
) -> dict[str, tuple[SignalEvidence, ...]]:
    """Add only directly observed topic evidence to matching score signals."""

    result = {name: tuple(items) for name, items in evidence.items()}
    for dimension in ("fashion", "beauty", "lifestyle"):
        existing = list(result.get(dimension, ()))
        existing_keys = {
            (
                item.source_field,
                item.source_reference,
                item.evidence_text,
                item.url,
            )
            for item in existing
        }
        for item in assessment.evidence:
            key = (
                item.source_field,
                item.source_reference,
                item.evidence_text,
                item.url,
            )
            if (
                item.signal_type == dimension
                and item.observation_type == "direct"
                and key not in existing_keys
            ):
                existing.append(item)
                existing_keys.add(key)
        result[dimension] = tuple(existing)
    return result


__all__ = [
    "AccountType",
    "AccountTypeAssessment",
    "account_type_counts",
    "assess_account_type",
    "with_account_theme_evidence",
]
