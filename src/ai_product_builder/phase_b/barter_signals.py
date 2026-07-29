"""Independent barter-readiness and explicit-refusal evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import CreatorProfile, SignalEvidence


@dataclass(frozen=True, slots=True)
class BarterSignalAssessment:
    barter_evidence: tuple[SignalEvidence, ...]
    no_barter_evidence: tuple[SignalEvidence, ...]

    @property
    def explicit_refusal(self) -> bool:
        return bool(self.no_barter_evidence)

    @property
    def explicit_barter_readiness(self) -> bool:
        return bool(self.barter_evidence) and not self.explicit_refusal

    def to_dict(self) -> dict[str, object]:
        return {
            "explicit_refusal": self.explicit_refusal,
            "explicit_barter_readiness": self.explicit_barter_readiness,
            "barter_evidence": [
                item.to_dict() for item in self.barter_evidence
            ],
            "no_barter_evidence": [
                item.to_dict() for item in self.no_barter_evidence
            ],
        }


_NO_BARTER_PATTERNS = (
    re.compile(
        r"\b(?:не\s+работаю|не\s+сотрудничаю)"
        r"(?:\s+(?:по|на))?\s+бартер(?:у|ом)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bбартер(?:\s+|ом\s+)(?:не\s+рассматриваю|не\s+работаю|"
        r"не\s+занимаюсь|не\s+интересует)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:только|исключительно)(?:\s+на)?\s+платн"
        r"(?:ое|ые|ая|ой)\s+(?:сотрудничество|интеграции|основе)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:only\s+paid|paid\s+(?:collaborations?|partnerships?|work)"
        r"\s+only)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:no\s+barter|do\s+not\s+accept\s+barter|"
        r"don['’]?t\s+accept\s+barter)\b",
        re.IGNORECASE,
    ),
)
_POSITIVE_BARTER_PATTERNS = (
    re.compile(r"\bбартер(?:а|у|ом|ный|ные|ное)?\b", re.IGNORECASE),
    re.compile(r"\bbarter(?:ing)?\b", re.IGNORECASE),
    re.compile(
        r"\bproduct\s+(?:exchange|for\s+content)\b",
        re.IGNORECASE,
    ),
)


def _sources(
    profile: CreatorProfile,
) -> list[tuple[str, str, str, str | None]]:
    sources = [
        (
            "biography",
            "profile",
            profile.biography,
            profile.identity.canonical_profile_url,
        )
    ]
    for index, post in enumerate(profile.recent_posts):
        sources.append(
            (
                "recent_posts.caption",
                post.post_id or post.url or f"index:{index}",
                post.caption,
                post.url,
            )
        )
    return sources


def _excerpt(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - 45)
    end = min(len(text), match.end() + 45)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def assess_barter_signals(
    profile: CreatorProfile,
) -> BarterSignalAssessment:
    """Detect direct barter language without inferring from commercial terms.

    A price list, manager, contact address, advertising offer, or generic paid
    collaboration wording is not a refusal. Only an explicit negative or
    paid-only statement creates ``no_barter_evidence``.
    """

    positive: list[SignalEvidence] = []
    negative: list[SignalEvidence] = []
    for source_field, source_reference, text, url in _sources(profile):
        refusal_spans: list[tuple[int, int]] = []
        for pattern in _NO_BARTER_PATTERNS:
            for match in pattern.finditer(text):
                refusal_spans.append(match.span())
                negative.append(
                    SignalEvidence(
                        signal_type="no_barter",
                        source_field=source_field,
                        source_reference=source_reference,
                        evidence_text=(
                            "Observed explicit barter refusal: "
                            f"{_excerpt(text, match)}"
                        ),
                        observation_type="direct",
                        url=url,
                    )
                )
        for pattern in _POSITIVE_BARTER_PATTERNS:
            for match in pattern.finditer(text):
                if any(
                    start <= match.start() and match.end() <= end
                    for start, end in refusal_spans
                ):
                    continue
                positive.append(
                    SignalEvidence(
                        signal_type="barter_readiness",
                        source_field=source_field,
                        source_reference=source_reference,
                        evidence_text=(
                            "Observed explicit barter wording: "
                            f"{_excerpt(text, match)}"
                        ),
                        observation_type="direct",
                        url=url,
                    )
                )

    def unique(
        values: list[SignalEvidence],
    ) -> tuple[SignalEvidence, ...]:
        result: list[SignalEvidence] = []
        seen: set[tuple[str, str, str]] = set()
        for item in values:
            key = (
                item.source_field,
                item.source_reference,
                item.evidence_text,
            )
            if key not in seen:
                seen.add(key)
                result.append(item)
        return tuple(result)

    return BarterSignalAssessment(
        barter_evidence=unique(positive),
        no_barter_evidence=unique(negative),
    )


__all__ = ["BarterSignalAssessment", "assess_barter_signals"]
