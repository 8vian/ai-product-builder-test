from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from ai_product_builder.analysis import (
    CONTACT_URL_MARKERS,
    EMAIL_PATTERN,
    NATIVE_INTEGRATION_TERMS,
    PR_PATTERN,
    SIGNAL_TERMS,
)

from .models import CreatorProfile, RecentPost, SignalEvidence

SIGNAL_NAMES = (
    "commercial_pr",
    "contact",
    "fashion",
    "beauty",
    "lifestyle",
    "ugc",
    "marketplace",
    "native_product_integration",
)

TARGET_CONTENT_SIGNALS = (
    "fashion",
    "beauty",
    "lifestyle",
    "ugc",
    "marketplace",
    "native_product_integration",
)

_CONTACT_TERMS = tuple(
    dict.fromkeys(
        (
            *SIGNAL_TERMS["contact"],
            "contact",
            "для связи",
            "связь",
            "whatsapp",
            "what's app",
            "wa.me",
        )
    )
)
_CONTACT_URL_MARKERS = (
    *CONTACT_URL_MARKERS,
    "telegram.",
    "whatsapp.",
)
_SIGNAL_TERMS = {
    **SIGNAL_TERMS,
    "commercial_pr": tuple(
        dict.fromkeys((*SIGNAL_TERMS["commercial_pr"], "pr"))
    ),
    "ugc": tuple(
        term
        for term in SIGNAL_TERMS["ugc"]
        if term.casefold().strip() != "creator"
    ),
    "marketplace": tuple(
        dict.fromkeys((*SIGNAL_TERMS["marketplace"], "marketplace"))
    ),
}
_NATIVE_TERMS = tuple(
    dict.fromkeys((*_SIGNAL_TERMS["marketplace"], *NATIVE_INTEGRATION_TERMS))
)


def _post_reference(post: RecentPost, index: int) -> str:
    return post.post_id or post.url or f"index:{index}"


def _contains_term(text: str, term: str) -> bool:
    folded = text.casefold()
    needle = term.casefold().strip()
    if not needle:
        return False
    if needle == "pr":
        return bool(PR_PATTERN.search(text))
    # Preserve the audited Phase A substring behavior for stems and phrases.
    return needle in folded


def _text_sources(
    profile: CreatorProfile,
) -> list[tuple[str, str, str, str | None]]:
    sources: list[tuple[str, str, str, str | None]] = [
        ("full_name", "profile", profile.full_name, profile.identity.profile_url),
        ("biography", "profile", profile.biography, profile.identity.profile_url),
    ]
    for index, post in enumerate(profile.recent_posts):
        reference = _post_reference(post, index)
        sources.append(("recent_posts.caption", reference, post.caption, post.url))
        if post.hashtags:
            sources.append(
                (
                    "recent_posts.hashtags",
                    reference,
                    " ".join(post.hashtags),
                    post.url,
                )
            )
    return sources


def _term_evidence(
    signal_type: str,
    terms: Iterable[str],
    sources: Iterable[tuple[str, str, str, str | None]],
) -> list[SignalEvidence]:
    evidence: list[SignalEvidence] = []
    seen: set[tuple[str, str, str]] = set()
    for source_field, source_reference, source_text, url in sources:
        searchable_text = (
            EMAIL_PATTERN.sub(" ", source_text)
            if signal_type == "ugc"
            else source_text
        )
        for term in terms:
            normalized = term.strip()
            if signal_type == "ugc" and normalized.casefold() == "ugc":
                pattern = re.compile(
                    rf"(?<![\w.@]){re.escape(normalized)}(?![\w.@])",
                    re.IGNORECASE,
                )
                matched = bool(pattern.search(searchable_text))
            else:
                matched = _contains_term(searchable_text, normalized)
            if not matched:
                continue
            key = (source_field, source_reference, normalized.casefold())
            if key in seen:
                continue
            seen.add(key)
            evidence.append(
                SignalEvidence(
                    signal_type=signal_type,
                    source_field=source_field,
                    source_reference=source_reference,
                    evidence_text=f"Matched explicit term: {normalized}",
                    observation_type="direct",
                    url=url,
                )
            )
    return evidence


def collect_signal_evidence(
    profile: CreatorProfile,
) -> dict[str, tuple[SignalEvidence, ...]]:
    """Collect independent, directly observed evidence for every scoring signal.

    This function never derives one signal from another. In particular, contact
    evidence does not create commercial evidence, UGC does not create commercial
    evidence, and a paid-partnership flag does not create native integration.
    """

    sources = _text_sources(profile)
    result: dict[str, list[SignalEvidence]] = {
        name: [] for name in SIGNAL_NAMES
    }

    for signal_type in (
        "commercial_pr",
        "fashion",
        "beauty",
        "lifestyle",
        "ugc",
        "marketplace",
    ):
        result[signal_type].extend(
            _term_evidence(signal_type, _SIGNAL_TERMS[signal_type], sources)
        )

    result["contact"].extend(_term_evidence("contact", _CONTACT_TERMS, sources))
    for email in sorted(set(EMAIL_PATTERN.findall(profile.biography))):
        result["contact"].append(
            SignalEvidence(
                signal_type="contact",
                source_field="biography",
                source_reference="profile",
                evidence_text=f"Observed email address: {email}",
                observation_type="direct",
                url=profile.identity.profile_url,
            )
        )
    for url in profile.external_urls:
        if not any(marker in url.casefold() for marker in _CONTACT_URL_MARKERS):
            continue
        result["contact"].append(
            SignalEvidence(
                signal_type="contact",
                source_field="external_urls",
                source_reference="profile",
                evidence_text=f"Observed contact URL: {url}",
                observation_type="direct",
                url=url,
            )
        )

    for index, post in enumerate(profile.recent_posts):
        reference = _post_reference(post, index)
        if post.paid_partnership:
            result["commercial_pr"].append(
                SignalEvidence(
                    signal_type="commercial_pr",
                    source_field="recent_posts.paid_partnership",
                    source_reference=reference,
                    evidence_text="Observed paid-partnership flag.",
                    observation_type="direct",
                    url=post.url,
                )
            )

        post_native: list[SignalEvidence] = []
        if post.mentions:
            post_native.append(
                SignalEvidence(
                    signal_type="native_product_integration",
                    source_field="recent_posts.mentions",
                    source_reference=reference,
                    evidence_text=(
                        "Observed structured account mention(s): "
                        + ", ".join(post.mentions)
                    ),
                    observation_type="direct",
                    url=post.url,
                )
            )
        if post.tagged_usernames:
            post_native.append(
                SignalEvidence(
                    signal_type="native_product_integration",
                    source_field="recent_posts.tagged_usernames",
                    source_reference=reference,
                    evidence_text=(
                        "Observed tagged account(s): "
                        + ", ".join(post.tagged_usernames)
                    ),
                    observation_type="direct",
                    url=post.url,
                )
            )
        caption_matches = sorted(
            {
                term.strip()
                for term in _NATIVE_TERMS
                if _contains_term(post.caption, term)
            },
            key=str.casefold,
        )
        if caption_matches:
            post_native.append(
                SignalEvidence(
                    signal_type="native_product_integration",
                    source_field="recent_posts.caption",
                    source_reference=reference,
                    evidence_text=(
                        "Observed product-focused caption term(s): "
                        + ", ".join(caption_matches[:8])
                    ),
                    observation_type="direct",
                    url=post.url,
                )
            )
        result["native_product_integration"].extend(post_native)

    # Stable de-duplication keeps fixture and live providers contract-compatible.
    normalized: dict[str, tuple[SignalEvidence, ...]] = {}
    for signal_type, items in result.items():
        seen: set[tuple[str, str, str, str, str | None]] = set()
        unique: list[SignalEvidence] = []
        for item in items:
            key = (
                item.signal_type,
                item.source_field,
                item.source_reference,
                item.evidence_text,
                item.url,
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        normalized[signal_type] = tuple(unique)
    return normalized


def signal_is_present(
    evidence: Mapping[str, Iterable[SignalEvidence]], signal_type: str
) -> bool:
    return any(
        item.observation_type == "direct"
        for item in evidence.get(signal_type, ())
    )


def flatten_evidence(
    evidence: Mapping[str, Iterable[SignalEvidence]],
) -> tuple[SignalEvidence, ...]:
    return tuple(
        item
        for signal_type in SIGNAL_NAMES
        for item in evidence.get(signal_type, ())
    )


def count_evidenced_posts(
    evidence: Mapping[str, Iterable[SignalEvidence]], signal_type: str
) -> int:
    return len(
        {
            item.source_reference
            for item in evidence.get(signal_type, ())
            if item.observation_type == "direct"
            and item.source_reference != "profile"
        }
    )


def has_direct_target_content_evidence(
    evidence: Mapping[str, Iterable[SignalEvidence]],
) -> bool:
    return any(signal_is_present(evidence, name) for name in TARGET_CONTENT_SIGNALS)


def evidence_for_post(
    evidence: Mapping[str, Iterable[SignalEvidence]], post: RecentPost
) -> tuple[SignalEvidence, ...]:
    references = {value for value in (post.post_id, post.url) if value}
    return tuple(
        item
        for items in evidence.values()
        for item in items
        if item.source_reference in references or item.url == post.url
    )
