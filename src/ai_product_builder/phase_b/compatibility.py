"""Conservative language and delivery-market compatibility review."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import CampaignBrief, CreatorProfile, SignalEvidence


@dataclass(frozen=True, slots=True)
class CompatibilityAssessment:
    detected_content_language: str
    campaign_language_compatible: bool
    detected_geography: str | None
    delivery_market_review_required: bool
    delivery_market_conflict: bool
    explanation: str
    evidence: tuple[SignalEvidence, ...]

    @property
    def language_review_required(self) -> bool:
        return not self.campaign_language_compatible

    def to_dict(self) -> dict[str, object]:
        return {
            "detected_content_language": self.detected_content_language,
            "campaign_language_compatible": (
                self.campaign_language_compatible
            ),
            "detected_geography": self.detected_geography,
            "delivery_market_review_required": (
                self.delivery_market_review_required
            ),
            "delivery_market_conflict": self.delivery_market_conflict,
            "language_review_required": self.language_review_required,
            "explanation": self.explanation,
            "evidence": [item.to_dict() for item in self.evidence],
        }


_SCRIPT_PATTERNS = {
    "bn": re.compile(r"[\u0980-\u09ff]"),
    "hi_or_mr": re.compile(r"[\u0900-\u097f]"),
    "ar": re.compile(r"[\u0600-\u06ff]"),
    "zh": re.compile(r"[\u4e00-\u9fff]"),
}
_CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")
_RUSSIAN_LEXICAL_PATTERN = re.compile(
    r"\b(?:я|мой|моя|мои|для|это|как|новый|новая|мода|стиль|"
    r"одежда|образ|образы|примерка|примерки|красота|косметика|"
    r"уход|блог|блогер|сотрудничество|реклама|россия|москва)\b",
    re.IGNORECASE,
)
_LATIN_LANGUAGE_MARKERS = {
    "en": (
        " the ",
        " and ",
        " with ",
        " my ",
        " creator",
        "collaboration",
        "outfit",
        "beauty",
        "wellness",
    ),
    "pt": (
        " você ",
        " gente ",
        " hoje ",
        " comida ",
        " família ",
        " viver ",
        " receita ",
    ),
    "fr": (
        " de ",
        " des ",
        " avec ",
        " lifestyle",
        "collaborations",
        "finances de l",
    ),
}
_GEOGRAPHY_PATTERNS = (
    (re.compile(r"\b(?:россия|russia)\b", re.IGNORECASE), "Россия"),
    (
        re.compile(
            r"\b(?:доставк|отправк|работа)\w*\s+по\s+россии\b",
            re.IGNORECASE,
        ),
        "Россия",
    ),
    (re.compile(r"\b(?:москва|moscow)\b", re.IGNORECASE), "Москва, Россия"),
    (
        re.compile(
            r"\b(?:санкт[-\s]?петербург|saint\s+petersburg|st\.?\s*petersburg)\b",
            re.IGNORECASE,
        ),
        "Санкт-Петербург, Россия",
    ),
    (
        re.compile(
            r"\b(?:казань|екатеринбург|новосибирск|нижний\s+новгород|"
            r"ростов(?:-на-дону)?|краснодар|самара|уфа|пермь|омск|"
            r"воронеж)\b",
            re.IGNORECASE,
        ),
        "крупный город, Россия",
    ),
    (re.compile(r"\bfrom\s+kolkata\b", re.IGNORECASE), "Kolkata"),
    (re.compile(r"\bkolkata\b", re.IGNORECASE), "Kolkata"),
    (re.compile(r"\brajasthan(?:i)?\b", re.IGNORECASE), "Rajasthan"),
    (re.compile(r"\bbalotra\b", re.IGNORECASE), "Balotra"),
    (re.compile(r"\bbishkek\b", re.IGNORECASE), "Bishkek"),
    (re.compile(r"\balgeria\b", re.IGNORECASE), "Algeria"),
    (re.compile(r"🇩🇿"), "Algeria"),
    (re.compile(r"\bbrazil\b", re.IGNORECASE), "Brazil"),
    (re.compile(r"\bjakarta\b", re.IGNORECASE), "Jakarta"),
)


def _sources(
    profile: CreatorProfile,
) -> list[tuple[str, str, str, str | None]]:
    result = [
        (
            "biography",
            "profile",
            profile.biography,
            profile.identity.canonical_profile_url,
        )
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


def _detected_languages(
    sources: list[tuple[str, str, str, str | None]],
) -> tuple[tuple[str, ...], list[SignalEvidence]]:
    combined = " ".join(text for _, _, text, _ in sources)
    languages: set[str] = set()
    evidence: list[SignalEvidence] = []
    russian_matches = _RUSSIAN_LEXICAL_PATTERN.findall(combined)
    cyrillic_count = len(_CYRILLIC_PATTERN.findall(combined))
    if len(russian_matches) >= 2:
        languages.add("ru")
        source = next(
            (
                item
                for item in sources
                if len(_RUSSIAN_LEXICAL_PATTERN.findall(item[2])) >= 2
            ),
            sources[0],
        )
        evidence.append(
            SignalEvidence(
                signal_type="content_language",
                source_field=source[0],
                source_reference=source[1],
                evidence_text=(
                    "Observed direct Russian lexical evidence in saved "
                    f"profile/post text ({len(russian_matches)} matches)."
                ),
                observation_type="direct",
                url=source[3],
            )
        )
    elif cyrillic_count >= 3:
        languages.add("cyrillic_undetermined")
        source = next(
            (
                item
                for item in sources
                if len(_CYRILLIC_PATTERN.findall(item[2])) >= 3
            ),
            sources[0],
        )
        evidence.append(
            SignalEvidence(
                signal_type="content_language",
                source_field=source[0],
                source_reference=source[1],
                evidence_text=(
                    f"Observed {cyrillic_count} Cyrillic characters, but "
                    "Russian lexical evidence was insufficient."
                ),
                observation_type="direct",
                url=source[3],
            )
        )
    for language, pattern in _SCRIPT_PATTERNS.items():
        count = len(pattern.findall(combined))
        if count < 3:
            continue
        languages.add(language)
        source = next(
            (
                item
                for item in sources
                if len(pattern.findall(item[2])) >= 3
            ),
            sources[0],
        )
        evidence.append(
            SignalEvidence(
                signal_type="content_language",
                source_field=source[0],
                source_reference=source[1],
                evidence_text=(
                    f"Observed {count} characters from the {language} "
                    "language/script evidence set."
                ),
                observation_type="direct",
                url=source[3],
            )
        )

    padded = f" {combined.casefold()} "
    latin_count = len(re.findall(r"[a-z]", padded))
    if latin_count >= 12:
        marker_scores = {
            language: sum(padded.count(marker) for marker in markers)
            for language, markers in _LATIN_LANGUAGE_MARKERS.items()
        }
        best_score = max(marker_scores.values(), default=0)
        if best_score > 0:
            best = sorted(
                language
                for language, score in marker_scores.items()
                if score == best_score
            )[0]
        else:
            best = "latin_undetermined"
        languages.add(best)
        evidence.append(
            SignalEvidence(
                signal_type="content_language",
                source_field="biography_and_recent_posts.caption",
                source_reference="sample",
                evidence_text=(
                    f"Observed Latin-script content; lexical evidence "
                    f"classified it as {best}."
                ),
                observation_type="derived",
                url=profile_url_from_sources(sources),
            )
        )
    return tuple(sorted(languages)), evidence


def profile_url_from_sources(
    sources: list[tuple[str, str, str, str | None]],
) -> str | None:
    return next((url for _, _, _, url in sources if url), None)


def _detected_geography(
    sources: list[tuple[str, str, str, str | None]],
) -> tuple[str | None, list[SignalEvidence]]:
    matches: list[tuple[str, SignalEvidence]] = []
    for source_field, source_reference, text, url in sources:
        for pattern, geography in _GEOGRAPHY_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            matches.append(
                (
                    geography,
                    SignalEvidence(
                        signal_type="detected_geography",
                        source_field=source_field,
                        source_reference=source_reference,
                        evidence_text=(
                            f"Observed explicit geography evidence: "
                            f"{match.group(0)}"
                        ),
                        observation_type="direct",
                        url=url,
                    ),
                )
            )
    if not matches:
        return None, []
    distinct = list(dict.fromkeys(value for value, _ in matches))
    return ", ".join(distinct), [item for _, item in matches]


def assess_compatibility(
    profile: CreatorProfile,
    campaign: CampaignBrief,
) -> CompatibilityAssessment:
    """Review language and physical-delivery compatibility from direct text."""

    sources = _sources(profile)
    languages, language_evidence = _detected_languages(sources)
    detected_language = (
        "undetermined"
        if not languages
        else languages[0]
        if len(languages) == 1
        else "mixed:" + ",".join(languages)
    )
    campaign_languages = tuple(
        item.casefold().split("-", 1)[0]
        for item in (
            campaign.target_content_languages or (campaign.language,)
        )
    )
    compatible = any(
        language in languages for language in campaign_languages
    )
    geography, geography_evidence = _detected_geography(sources)

    delivery_markets = tuple(
        item.strip()
        for item in (
            campaign.delivery_markets
            or ((campaign.geography,) if campaign.geography else ())
        )
        if item.strip()
    )
    if not delivery_markets:
        delivery_review = True
        delivery_conflict = False
        delivery_reason = (
            "Campaign geography/delivery market is not configured; physical "
            "barter delivery requires manual confirmation"
            + (
                f" for observed geography {geography}."
                if geography
                else ", and no candidate geography was directly evidenced."
            )
        )
    elif geography is None:
        delivery_review = True
        delivery_conflict = False
        delivery_reason = (
            "Campaign delivery market is "
            f"{', '.join(delivery_markets)}, but candidate geography is not "
            "directly evidenced."
        )
    else:
        delivery_conflict = not any(
            market.casefold() in geography.casefold()
            for market in delivery_markets
        )
        delivery_review = delivery_conflict
        delivery_reason = (
            f"Observed geography {geography} "
            + (
                "conflicts with campaign delivery market "
                f"{', '.join(delivery_markets)}."
                if delivery_conflict
                else "is compatible with campaign delivery market "
                f"{', '.join(delivery_markets)}."
            )
        )

    campaign_language_label = ", ".join(campaign_languages)
    language_reason = (
        f"Detected content language {detected_language} includes campaign "
        f"language {campaign_language_label}."
        if compatible
        else (
            f"Detected content language is {detected_language}; no direct "
            f"evidence of campaign-language comprehension "
            f"({campaign_language_label}) was found."
        )
    )
    return CompatibilityAssessment(
        detected_content_language=detected_language,
        campaign_language_compatible=compatible,
        detected_geography=geography,
        delivery_market_review_required=delivery_review,
        delivery_market_conflict=delivery_conflict,
        explanation=f"{language_reason} {delivery_reason}",
        evidence=tuple((*language_evidence, *geography_evidence)),
    )


__all__ = ["CompatibilityAssessment", "assess_compatibility"]
