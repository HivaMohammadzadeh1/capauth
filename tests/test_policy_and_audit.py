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
