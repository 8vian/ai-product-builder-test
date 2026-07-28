from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .errors import InsufficientCandidatePoolError
from .models import CandidateResult, EligibilityStatus
from .normalization import normalize_username


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    selected: tuple[CandidateResult, ...]
    eligible_count: int
    requested_count: int
    warning: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "selected": [item.to_dict() for item in self.selected],
            "eligible_count": self.eligible_count,
            "requested_count": self.requested_count,
            "warning": self.warning,
        }


def _is_eligible(candidate: CandidateResult) -> bool:
    status = candidate.eligibility_status
    value = status.value if isinstance(status, EligibilityStatus) else str(status)
    return value == EligibilityStatus.ELIGIBLE.value


def candidate_sort_key(
    candidate: CandidateResult,
) -> tuple[float, float, float, float, str]:
    engagement = (
        candidate.engagement_rate
        if candidate.engagement_rate is not None
        else float("-inf")
    )
    return (
        -candidate.score,
        -candidate.discovery_confidence,
        -candidate.data_completeness,
        -engagement,
        normalize_username(candidate.username),
    )


def rank_candidates(
    candidates: Iterable[CandidateResult],
) -> list[CandidateResult]:
    """Return only eligible candidates in the documented deterministic order."""

    return sorted(
        (candidate for candidate in candidates if _is_eligible(candidate)),
        key=candidate_sort_key,
    )


def selection_explanation(
    candidate: CandidateResult, rank: int, eligible_pool_size: int
) -> str:
    return (
        f"Rank {rank} of {eligible_pool_size} eligible candidates: "
        f"score {candidate.score:.2f}/100, discovery confidence "
        f"{candidate.discovery_confidence:.3f}, data completeness "
        f"{candidate.data_completeness:.3f}, engagement rate "
        + (
            f"{candidate.engagement_rate:.3f}%."
            if candidate.engagement_rate is not None
            else "unavailable."
        )
    )


def select_top_candidates(
    candidates: Iterable[CandidateResult],
    *,
    final_count: int = 5,
    minimum_count: int = 3,
) -> SelectionOutcome:
    if final_count < 1:
        raise ValueError("final_count must be positive")
    if minimum_count < 1 or minimum_count > final_count:
        raise ValueError("minimum_count must be between 1 and final_count")

    ranked = rank_candidates(candidates)
    if len(ranked) < minimum_count:
        raise InsufficientCandidatePoolError(
            f"Only {len(ranked)} eligible candidates; at least "
            f"{minimum_count} are required.",
            details={
                "eligible_count": len(ranked),
                "minimum_count": minimum_count,
                "requested_count": final_count,
            },
        )
    warning = None
    if len(ranked) < final_count:
        warning = (
            f"Incomplete target: selected {len(ranked)} eligible candidates "
            f"instead of the requested {final_count}; no ineligible candidates "
            "were used as padding."
        )
    return SelectionOutcome(
        selected=tuple(ranked[:final_count]),
        eligible_count=len(ranked),
        requested_count=final_count,
        warning=warning,
    )
