from __future__ import annotations

import re


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("auth_or_token", re.compile(r"\b(401|403|auth|token|credential|expired)\b", re.I)),
    ("http_500", re.compile(r"\b(500|502|503|504|internal server error|bad gateway)\b", re.I)),
    ("timeout", re.compile(r"\b(timeout|timed out|deadline exceeded)\b", re.I)),
    ("connection_refused", re.compile(r"\b(connection refused|connect refused|econnrefused)\b", re.I)),
    ("permission_denied", re.compile(r"\b(permission denied|not allowed|forbidden)\b", re.I)),
    ("mcp_unavailable", re.compile(r"\b(mcp.*unavailable|mcp.*failed|server unavailable)\b", re.I)),
]


def error_fingerprint(text: str) -> str:
    """Return a stable failure bucket for grouping related signals."""
    normalized = text.lower()
    for name, pattern in _PATTERNS:
        if pattern.search(normalized):
            return name
    normalized = re.sub(r"sess_[a-z0-9_\\-]+", "sess", normalized)
    normalized = re.sub(r"\b(?:h200|b300)-\d+\b", "node", normalized)
    normalized = re.sub(r"\b\d+\b", "n", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:80] or "unknown_error"


def problem_title_for_tool(tool_name: str | None, fingerprint: str) -> str:
    if tool_name == "submit_triage_alert":
        return "Alert submission path broken"
    if tool_name == "delegate_to_agent":
        return "Delegation path failed"
    if tool_name in {"reallocate_node", "run_full_config", "clone_and_allocate"}:
        return "Recycler return-to-service flow failing"
    if tool_name:
        return f"{tool_name} capability failing"
    return f"Agent capability failing: {fingerprint}"
