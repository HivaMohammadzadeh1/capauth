"""Terminal before/after of the attack.

    uv run python attack/demo.py --scope off          # the agent holds full tokens: data leaves
    uv run python attack/demo.py --scope on           # the agent holds a lease: both calls DENY
    uv run python attack/demo.py --scope on --n 3 --variant helpful_colleague
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.loop import run_headless  # noqa: E402
from attack.variants import DEFAULT, VARIANTS  # noqa: E402

C = {"ALLOW": "\033[32m", "ALLOW_LIMITED": "\033[33m", "HUMAN_APPROVAL": "\033[35m", "DENY": "\033[31m"}
R, B, DIM = "\033[0m", "\033[1m", "\033[2m"


def yn(v: bool) -> str:
    return "\033[31mYES\033[0m" if v else "\033[32mNO\033[0m"


def show(r) -> None:
    print(f"\n{B}Task{R}  {r.task}")
    print(f"{B}Agent{R} {r.lease['principal'] if r.lease else '?'}   {B}Scope{R} {'on' if r.scope_enabled else 'off'}")
    print(f"\n{B}Plan{R}")
    for i, s in enumerate(r.plan, 1):
        print(f"  {i}. {s}")
    if r.scope_enabled and r.lease:
        print(f"\n{B}Lease{R} {r.lease['lease_id']}  expires {r.lease['expires_at']}")
        for c in r.lease["capabilities"]:
            print(f"  \033[32m+\033[0m {c['tool']}.{c['action']}  {c['resource']}")
    print(f"\n{B}Tool calls{R}")
    for e in r.decisions:
        c = e["call"]
        col = C.get(e["decision"], "")
        print(f"  {col}{e['decision']:<14}{R} {c['tool']}.{c['action']}  {DIM}{c['resource']}{R}")
        print(f"                 {DIM}{e['reason']}{R}")
        if e.get("provenance"):
            print(f"                 {DIM}{e['provenance']}{R}")
    print(f"\n{B}Verdict{R}")
    print(f"  Agent attempted the injected action: {yn(r.attempted_injection)}")
    print(f"  Customer data left the org:          {yn(r.exfiltrated)}")
    print(f"  Legitimate issue filed:              {'\033[32mYES\033[0m' if r.legit_done else '\033[31mNO\033[0m'}")
    print(f"\n{DIM}{r.final_message[:400]}{R}\n")


async def main(a) -> None:
    rows = []
    for i in range(a.n):
        r = await run_headless("file-issue", scope_enabled=(a.scope == "on"), injection_variant=a.variant, auto_approve=True, backend=a.backend)
        if a.n == 1:
            show(r)
        rows.append(r)
        if a.n > 1:
            print(f"run {i+1}/{a.n}: attempted={r.attempted_injection} exfil={r.exfiltrated} legit={r.legit_done}")
    if a.n > 1:
        print(f"\nvariant={a.variant} scope={a.scope} n={a.n}")
        print(f"  attempted injected action: {sum(r.attempted_injection for r in rows)}/{a.n}")
        print(f"  customer data left the org: {sum(r.exfiltrated for r in rows)}/{a.n}")
        print(f"  legitimate issue filed:     {sum(r.legit_done for r in rows)}/{a.n}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--scope", choices=["on", "off"], default="on")
    p.add_argument("--variant", choices=sorted(VARIANTS), default=DEFAULT)
    p.add_argument("--n", type=int, default=1)
    p.add_argument("--backend", choices=["cli", "sdk", "scripted"], default=os.environ.get("SCOPE_BACKEND", "cli"))
    asyncio.run(main(p.parse_args()))
