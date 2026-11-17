"""Screenshot the caller talk tracks (P5-11), light and dark.

Runs the real server in process (memory settings, no login) on the built portal, so the
beats are the real GET /demo/personas data:

    cd portal && npm run build && cd ..
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        python portal/scripts/shoot_talk_tracks.py docs/screenshots/talk-tracks

Writes OUT/demos-track-{light,dark}.png (Maria Lopez's card with the Caller talk track
open and the first checkpoints ticked, full card height) and
OUT/caller-beats-{light,dark}.png (/caller?persona=maria_lopez, the caller's beats,
full page). The caller page loads fonts and daily-js from their CDNs.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from shoot_demos import VIEWPORT, free_port, theme  # noqa: E402


async def main(out: Path) -> None:
    import uvicorn
    from agent_assist_server.app import app
    from playwright.async_api import async_playwright, expect

    for k in ("APP_PASSWORD", "SETTINGS_PERSIST", "DATABASE_URL", "QUEUE_PERSIST"):
        os.environ.pop(k, None)
    os.environ["WARM_BOT_POOL"] = "0"
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            for scheme in ("light", "dark"):
                ctx = await browser.new_context(viewport=VIEWPORT, color_scheme=scheme)
                page = await ctx.new_page()
                page.on("console", lambda m: m.type == "error" and print(f"  [page] {m.text}"))
                # The caller page: beats, caller side only. Theme follows the system.
                await page.goto(f"{base}/caller?persona=maria_lopez")
                await expect(page.get_by_test_id("caller-beat").first).to_be_visible(timeout=10_000)
                await page.wait_for_timeout(600)
                path = out / f"caller-beats-{scheme}.png"
                await page.screenshot(path=str(path), full_page=True, animations="disabled")
                print(path)
                await ctx.close()

            ctx = await browser.new_context(viewport=VIEWPORT, color_scheme="light")
            page = await ctx.new_page()
            await page.goto(f"{base}/agent/")
            await page.get_by_label("Your name").fill("Agent Ana")
            await page.get_by_role("button", name="Sign in").click()
            await page.get_by_role("button", name="Demos").click()
            maria = page.get_by_test_id("demo-card-maria_lopez")
            await expect(maria).to_be_visible(timeout=10_000)
            await maria.get_by_role("button", name="Caller talk track").click()
            boxes = maria.get_by_role("checkbox")
            for i in range(3):  # a tester part way through the call
                await boxes.nth(i).click()

            async def shot(name: str) -> None:
                # The page scrolls inside .demos: grow the viewport to its content
                # height, then shoot the card itself.
                region = page.get_by_role("region", name="Demos")
                h = await region.evaluate("el => el.scrollHeight")
                await page.set_viewport_size({"width": VIEWPORT["width"], "height": h + 140})
                await page.wait_for_timeout(300)
                path = out / f"{name}.png"
                await maria.screenshot(path=str(path), animations="disabled")
                await page.set_viewport_size(VIEWPORT)
                print(path)

            await shot("demos-track-light")
            await theme(page, "Dark")
            await shot("demos-track-dark")
            await browser.close()
    finally:
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots/talk-tracks")))
