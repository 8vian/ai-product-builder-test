"""Phase B creator discovery contracts and provider adapters.

The package deliberately keeps provider-specific payloads at its boundary.  All
pipeline stages consume the typed models exported here.
"""

from .config import PhaseBConfig, load_phase_b_config
from .exclusions import ExclusionMatch, ExclusionRegistry
from .models import (
    CampaignBrief,
    CandidateIdentity,
    CandidateMetrics,
    CandidateResult,
    CandidateScore,
    DiscoveryHit,
    EligibilityDecision,
    EligibilityStatus,
    ManualVerificationStatus,
    OfferDraft,
    PhaseBRunManifest,
    QuerySpec,
    RecentPost,
    ScoreComponent,
    SignalEvidence,
    CreatorProfile,
)
from .normalization import canonicalize_profile_url, normalize_username
from .pipeline import (
    PhaseBRunResult,
    deduplicate_discovery_hits,
    run_phase_b,
    validate_phase_b_config,
)
from .queries import generate_queries

__all__ = [
    "CampaignBrief",
    "CandidateIdentity",
    "CandidateMetrics",
    "CandidateResult",
    "CandidateScore",
    "CreatorProfile",
    "DiscoveryHit",
    "EligibilityDecision",
    "EligibilityStatus",
    "ExclusionMatch",
    "ExclusionRegistry",
    "ManualVerificationStatus",
    "OfferDraft",
    "PhaseBConfig",
    "PhaseBRunManifest",
    "PhaseBRunResult",
    "QuerySpec",
    "RecentPost",
    "ScoreComponent",
    "SignalEvidence",
    "canonicalize_profile_url",
    "deduplicate_discovery_hits",
    "generate_queries",
    "load_phase_b_config",
    "normalize_username",
    "run_phase_b",
    "validate_phase_b_config",
]
