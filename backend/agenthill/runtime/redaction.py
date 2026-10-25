"""Redaction of sensitive values before traces and responses are persisted."""
from __future__ import annotations

import re
from typing import Any

PATTERNS: list[tuple[re.Pattern, Any]] = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-NATIONAL-ID]"),
    (re.compile(r"\b(sk|key|token)-[A-Za-z0-9_\-]{8,}\b"), "[REDACTED-SECRET]"),
    (re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"), lambda m: f"{m.group(1)}***@{m.group(2)}"),
    (re.compile(r"\+1-555-\d{4}\b"), "+1-555-****"),
]
SENSITIVE_KEYS = {"national_id", "base_salary", "api_key", "password"}


def redact_text(text: str) -> str:
    for pat, repl in PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if k in SENSITIVE_KEYS and v is not None else redact(v)) for k, v in obj.items()}
    return obj


def contains_sensitive(text: str) -> list[str]:
    found = []
    if PATTERNS[0][0].search(text or ""):
        found.append("national_id")
    if PATTERNS[1][0].search(text or ""):
        found.append("secret")
    return found
