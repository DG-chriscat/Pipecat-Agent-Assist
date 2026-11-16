"""Screenshot the Settings page's Listening group (P5-7, IN-14), light and dark.

Runs the real server in process (memory settings, no login) on the built portal:

    cd portal && npm run build && cd ..
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \
        python portal/scripts/shoot_stt_settings.py docs/screenshots/stt-models

Writes OUT/settings-listening-{light,dark}.png (defaults: Flux intake, Nova-3 assist)
and OUT/settings-listening-medical-{light,dark}.png (Nova-3 intake, Nova-3 Medical
assist, saved), at 1600x1000, scrolled so the Listening group is in view.
No network beyond localhost: the voice catalog falls back to its known list.
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
            await page.get_by_role("button", name="Settings").click()
            region = page.get_by_role("region", name="Settings")
            listening = region.get_by_role("group", name="Listening")
            await expect(listening).to_be_visible(timeout=10_000)

            async def shoot(name: str) -> None:
                await listening.scroll_into_view_if_needed()
                await page.evaluate(
                    "document.getElementById('listening-label').scrollIntoView({block: 'center'})"
                )
                await page.wait_for_timeout(300)
                path = out / f"{name}.png"
                await page.screenshot(path=str(path), animations="disabled")
                print(path)

            await shoot("settings-listening-light")
            await theme(page, "Dark")
            await shoot("settings-listening-dark")

            intake = listening.get_by_role("radiogroup", name="Intake bot")
            assist = listening.get_by_role("radiogroup", name="Live assist")
            await intake.get_by_role("radio", name="Nova-3", exact=True).click()
            await assist.get_by_role("radio", name="Nova-3 Medical").click()
            await region.get_by_role("button", name="Save", exact=True).click()
            await expect(region.get_by_role("status")).to_contain_text("Applies to the next call")
            await shoot("settings-listening-medical-dark")
            await theme(page, "Light")
            await shoot("settings-listening-medical-light")
            await browser.close()
    finally:
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots/stt-models")))
