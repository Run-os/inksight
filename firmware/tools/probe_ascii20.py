#!/usr/bin/env python3
# Probe Inter TTF metrics at a target pixel size to decide the ascii20 cell:
# - overall pixel bbox across all printable ASCII (top/bottom relative to baseline)
# - max glyph advance/ink width
# so we can fix cell height H, baseline row BASE, and cell column count AW.
import io
from PIL import Image, ImageFont, ImageDraw

TTF = r"D:/文档/GitHub/inksight/backend/fonts/truetype/Inter_24pt-Medium.ttf"
FIRST, LAST = 0x20, 0x7E

def load(size):
    with open(TTF, "rb") as fh:
        return ImageFont.truetype(io.BytesIO(fh.read()), size)

def probe(size):
    font = load(size)
    asc, desc = font.getmetrics()
    # Render each glyph on a tall canvas with a known baseline, scan ink bbox.
    PAD = 20
    BASE = PAD + asc            # baseline y on canvas
    top_min = 999; bot_max = -999; wmax = 0; wch = ""
    per = {}
    for cp in range(FIRST, LAST + 1):
        ch = chr(cp)
        canvas = Image.new("L", (size * 3 + 2 * PAD, size * 3), 0)
        d = ImageDraw.Draw(canvas)
        d.text((PAD, BASE), ch, fill=255, font=font, anchor="ls")
        bbox = canvas.getbbox()  # (l,t,r,b) of ink, None if blank
        if bbox:
            l, t, r, b = bbox
            top_rel = t - BASE     # negative = above baseline
            bot_rel = b - BASE     # positive = below baseline
            w = r - PAD            # ink right edge relative to pen x
            top_min = min(top_min, top_rel)
            bot_max = max(bot_max, bot_rel)
            if w > wmax:
                wmax = w; wch = ch
            per[ch] = (top_rel, bot_rel, w)
        else:
            per[ch] = (0, 0, 0)
    print(f"--- size={size} ---")
    print(f"  font ascent={asc} descent={desc}")
    print(f"  ink top_min(rel baseline)={top_min}  bot_max={bot_max}")
    print(f"  glyph pixel height span = {bot_max - top_min}")
    print(f"  max ink width={wmax} (char {wch!r})")
    # cap height sample
    for c in "WMA@mwHxg y":
        if c in per:
            print(f"    {c!r}: top={per[c][0]} bot={per[c][1]} w={per[c][2]}")
    return top_min, bot_max, wmax

for s in (18, 19, 20, 21):
    probe(s)
