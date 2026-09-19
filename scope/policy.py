"""Org policy: the ceiling per agent identity. Loaded from YAML, never from a model."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from scope.model import Capability

POLICY_DIR = Path(__file__).parent / "policy"


@dataclass(frozen=True)
class Policy:
    principal: str
    org_domain: str
    ttl_seconds: int
    ceiling: tuple[Capability, ...]
    sensitive: frozenset[tuple[str, str]]  # (tool, action)
    never: frozenset[tuple[str, str]]

    def is_sensitive(self, tool: str, action: str) -> bool:
        return (tool, action) in self.sensitive

    def is_forbidden(self, tool: str, action: str) -> bool:
        return (tool, action) in self.never

    def clamp(self, proposed: list[Capability]) -> list[Capability]:
        """Return the proposed capabilities, each cut down to the ceiling.

        A proposal outside the ceiling is dropped. A proposal wider than the
        ceiling is narrowed to the ceiling's resource. A proposal for a
        forbidden action is dropped. Nothing here can widen the ceiling.
        """
        kept: list[Capability] = []
        for cap in proposed:
            if self.is_forbidden(cap.tool, cap.action):
                continue
            for lim in self.ceiling:
                if (lim.tool, lim.action) != (cap.tool, cap.action):
                    continue
                if cap.is_narrower_than(lim):
                    kept.append(cap)
                elif lim.is_narrower_than(cap):
                    kept.append(lim)
                break
        # de-duplicate, keep order
        seen: set[Capability] = set()
        out = []
        for c in kept:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def excluded_by(self, lease_caps: list[Capability]) -> list[dict[str, str]]:
        """Ceiling entries the lease left out, for display as crossed-out lines."""
        out = []
        for lim in self.ceiling:
            same = [c for c in lease_caps if (c.tool, c.action) == (lim.tool, lim.action)]
            if not same:
                out.append({**lim.as_dict(), "label": f"{lim.action.replace('_', ' ')} ({lim.resource})"})
            elif all(c.resource != lim.resource for c in same):
                out.append({**lim.as_dict(), "label": f"{lim.action.replace('_', ' ')} outside {same[0].resource}"})
        for tool, action in sorted(self.never):
            out.append({"tool": tool, "action": action, "resource": "*", "label": f"{action.replace('_', ' ')} (never for this identity)"})
        return out


def load_policy(agent: str) -> Policy:
    path = POLICY_DIR / f"{agent}.yaml"
    raw = yaml.safe_load(path.read_text())
    return Policy(
        principal=raw["principal"],
        org_domain=raw["org_domain"],
        ttl_seconds=int(raw.get("ttl_seconds", 600)),
        ceiling=tuple(Capability(**c) for c in raw.get("ceiling", [])),
        sensitive=frozenset((c["tool"], c["action"]) for c in raw.get("sensitive", [])),
        never=frozenset((c["tool"], c["action"]) for c in raw.get("never", [])),
    )
