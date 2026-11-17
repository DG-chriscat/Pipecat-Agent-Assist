"""Screenshot the Demos page (P5-10), light and dark.

Runs the real server in process (memory settings, no login) on the built portal, so the
cards are the real GET /demo/personas data:

    cd portal && npm run build && cd ..
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        python portal/scripts/shoot_demos.py docs/screenshots/demos

Writes OUT/demos-{light,dark}.png (the page, full height) and
OUT/demos-script-{light,dark}.png (Maria Lopez's card with the Caller talk track open,
1600x1600); the full pages are 1600 wide. No network beyond localhost.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

VIEWPORT = {"width": 1600, "height": 1000}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def theme(page, name: str) -> None:
    await page.get_by_role("button", name="Agent menu").click()
    await page.get_by_role("menuitemradio", name=name).click()
    await page.keyboard.press("Escape")
    await page.wait_for_function(f"document.documentElement.dataset.theme === '{name.lower()}'")
    await page.wait_for_timeout(400)


async def main(out: Path) -> None:
    import uvicorn
    from agent_assist_server.app import app
    from playwright.async_api import async_playwright, expect

    for k in ("APP_PASSWORD", "SETTINGS_PERSIST", "DATABASE_URL", "QUEUE_PERSIST"):
        os.environ.pop(k, None)
    os.environ["WARM_BOT_POOL"] = "0"
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            ctx = await browser.new_context(viewport=VIEWPORT, color_scheme="light")
            page = await ctx.new_page()
            page.on("console", lambda m: m.type == "error" and print(f"  [portal] {m.text}"))
            await page.goto(f"http://127.0.0.1:{port}/agent/")
            await page.get_by_label("Your name").fill("Agent Ana")
            await page.get_by_role("button", name="Sign in").click()
            await page.get_by_role("button", name="Demos").click()
            region = page.get_by_role("region", name="Demos")
            maria = page.get_by_test_id("demo-card-maria_lopez")
            await expect(maria).to_be_visible(timeout=10_000)

            async def full(name: str) -> None:
                # The page scrolls inside .demos: grow the viewport to its content height.
                h = await region.evaluate("el => el.scrollHeight")
                height = max(h + 140, 1000)
                await page.set_viewport_size({"width": VIEWPORT["width"], "height": height})
                await page.wait_for_timeout(300)
                path = out / f"{name}.png"
                await page.screenshot(path=str(path), animations="disabled", full_page=True)
                await page.set_viewport_size(VIEWPORT)
                print(path)

            async def card(name: str) -> None:
                # Tall enough for the whole expanded card, top of the page in view.
                await page.set_viewport_size({"width": VIEWPORT["width"], "height": 1600})
                await region.evaluate("el => el.scrollTo(0, 0)")
                await page.wait_for_timeout(300)
                path = out / f"{name}.png"
                await page.screenshot(path=str(path), animations="disabled")
                await page.set_viewport_size(VIEWPORT)
                print(path)

            await full("demos-light")
            await maria.get_by_role("button", name="Caller talk track").click()
            await card("demos-script-light")
            await theme(page, "Dark")
            await card("demos-script-dark")
            await maria.get_by_role("button", name="Hide talk track").click()
            await full("demos-dark")
            await browser.close()
    finally:
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots/demos")))
