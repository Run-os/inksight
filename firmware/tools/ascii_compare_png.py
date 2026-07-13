"""Render a side-by-side PNG comparing the three ASCII rendering paths,
so we can visually confirm which strokes are incomplete."""
import io
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
TTD = REPO / "backend" / "fonts" / "truetype"
BMD = REPO / "backend" / "fonts" / "bitmap"

def _ttf(path, size):
    with open(path, "rb") as fh:
        return ImageFont.truetype(io.BytesIO(fh.read()), size)

# Firmware path: NotoSerifSC TTF @ 16px, mono, baseline=13, sample 8x16 (gen_cjk16.py exact replica)
def fw_glyph(cp):
    f = _ttf(TTD / "NotoSerifSC-Regular.ttf", 16)
    img = Image.new("1", (10, 22), 1)
    d = ImageDraw.Draw(img); d.fontmode = "1"
    d.text((0, 13), chr(cp), font=f, fill=0, anchor="ls")
    return img.crop((0, 0, 8, 16))

# Backend PCF path: NotoSerifSC-Regular-12.pcf loaded at 16
def pcf_glyph(cp):
    p = BMD / "NotoSerifSC-Regular-12.pcf"
    with open(p, "rb") as fh:
        f = ImageFont.truetype(io.BytesIO(fh.read()), 16)
    img = Image.new("1", (40, 40), 1)
    d = ImageDraw.Draw(img); d.fontmode = "1"
    d.text((4, 24), chr(cp), font=f, fill=0)
    return img.crop((0, 8, 8, 24))

# Backend Latin path: Inter TTF @ 12px (English body text font)
def inter_glyph(cp):
    f = _ttf(TTD / "Inter_24pt-Medium.ttf", 12)
    img = Image.new("1", (40, 40), 1)
    d = ImageDraw.Draw(img); d.fontmode = "1"
    d.text((4, 22), chr(cp), font=f, fill=0)
    return img.crop((0, 8, 8, 24))

sample = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:-/."

SCALE = 4
CELL_W, CELL_H = 8 * SCALE, 16 * SCALE
GAP = 6
PAD = 16
LABEL_H = 14
COLS = len(sample)

def render_row(title, fn, y0, canvas):
    d = ImageDraw.Draw(canvas)
    d.text((PAD, y0 - LABEL_H + 2), title, fill=0)
    for i, ch in enumerate(sample):
        g = fn(ord(ch))
        x = PAD + i * (CELL_W + GAP)
        # upscale
        big = g.resize((CELL_W, CELL_H), Image.NEAREST)
        canvas.paste(big, (x, y0))
        d.text((x + CELL_W//2 - 3, y0 + CELL_H + 2), ch, fill=0)

total_w = PAD * 2 + COLS * (CELL_W + GAP) - GAP
total_h = PAD * 2 + 3 * (CELL_H + LABEL_H + 16) + 20
canvas = Image.new("1", (total_w, total_h), 1)
d = ImageDraw.Draw(canvas)
title_font = _ttf(TTD / "NotoSerifSC-Regular.ttf", 14)
d.text((PAD, 4), "ASCII stroke comparison (white=stroke)", fill=0, font=title_font)

y = PAD + 24
render_row("1) Firmware: NotoSerifSC TTF @16px mono (cjk16.h source)", fw_glyph, y, canvas)
y += CELL_H + LABEL_H + 22
render_row("2) Backend PCF: NotoSerifSC-Regular-12.pcf @16 (pre-hinted bitmap)", pcf_glyph, y, canvas)
y += CELL_H + LABEL_H + 22
render_row("3) Backend Latin: Inter_24pt-Medium TTF @12px mono (English body)", inter_glyph, y, canvas)

out = Path(__file__).resolve().parent / "ascii_compare.png"
canvas.save(str(out))
print(f"[ok] {out}  ({total_w}x{total_h})")

# Also dump pixel counts for firmware path to flag suspiciously thin glyphs
print("\nFirmware-path black-pixel counts (8x16=128 max):")
for ch in sample:
    g = fw_glyph(ord(ch))
    px = g.load()
    n = sum(1 for y in range(g.height) for x in range(g.width) if px[x,y]==0)
    flag = "  <-- THIN" if n <= 8 and ch.isalpha() else ""
    print(f"  {ch!r}: {n:3d}{flag}")
