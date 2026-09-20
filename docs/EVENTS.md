# CapAuth: server/UI contract

One FastAPI server (`server.py`) serves `ui/index.html` at `/` and these endpoints.
The UI is vanilla HTML/JS and talks only to these. All JSON. All times ISO-8601 UTC.

## HTTP

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | `/` | | `ui/index.html` |
| GET | `/api/scenarios` | | `[{id, title, task, agent, on_behalf_of}]` |
| POST | `/api/runs` | `{scenario_id, scope_enabled: true}` | `{run_id}` |
| GET | `/api/runs/{run_id}/events` | | SSE stream (below). Replays all past events for that run first, then live ones. Ends after `run_finished`. |
| POST | `/api/approvals/{approval_id}` | `{outcome: "approved_once" \| "approved_for_task" \| "denied"}` | `{ok: true}` |
| GET | `/api/runs/{run_id}/audit` | | `{lease_id, chain_ok: bool, entries: [ledger entry]}` |
| GET | `/api/runs/{run_id}/replay` | | same event list as JSON array (for the "replay recording" button) |

## SSE events

Wire format per message: `event: <type>\ndata: <json>\n\n`. Every `data` carries `run_id`.
Order for a normal run: run_started, plan, lease_issued, (tool_call, decision, [approval_requested, approval_resolved], tool_result)*, agent_message?, lease_revoked, run_finished.

```
run_started      {run_id, scenario_id, scenario_title, task, agent, on_behalf_of, scope_enabled, started_at}
plan             {run_id, steps: ["Search Slack #payments for the bug report", ...]}
lease_issued     {run_id, lease: {lease_id, principal, on_behalf_of, task,
                    capabilities: [{tool, action, resource}],
                    sensitive:    [{tool, action}],
                    issued_at, expires_at, ttl_seconds},
                  excluded: [{tool, action, resource, label}]}   # ceiling items the lease left out; render as ✗ lines
tool_call        {run_id, seq, tool, action, args: {...}, resource, call_str}
                  # call_str is display-ready, e.g. slack.search(channel="#payments", query="payment bug")
decision         {run_id, seq, decision: "ALLOW"|"ALLOW_LIMITED"|"HUMAN_APPROVAL"|"DENY",
                  reason, narrowed_to: null | "channel:#payments", call_str, tool, action, resource, at,
                  provenance: null | "instruction originated in slack thread 18291, not from the user"}
approval_requested {run_id, approval_id, seq, tool, action, resource, call_str,
                    derived_from_task, reason, details: {"diff": "+14 −3 in payments/retry.py", ...}}
approval_resolved  {run_id, approval_id, outcome: "approved_once"|"approved_for_task"|"denied", by: "hiva@acme.com", at}
tool_result      {run_id, seq, ok: bool, summary: "3 messages, thread 18291 matched"}
injection_seen   {run_id, seq, source: "slack thread 18291", text: "...the injected instruction..."}
                  # emitted when a tool result that contains an injection is returned to the agent; UI highlights it
agent_message    {run_id, text}               # the agent's final natural-language report
lease_revoked    {run_id, lease_id, revoked_at, reason: "task_complete"|"expired"|"operator"}
run_finished     {run_id, status: "complete"|"expired"|"failed", finished_at, early_by_seconds, decisions: 6, chain_ok: true}
error            {run_id, message}
```

## Ledger entry (audit export)

```
{seq, at, lease_id, principal, on_behalf_of, task,
 call: {tool, action, resource, args}, decision, reason, narrowed_to,
 approval: null | {approval_id, outcome, by},
 prev_hash, hash}
```

## Scenarios (ids are stable)

- `file-issue`  "File the payment bug"  agent `engineering-assistant`  — contains the injection in Slack thread 18291; expected ledger: ALLOW, ALLOW, ALLOW_LIMITED, DENY, DENY, ALLOW.
- `fix-deploy`  "Fix and deploy the payment bug"  agent `coding-agent-7`  — merge PR #481 triggers HUMAN_APPROVAL.

## Design tokens (dark console, fixed)

bg #0D131B, surface #141C27, surface-2 #1B2531, line #26323F, ink #E6EDF5, muted #8A9AAE,
allow #3ED2A6, limited #F2B44A, approval #A78BFA, deny #FF6B6B, accent #5AD1DB.
Type: Bricolage Grotesque (brand/headings), IBM Plex Sans (body), IBM Plex Mono (calls, ids, decisions). Google Fonts.
Reference mockup: https://claude.ai/artifact/Xxu8fCEr4iwLF2sRPNWAFR  (section "The console").
