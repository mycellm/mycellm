"""Privacy guardrails — sensitive content detection and trust policies.

Scans outgoing prompts for common sensitive data patterns before
they leave the device. Works like GitHub's secret scanning — catches
structured secrets with high confidence, low false positives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SensitiveMatch:
    """A detected sensitive content match."""
    type: str       # "api_key", "password", "credit_card", etc.
    label: str      # Human-readable label
    pattern: str    # What was matched (redacted)
    severity: str   # "high", "medium", "low"


# Patterns: (type, label, regex, severity)
_PATTERNS: list[tuple[str, str, str, str]] = [
    # API keys (relaxed thresholds to catch shorter test keys too)
    ("api_key", "OpenAI API key", r"sk-[a-zA-Z0-9]{16,}", "high"),
    ("api_key", "OpenRouter API key", r"sk-or-v1-[a-zA-Z0-9]{10,}", "high"),
    ("api_key", "GitHub token", r"gh[ps]_[a-zA-Z0-9]{20,}", "high"),
    ("api_key", "AWS access key", r"AKIA[A-Z0-9]{12,}", "high"),
    ("api_key", "Slack token", r"xox[baprs]-[a-zA-Z0-9\-]{10,}", "high"),
    ("api_key", "Anthropic API key", r"sk-ant-[a-zA-Z0-9\-]{16,}", "high"),
    ("api_key", "HuggingFace token", r"hf_[a-zA-Z0-9]{10,}", "high"),
    ("api_key", "Generic secret key", r"(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{8,}", "medium"),

    # Private keys
    ("private_key", "Private key block", r"-----BEGIN\s+(?:RSA|EC|ED25519|OPENSSH|PGP)\s+PRIVATE\s+KEY-----", "high"),

    # Passwords
    ("password", "Password in context", r"(?:password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\"]{4,}", "medium"),
    ("password", "Credentials mention", r"(?:my\s+password\s+is|login\s+credentials?|auth\s+token)\s+\S+", "medium"),

    # Financial
    ("credit_card", "Credit card number", r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", "high"),
    ("ssn", "Social Security Number", r"\b\d{3}-\d{2}-\d{4}\b", "high"),

    # Connection strings
    ("connection_string", "Database connection string", r"(?:postgresql|mysql|mongodb|redis)://[^\s]{10,}", "high"),
]

_COMPILED = [(t, l, re.compile(p, re.IGNORECASE), s) for t, l, p, s in _PATTERNS]


def scan_sensitive(text: str) -> list[SensitiveMatch]:
    """Scan text for sensitive content patterns.

    Returns list of matches. Empty list = no sensitive content detected.
    """
    matches = []
    for type_, label, pattern, severity in _COMPILED:
        for m in pattern.finditer(text):
            matched = m.group(0)
            # Redact: show first 4 and last 2 chars
            if len(matched) > 10:
                redacted = matched[:4] + "..." + matched[-2:]
            else:
                redacted = matched[:3] + "..."
            matches.append(SensitiveMatch(
                type=type_,
                label=label,
                pattern=redacted,
                severity=severity,
            ))
    return matches


def format_warning(matches: list[SensitiveMatch]) -> str:
    """Format a human-readable warning for detected sensitive content."""
    if not matches:
        return ""
    high = [m for m in matches if m.severity == "high"]
    lines = ["Sensitive content detected:"]
    for m in matches:
        icon = "!!!" if m.severity == "high" else "!"
        lines.append(f"  {icon} {m.label}: {m.pattern}")
    lines.append("")
    lines.append("On the public network, prompts are processed by untrusted peers.")
    if high:
        lines.append("HIGH severity items detected — strongly recommend NOT sending.")
    return "\n".join(lines)


# Trust levels for swarm policies
TRUST_LEVELS = {
    "untrusted": {
        "label": "Public Swarm",
        "sensitive_data": "Do not send",
        "logging": "Metadata only",
        "guardrails": "Client-side detection + warnings",
    },
    "trusted": {
        "label": "Private Swarm",
        "sensitive_data": "Use judgment",
        "logging": "Configurable",
        "guardrails": "--private flag available",
    },
    "full": {
        "label": "Org Swarm",
        "sensitive_data": "Permitted",
        "logging": "Org-defined policy",
        "guardrails": "IT-managed trust",
    },
    "local": {
        "label": "Local Inference",
        "sensitive_data": "Anything",
        "logging": "Your machine",
        "guardrails": "No network exposure",
    },
}
