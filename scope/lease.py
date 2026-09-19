"""Leases: short-lived, signed, task-bound capability sets."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from scope.model import Capability

_SIGNING_KEY = os.environ.get("SCOPE_SIGNING_KEY", secrets.token_hex(32)).encode()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Lease:
    lease_id: str
    principal: str
    on_behalf_of: str
    task: str
    capabilities: list[Capability]
    sensitive: list[tuple[str, str]]
    issued_at: datetime
    expires_at: datetime
    sig: str = ""
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    approved_for_task: set[tuple[str, str, str]] = field(default_factory=set)
    parent_lease_id: str | None = None
    depth: int = 0

    # -- signing -----------------------------------------------------------
    def _payload(self) -> bytes:
        body = {
            "lease_id": self.lease_id,
            "principal": self.principal,
            "on_behalf_of": self.on_behalf_of,
            "task": self.task,
            "capabilities": [c.as_dict() for c in self.capabilities],
            "sensitive": sorted(self.sensitive),
            "issued_at": _iso(self.issued_at),
            "expires_at": _iso(self.expires_at),
            "parent_lease_id": self.parent_lease_id,
            "depth": self.depth,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    def sign(self) -> "Lease":
        digest = hmac.new(_SIGNING_KEY, self._payload(), hashlib.sha256).hexdigest()
        self.sig = f"hmac-sha256:{digest}"
        return self

    def verify(self) -> bool:
        if not self.sig.startswith("hmac-sha256:"):
            return False
        expected = hmac.new(_SIGNING_KEY, self._payload(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(self.sig.split(":", 1)[1], expected)

    # -- lifecycle ---------------------------------------------------------
    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at

    def is_active(self, now: datetime | None = None) -> bool:
        return self.revoked_at is None and not self.is_expired(now)

    def revoke(self, reason: str, now: datetime | None = None) -> None:
        self.revoked_at = now or datetime.now(timezone.utc)
        self.revoke_reason = reason

    def find(self, tool: str, action: str) -> list[Capability]:
        return [c for c in self.capabilities if (c.tool, c.action) == (tool, action)]

    @property
    def ttl_seconds(self) -> int:
        return int((self.expires_at - self.issued_at).total_seconds())

    def as_dict(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "principal": self.principal,
            "on_behalf_of": self.on_behalf_of,
            "task": self.task,
            "capabilities": [c.as_dict() for c in self.capabilities],
            "sensitive": [{"tool": t, "action": a} for t, a in self.sensitive],
            "issued_at": _iso(self.issued_at),
            "expires_at": _iso(self.expires_at),
            "ttl_seconds": self.ttl_seconds,
            "sig": self.sig,
            "revoked_at": _iso(self.revoked_at) if self.revoked_at else None,
            "parent_lease_id": self.parent_lease_id,
            "depth": self.depth,
        }


def lease_from_dict(d: dict) -> Lease:
    def _parse(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    return Lease(
        lease_id=d["lease_id"], principal=d["principal"], on_behalf_of=d["on_behalf_of"], task=d["task"],
        capabilities=[Capability(**c) for c in d["capabilities"]],
        sensitive=[(s["tool"], s["action"]) for s in d["sensitive"]],
        issued_at=_parse(d["issued_at"]), expires_at=_parse(d["expires_at"]), sig=d.get("sig", ""),
        parent_lease_id=d.get("parent_lease_id"), depth=int(d.get("depth", 0)),
    )


def issue_lease(
    *,
    principal: str,
    on_behalf_of: str,
    task: str,
    capabilities: list[Capability],
    sensitive: list[tuple[str, str]],
    ttl_seconds: int,
    now: datetime | None = None,
) -> Lease:
    now = now or datetime.now(timezone.utc)
    lease = Lease(
        lease_id=f"sc_{secrets.token_hex(2)}",
        principal=principal,
        on_behalf_of=on_behalf_of,
        task=task,
        capabilities=list(capabilities),
        sensitive=list(sensitive),
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    return lease.sign()


class DelegationError(ValueError):
    """The child lease would hold more than its parent."""


def delegate(parent: Lease, *, child_principal: str, task: str, capabilities: list[Capability],
             ttl_seconds: int | None = None, now: datetime | None = None) -> Lease:
    """Issue a child lease for a sub-agent. Authority narrows at every hop:

    - every child capability must be covered by some parent capability with the same tool and action
    - the child cannot outlive the parent
    - the child carries parent_lease_id and depth + 1, so the ledger shows the chain
    """
    now = now or datetime.now(timezone.utc)
    if not parent.is_active(now):
        raise DelegationError("parent lease is not active")
    if not parent.verify():
        raise DelegationError("parent lease signature invalid")
    for cap in capabilities:
        covering = [p for p in parent.find(cap.tool, cap.action) if cap.is_narrower_than(p)]
        if not covering:
            raise DelegationError(f"{cap.tool}.{cap.action} on {cap.resource} is wider than the parent lease")
    remaining = int((parent.expires_at - now).total_seconds())
    ttl = min(ttl_seconds or remaining, remaining)
    child = Lease(
        lease_id=f"sc_{secrets.token_hex(2)}",
        principal=child_principal,
        on_behalf_of=parent.on_behalf_of,
        task=task,
        capabilities=list(capabilities),
        sensitive=list(parent.sensitive),
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl),
        parent_lease_id=parent.lease_id,
        depth=parent.depth + 1,
    )
    return child.sign()
