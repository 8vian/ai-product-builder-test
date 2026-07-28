"""Instagram identity normalization with punctuation-preserving semantics."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

INSTAGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
RESERVED_PATHS = {"p", "reel", "reels", "stories", "explore", "accounts"}


def normalize_username(value: str | None) -> str | None:
    """Normalize for matching while preserving every dot and underscore.

    Only surrounding whitespace, an optional ``@``, URL syntax, query
    parameters, fragments, and trailing slashes are removed.  Case-folding is
    intentional because Instagram usernames compare case-insensitively.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "://" in text or text.casefold().startswith(
        ("instagram.com/", "www.instagram.com/", "m.instagram.com/")
    ):
        candidate_url = text if "://" in text else f"https://{text}"
        parsed = urlparse(candidate_url)
        if parsed.hostname and parsed.hostname.casefold() not in INSTAGRAM_HOSTS:
            return None
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if not parts or parts[0].casefold() in RESERVED_PATHS:
            return None
        text = parts[0]
    else:
        text = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    candidate = text.lstrip("@")
    if not INSTAGRAM_USERNAME_RE.fullmatch(candidate):
        return None
    return candidate.casefold()


def canonicalize_profile_url(value: str | None) -> str | None:
    username = normalize_username(value)
    return f"https://www.instagram.com/{username}/" if username else None


def identity_key(username: str | None, profile_url: str | None) -> tuple[str, str] | None:
    normalized = normalize_username(username) or normalize_username(profile_url)
    if not normalized:
        return None
    return normalized, canonicalize_profile_url(normalized) or ""


def is_canonical_instagram_profile_url(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    canonical = canonicalize_profile_url(value)
    return canonical is not None and value.strip().casefold() == canonical.casefold()
