# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""SSRF / cloud-metadata egress detector.

Complements `EnterpriseGuard` (outbound credential/DLP content) and
`safety_guard.SafetyGuard` (advisory flag for destructive-command *text*).
This module is narrower and blocking-oriented: it looks for the *shape* of a
URL that targets a cloud metadata endpoint, a loopback/link-local address, an
RFC1918 private range, or a non-HTTP scheme classically abused for SSRF
(file://, gopher://, dict://) — the kind of destination an agent's own tool
call (WebFetch, an HTTP client tool, a webhook URL argument) should never be
allowed to reach on the caller's behalf.

Deliberately conservative — every pattern requires a scheme (``xxx://``) so a
plain-prose mention of an IP address or "localhost" in a chat message does not
trip it; this is a guard on URL-shaped values passed to a tool, not a text
classifier.

Zero ML, regex-only — same philosophy as the rest of Synthelion.
"""
from __future__ import annotations

import re

_SCHEME = r"[a-zA-Z][a-zA-Z0-9+.-]*://"

# Cloud metadata endpoints — the single highest-value SSRF target class (IAM
# role credentials on AWS/GCP/Azure, project metadata, etc.), reachable only
# from inside the host/VM/container itself.
_METADATA_HOSTS = (
    r"169\.254\.169\.254",              # AWS / GCP / Azure / OpenStack IMDS
    r"169\.254\.170\.2",                # AWS ECS task metadata
    r"fd00:ec2::254",                   # AWS IMDSv2, IPv6
    r"metadata\.google\.internal",
    r"metadata\.azure\.com",
    r"100\.100\.100\.200",              # Alibaba Cloud
)
_METADATA_RE = re.compile(
    r"\b" + _SCHEME + r"(?:" + "|".join(_METADATA_HOSTS) + r")", re.IGNORECASE
)

# Loopback — a tool reaching back into the host running the agent itself
# (frequently how an internal admin panel/debug endpoint gets exposed).
_LOOPBACK_RE = re.compile(
    r"\b" + _SCHEME + r"(?:127\.\d{1,3}\.\d{1,3}\.\d{1,3}|localhost|0\.0\.0\.0|\[::1\])(?::\d+)?",
    re.IGNORECASE,
)

# RFC1918 private ranges — an agent given a public-facing task (e.g. a RAG
# agent fetching a URL a document pointed it to) should not be able to pivot
# into an internal network via that same fetch capability.
_PRIVATE_RANGE_RE = re.compile(
    r"\b" + _SCHEME + r"(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r")(?::\d+)?",
    re.IGNORECASE,
)

# Non-HTTP schemes classically abused to turn a "fetch this URL" tool into a
# local-file read or a raw-socket primitive (gopher/dict smuggle arbitrary
# protocol traffic through what looks like a URL fetch).
_DANGEROUS_SCHEME_RE = re.compile(r"\b(?:file|gopher|dict)://", re.IGNORECASE)

_DETECTORS: tuple[tuple[re.Pattern, str], ...] = (
    (_METADATA_RE, "cloud-metadata-endpoint"),
    (_LOOPBACK_RE, "loopback-address"),
    (_PRIVATE_RANGE_RE, "private-network-range"),
    (_DANGEROUS_SCHEME_RE, "dangerous-scheme"),
)


def find_ssrf_target(text: str) -> str | None:
    """Scans *text* for an SSRF-shaped URL. Returns a stable class name (for
    logging/tests/policy) if something tripped, or None. Callers should treat
    a non-None result as "do not let this request go out" — same posture as
    `sensitive_guard.find_sensitive` for credentials."""
    if not text:
        return None
    for pattern, label in _DETECTORS:
        if pattern.search(text):
            return label
    return None
