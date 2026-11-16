"""Check every external link in the Architecture page content (P5-6, PRD AP-21).

Usage:
    uv run python scripts/check_links.py [path/to/content.ts]

Reads every http(s) URL from portal/src/architecture/content.ts, requests each one
(GET, following redirects, a browser like User-Agent), and prints the final status.
Exits 1 if any link does not end in HTTP 200. Needs network access, so it is not part
of the default test run.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTENT = ROOT / "portal" / "src" / "architecture" / "content.ts"
URL = re.compile(r"https?://[^\"'`\s)]+")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


def links(path: Path) -> list[str]:
    return sorted(set(URL.findall(path.read_text())))


def check(urls: list[str]) -> list[tuple[str, int | str, str]]:
    out: list[tuple[str, int | str, str]] = []
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20) as client:
        for url in urls:
            try:
                r = client.get(url)
                final = str(r.url) if str(r.url) != url else ""
                out.append((url, r.status_code, final))
            except httpx.HTTPError as e:
                out.append((url, type(e).__name__, ""))
    return out


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_CONTENT
    urls = links(path)
    results = check(urls)
    bad = 0
    for url, status, final in results:
        ok = status == 200
        bad += not ok
        note = f" -> {final}" if final else ""
        print(f"{'ok ' if ok else 'BAD'} {status} {url}{note}")
    print(f"{len(results) - bad} of {len(results)} links returned 200")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
