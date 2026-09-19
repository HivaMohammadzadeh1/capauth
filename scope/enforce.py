"""The enforcer. Pure code. No model call. No I/O.

decide(call, lease, policy) -> Decision
"""
from __future__ import annotations

from datetime import datetime, timezone

from scope.lease import Lease
from scope.model import Decision, Outcome, ToolCall
from scope.policy import Policy


def decide(call: ToolCall, lease: Lease, policy: Policy, now: datetime | None = None) -> Decision:
    now = now or datetime.now(timezone.utc)

    if not lease.verify():
        return Decision(Outcome.DENY, "lease signature invalid")
    if lease.revoked_at is not None:
        return Decision(Outcome.DENY, f"lease revoked ({lease.revoke_reason})")
    if lease.is_expired(now):
        return Decision(Outcome.DENY, "lease expired")
    if policy.is_forbidden(call.tool, call.action):
        return Decision(Outcome.DENY, f"{call.tool}.{call.action} is never permitted for {lease.principal}")

    caps = lease.find(call.tool, call.action)
    if not caps:
        return Decision(Outcome.DENY, f"action not in lease: {call.tool}.{call.action}. The task does not require it.")

    covering = [c for c in caps if c.covers(call.resource)]
    if covering:
        if policy.is_sensitive(call.tool, call.action):
            key = (call.tool, call.action, call.resource)
            if key in lease.approved_for_task:
                return Decision(Outcome.ALLOW, f"approved for this task earlier: {call.tool}.{call.action}")
            return Decision(Outcome.HUMAN_APPROVAL, f"{call.tool}.{call.action} is sensitive under policy for {lease.principal}")
        return Decision(Outcome.ALLOW, f"inside lease: {call.tool}.{call.action} on {covering[0].resource}")

    # The call asks for more than the lease holds. If the lease's resource is a
    # strict subset of what was asked, narrow the call to it instead of failing.
    narrowable = [c for c in caps if c.is_narrower_than(_as_cap(call))]
    if narrowable:
        if policy.is_sensitive(call.tool, call.action):
            return Decision(Outcome.HUMAN_APPROVAL, f"{call.tool}.{call.action} is sensitive under policy", narrowed_to=narrowable[0].resource)
        return Decision(
            Outcome.ALLOW_LIMITED,
            f"{call.resource} narrowed to {narrowable[0].resource}, then run",
            narrowed_to=narrowable[0].resource,
        )

    held = ", ".join(c.resource for c in caps)
    return Decision(Outcome.DENY, f"resource not in lease: {call.resource}. Lease holds {held} for this action.")


def _as_cap(call: ToolCall):
    from scope.model import Capability

    return Capability(call.tool, call.action, call.resource)
