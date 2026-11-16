"""Screenshot every portal screen from the dev gallery (P5-3).

Usage (with `vite` serving portal/ on PORT):
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright --with pillow \
        python portal/scripts/shoot_screens.py OUT_DIR [PREFIX] [--port 5173] [--size 1600x1000]

Writes OUT_DIR/<PREFIX><screen>-<theme>.png for every gallery screen in light and dark.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

SCREENS = [
    "sign-in",
    "sign-in-error",
    "home",
    "home-offline",
    "home-reconnecting",
    "agent-drop",
    "incoming",
    "in-call-empty",
    "in-call-listening",
    "in-call-suggestion",
    "in-call-accepted",
    "in-call-llm-error",
    "in-call-slow-connector",
    "in-call-ask",
    "in-call-audio-error",
    "in-call-verify",
    "in-call-verified",
    "wrap-up",
    "wrap-up-loading",
    "wrap-up-error",
]

# Screens whose state needs time to show (the slow lookup note waits SLOW_ASK_MS, 4 s).
EXTRA_WAIT_MS = {"in-call-slow-connector": 4200}


async def main() -> None:
    from playwright.async_api import async_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("prefix", nargs="?", default="")
    ap.add_argument("--port", type=int, default=5173)
    ap.add_argument("--size", default="1600x1000")
    ap.add_argument("--only", default="")
    ap.add_argument("--reduced-motion", action="store_true")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.split("x"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    only = [s for s in a.only.split(",") if s]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(
            viewport={"width": w, "height": h},
            reduced_motion="reduce" if a.reduced_motion else "no-preference",
        )
        page = await ctx.new_page()
        page.on("console", lambda m: m.type == "error" and print(f"  [console] {m.text}"))
        page.on("pageerror", lambda e: print(f"  [pageerror] {e}"))
        for name in only or SCREENS:
            for theme in ("light", "dark"):
                url = f"http://localhost:{a.port}/agent/screens.html?screen={name}&theme={theme}"
                path = out / f"{a.prefix}{name}-{theme}.png"
                for attempt in range(3):
                    try:
                        await page.goto(url)
                        await page.wait_for_load_state("networkidle")
                        await page.evaluate("document.fonts.ready")
                        await page.wait_for_timeout(700 + EXTRA_WAIT_MS.get(name, 0))
                        await page.screenshot(path=str(path), animations="disabled", timeout=60_000)
                        break
                    except Exception as e:  # noqa: BLE001 - vite may reload while optimizing deps
                        print(f"  retry {name}-{theme}: {type(e).__name__}")
                        if attempt == 2:
                            raise
                print(path)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
