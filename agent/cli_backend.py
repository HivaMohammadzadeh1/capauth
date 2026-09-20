"""Model backend that drives Claude Code (`claude -p`) instead of the API. CapAuth is its only tool source.

Structured calls (plan, security planner) use --json-schema. The agent phase runs
Claude Code with Scope as its only tool source via --mcp-config; events flow back
through the run directory's events.jsonl.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

CLAUDE = os.environ.get("SCOPE_CLAUDE_BIN", "claude")
CLI_MODEL = os.environ.get("SCOPE_CLI_MODEL", "opus")
REPO = Path(__file__).resolve().parent.parent


def _base(prompt: str, model: str | None = None) -> list[str]:
    return [CLAUDE, "-p", prompt, "--output-format", "json", "--model", model or CLI_MODEL, "--tools", "",
            "--no-session-persistence", "--setting-sources", ""]


async def _run(cmd: list[str], env: dict[str, str] | None = None, timeout: float = 600) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                env={**os.environ, **(env or {})}, cwd=str(REPO))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("claude cli timed out")
    if proc.returncode != 0:
        raise RuntimeError(f"claude cli exit {proc.returncode}: {err.decode()[-800:] or out.decode()[-800:]}")
    try:
        data = json.loads(out.decode())
    except json.JSONDecodeError:
        raise RuntimeError(f"claude cli returned non-JSON: {out.decode()[:300]}")
    if data.get("is_error"):
        raise RuntimeError(f"claude cli error: {str(data.get('result'))[:300]}")
    return data


async def cli_complete_json(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    cmd = _base(user) + ["--json-schema", json.dumps(schema), "--append-system-prompt", system]
    data = await _run(cmd, timeout=180)
    so = data.get("structured_output")
    if so is None:
        so = json.loads(data.get("result") or "{}")
    return so


def write_mcp_config(run_dir: Path, env: dict[str, str]) -> Path:
    python = sys.executable
    cfg = {"mcpServers": {"capauth": {"command": python, "args": ["-m", "scope.mcp_server"],
                                     "env": {**env, "PYTHONPATH": str(REPO), "PYTHONUNBUFFERED": "1"}}}}
    path = run_dir / "mcp.json"
    path.write_text(json.dumps(cfg))
    return path


async def cli_agent(prompt: str, system: str, run_dir: Path, env: dict[str, str], max_turns: int = 20, model: str | None = None) -> tuple[str, dict[str, Any]]:
    cfg = write_mcp_config(run_dir, env)
    cmd = _base(prompt, model) + ["--mcp-config", str(cfg), "--strict-mcp-config", "--dangerously-skip-permissions",
                           "--max-turns", str(max_turns), "--append-system-prompt", system]
    data = await _run(cmd, env={"MCP_TOOL_TIMEOUT": "600000", "MCP_TIMEOUT": "60000"}, timeout=900)
    return str(data.get("result") or ""), data
