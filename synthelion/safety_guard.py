# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_CRITICAL_PATTERNS = (
    "security", "vulnerability", "exploit", "cve-",
    "data breach", "leak", "exposed",
    "malware", "ransomware", "trojan",
    "authentication bypass", "unauthorized access",
    "remote code execution", "rce",
    "sql injection", "sqli", "xss", "csrf",
    "privilege escalation",
    "denial of service", "dos", "ddos",
    "buffer overflow", "overflow",
    "certificate expired", "tls", "ssl",
    "encryption key", "private key", "secret exposed",
    "firewall", "intrusion",
)

_DESTRUCTIVE_PATTERNS = (
    "rm -rf", "del /f", "format ", "mkfs",
    "dd if=", "> /dev/sda", "> /dev/sd",
    "drop database", "drop table", "truncate table",
    "delete from", "shutdown", "reboot",
    "chmod 777", "chown -r",
    "git push --force", "git reset --hard",
    ">|", ":!q", ":w!",
    "cargo clean", "npm cache clean",
    "docker rmi -f", "docker system prune -a",
)

_WARNING_PATTERNS = (
    "warning", "caution", "important",
    "do not", "never", "avoid",
    "deprecated", "removed", "obsolete",
    "experimental", "unstable", "untested",
    "backup", "back up",
    "permission denied", "access denied",
    "rate limit", "timeout",
    "irreversible", "irreversibile",
    "production", "prod", "deploy",
    "rollback", "migration",
)


def _build_matcher(pattern: str) -> re.Pattern:
    body = re.escape(pattern)
    left = r"(?<![A-Za-z0-9])" if pattern[0].isalnum() else ""
    right = r"(?![A-Za-z0-9])" if pattern[-1].isalnum() else ""
    return re.compile(left + body + right, re.IGNORECASE)


def _build(patterns: tuple[str, ...] | None) -> list[tuple[str, re.Pattern]]:
    if not patterns:
        return []
    return [(p, _build_matcher(p)) for p in patterns if p]


_CRITICAL_MATCHERS = _build(_CRITICAL_PATTERNS)
_DESTRUCTIVE_MATCHERS = _build(_DESTRUCTIVE_PATTERNS)
_WARNING_MATCHERS = _build(_WARNING_PATTERNS)


def find_destructive_command(text: str) -> str | None:
    """Scans *text* for a destructive-shell pattern (rm -rf, drop table,
    git push --force, ...). Returns the matched pattern, or None.

    Public, reusable entry point for callers that need a *blocking* decision
    (e.g. `enterprise_guard.EnterpriseGuard.check_tool_call`'s PreToolUse
    veto) — separate from `SafetyGuard.check`, which only ever produces an
    advisory verdict (skip compression), never a block."""
    if not text or not text.strip():
        return None
    return _first_match(_DESTRUCTIVE_MATCHERS, text)

class SafetyLevel(Enum):
    NORMAL = "Normal"
    WARNING = "Warning"
    CRITICAL = "Critical"


@dataclass
class SafetyVerdict:
    level: SafetyLevel = SafetyLevel.NORMAL
    reason: str = ""

    @property
    def should_compress(self) -> bool:
        return self.level == SafetyLevel.NORMAL


def _first_match(matchers: list[tuple[str, re.Pattern]], message: str) -> str | None:
    for pattern, rx in matchers:
        if rx.search(message):
            return pattern
    return None


class SafetyGuard:
    """Auto-disables compression for security-critical or destructive content.

    Ported from C# CavemanSafetyGuard. Word-boundary-aware matching so acronyms like
    "dos"/"rce" don't match inside "dose"/"force", while command strings like "rm -rf"
    or "> /dev/sda" still match by content.
    """

    def __init__(
        self,
        extra_critical_patterns: tuple[str, ...] | None = None,
        extra_warning_patterns: tuple[str, ...] | None = None,
    ) -> None:
        self._extra_critical = _build(extra_critical_patterns)
        self._extra_warning = _build(extra_warning_patterns)

    def check(self, message: str) -> SafetyVerdict:
        if not message or not message.strip():
            return SafetyVerdict(level=SafetyLevel.NORMAL)

        p = _first_match(_CRITICAL_MATCHERS, message) or _first_match(self._extra_critical, message)
        if p:
            return SafetyVerdict(level=SafetyLevel.CRITICAL, reason=f"Critical security pattern detected: '{p}'")

        p = _first_match(_DESTRUCTIVE_MATCHERS, message)
        if p:
            return SafetyVerdict(level=SafetyLevel.CRITICAL, reason=f"Destructive command pattern detected: '{p}'")

        p = _first_match(_WARNING_MATCHERS, message) or _first_match(self._extra_warning, message)
        if p:
            return SafetyVerdict(level=SafetyLevel.WARNING, reason=f"Warning pattern detected: '{p}'")

        return SafetyVerdict(level=SafetyLevel.NORMAL)

    def should_compress(self, message: str) -> bool:
        return self.check(message).should_compress
