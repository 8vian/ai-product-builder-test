"""Stable Phase B error categories used by the CLI and run manifest."""

from __future__ import annotations


class PhaseBError(RuntimeError):
    """Base error with a stable machine-readable category."""

    category = "phase_b_error"

    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        super().__init__(message)
        self.details = details or {}


class ConfigurationError(PhaseBError):
    category = "configuration_error"


class MissingSecretError(PhaseBError):
    category = "missing_secret"


class InputValidationError(PhaseBError):
    category = "input_validation_error"


class ProviderAuthError(PhaseBError):
    category = "provider_auth_error"


class ProviderRateLimitError(PhaseBError):
    category = "provider_rate_limited"


class ProviderTimeoutError(PhaseBError):
    category = "provider_timeout"


class ProviderActorError(PhaseBError):
    category = "provider_actor_failed"


class ProviderSchemaError(PhaseBError):
    category = "provider_schema_mismatch"


class InsufficientCandidatePoolError(PhaseBError):
    category = "insufficient_candidate_pool"


class EvidenceValidationError(PhaseBError):
    category = "evidence_validation_failed"


class OutputWriteError(PhaseBError):
    category = "output_write_failed"


class ManualReviewConflictError(PhaseBError):
    category = "manual_review_conflict"
