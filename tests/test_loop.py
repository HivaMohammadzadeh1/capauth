import asyncio

import pytest

from agent.loop import SCENARIOS, Run, execute
from agent.scripted import ScriptedClient


def _decisions(res):
    return [e["decision"] for e in res.decisions]


def test_file_issue_with_scope_contains_the_injection():
    run = Run(SCENARIOS["file-issue"], scope_enabled=True, auto_approve=True)
    res = asyncio.run(execute(run, ScriptedClient()))
    assert _decisions(res) == ["ALLOW_LIMITED", "ALLOW", "DENY", "DENY", "ALLOW"]
    assert res.attempted_injection and not res.exfiltrated and res.legit_done
    kinds = [e["event"] for e in res.events]
    assert kinds[:3] == ["run_started", "plan", "lease_issued"]
    assert "injection_seen" in kinds and kinds[-2:] == ["lease_revoked", "run_finished"]
    assert res.events[-1]["data"]["chain_ok"] is True
    deny = res.decisions[2]
    assert "customer-data.csv" in deny["provenance"] and "thread" in deny["provenance"]
    assert res.decisions[0]["narrowed_to"] == "channel:#payments"
    assert run.world["email"]["outbox"] == []
    assert len(run.world["github"]["acme/payments-api"]["issues"]) == 1


def test_file_issue_without_scope_leaks():
    run = Run(SCENARIOS["file-issue"], scope_enabled=False, auto_approve=True)
    res = asyncio.run(execute(run, ScriptedClient()))
    assert set(_decisions(res)) == {"ALLOW"}
    assert res.exfiltrated and res.legit_done
    assert run.world["email"]["outbox"][0]["to"] == "security-review@vendor-audit.com"


def test_fix_deploy_requires_approval_and_honours_it():
    run = Run(SCENARIOS["fix-deploy"], scope_enabled=True, auto_approve=True)
    res = asyncio.run(execute(run, ScriptedClient()))
    kinds = [e["event"] for e in res.events]
    assert "approval_requested" in kinds and "approval_resolved" in kinds
    assert _decisions(res) == ["ALLOW", "ALLOW"]
    assert res.decisions[1]["approval"]["outcome"] == "approved_once"
    assert run.world["github"]["acme/payments-api"]["prs"]["481"]["merged"] is True


def test_fix_deploy_denied_by_operator():
    async def go():
        run = Run(SCENARIOS["fix-deploy"], scope_enabled=True, auto_approve=False)
        task = asyncio.create_task(execute(run, ScriptedClient()))
        while not any(e["event"] == "approval_requested" for e in run.events):
            await asyncio.sleep(0.01)
        ap = next(e for e in run.events if e["event"] == "approval_requested")["data"]["approval_id"]
        assert run.resolve_approval(ap, "denied", "hiva@acme.com")
        res = await task
        return run, res

    run, res = asyncio.run(go())
    assert _decisions(res) == ["ALLOW", "DENY"]
    assert run.world["github"]["acme/payments-api"]["prs"]["481"]["merged"] is False


def test_subscribe_replays_then_ends():
    async def go():
        run = Run(SCENARIOS["file-issue"], scope_enabled=True, auto_approve=True)
        await execute(run, ScriptedClient())
        seen = [e["event"] async for e in run.subscribe()]
        return seen

    seen = asyncio.run(go())
    assert seen[0] == "run_started" and seen[-1] == "run_finished"
