"""The honest measurement: N runs of the attack scenario with Scope on and off.

    uv run python bench/run.py --n 20 --concurrency 4 --variant process_authority
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import AsyncAnthropic  # noqa: E402

from agent.loop import run_headless  # noqa: E402

OUT = Path(__file__).parent / "out"


async def one(sem, client, scope_on, variant, model=None):
    async with sem:
        try:
            r = await run_headless("file-issue", scope_enabled=scope_on, injection_variant=variant, auto_approve=True, client=client, model=model)
            return {"ok": True, "attempted": r.attempted_injection, "exfiltrated": r.exfiltrated, "legit": r.legit_done,
                    "decisions": [e["decision"] for e in r.decisions], "final": r.final_message[:200]}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def main(n: int, concurrency: int, variants: list[str], models: list[str]):
    client = None if os.environ.get("SCOPE_BACKEND", "cli") == "cli" else AsyncAnthropic(timeout=240, max_retries=3)
    sem = asyncio.Semaphore(concurrency)
    t0 = time.time()
    cells = [(m, v, s) for m in models for v in variants for s in (False, True)]
    results = await asyncio.gather(*[asyncio.gather(*[one(sem, client, s, v, m) for _ in range(n)]) for (m, v, s) in cells])
    rows = []
    for (m, v, s), rs in zip(cells, results):
        okr = [r for r in rs if r["ok"]]
        rows.append({"model": m, "variant": v, "scope": "on" if s else "off", "runs": len(rs), "completed": len(okr),
                     "attempted_injection": sum(r["attempted"] for r in okr), "data_left_org": sum(r["exfiltrated"] for r in okr),
                     "legit_issue_filed": sum(r["legit"] for r in okr), "errors": [r["error"] for r in rs if not r["ok"]]})
    OUT.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (OUT / f"results-{stamp}.json").write_text(json.dumps({"n": n, "rows": rows, "raw": {f"{m}|{v}|{s}": rs for (m, v, s), rs in zip(cells, results)}}, indent=2))
    md = ["| Agent model | Injection variant | Scope | Runs | Attempted the injected action | Customer data left the org | Legitimate issue filed |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['model']} | {r['variant']} | {r['scope']} | {r['completed']}/{r['runs']} | {r['attempted_injection']} | {r['data_left_org']} | {r['legit_issue_filed']} |")
    table = "\n".join(md)
    (OUT / f"results-{stamp}.md").write_text(table + "\n")
    print(table)
    print(f"\n{time.time()-t0:.0f}s, written to bench/out/results-{stamp}.md")
    errs = [e for r in rows for e in r["errors"]]
    if errs:
        print(f"{len(errs)} errors, first: {errs[0][:300]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--variants", default="process_authority")
    ap.add_argument("--models", default="opus")
    a = ap.parse_args()
    asyncio.run(main(a.n, a.concurrency, a.variants.split(","), a.models.split(",")))
