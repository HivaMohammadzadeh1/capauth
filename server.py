"""Scope demo server: starts runs, streams events, takes approvals, exports audits."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from anthropic import AsyncAnthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent.loop import BACKEND, SCENARIOS, Run, execute

UI = Path(__file__).parent / "ui"
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


@app.post("/api/runs")
async def start(body: StartRun):
    if body.scenario_id not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    run = Run(SCENARIOS[body.scenario_id], scope_enabled=body.scope_enabled)
    RUNS[run.run_id] = run
    asyncio.create_task(execute(run, None if BACKEND == "cli" else client()))
    return {"run_id": run.run_id}


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


@app.get("/api/runs/{run_id}/replay")
async def replay(run_id: str):
    return _run(run_id).events


@app.get("/api/runs")
async def list_runs():
    return [{"run_id": r.run_id, "scenario_id": r.scenario.id, "status": r.status, "scope_enabled": r.scope_enabled} for r in RUNS.values()]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
