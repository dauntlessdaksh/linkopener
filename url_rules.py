"""
Pure-Python helpers for LinkedIn profile URL strings.

No browser calls here — only string checks used before opening Selenium tabs.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Public profile paths look like /in/<slug>/ (slug rules are loose on purpose).
_IN_PATH_RE = re.compile(
    r"^/in/[^/]+/?.*$",
    re.IGNORECASE,
)

_LINKEDIN_HOSTS = frozenset(
    {
        "linkedin.com",
        "www.linkedin.com",
        "mobile.linkedin.com",
    }
)


def normalize_url(raw: str) -> str:
    """Strip whitespace; add https:// if the user pasted a host/path without scheme."""
    s = (raw or "").strip()
    if not s:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        s = "https://" + s
    return s


def is_blank(raw: str | None) -> bool:
    return raw is None or not str(raw).strip()


def precheck_linkedin_profile_url(raw: str) -> tuple[bool, str | None]:
    """
    Returns (ok_to_open_in_browser, reason_if_corrupted).

    ok_to_open_in_browser is True only for strings that look like a LinkedIn /in/ profile URL.
    """
    if is_blank(raw):
        return (False, "empty")

    url = normalize_url(str(raw))
    try:
        parsed = urlparse(url)
    except Exception:
        return (False, "unparseable_url")

    host = (parsed.netloc or "").lower()
    # Strip default port if present
    if ":" in host:
        host = host.split(":")[0]

    if host not in _LINKEDIN_HOSTS:
        return (False, "not_linkedin_host")

    path = parsed.path or "/"
    if not _IN_PATH_RE.match(path):
        return (False, "not_profile_in_path")

    return (True, None)
