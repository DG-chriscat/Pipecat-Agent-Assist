"""Screenshot the Architecture page (P5-6, PRD AP-21) from the dev gallery.

Usage (with `vite` serving portal/ on PORT):
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \
        python portal/scripts/shoot_architecture.py docs/screenshots/architecture [--port 5173]

Writes, in light and dark, at 1600x1000:
    business-node-<theme>.png      Business view, a node selected (Assist listening)
    technical-<theme>.png          Technical view, Supabase Postgres selected
    walk-step-7-<theme>.png        Walk a call, step 7 Live assist
    prospect-questions-<theme>.png Sections panel, Prospect questions tab
    present-<theme>.png            Present mode (no rail or header), walk step 3
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path


async def shoot(page, out: Path, name: str) -> None:
    await page.wait_for_timeout(400)
    await page.screenshot(path=str(out / name), animations="disabled")
    print(f"wrote {out / name}")


async def main() -> None:
    from playwright.async_api import async_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--port", type=int, default=5173)
    ap.add_argument("--size", default="1600x1000")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.split("x"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": w, "height": h})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"  [pageerror] {e}"))
        for theme in ("light", "dark"):
            url = f"http://localhost:{a.port}/agent/screens.html?screen=architecture&theme={theme}"

            async def fresh(url: str = url) -> None:
                await page.goto(url)
                await page.wait_for_load_state("networkidle")
                await page.evaluate("document.fonts.ready")
                await page.wait_for_selector(".arch-node")

            await fresh()
            await page.click('[data-node="assist-stt"]')
            await shoot(page, out, f"business-node-{theme}.png")

            await fresh()
            await page.get_by_role("radio", name="Technical").click()
            await page.click('[data-node="db"]')
            await shoot(page, out, f"technical-{theme}.png")

            await fresh()
            await page.get_by_role("button", name="Walk a call").click()
            for _ in range(6):
                await page.keyboard.press("ArrowRight")
            await shoot(page, out, f"walk-step-7-{theme}.png")

            await fresh()
            await page.get_by_role("tab", name="Prospect questions").click()
            await shoot(page, out, f"prospect-questions-{theme}.png")

            await fresh()
            await page.get_by_role("button", name="Walk a call").click()
            await page.keyboard.press("ArrowRight")
            await page.keyboard.press("ArrowRight")
            await page.get_by_role("button", name="Present").click()
            await shoot(page, out, f"present-{theme}.png")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
