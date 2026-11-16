"""Crop Deepgram material swatches into textures for the PoC mockups.

Outputs to /opt/data/work/mockups/v3/tex/: <name>.jpg (raw crop) and <name>_tile.jpg (2x2 mirrored, seamless).
"""
from pathlib import Path

from PIL import Image, ImageOps

SRC = "/opt/data/work/mockups/brand_materials/slide_14_img0.png"
OUT = Path("/opt/data/work/mockups/v3/tex")
OUT.mkdir(parents=True, exist_ok=True)
# Interior boxes in original 2048x1300 pixels (inset from the edges and labels).
BOXES = {
    "paper": (175, 150, 305, 400),
    "clay": (690, 150, 840, 470),
    "stone": (1210, 170, 1350, 450),
    "bakelite": (1730, 130, 1880, 470),
    "vinyl": (165, 830, 320, 1150),
    "copper": (690, 820, 840, 1150),
    "aluminum": (1210, 820, 1360, 1150),
}
im = Image.open(SRC).convert("RGB")
for name, box in BOXES.items():
    c = im.crop(box)
    c.save(OUT / f"{name}.jpg", quality=90)
    w, h = c.size
    tile = Image.new("RGB", (w * 2, h * 2))
    tile.paste(c, (0, 0))
    tile.paste(ImageOps.mirror(c), (w, 0))
    tile.paste(ImageOps.flip(c), (0, h))
    tile.paste(ImageOps.flip(ImageOps.mirror(c)), (w, h))
    tile.save(OUT / f"{name}_tile.jpg", quality=88)
    px = c.resize((1, 1)).getpixel((0, 0))
    print(name, c.size, "avg", "#%02x%02x%02x" % px)
