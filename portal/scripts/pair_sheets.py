"""Before and after sheets for P5-3: one JPEG per screen and theme, labelled.

uv run --with pillow python portal/scripts/pair_sheets.py \
    BEFORE_DIR AFTER_DIR OUT_DIR [--suffix -1440] NAME...
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def sheet(before: Path, after: Path, out: Path, label: str) -> None:
    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    w, h = a.size
    pad, head = 24, 48
    s = Image.new("RGB", (w * 2 + pad * 3, h + head + pad), (244, 240, 234))
    s.paste(a, (pad, head))
    s.paste(b, (w + pad * 2, head))
    d = ImageDraw.Draw(s)
    d.text((pad, 16), f"BEFORE  {label}", fill=(29, 29, 29))
    d.text((w + pad * 2, 16), f"AFTER  {label}", fill=(29, 29, 29))
    s.save(out, "JPEG", quality=82, optimize=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("out")
    ap.add_argument("names", nargs="+")
    ap.add_argument("--suffix", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(a.names, 1):
        for theme in ("light", "dark"):
            src = f"{name}-{theme}.png"
            dest = out / f"p5-3-{i:02d}-{name}{a.suffix}-{theme}.jpg"
            sheet(Path(a.before) / src, Path(a.after) / src, dest, f"{name}{a.suffix}, {theme}")
            print(dest)


if __name__ == "__main__":
    main()
