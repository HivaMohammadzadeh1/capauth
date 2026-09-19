from datetime import datetime, timedelta, timezone

import pytest

from scope.enforce import decide
from scope.lease import issue_lease
from scope.model import Capability, Outcome, ToolCall
from scope.policy import load_policy

NOW = datetime(2026, 9, 19, 16, 32, 10, tzinfo=timezone.utc)


@pytest.fixture
def policy():
    return load_policy("engineering-assistant")


@pytest.fixture
def lease(policy):
    caps = [
        Capability("slack", "search", "channel:#payments"),
        Capability("slack", "read_thread", "channel:#payments/*"),
        Capability("github", "create_issue", "repo:acme/payments-api"),
    ]
    return issue_lease(principal=policy.principal, on_behalf_of="user:hiva@acme.com", task="file the payment bug",
                       capabilities=caps, sensitive=sorted(policy.sensitive), ttl_seconds=600, now=NOW)


def test_allow_inside_lease(lease, policy):
    d = decide(ToolCall("slack", "search", "channel:#payments", {"channel": "#payments"}), lease, policy, NOW)
    assert d.outcome is Outcome.ALLOW


def test_allow_thread_under_glob(lease, policy):
    d = decide(ToolCall("slack", "read_thread", "channel:#payments/thread:18291"), lease, policy, NOW)
    assert d.outcome is Outcome.ALLOW


def test_limited_narrows_wildcard(lease, policy):
    d = decide(ToolCall("slack", "search", "channel:*", {"channel": "*"}), lease, policy, NOW)
    assert d.outcome is Outcome.ALLOW_LIMITED
    assert d.narrowed_to == "channel:#payments"


def test_deny_resource_outside_lease(lease, policy):
    d = decide(ToolCall("drive", "read_file", "file:customer-data.csv"), lease, policy, NOW)
    assert d.outcome is Outcome.DENY
    assert "not in lease" in d.reason


def test_deny_other_channel(lease, policy):
    d = decide(ToolCall("slack", "search", "channel:#exec-private"), lease, policy, NOW)
    assert d.outcome is Outcome.DENY


def test_deny_external_email(lease, policy):
    d = decide(ToolCall("email", "send", "recipient:security-review@vendor-audit.com"), lease, policy, NOW)
    assert d.outcome is Outcome.DENY


def test_human_approval_for_sensitive(policy):
    caps = [Capability("github", "merge_pr", "repo:acme/*")]
    lease = issue_lease(principal=policy.principal, on_behalf_of="u", task="deploy", capabilities=caps,
                        sensitive=sorted(policy.sensitive), ttl_seconds=600, now=NOW)
    d = decide(ToolCall("github", "merge_pr", "repo:acme/payments-api/pr:481"), lease, policy, NOW)
    assert d.outcome is Outcome.HUMAN_APPROVAL
    lease.approved_for_task.add(("github", "merge_pr", "repo:acme/payments-api/pr:481"))
    assert decide(ToolCall("github", "merge_pr", "repo:acme/payments-api/pr:481"), lease, policy, NOW).outcome is Outcome.ALLOW


def test_deny_after_expiry(lease, policy):
    later = NOW + timedelta(seconds=601)
    d = decide(ToolCall("slack", "search", "channel:#payments"), lease, policy, later)
    assert d.outcome is Outcome.DENY and "expired" in d.reason


def test_deny_after_revoke(lease, policy):
    lease.revoke("task_complete", NOW)
    assert decide(ToolCall("slack", "search", "channel:#payments"), lease, policy, NOW).outcome is Outcome.DENY


def test_deny_tampered_lease(lease, policy):
    lease.capabilities.append(Capability("drive", "read_file", "file:*"))
    d = decide(ToolCall("drive", "read_file", "file:customer-data.csv"), lease, policy, NOW)
    assert d.outcome is Outcome.DENY and "signature" in d.reason


def test_never_list_wins(policy):
    caps = [Capability("github", "delete_repo", "repo:acme/*")]
    lease = issue_lease(principal=policy.principal, on_behalf_of="u", task="t", capabilities=caps,
                        sensitive=[], ttl_seconds=600, now=NOW)
    assert decide(ToolCall("github", "delete_repo", "repo:acme/website"), lease, policy, NOW).outcome is Outcome.DENY
