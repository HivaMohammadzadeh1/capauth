from scope.audit import Ledger
from scope.model import Capability
from scope.policy import load_policy


def test_clamp_cannot_widen():
    p = load_policy("engineering-assistant")
    proposed = [
        Capability("slack", "search", "channel:#payments"),        # inside: kept
        Capability("email", "send", "recipient:*"),                # wider than ceiling: clamped
        Capability("github", "delete_repo", "repo:acme/*"),        # never: dropped
        Capability("aws", "read_secret", "secret:*"),              # unknown tool: dropped
    ]
    out = p.clamp(proposed)
    assert Capability("slack", "search", "channel:#payments") in out
    assert Capability("email", "send", "recipient:*@acme.com") in out
    assert all(c.tool != "aws" for c in out)
    assert all(c.action != "delete_repo" for c in out)


def test_excluded_labels():
    p = load_policy("engineering-assistant")
    ex = p.excluded_by([Capability("slack", "search", "channel:#payments")])
    labels = [e["label"] for e in ex]
    assert any("search outside channel:#payments" in l for l in labels)
    assert any("read file" in l for l in labels)


def test_ledger_chain_detects_tamper():
    led = Ledger("sc_1", "agent:x", "user:y", "task")
    led.append(call={"tool": "slack", "action": "search", "resource": "channel:#payments", "args": {}}, decision="ALLOW", reason="ok")
    led.append(call={"tool": "drive", "action": "read_file", "resource": "file:customer-data.csv", "args": {}}, decision="DENY", reason="no")
    assert led.verify()
    led.entries[0]["decision"] = "DENY"
    assert not led.verify()


def test_delegation_narrows_at_each_hop():
    from datetime import datetime, timedelta, timezone

    from scope.lease import DelegationError, delegate, issue_lease

    now = datetime(2026, 9, 19, 16, 0, tzinfo=timezone.utc)
    parent = issue_lease(principal="agent:coding-agent-7", on_behalf_of="user:hiva@acme.com", task="deploy",
                         capabilities=[Capability("slack", "read_thread", "channel:#payments/*"), Capability("github", "merge_pr", "repo:acme/*")],
                         sensitive=[("github", "merge_pr")], ttl_seconds=600, now=now)
    child = delegate(parent, child_principal="agent:summarizer", task="summarize the thread",
                     capabilities=[Capability("slack", "read_thread", "channel:#payments/thread:18291")], ttl_seconds=3600, now=now + timedelta(seconds=60))
    assert child.verify() and child.parent_lease_id == parent.lease_id and child.depth == 1
    assert child.expires_at <= parent.expires_at            # cannot outlive the parent
    assert child.on_behalf_of == parent.on_behalf_of        # the human is carried through
    import pytest

    with pytest.raises(DelegationError):
        delegate(parent, child_principal="agent:x", task="t", capabilities=[Capability("slack", "read_thread", "channel:*")], now=now)
    with pytest.raises(DelegationError):
        delegate(parent, child_principal="agent:x", task="t", capabilities=[Capability("drive", "read_file", "file:*")], now=now)
    parent.revoke("task_complete", now)
    with pytest.raises(DelegationError):
        delegate(parent, child_principal="agent:x", task="t", capabilities=[], now=now)


def test_attestation_and_jsonl():
    import json

    from scope.audit import attest, to_jsonl, verify_entries

    led = Ledger("sc_1", "agent:x", "user:y", "task")
    led.append(call={"tool": "slack", "action": "search", "resource": "channel:#payments", "args": {}}, decision="ALLOW", reason="ok")
    led.append(call={"tool": "drive", "action": "read_file", "resource": "file:customer-data.csv", "args": {}}, decision="DENY", reason="no")
    a = attest(led.export(), {"sig": "hmac-sha256:abc"})
    assert a["chain_ok"] and a["entries"] == 2 and a["decisions"]["DENY"] == 1 and a["head_hash"] == led.entries[-1]["hash"]
    lines = to_jsonl(led.entries).splitlines()
    assert len(lines) == 2 and verify_entries([json.loads(l) for l in lines])
