"""Guardrails: pluggable input/output filters that secure the agent boundary.

Guards run on user input before it reaches the model, and on model output
before it reaches the caller. Each guard returns a GuardResult; a guard can
pass, flag, transform (redact/truncate), or block the text entirely.

Configured declaratively in fastagent.yaml:

    agents:
      assistant:
        guardrails:
          input:
            - type: prompt_injection
              action: block
            - type: blocklist
              patterns: ["(?i)internal use only"]
          output:
            - type: pii_redact
            - type: max_length
              limit: 4000
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class GuardResult:
    guard: str
    direction: str  # "input" | "output"
    passed: bool
    action: str  # "pass" | "flag" | "redact" | "truncate" | "block"
    detail: str = ""


class Guard(ABC):
    """Base class for all guards."""

    name: str = "guard"

    @abstractmethod
    def check(self, text: str, direction: str) -> tuple[str, GuardResult]:
        """Inspect (and possibly transform) text. Returns (new_text, result)."""


class BlocklistGuard(Guard):
    """Blocks or flags text matching any of the given regex patterns."""

    name = "blocklist"

    def __init__(self, patterns: list[str], action: str = "block"):
        self.patterns = [re.compile(p) for p in patterns]
        self.action = action

    def check(self, text: str, direction: str) -> tuple[str, GuardResult]:
        for pattern in self.patterns:
            if pattern.search(text):
                return text, GuardResult(
                    guard=self.name,
                    direction=direction,
                    passed=False,
                    action=self.action,
                    detail=f"matched pattern {pattern.pattern!r}",
                )
        return text, GuardResult(self.name, direction, True, "pass")


class PromptInjectionGuard(BlocklistGuard):
    """Heuristic screen for common prompt-injection phrasings.

    This is a first line of defense, not a guarantee — combine with least-
    privilege tools and the --approve flow for anything destructive.
    """

    name = "prompt_injection"

    DEFAULT_PATTERNS = [
        r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts?)",
        r"(?i)disregard\s+(your|the)\s+(system\s+)?(prompt|instructions)",
        r"(?i)you\s+are\s+now\s+(DAN|jailbroken|unrestricted)",
        r"(?i)reveal\s+(your\s+)?(system\s+prompt|hidden\s+instructions)",
        r"(?i)repeat\s+(everything|the\s+text)\s+(above|before)",
        r"(?i)pretend\s+(you\s+have\s+no|there\s+are\s+no)\s+(rules|restrictions|guidelines)",
    ]

    def __init__(self, patterns: list[str] | None = None, action: str = "block"):
        super().__init__(patterns or self.DEFAULT_PATTERNS, action=action)


class PIIRedactGuard(Guard):
    """Redacts common PII patterns (emails, phone numbers, card numbers, SSNs)."""

    name = "pii_redact"

    PATTERNS = {
        "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
        "phone": re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(\d{2,4}\)[-.\s]?)?\d{3,4}[-.\s]\d{3,4}(?:[-.\s]\d{2,4})?\b"),
        "card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    }

    def check(self, text: str, direction: str) -> tuple[str, GuardResult]:
        hits: list[str] = []
        redacted = text
        for label, pattern in self.PATTERNS.items():
            redacted, count = pattern.subn(f"[REDACTED_{label.upper()}]", redacted)
            if count:
                hits.append(f"{label} x{count}")
        if hits:
            return redacted, GuardResult(
                self.name, direction, False, "redact", detail=", ".join(hits)
            )
        return text, GuardResult(self.name, direction, True, "pass")


class MaxLengthGuard(Guard):
    """Truncates text longer than `limit` characters."""

    name = "max_length"

    def __init__(self, limit: int = 8000):
        self.limit = limit

    def check(self, text: str, direction: str) -> tuple[str, GuardResult]:
        if len(text) > self.limit:
            return (
                text[: self.limit] + "\n[truncated by max_length guard]",
                GuardResult(
                    self.name, direction, False, "truncate",
                    detail=f"{len(text)} chars > limit {self.limit}",
                ),
            )
        return text, GuardResult(self.name, direction, True, "pass")


_GUARD_TYPES: dict[str, type[Guard]] = {
    "blocklist": BlocklistGuard,
    "prompt_injection": PromptInjectionGuard,
    "pii_redact": PIIRedactGuard,
    "max_length": MaxLengthGuard,
}


def build_guards(specs: list[dict[str, Any]]) -> list[Guard]:
    """Instantiate guards from config dicts like {"type": "pii_redact", ...}."""
    guards: list[Guard] = []
    for spec in specs or []:
        spec = dict(spec)
        guard_type = spec.pop("type")
        try:
            cls = _GUARD_TYPES[guard_type]
        except KeyError:
            known = ", ".join(sorted(_GUARD_TYPES))
            raise ValueError(f"Unknown guard type {guard_type!r}. Known types: {known}") from None
        guards.append(cls(**spec))
    return guards


def run_guards(
    guards: list[Guard], text: str, direction: str
) -> tuple[str, list[GuardResult], bool]:
    """Run guards in order. Returns (transformed_text, results, blocked)."""
    results: list[GuardResult] = []
    for guard in guards:
        text, result = guard.check(text, direction)
        results.append(result)
        if result.action == "block" and not result.passed:
            return text, results, True
    return text, results, False
