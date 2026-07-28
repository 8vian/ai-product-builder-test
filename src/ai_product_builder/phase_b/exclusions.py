"""Immutable, traceable exclusion registry for Phase A identities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .errors import InputValidationError
from .normalization import canonicalize_profile_url, normalize_username


@dataclass(frozen=True, slots=True)
class ExclusionMatch:
    normalized_username: str
    canonical_profile_url: str
    reasons: tuple[str, ...]
    source_values: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalized_username": self.normalized_username,
            "canonical_profile_url": self.canonical_profile_url,
            "reasons": list(self.reasons),
            "source_values": list(self.source_values),
        }


class ExclusionRegistry:
    """Read-only identity lookup with all observed exclusion reasons."""

    __slots__ = ("_by_username", "_by_url")

    def __init__(self, matches: Iterable[ExclusionMatch]):
        by_username: dict[str, ExclusionMatch] = {}
        by_url: dict[str, ExclusionMatch] = {}
        for match in matches:
            by_username[match.normalized_username] = match
            by_url[match.canonical_profile_url.casefold()] = match
        self._by_username = MappingProxyType(by_username)
        self._by_url = MappingProxyType(by_url)

    @classmethod
    def from_files(
        cls, instagram_profiles_path: Path, manual_audit_path: Path
    ) -> "ExclusionRegistry":
        try:
            profiles = json.loads(instagram_profiles_path.read_text(encoding="utf-8"))
            audit = json.loads(manual_audit_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise InputValidationError(f"exclusion input not found: {exc.filename}") from exc
        except json.JSONDecodeError as exc:
            raise InputValidationError(
                f"invalid exclusion JSON at line {exc.lineno}, column {exc.colno}"
            ) from exc
        return cls.build(profiles, audit)

    @classmethod
    def build(cls, profiles: Any, manual_audit: Any) -> "ExclusionRegistry":
        if not isinstance(profiles, list):
            raise InputValidationError("instagram_profiles.json must contain an array")
        if not isinstance(manual_audit, Mapping):
            raise InputValidationError(
                "manual_verification_audit.json must contain an object"
            )
        accumulated: dict[str, dict[str, set[str]]] = {}

        def add(value: Any, reason: str) -> None:
            if not isinstance(value, str):
                return
            normalized = normalize_username(value)
            if not normalized:
                return
            item = accumulated.setdefault(
                normalized, {"reasons": set(), "source_values": set()}
            )
            item["reasons"].add(reason)
            item["source_values"].add(value)

        for profile in profiles:
            if not isinstance(profile, Mapping):
                continue
            add(profile.get("username"), "phase_a_source")
            for field in ("inputUrl", "url"):
                add(profile.get(field), "phase_a_source_url")

        recovery = manual_audit.get("manual_human_in_the_loop_recovery", {})
        if isinstance(recovery, Mapping):
            corrections = recovery.get("confirmed_corrections", [])
            if isinstance(corrections, list):
                for correction in corrections:
                    if not isinstance(correction, Mapping):
                        continue
                    add(correction.get("source_username"), "historical_alias")
                    add(correction.get("verified_username"), "verified_replacement")
            false_leads = recovery.get("rejected_false_leads", [])
            if isinstance(false_leads, list):
                for false_lead in false_leads:
                    if isinstance(false_lead, Mapping):
                        add(
                            false_lead.get("candidate_username"),
                            "rejected_false_lead",
                        )
                        add(
                            false_lead.get("source_username"),
                            "historical_alias",
                        )
        unresolved = manual_audit.get("remaining_unresolved", [])
        if isinstance(unresolved, list):
            for username in unresolved:
                add(username, "phase_a_unresolved")
        add("nike", "brand_reference")
        add("apple", "brand_reference")

        matches = [
            ExclusionMatch(
                normalized_username=username,
                canonical_profile_url=canonicalize_profile_url(username) or "",
                reasons=tuple(sorted(values["reasons"])),
                source_values=tuple(sorted(values["source_values"], key=str.casefold)),
            )
            for username, values in sorted(accumulated.items())
        ]
        return cls(matches)

    def match(
        self, username: str | None = None, profile_url: str | None = None
    ) -> ExclusionMatch | None:
        normalized = normalize_username(username) or normalize_username(profile_url)
        if normalized and normalized in self._by_username:
            return self._by_username[normalized]
        canonical = canonicalize_profile_url(profile_url)
        return self._by_url.get(canonical.casefold()) if canonical else None

    def contains(
        self, username: str | None = None, profile_url: str | None = None
    ) -> bool:
        return self.match(username, profile_url) is not None

    def __len__(self) -> int:
        return len(self._by_username)

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self),
            "identities": [
                match.to_dict()
                for _, match in sorted(self._by_username.items())
            ],
        }
