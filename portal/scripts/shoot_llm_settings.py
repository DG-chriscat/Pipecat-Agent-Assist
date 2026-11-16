"""Screenshot the Settings page's Thinking group (P5-9, DC-11), light and dark.

Runs the real server in process (memory settings, no login) on the built portal:

    cd portal && npm run build && cd ..
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \
        python portal/scripts/shoot_llm_settings.py docs/screenshots/llm

Writes OUT/settings-llm-{light,dark}.png (the defaults) and
OUT/settings-llm-thinkfast-{light,dark}.png (Think-Fast picked for wrap up, its training
note flagged), at 1600x1000, scrolled so the Thinking group is in view.
No network beyond localhost.
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
            thinking = region.get_by_role("group", name="Thinking")
            await expect(thinking).to_be_visible(timeout=10_000)

            async def shoot(name: str) -> None:
                await page.evaluate(
                    "document.getElementById('thinking-label').scrollIntoView({block: 'start'})"
                )
                await page.wait_for_timeout(300)
                path = out / f"{name}.png"
                await page.screenshot(path=str(path), animations="disabled")
                print(path)

            await shoot("settings-llm-light")
            await theme(page, "Dark")
            await shoot("settings-llm-dark")

            wrap = thinking.get_by_role("radiogroup", name="Wrap up LLM")
            await wrap.locator("[data-model='think-fast']").click()
            await shoot("settings-llm-thinkfast-dark")
            await theme(page, "Light")
            await shoot("settings-llm-thinkfast-light")
            await browser.close()
    finally:
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots/llm")))
