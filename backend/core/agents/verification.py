"""
backend/core/agents/verification.py

"Did configure() actually take effect?" -- agent-agnostic.

Writing an agent's config is only half the job. An agent can accept a
`config set` and still ignore it: Hermes reads its model id from
`model.default`, so writing `model.name` succeeded silently while the banner
stayed on the stock cloud model. This module reads every setting back
through the adapter and reports, per key, one of:

  ok          the agent reports the value we asked for
  mismatch    the agent reports something else (the interesting case)
  unreadable  the adapter couldn't read it back (unsupported / command failed)
              -- NOT a failure: we simply can't tell
  skipped     a sensitive value (API key); never read, compared or shown

Comparison is deliberately lenient about presentation (case, quotes,
`key: value` / YAML framing) because every agent prints settings
differently: the expected value just has to appear in what the agent says.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from .base import AgentAdapter

# A value that still looks like a hosted-provider model after configure() is
# almost always a stale default that won the lookup.
_CLOUD_HINT = re.compile(
    r"claude|anthropic|opus|sonnet|haiku|gpt-?\d|openai|gemini|mistral-large|grok|deepseek-(chat|reasoner)",
    re.IGNORECASE,
)


def looks_like_cloud_model(value: Optional[str]) -> bool:
    return bool(value) and bool(_CLOUD_HINT.search(value))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace('"', "").replace("'", "")).strip().lower()


def value_matches(expected: str, actual: Optional[str]) -> bool:
    if actual is None:
        return False
    return _normalize(expected) in _normalize(actual)


@dataclass
class SettingCheck:
    key: str
    status: str  # ok | mismatch | unreadable | skipped
    expected: Optional[str] = None  # None for sensitive keys
    actual: Optional[str] = None  # None for sensitive keys
    detail: Optional[str] = None


@dataclass
class VerificationReport:
    backend_id: str
    checks: list[SettingCheck] = field(default_factory=list)

    @property
    def mismatches(self) -> list[SettingCheck]:
        return [c for c in self.checks if c.status == "mismatch"]

    @property
    def verified(self) -> bool:
        """True only if at least one value was positively confirmed and none
        contradicted. All-unreadable is "unknown", not verified."""
        return not self.mismatches and any(c.status == "ok" for c in self.checks)

    @property
    def ok(self) -> bool:
        """No contradiction found (unknown is not a contradiction)."""
        return not self.mismatches

    def to_dict(self) -> dict:
        return {
            "backend_id": self.backend_id,
            "ok": self.ok,
            "verified": self.verified,
            "checks": [asdict(c) for c in self.checks],
        }


def verify_settings(adapter: AgentAdapter, expected: dict[str, str]) -> VerificationReport:
    report = VerificationReport(backend_id=adapter.backend_id)
    for key, want in expected.items():
        if key in adapter.sensitive_settings:
            report.checks.append(SettingCheck(key, "skipped", detail="sensitive value not checked"))
            continue
        try:
            actual = adapter.read_setting(key)
        except Exception as exc:  # noqa: BLE001 - any reader failure means "can't tell"
            report.checks.append(SettingCheck(key, "unreadable", expected=want, detail=str(exc)))
            continue
        if value_matches(want, actual):
            report.checks.append(SettingCheck(key, "ok", expected=want, actual=actual))
            continue
        detail = "the agent reports a different value than the one we set"
        if looks_like_cloud_model(actual):
            detail += " -- it still looks like a hosted cloud model (a stale default is winning)"
        report.checks.append(SettingCheck(key, "mismatch", expected=want, actual=actual, detail=detail))
    return report
