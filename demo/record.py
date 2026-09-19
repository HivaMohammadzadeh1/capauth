#!/usr/bin/env python3
"""Record both live Scope runs, then optionally assemble the demo."""

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
TITLES = {
    "before": "Before: the agent holds the user's full tokens",
    "after": "After: the agent holds a task-scoped lease from Scope",
}


async def title_cards(browser):
    context = await browser.new_context(viewport=SIZE, device_scale_factor=1)
    try:
        page = await context.new_page()
        for name, title in TITLES.items():
            await page.set_content(
                '<html><body style="margin:0;background:#0B1017;color:white;'
                'width:1440px;height:900px;display:flex;align-items:center;'
                'justify-content:center;font-family:Arial,sans-serif">'
                '<div style="max-width:1160px;text-align:center;font-size:52px;'
                'font-weight:600;line-height:1.3">'
                + html.escape(title) + "</div></body></html>"
            )
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
            await page.locator("#ledger .end").filter(has_text="Task complete").wait_for(
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--record-only", action="store_true")
    args = parser.parse_args()
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
