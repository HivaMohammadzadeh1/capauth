"""Scope demo server: starts runs, streams events, takes approvals, exports audits."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from anthropic import AsyncAnthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent.loop import BACKEND, SCENARIOS, Run, _issue_preview, execute
from scope.audit import attest, to_jsonl
from scope.lease import DelegationError, delegate
from scope.model import Capability
from tools.fixtures import DEFAULT_INJECTION
from fastapi.responses import PlainTextResponse

UI = Path(__file__).parent / "ui"
RECORDINGS = Path(__file__).parent / "recordings"
app = FastAPI(title="Scope")
RUNS: dict[str, Run] = {}
_client: AsyncAnthropic | None = None


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(timeout=180, max_retries=2)
    return _client


class StartRun(BaseModel):
    scenario_id: str
    scope_enabled: bool = True
    model: str | None = None            # agent under test: opus | sonnet | haiku (cli backend only)
    injection_variant: str | None = None  # attack.variants key; default process_authority


class Approval(BaseModel):
    outcome: str
    by: str = "hiva@acme.com"


@app.get("/", response_class=HTMLResponse)
async def index():
    page = UI / "index.html"
    if page.exists():
        return HTMLResponse(page.read_text())
    return HTMLResponse("<p style='font-family:sans-serif'>ui/index.html is not built yet. API is live at /api/scenarios.</p>")


@app.get("/ui/{name}")
async def ui_asset(name: str):
    p = UI / name
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/scenarios")
async def scenarios():
    return [s.public() for s in SCENARIOS.values()]


@app.get("/api/config")
async def config():
    try:
        from attack.variants import DEFAULT, VARIANTS

        variants, default = sorted(VARIANTS), DEFAULT
    except ImportError:
        variants, default = ["process_authority"], "process_authority"
    return {"backend": BACKEND, "models": ["opus", "sonnet", "haiku"], "default_model": "opus", "variants": variants, "default_variant": default}


@app.post("/api/runs")
async def start(body: StartRun):
    if body.scenario_id not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    text = DEFAULT_INJECTION
    if body.injection_variant:
        try:
            from attack.variants import VARIANTS

            text = VARIANTS.get(body.injection_variant, DEFAULT_INJECTION)
        except ImportError:
            pass
    model = body.model if body.model in (None, "opus", "sonnet", "haiku") else None
    run = Run(SCENARIOS[body.scenario_id], scope_enabled=body.scope_enabled, injection_text=text, model=model)
    RUNS[run.run_id] = run

    async def go():
        await execute(run, None if BACKEND == "cli" else client())
        if run.status == "complete" and BACKEND != "scripted":
            RECORDINGS.mkdir(exist_ok=True)
            name = f"{run.scenario.id}-{'on' if run.scope_enabled else 'off'}"
            (RECORDINGS / f"{name}.json").write_text(json.dumps(run.events))

    asyncio.create_task(go())
    return {"run_id": run.run_id}


@app.get("/api/recordings")
async def recordings():
    return sorted(p.stem for p in RECORDINGS.glob("*.json")) if RECORDINGS.exists() else []


@app.get("/api/recordings/{name}")
async def recording(name: str):
    p = RECORDINGS / f"{name}.json"
    if not p.exists() or "/" in name:
        raise HTTPException(404, "no recording")
    return JSONResponse(json.loads(p.read_text()))


def _run(run_id: str) -> Run:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    return run


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str):
    run = _run(run_id)

    async def gen():
        async for e in run.subscribe():
            yield f"event: {e['event']}\ndata: {json.dumps(e['data'])}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/approvals/{approval_id}")
async def approve(approval_id: str, body: Approval):
    if body.outcome not in ("approved_once", "approved_for_task", "denied"):
        raise HTTPException(400, "bad outcome")
    for run in RUNS.values():
        if run.resolve_approval(approval_id, body.outcome, body.by):
            return {"ok": True}
    raise HTTPException(404, "no pending approval with that id")


@app.get("/api/runs/{run_id}/audit")
async def audit(run_id: str):
    return JSONResponse(_run(run_id).audit())


@app.get("/api/runs/{run_id}/audit.jsonl", response_class=PlainTextResponse)
async def audit_jsonl(run_id: str):
    """SIEM-friendly export: one ledger entry per line."""
    return PlainTextResponse(to_jsonl(_run(run_id).ledger_entries), media_type="application/x-ndjson")


@app.get("/api/runs/{run_id}/attestation")
async def attestation(run_id: str):
    run = _run(run_id)
    return attest(run.audit(), run.lease.as_dict() if run.lease else None)


class LeasePreview(BaseModel):
    scenario_id: str | None = None
    task: str | None = None
    agent: str = "engineering-assistant"
    plan: list[str] | None = None


@app.post("/api/leases/preview")
async def lease_preview(body: LeasePreview):
    """What would this task get? Runs plan + planner + ceiling clamp and returns the lease without executing anything."""
    if body.scenario_id and body.scenario_id in SCENARIOS:
        sc = SCENARIOS[body.scenario_id]
        task, agent = sc.task, sc.agent
    elif body.task:
        task, agent = body.task, body.agent
    else:
        raise HTTPException(400, "scenario_id or task required")
    try:
        return await _issue_preview(task=task, agent=agent, plan=body.plan, client=None if BACKEND == "cli" else client())
    except FileNotFoundError:
        raise HTTPException(404, f"no policy for identity {agent}")


class Delegate(BaseModel):
    child_principal: str
    task: str
    capabilities: list[dict]
    ttl_seconds: int | None = None


@app.post("/api/runs/{run_id}/delegate")
async def delegate_lease(run_id: str, body: Delegate):
    """Issue a child lease for a sub-agent. It must be a strict subset of this run's lease."""
    run = _run(run_id)
    if run.lease is None:
        raise HTTPException(409, "no lease on this run yet")
    try:
        child = delegate(run.lease, child_principal=body.child_principal, task=body.task,
                         capabilities=[Capability(**c) for c in body.capabilities], ttl_seconds=body.ttl_seconds)
    except DelegationError as exc:
        raise HTTPException(403, str(exc))
    return child.as_dict()


@app.get("/api/policies/{agent}")
async def policy(agent: str):
    from scope.policy import load_policy

    try:
        pol = load_policy(agent)
    except FileNotFoundError:
        raise HTTPException(404, "no policy for that identity")
    return {"principal": pol.principal, "org_domain": pol.org_domain, "ttl_seconds": pol.ttl_seconds,
            "ceiling": [c.as_dict() for c in pol.ceiling], "sensitive": sorted(pol.sensitive), "never": sorted(pol.never)}


@app.get("/api/runs/{run_id}/replay")
async def replay(run_id: str):
    return _run(run_id).events


@app.get("/api/runs")
async def list_runs():
    return [{"run_id": r.run_id, "scenario_id": r.scenario.id, "status": r.status, "scope_enabled": r.scope_enabled} for r in RUNS.values()]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
