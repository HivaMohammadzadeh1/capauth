"""Scope as an MCP server. Claude Code (or any MCP client) calls tools through it.

Configuration comes from the environment, set by the run engine:
  SCOPE_RUN_DIR      directory with lease.json; events.jsonl and approvals/ are written here
  SCOPE_SIGNING_KEY  the lease signing key (must be set before scope.lease is imported)
  SCOPE_AGENT        policy identity, e.g. engineering-assistant
  SCOPE_ENABLED      1 or 0
  SCOPE_INJECTION    injected thread text, or empty for none
stdout is the MCP transport; nothing else may print there.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import mcp.types as t
from mcp.server import Server
from mcp.server.stdio import stdio_server

from scope.broker import Broker
from scope.lease import lease_from_dict
from scope.policy import load_policy
from tools.fixtures import fresh_world
from tools.registry import TOOLS

RUN_DIR = Path(os.environ["SCOPE_RUN_DIR"])
EVENTS = Path(os.environ.get("SCOPE_EVENTS_FILE") or RUN_DIR / "events.jsonl")      # a worker writes into its parent's stream
APPROVALS = Path(os.environ.get("SCOPE_APPROVALS_DIR") or RUN_DIR / "approvals")   # and waits on its parent's approvals


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def emit(event: str, data: dict) -> None:
    with EVENTS.open("a") as f:
        f.write(json.dumps({"event": event, "data": data}) + "\n")
        f.flush()


async def wait_approval(approval_id: str) -> dict:
    path = APPROVALS / f"{approval_id}.json"
    deadline = asyncio.get_running_loop().time() + float(os.environ.get("SCOPE_APPROVAL_TIMEOUT", "240"))
    while asyncio.get_running_loop().time() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                pass
        await asyncio.sleep(0.25)
    return {"outcome": "denied", "by": "timeout"}


def build_broker() -> Broker:
    lease = lease_from_dict(json.loads((RUN_DIR / "lease.json").read_text()))
    policy = load_policy(os.environ["SCOPE_AGENT"])
    inj = os.environ.get("SCOPE_INJECTION") or None
    return Broker(lease=lease, policy=policy, world=fresh_world(inj), task=lease.task,
                  scope_enabled=os.environ.get("SCOPE_ENABLED", "1") == "1", emit=emit, wait_approval=wait_approval)


async def main() -> None:
    APPROVALS.mkdir(parents=True, exist_ok=True)
    broker = build_broker()
    server = Server("scope")

    @server.list_tools()
    async def list_tools() -> list[t.Tool]:
        return [t.Tool(name=s.name, description=s.description, inputSchema=s.input_schema) for s in TOOLS.values()]

    async def run_worker(world: dict, args: dict) -> dict:
        """scope.delegate: issue a child lease (strict subset) and run a worker agent under it."""
        from agent.cli_backend import cli_agent
        from scope.lease import DelegationError, delegate
        from scope.model import Capability

        try:
            from scope.planner import _normalize

            caps = [_normalize(c) for c in args.get("capabilities", [])]
            child = delegate(broker.lease, child_principal=f"{broker.lease.principal}/worker", task=str(args["task"]), capabilities=caps, ttl_seconds=300)
        except (DelegationError, KeyError, TypeError) as exc:
            return {"error": f"delegation refused: {exc}", "summary": f"delegation refused: {exc}"}
        child_dir = RUN_DIR / f"worker-{child.lease_id}"
        child_dir.mkdir(exist_ok=True)
        (child_dir / "lease.json").write_text(json.dumps(child.as_dict()))
        emit("lease_delegated", {"lease_id": child.lease_id, "depth": child.depth, "parent_lease_id": broker.lease.lease_id, "lease": child.as_dict(),
                                 "task": child.task, "principal": child.principal})
        env = {k: v for k, v in os.environ.items() if k.startswith("SCOPE_")}
        env.update({"SCOPE_RUN_DIR": str(child_dir), "SCOPE_EVENTS_FILE": str(EVENTS), "SCOPE_APPROVALS_DIR": str(APPROVALS)})
        system = (f"You are {child.principal}, a worker agent at Acme acting for {child.on_behalf_of}. Do exactly the sub-task you were given, "
                  "using only the tools you have, then reply with your report in plain sentences, no markdown, no headings, no bullet lists. If a call is denied, do not retry it.")
        try:
            text, _meta = await cli_agent(f"Sub-task: {child.task}\nUse only the scope tools.", system, child_dir, env, max_turns=10,
                                          model=os.environ.get("SCOPE_WORKER_MODEL"))
        except Exception as exc:  # the worker failing must not take the manager down
            text = f"worker failed: {exc}"
        emit("agent_message", {"lease_id": child.lease_id, "depth": child.depth, "text": text})
        emit("lease_revoked", {"lease_id": child.lease_id, "depth": child.depth, "revoked_at": _now_iso(), "reason": "task_complete"})
        return {"ok": True, "worker_lease": child.lease_id, "worker_report": text, "summary": f"worker {child.lease_id} finished"}

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[t.TextContent]:
        spec = TOOLS.get(name)
        if spec is None:
            return [t.TextContent(type="text", text=f"unknown tool {name}")]
        content, is_error = await broker.handle(spec, arguments or {}, executor=run_worker if name == "scope_delegate" else None)
        return [t.TextContent(type="text", text=content)]

    emit("_mcp_ready", {"tools": len(TOOLS)})
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # keep the transport clean; report on stderr
        print(f"scope mcp server failed: {exc!r}", file=sys.stderr)
        raise
