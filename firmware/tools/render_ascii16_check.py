#!/usr/bin/env python3
# Render the proportional ascii16 glyphs from cjk16.h using the EXACT same
# bit layout as firmware/src/display.cpp::drawAscii16, so we can visually
# confirm that wide glyphs (W/m/w/@/M) are NOT clipped and that the
# inter-character gap is tight (width + 1px only).
import re, sys
from PIL import Image, ImageDraw

HDR = r"D:/文档/GitHub/inksight/firmware/src/cjk16.h"
OUT = r"D:/文档/GitHub/inksight/firmware/tools/ascii16_proportional_check.png"

def load():
    src = open(HDR, encoding="utf-8").read()
    # glyph block — isolate the ascii16 section, then parse its 16-value rows
    blk = re.search(r"static const uint16_t ascii16_glyphs\[\]\[16\] = \{(.*?)\};",
                    src, re.S).group(1)
    rows = re.findall(r"\{(0x[0-9a-fA-F]+(?:\s*,\s*0x[0-9a-fA-F]+){15})\}", blk)
    glyphs = []
    for r in rows:
        vals = [int(x.strip(), 0) for x in r.split(",") if x.strip()]
        glyphs.append(vals)  # 16 vals per glyph, each a 16-bit scanline
    # width array
    wm = re.search(r"static const uint8_t ascii16_width\[\] = \{(.*?)\};", src, re.S)
    widths = [int(x.strip(), 0) for x in wm.group(1).split(",") if x.strip()]
    return glyphs, widths

def glyph_for(cp, glyphs, first=0x20):
    idx = cp - first
    if 0 <= idx < len(glyphs):
        return glyphs[idx]
    return None

def draw_text(draw, x0, y0, text, glyphs, widths, first=0x20, scale=4,
              fg=0, gap=1):
    cx = x0
    for ch in text:
        cp = ord(ch)
        g = glyph_for(cp, glyphs, first)
        if g:
            for row in range(16):
                bits = g[row]
                for col in range(12):
                    if bits & (1 << (11 - col)):
                        px = cx + col * scale
                        py = y0 + row * scale
                        if draw is not None:
                            draw.rectangle([px, py, px + scale - 1, py + scale - 1],
                                          fill=fg)
            w = widths[cp - first] if 0 <= (cp - first) < len(widths) else 0
            cx += (max(1, w) + gap) * scale
    return cx

def main():
    glyphs, widths = load()
    print(f"glyphs={len(glyphs)} widths={len(widths)}")
    S = 6
    samples = ["W m w @ M A i l t",
               "InkSight Todo 2026",
               "Hello, World! ww mm @@",
               "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
               "abcdefghijklmnopqrstuvwxyz"]
    pad = 16
    line_h = 16 * S + 18
    W = 0
    for s in samples:
        w_end = draw_text(None, 0, 0, s, glyphs, widths)  # dry run for width
        W = max(W, w_end)
    W += pad * 2
    H = pad * 2 + line_h * len(samples)
    img = Image.new("1", (W, H), 1)  # white bg
    d = ImageDraw.Draw(img)
    y = pad
    for s in samples:
        draw_text(d, pad, y, s, glyphs, widths)
        y += line_h
    img.save(OUT)
    print("wrote", OUT, img.size)

if __name__ == "__main__":
    main()
