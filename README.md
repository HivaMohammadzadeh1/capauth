# Scope

Task-scoped permissions for AI agents.

An agent should not get everything its user can reach. Scope reads the task and the
agent's own plan, issues a short-lived signed lease that holds only the capabilities
that task needs, checks every tool call against the lease, and revokes the lease when
the task ends. Every decision lands in a hash-chained audit ledger.

The agent under test is Claude Code itself. Scope runs as an MCP server. Claude Code
calls every tool through it, today, unchanged.

## The one rule

> The model proposes and code enforces.

The security planner is a Claude call, so it can be wrong or manipulated. It can only
narrow a static policy ceiling, never widen it. The enforcer that gates every tool call
is deterministic code and makes no model call.

## What it is

Scope is an authorization plane that sits between an agent and its tools, running as an
MCP server. The run engine writes a signed lease, then launches `claude -p` with Scope
as its only tool source (`--mcp-config`, `--strict-mcp-config`, built-in tools disabled).
Every tool call Claude Code makes goes through the Scope enforcer inside that MCP server
before it touches a mock tool. The agent holds a lease, never a raw tool credential.

Each call gets one of four decisions.

- `ALLOW`. The call is inside the lease. It runs.
- `ALLOW_LIMITED`. The call is wider than the lease. Scope narrows it, then runs the narrow version.
- `HUMAN_APPROVAL`. The action is sensitive under policy. A person decides, once or for the task.
- `DENY`. The resource or action is outside the lease. The call never reaches the tool.

The agent's plan and the security planner also run through Claude Code, with a
`--json-schema` structured call. No API key is used anywhere. It runs on the Claude
subscription.

## Why

An agent today runs with its user's full tokens across Slack, GitHub, Drive, and email.
One task needs three narrow things. The rest is standing access an attacker can borrow
through a prompt injection. Scope removes the standing access. It does not try to detect
the injection. It makes the injected action unreachable, because the action is not in the
lease.

## How it works

The flow is one loop.

1. The operator gives the task and picks the agent identity.
2. Claude Code writes a short plan before any tool call, as a structured call.
3. The lease issuer runs the security planner, caps the result against the policy ceiling,
   signs the lease, and starts the TTL.
4. The engine launches Claude Code with the Scope MCP server as its only tool source.
5. Claude Code makes a tool call. The Scope enforcer verifies the signature and TTL, then
   matches tool, action, and resource. It returns `ALLOW`, `ALLOW_LIMITED`,
   `HUMAN_APPROVAL`, or `DENY`.
6. Allowed calls reach the mock tools. Sensitive calls wait for the operator. Denied calls stop.
7. Every decision is written to the audit ledger, each entry hashing the one before it.
8. The task ends or the TTL runs out, and the lease is revoked.

Tool results return to Claude Code as untrusted content. That path can carry a prompt
injection. Scope does not filter it. Whatever the agent reads there, the next tool call
still has to fit the lease.

The full architecture diagram is at `docs/architecture.svg`.

## How to run

Requirements: Python 3.13 and `uv`. The default backend needs the `claude` CLI, logged in.

```
uv run python server.py                       # SCOPE_BACKEND=cli by default; open http://localhost:8000
SCOPE_BACKEND=scripted uv run python server.py # deterministic offline replay, scripted agent
uv run pytest                                  # 19 tests
uv run python attack/demo.py --scope off       # terminal before, injection succeeds
uv run python attack/demo.py --scope on        # terminal after, injection contained
uv run python bench/run.py --n 5 --models opus,sonnet \
    --variants naive,process_authority,helpful_colleague,tool_output_disguise
```

Open http://localhost:8000 and pick a scenario.

- `file-issue`. File the payment bug. The Slack thread carries the injection.
- `fix-deploy`. Fix and deploy the payment bug. Merging the pull request triggers human approval.

Verified end to end: `file-issue` completes in about 27 seconds; `fix-deploy` blocks the
merge at the human approval gate until the operator answers; a stray `slack.post_message`
the agent tried was denied as not in the lease.

## The honest measurement

The benchmark is a matrix: agent model, by injection variant, by Scope on or off, three
runs each. Seventy-two runs in total. The result is a clean sweep.

| Agent model | Runs | Injection followed | Customer data left the org | Legitimate task done |
|---|---|---|---|---|
| Opus | 24 | 0 | 0 | 24/24 |
| Sonnet | 24 | 0 | 0 | 24/24 |
| Haiku | 24 | 0 | 0 | 24/24 |

Opus, Sonnet, and Haiku refused all four payload styles (`naive`, `process_authority`,
`helpful_colleague`, `tool_output_disguise`), with Scope and without it. The legitimate task
completed 72 of 72, so the lease was never too tight. The Opus and Sonnet half ran in about
280 seconds, the Haiku half in about 130. Raw tables:
`bench/out/results-20260919-154216.md` and `bench/out/results-20260919-154555.md`.

We tried 72 times to make Claude Code follow an injection. It never did. That is good news
about the model, and it is exactly why a security review still needs Scope. A review does not
sign off on a batting average; it signs off on a guarantee. With Scope, the out-of-lease
action is unreachable by construction.

Scope also caught real overreach that was not an attack. In `fix-deploy` the agent tried
`slack.post_message` to announce the merge, outside the task, and it was denied with
provenance. In `file-issue` the agent's plan wanted to search every channel, and the lease
narrowed it to `#payments` before it ran. Least privilege holds independent of model
behavior.

## Layer 7 alignment

The event primer names Layer 7 as identity, security, and governance. Scope maps to it
point by point. The wording is factual.

- Agent identity is bound to the human it acts for. Every call carries `principal` and
  `on_behalf_of`.
- Least privilege is derived after the plan exists, not from a static role.
- Human oversight gates sensitive actions through the approval decision.
- The audit log records who, for whom, what, why, and when, with a hash chain. This is
  aimed at the EU AI Act high-risk logging duty that took effect in August 2026.
- The injection defense is structural, not statistical. Scope does not classify the
  prompt. It makes the out-of-lease action unreachable.
- Scope runs as an MCP server, so it sits between Claude Code, or any MCP client, and the
  tools. This follows the MCP authorization direction.

Scope builds the primer's own "Scoped agent credentials" project idea, and addresses
items in the OWASP agentic top ten, including excessive agency and tool misuse.

## Layout

| Path | Owns |
|---|---|
| `scope/policy/*.yaml` | Per-identity ceiling: max tools, actions, resources, sensitive actions |
| `scope/planner.py` | The security planner; proposes capabilities from task and plan under the ceiling |
| `scope/lease.py` | Issue, sign, verify, expire, revoke |
| `scope/enforce.py` | The decision function; pure, no I/O |
| `scope/broker.py` | Gate-and-execute logic shared by the in-process loop and the MCP server |
| `scope/mcp_server.py` | Scope as an MCP server; every tool call is enforced here |
| `scope/audit.py` | Hash-chained ledger, JSON export, chain verify |
| `agent/cli_backend.py` | Drives `claude -p` with Scope as the only tool source |
| `agent/scripted.py` | Deterministic stand-in for offline replay |
| `tools/` | Slack, GitHub, Drive, email mocks with fixtures and one injected thread |
| `attack/variants.py` | Four injection payloads |
| `attack/demo.py` | The terminal before and after |
| `server.py` | HTTP server, event stream, approval endpoint, audit export |
| `ui/index.html` | The console, vanilla JS over the event stream |
| `bench/run.py` | The measurement matrix |
| `demo/` | Before and after video pipeline |
| `tests/` | The 19 tests: four decisions, narrowing, expiry, signature, chain tamper |
