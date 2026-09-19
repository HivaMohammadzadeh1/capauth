"""Shared value types. Pure data, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from fnmatch import fnmatchcase
from typing import Any


class Outcome(str, Enum):
    ALLOW = "ALLOW"
    ALLOW_LIMITED = "ALLOW_LIMITED"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    DENY = "DENY"


@dataclass(frozen=True)
class Capability:
    tool: str
    action: str
    resource: str  # glob, e.g. "channel:#payments" or "repo:acme/*"

    def covers(self, resource: str) -> bool:
        return fnmatchcase(resource, self.resource)

    def is_narrower_than(self, other: "Capability") -> bool:
        """True when every resource this capability covers, `other` also covers."""
        return fnmatchcase(self.resource, other.resource)

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ToolCall:
    tool: str
    action: str
    resource: str
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def call_str(self) -> str:
        inner = ", ".join(f"{k}={v!r}" for k, v in self.args.items() if k != "body")
        return f"{self.tool}.{self.action}({inner})"


@dataclass
class Decision:
    outcome: Outcome
    reason: str
    narrowed_to: str | None = None
    provenance: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.outcome.value,
            "reason": self.reason,
            "narrowed_to": self.narrowed_to,
            "provenance": self.provenance,
        }
