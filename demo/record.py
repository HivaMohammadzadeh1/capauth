#!/usr/bin/env python3
"""Record the original pair, or a selected live/replay Scope scenario."""

import argparse
import asyncio
import html
import json
from pathlib import Path
import subprocess
import tempfile

from playwright.async_api import async_playwright, expect

HERE = Path(__file__).resolve().parent
SIZE = {"width": 1440, "height": 900}

CARD_CSS = ("<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;800&display=swap'>"
            "<style>body{margin:0;background:#F3F4F6;color:#1B2430;width:1440px;height:900px;font-family:'Public Sans',system-ui,sans-serif;display:flex;align-items:center;justify-content:center}"
            ".card{width:1100px}.brand{display:flex;align-items:center;gap:12px;font-weight:700;font-size:26px;margin-bottom:42px}.brand svg{display:block}"
            ".brand small{font-weight:400;color:#66707E;font-size:16px;margin-left:8px}h1{font-size:54px;font-weight:800;letter-spacing:-0.02em;line-height:1.12;margin:0 0 22px}"
            "p{font-size:26px;color:#3C4757;line-height:1.45;margin:0;max-width:60ch}.tag{display:inline-block;margin-top:28px;font-size:18px;font-weight:600;color:#8A5A00;background:#FFF3D1;border:1px solid #F1DDA2;border-radius:999px;padding:6px 16px}"
            ".tag.live{color:#136C3A;background:#E1F5E9;border-color:#BFE5CF}.foot{margin-top:46px;color:#66707E;font-size:18px}</style>")


def card_html(title: str) -> str:
    """A title card in the product style. 'Title | subtitle' splits into a headline and a line under it."""
    head, _, sub = title.partition(" | ")
    tag = ""
    low = title.lower()
    if "simulated" in low:
        tag = "<span class='tag'>Simulated agent, labeled on screen</span>"
    elif "live" in low:
        tag = "<span class='tag live'>Live run, Claude Code</span>"
    sub_html = f"<p>{html.escape(sub)}</p>" if sub else ""
    return ("<html><head>" + CARD_CSS + "</head><body><div class='card'><div class='brand'><svg width="30" height="30" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="CapAuth"><rect width="64" height="64" rx="14" fill="#2451B2"/><circle cx="32.0" cy="32.0" r="17" fill="none" stroke="#FFFFFF" stroke-opacity="0.28" stroke-width="6"/><circle cx="32.0" cy="32.0" r="17" fill="none" stroke="#FFFFFF" stroke-width="6" stroke-linecap="round" stroke-dasharray="80.11 106.81" transform="rotate(-90 32.0 32.0)"/><rect x="26.5" y="26.5" width="11" height="11" rx="2.5" fill="#FFFFFF"/></svg>CapAuth<small>Capability authorization for AI agents</small></div>"
            f"<h1>{html.escape(head)}</h1>{sub_html}{tag}<div class='foot'>github.com/HivaMohammadzadeh1/capauth</div></div></body></html>")


CAPTION_JS = r"""
(() => {
  if (window.__capauthCaption) return;
  window.__capauthCaption = true;
  window.REPLAY_MS = 1100;
  const bar = document.createElement('div');
  bar.id = 'capauthCaption';
  bar.style.cssText = 'position:fixed;left:50%;bottom:26px;transform:translateX(-50%);max-width:1100px;padding:14px 22px;border-radius:12px;background:rgba(27,36,48,.94);color:#fff;font:500 20px/1.4 "Public Sans",system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.25);z-index:9999;opacity:0;transition:opacity .25s';
  document.body.appendChild(bar);
  let hide = null;
  const show = (text, color) => { bar.innerHTML = (color ? '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + color + ';margin-right:10px;vertical-align:middle"></span>' : '') + text; bar.style.opacity = '1'; clearTimeout(hide); hide = setTimeout(() => bar.style.opacity = '0', 6000); };
  const C = {ALLOW:'#3ED2A6', ALLOW_LIMITED:'#F2B44A', HUMAN_APPROVAL:'#A78BFA', DENY:'#FF6B6B'};
  const short = (s) => String(s || '').split('(')[0];
  const orig = window.handle;
  window.handle = function(t, d){
    orig(t, d);
    try {
      if (t === 'run_started') show('Task started for ' + String(d.agent || '').replace('agent:', '') + ', acting for ' + String(d.on_behalf_of || '').replace('user:', ''));
      else if (t === 'plan') show('The agent wrote its plan: ' + (d.steps || []).length + ' steps, before any tool call');
      else if (t === 'lease_issued') { const n = (d.lease && d.lease.capabilities || []).length; show('CapAuth issued lease ' + d.lease.lease_id + ': ' + n + (n === 1 ? ' capability' : ' capabilities') + ', ' + Math.round((d.lease.ttl_seconds || 600) / 60) + ' minutes. Everything else stays off.', '#2451B2'); }
      else if (t === 'lease_delegated') show('Worker lease ' + d.lease_id + ' issued as a strict subset of ' + d.parent_lease_id, '#A78BFA');
      else if (t === 'decision') {
        const who = d.depth ? 'Worker: ' : '';
        if (d.decision === 'ALLOW') show(who + 'Allowed: ' + short(d.call_str) + ' is inside the lease', C.ALLOW);
        else if (d.decision === 'ALLOW_LIMITED') show(who + 'Narrowed: ' + short(d.call_str) + ' asked for more than the lease holds, so it runs on ' + (d.narrowed_to || 'the leased resource'), C.ALLOW_LIMITED);
        else if (d.decision === 'HUMAN_APPROVAL') show(who + 'Waiting for a person: ' + short(d.call_str) + ' is sensitive under policy', C.HUMAN_APPROVAL);
        else if (d.decision === 'DENY') show(who + 'Denied: ' + short(d.call_str) + '. ' + String(d.reason || '').split('.')[0], C.DENY);
      }
      else if (t === 'injection_seen') show('An instruction inside a tool result asks the agent to do something the task never did. CapAuth does not detect it. The lease makes it unreachable.', '#FF6B6B');
      else if (t === 'approval_resolved') show('Approved by the operator, recorded in the ledger', C.HUMAN_APPROVAL);
      else if (t === 'lease_revoked' && !d.depth) show('Task ended. Lease revoked, ' + (d.reason === 'expired' ? 'TTL ran out' : 'early') + '.', '#2451B2');
      else if (t === 'run_finished') show((d.status === 'complete' ? 'Complete. ' : 'Ended. ') + (d.decisions || 0) + ' decisions on a hash chain a reviewer can verify.', '#2451B2');
    } catch (e) {}
  };
})();
"""
TITLES = {
    "before": "Before: the agent holds the user's full tokens",
    "after": "After: the agent holds a task-scoped lease from Scope",
}


async def title_cards(browser):
    context = await browser.new_context(viewport=SIZE, device_scale_factor=1)
    try:
        page = await context.new_page()
        for name, title in TITLES.items():
            await page.set_content(card_html(title))
            await page.screenshot(path=str(HERE / f"{name}-title.png"))
    finally:
        await context.close()


async def record_run(browser, base_url, name, enabled, output_dir):
    context = await browser.new_context(
        viewport=SIZE, device_scale_factor=1, color_scheme="dark",
        record_video_dir=str(HERE / "raw"), record_video_size=SIZE,
    )
    page = await context.new_page()
    video = page.video
    page.set_default_timeout(20_000)
    try:
        # The entire run, including page load and final hold, is capped at 240 s.
        async with asyncio.timeout(240):
            await page.goto(base_url, wait_until="domcontentloaded")
            await page.wait_for_function(
                "!!document.querySelector('#scenario option[value=\"file-issue\"]')"
            )
            await page.locator("#scenario").select_option("file-issue")
            toggle = page.locator("#scopeToggle")
            # The input is transparent and pointer-events:none; click its label.
            if await toggle.is_checked() != enabled:
                await page.locator("label.switch").click()
            await expect(toggle).to_be_checked(checked=enabled)
            await expect(page.locator("#scopeLabel")).to_have_text("on" if enabled else "off")
            async with page.expect_response(
                lambda r: r.url.rstrip("/").endswith("/api/runs")
                and r.request.method == "POST"
            ) as started:
                await page.get_by_role("button", name="Run task", exact=True).click()
            response = await started.value
            if not response.ok:
                raise RuntimeError(f"Run failed to start: HTTP {response.status}")
            run_id = (await response.json())["run_id"]
            print(f"{name}: run {run_id}, Scope {'ON' if enabled else 'OFF'}", flush=True)
            await page.locator("#ledger .endrow, #ledger .end").filter(has_text="Task complete").wait_for(
                state="visible", timeout=240_000
            )
            # Intentional viewing hold, after DOM-based completion detection.
            await asyncio.sleep(3)
            await page.screenshot(path=str(output_dir / f"{name}.png"), full_page=True)
            audit_response = await context.request.get(f"{base_url}/api/runs/{run_id}/audit")
            if not audit_response.ok:
                raise RuntimeError(f"Audit request failed: HTTP {audit_response.status}")
            audit = await audit_response.json()
            (output_dir / f"{name}-audit.json").write_text(json.dumps(audit, indent=2) + "\n")
            rows = await page.locator("#ledger .row").all_inner_texts()
            (output_dir / f"{name}-ledger.txt").write_text("\n\n".join(rows) + "\n")
            print("\n".join(rows), flush=True)
            # Ensure the recording actually demonstrates both attack calls.
            expected = "DENY" if enabled else "ALLOW"
            for target in ("customer-data.csv", "security-review@vendor-audit.com"):
                matches = [row for row in rows if target in row]
                if not matches or not all(row.splitlines()[0].strip() == expected for row in matches):
                    raise RuntimeError(f"Expected {expected} for {target}; inspect {name}-error-ledger.txt")
    except BaseException:
        await page.screenshot(path=str(HERE / f"{name}-error.png"), full_page=True)
        for suffix in ("-audit.json", "-ledger.txt"):
            diagnostic = output_dir / f"{name}{suffix}"
            if diagnostic.exists():
                diagnostic.replace(HERE / f"{name}-error{suffix}")
        raise
    finally:
        await context.close()  # Flush recording before saving its stable filename.
        await video.save_as(str(output_dir / f"{name}.webm"))
        await video.delete()


async def capture(base_url):
    HERE.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await title_cards(browser)
            # Publish a new pair only after both runs pass validation.
            with tempfile.TemporaryDirectory(prefix="capture-", dir=HERE) as staging:
                output_dir = Path(staging)
                await record_run(browser, base_url, "before", False, output_dir)
                await record_run(browser, base_url, "after", True, output_dir)
                for artifact in output_dir.iterdir():
                    artifact.replace(HERE / artifact.name)
        finally:
            await browser.close()



async def single_capture(args):
    name = args.out
    if Path(name).name != name or name in (".", ".."):
        raise ValueError("--out must be a basename inside demo/")
    metadata = {"scenario": args.scenario, "mode": args.mode,
                "recording": args.recording, "scope": args.scope,
                "title": args.title, "approval_clicks": 0}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = None
        video = None
        page = None
        try:
            if args.title:
                title_context = await browser.new_context(viewport=SIZE, device_scale_factor=1)
                title_page = await title_context.new_page()
                await title_page.set_content(card_html(args.title))
                await title_page.screenshot(path=str(HERE / f"{name}-title.png"))
                await title_context.close()
            if args.title_only:
                return
            context = await browser.new_context(viewport=SIZE, device_scale_factor=1,
                color_scheme="dark", record_video_dir=str(HERE / "raw"), record_video_size=SIZE)
            page = await context.new_page()
            page.set_default_timeout(20_000)
            video = page.video
            await page.goto(args.url, wait_until="domcontentloaded")
            await page.wait_for_function("s => !!document.querySelector('#scenario option[value=\"' + s + '\"]')", arg=args.scenario)
            await page.evaluate(CAPTION_JS)
            await page.locator("#scenario").select_option(args.scenario)
            enabled = args.scope == "on"
            if await page.locator("#scopeToggle").is_checked() != enabled:
                await page.locator("label.switch").click()
            await expect(page.locator("#scopeToggle")).to_be_checked(checked=enabled)
            if args.mode == "replay":
                await page.wait_for_function("name => [...document.querySelector('#replaySelect').options].some(o => o.value === name)", arg=args.recording)
                await page.evaluate("name => { const s = document.querySelector('#replaySelect'); s.value = name; s.dispatchEvent(new Event('change', {bubbles:true})); }", args.recording)
                async with page.expect_response(lambda r: '/api/recordings/' in r.url) as response_info:
                    await page.locator("#replayBtn").click()
                response = await response_info.value
                if not response.ok:
                    raise RuntimeError(f"Recording request failed: {response.status}")
                (HERE / f"{name}-events.json").write_text(json.dumps(await response.json(), indent=2) + "\n")
            else:
                async with page.expect_response(lambda r: r.url.rstrip('/').endswith('/api/runs') and r.request.method == 'POST') as response_info:
                    await page.locator("#runBtn").click()
                response = await response_info.value
                if not response.ok:
                    raise RuntimeError(f"Run request failed: {response.status}")
                metadata['run_id'] = (await response.json())['run_id']
                print(f"{name}: live run {metadata['run_id']}", flush=True)
            async with asyncio.timeout(240):
                while True:
                    approval = page.get_by_role('button', name='Approve once', exact=True)
                    if await approval.count() and await approval.first.is_visible() and await approval.first.is_enabled():
                        await approval.first.click(timeout=1000)
                        metadata['approval_clicks'] += 1
                        print(f"{name}: clicked Approve once", flush=True)
                    terminal = page.locator('#ledger .endrow, #ledger .end')
                    if await terminal.count():
                        status = await terminal.inner_text()
                        if any(t in status for t in ('Task complete', 'Task failed', 'Task expired')):
                            metadata['terminal'] = status
                            if 'Task complete' not in status:
                                raise RuntimeError(status)
                            break
                    await asyncio.sleep(0.08)
            await asyncio.sleep(args.hold)
            await page.screenshot(path=str(HERE / f"{name}.png"), full_page=True)
            (HERE / f"{name}-ledger.txt").write_text(await page.locator('#ledger').inner_text() + "\n")
            if args.mode == 'live':
                response = await context.request.get(f"{args.url}/api/runs/{metadata['run_id']}/audit")
                metadata['audit_http_status'] = response.status
                if response.ok:
                    (HERE / f"{name}-audit.json").write_text(json.dumps(await response.json(), indent=2) + "\n")
            metadata['success'] = True
            print(f"{name}: {metadata['terminal']}", flush=True)
        except BaseException as error:
            metadata['success'] = False
            metadata['error'] = str(error) or type(error).__name__
            if page:
                await page.screenshot(path=str(HERE / f"{name}-error.png"), full_page=True)
                (HERE / f"{name}-error-ledger.txt").write_text(await page.locator('#ledger').inner_text() + "\n")
            raise
        finally:
            (HERE / f"{name}-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
            if context:
                await context.close()
            if video:
                await video.save_as(str(HERE / f"{name}.webm"))
                await video.delete()
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--record-only", action="store_true")
    parser.add_argument("--scenario", help="Scenario ID; omit to record the original before/after pair")
    parser.add_argument("--mode", choices=("live", "replay"), default="live")
    parser.add_argument("--recording", help="Recording name from /api/recordings")
    parser.add_argument("--out", default="capture", help="Output basename within demo/")
    parser.add_argument("--title", help="Text for a 1440x900 title PNG")
    parser.add_argument("--scope", choices=("on", "off"), default="on")
    parser.add_argument("--hold", type=float, default=4, help="Seconds to hold the completed run")
    parser.add_argument("--title-only", action="store_true")
    args = parser.parse_args()
    if args.scenario or args.title_only:
        if args.title_only and not args.title:
            parser.error("--title-only requires --title")
        if args.mode == "replay" and not args.recording and not args.title_only:
            parser.error("--mode replay requires --recording")
        args.url = args.url.rstrip("/")
        asyncio.run(single_capture(args))
        if not args.record_only and not args.title_only:
            segments = ([f"{args.out}-title.png:3"] if args.title else []) + [f"{args.out}.webm"]
            subprocess.run(["bash", str(HERE / "stitch.sh"), "--out", args.out, *segments], check=True)
        return
    asyncio.run(capture(args.url.rstrip("/")))
    if not args.record_only:
        subprocess.run(["bash", str(HERE / "stitch.sh")], check=True)
    else:
        for name in ("before.webm", "after.webm", "before.png", "after.png"):
            path = HERE / name
            assert path.stat().st_size > 0, path
            print(f"{path.name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
