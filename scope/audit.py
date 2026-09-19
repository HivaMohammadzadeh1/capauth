"""Hash-chained audit ledger. Each entry commits to the one before it."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

GENESIS = "0" * 64


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class Ledger:
    def __init__(self, lease_id: str, principal: str, on_behalf_of: str, task: str):
        self.lease_id = lease_id
        self.principal = principal
        self.on_behalf_of = on_behalf_of
        self.task = task
        self.entries: list[dict[str, Any]] = []

    def append(
        self,
        *,
        call: dict[str, Any],
        decision: str,
        reason: str,
        narrowed_to: str | None = None,
        provenance: str | None = None,
        approval: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        at = at or datetime.now(timezone.utc)
        prev = self.entries[-1]["hash"] if self.entries else GENESIS
        entry = {
            "seq": len(self.entries) + 1,
            "at": at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "lease_id": self.lease_id,
            "principal": self.principal,
            "on_behalf_of": self.on_behalf_of,
            "task": self.task,
            "call": call,
            "decision": decision,
            "reason": reason,
            "narrowed_to": narrowed_to,
            "provenance": provenance,
            "approval": approval,
            "prev_hash": prev,
        }
        entry["hash"] = hashlib.sha256((prev + _canon(entry)).encode()).hexdigest()
        self.entries.append(entry)
        return entry

    def verify(self) -> bool:
        prev = GENESIS
        for e in self.entries:
            body = {k: v for k, v in e.items() if k != "hash"}
            if body["prev_hash"] != prev:
                return False
            if hashlib.sha256((prev + _canon(body)).encode()).hexdigest() != e["hash"]:
                return False
            prev = e["hash"]
        return True

    def export(self) -> dict[str, Any]:
        return {"lease_id": self.lease_id, "chain_ok": self.verify(), "entries": list(self.entries)}


def attest(export: dict[str, Any], lease: dict[str, Any] | None = None) -> dict[str, Any]:
    """A compact record a reviewer can file: the lease, the head hash, the count, and whether the chain verifies."""
    entries = export.get("entries", [])
    head = entries[-1]["hash"] if entries else GENESIS
    return {
        "lease_id": export.get("lease_id"),
        "principal": entries[0]["principal"] if entries else None,
        "on_behalf_of": entries[0]["on_behalf_of"] if entries else None,
        "task": entries[0]["task"] if entries else None,
        "entries": len(entries),
        "decisions": {d: sum(1 for e in entries if e["decision"] == d) for d in ("ALLOW", "ALLOW_LIMITED", "HUMAN_APPROVAL", "DENY")},
        "head_hash": head,
        "chain_ok": verify_entries(entries),
        "lease_sig": (lease or {}).get("sig"),
        "attested_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def verify_entries(entries: list[dict[str, Any]]) -> bool:
    probe = Ledger("", "", "", "")
    probe.entries = list(entries)
    return probe.verify()


def to_jsonl(entries: list[dict[str, Any]]) -> str:
    """One JSON object per line, for a SIEM or log pipeline."""
    return "".join(_canon(e) + "\n" for e in entries)


if __name__ == "__main__":  # uv run python -m scope.audit verify audit.json
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "verify":
        data = json.load(open(sys.argv[2]))
        entries = data.get("entries", data if isinstance(data, list) else [])
        ok = verify_entries(entries)
        print(f"{len(entries)} entries, chain {'intact' if ok else 'BROKEN'}, head {entries[-1]['hash'][:16] if entries else GENESIS[:16]}")
        sys.exit(0 if ok else 1)
    print("usage: python -m scope.audit verify <audit.json>")
