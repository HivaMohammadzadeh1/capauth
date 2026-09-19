# Scope

Task-scoped permissions for AI agents.

An agent should not get everything its user can reach. Scope reads the task and the
agent's own plan, issues a short-lived signed lease that holds only the capabilities
that task needs, checks every tool call against the lease, and revokes the lease when
the task ends. Every decision lands in a hash-chained audit ledger.

## The one rule

> The model proposes. Code enforces.

The security planner is a Claude call, so it can be wrong or manipulated. It can only
narrow a static policy ceiling, never widen it. The enforcer that gates every tool call
is deterministic code and makes no model call.

## What it is

Scope is an authorization plane that sits between an agent and its tools. The agent holds
a lease, never a raw tool credential. Scope turns a lease into a tool call only when the
call fits the lease. Each call gets one of four decisions.

- `ALLOW`. The call is inside the lease. It runs.
- `ALLOW_LIMITED`. The call is wider than the lease. Scope narrows it, then runs the narrow version.
- `HUMAN_APPROVAL`. The action is sensitive under policy. A person decides, once or for the task.
- `DENY`. The resource or action is outside the lease. The call never reaches the tool.

## Why

An agent today runs with its user's full tokens across Slack, GitHub, Drive, and email.
One task needs three narrow things. The rest is standing access an attacker can borrow
through a prompt injection. Scope removes the standing access. It does not try to detect
the injection. It makes the injected action unreachable, because the action is not in the
lease.

## How it works

The flow is one loop.

1. The operator gives the agent a task and picks the agent identity.
2. The agent writes a short plan before any tool call.
3. The lease issuer runs the security planner, caps the result against the policy ceiling,
   signs the lease, and starts the TTL.
4. The agent sends each tool call with its lease to the enforcer.
5. The enforcer verifies the signature and TTL, then matches tool, action, and resource.
   It returns `ALLOW`, `ALLOW_LIMITED`, `HUMAN_APPROVAL`, or `DENY`.
6. Allowed calls reach the tools. Sensitive calls wait for the operator. Denied calls stop.
7. Every decision is written to the audit ledger, each entry hashing the one before it.
8. The task ends or the TTL runs out, and the lease is revoked.

Tool results return to the agent as untrusted content. That path can carry a prompt
injection. Scope does not filter it. Whatever the agent reads there, the next tool call
still has to fit the lease.

The full architecture diagram, lifecycle, and console mockup live in the design brief.
A standalone diagram is at `docs/architecture.svg`.

## How to run

Requirements: Python 3.13 and `uv`.

```
uv run python server.py     # serve the console at http://localhost:8000
uv run pytest               # run the enforcement and ledger tests
uv run python bench/run.py  # run the measurement, Scope on vs off
```

Open http://localhost:8000 and pick a scenario.

- `file-issue`. File the payment bug. The Slack thread carries the injection.
- `fix-deploy`. Fix and deploy the payment bug. Merging the pull request triggers human approval.

## The honest measurement

The script runs scenario 1 twenty times with Scope on and twenty times with Scope off,
against the same injected thread. It reports what the agent tried and what actually left
the org. The table below shows the shape. Numbers arrive after the benchmark runs.

| Condition | Runs | Injected action attempted | Customer data left the org | Legitimate issue filed |
|---|---|---|---|---|
| Scope off, full tokens | 20 | measured at 17:00 | measured at 17:00 | measured at 17:00 |
| Scope on, a lease | 20 | measured at 17:00 | 0 by construction | measured at 17:00 |

The claim is the second row, fourth column. The cost we admit is the last column. A lease
that is too tight fails the legitimate task, and that number sits in the same table.

## Layout

| Path | Owns |
|---|---|
| `scope/policy/*.yaml` | Per-identity ceiling: max tools, actions, resources, sensitive actions |
| `scope/planner.py` | Claude call with a strict JSON schema; proposes capabilities from task and plan |
| `scope/lease.py` | Issue, sign, verify, expire, revoke |
| `scope/enforce.py` | The decision function; pure, no I/O |
| `scope/audit.py` | Hash-chained ledger, JSON export, chain verify |
| `tools/` | Slack, GitHub, Drive, email mocks with fixtures and one injected thread |
| `agent/loop.py` | Claude tool-use loop; every call goes through the enforcer, then the tool |
| `server.py` | HTTP server, event stream, approval endpoint, audit export |
| `ui/index.html` | The console, vanilla JS over the event stream |
| `bench/run.py` | The measurement |
| `tests/` | Four decisions, narrowing, expiry, signature, chain tamper |
