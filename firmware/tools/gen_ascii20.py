#!/usr/bin/env python3
# Generate a proportional ASCII bitmap font from Inter TTF at a chosen pixel size
# and patch it into firmware/src/cjk16.h, replacing the existing ASCII block.
#
# Why a size other than the CJK 16px: the CJK glyphs are a fixed 16x16 grid whose
# ink fills the whole cell, so 16px ASCII (cap-height ~12px) looks smaller than the
# Chinese next to it. Inter's cap-height is ~0.75x the point size, so to visually
# match the 16px CJK we pick a slightly larger size. 18px was chosen as the sweet
# spot (16 too small, 20 too big).
#
# Geometry is MEASURED from the font at the requested size (no hardcoded numbers),
# so the same script can regenerate any size:
#   python gen_ascii20.py --size 18 --apply
#   python gen_ascii20.py --size 16          # dry-run preview
#
# Cell geometry (example at size=18):
#   ink spans baseline-relative [top_min, bot_max]  ->  AH = -top_min + bot_max + 1 rows
#   baseline sits on row BASE = -top_min
#   cap top (uppercase, measured from 'H') is at row CAP  -> drawMixed aligns this to the 16px CJK top
#   widest ink + left side bearing ->  AW columns, stored MSB-first in a uint32_t
#
# Each glyph row is a uint32_t scanline: bit(AW-1) = leftmost column, black = 1.
# ascii{N}_width[] holds each glyph's advance width (proportional stepping).
import io, re, sys
from PIL import Image, ImageFont, ImageDraw

TTF   = r"D:/文档/GitHub/inksight/backend/fonts/truetype/Inter_24pt-Medium.ttf"
HDR   = r"D:/文档/GitHub/inksight/firmware/src/cjk16.h"
FIRST, LAST = 0x20, 0x7E

def load(size):
    with open(TTF, "rb") as fh:
        return ImageFont.truetype(io.BytesIO(fh.read()), size)

def measure(size):
    """Return (AW, AH, BASE, CAP, OFF) for the requested size."""
    font = load(size)
    asc, desc = font.getmetrics()
    PAD = size + 4
    BASE_C = PAD + asc                          # canvas baseline (for probing)
    top_min, bot_max, wmax, lmin = 999, -999, 0, 999
    for cp in range(FIRST, LAST + 1):
        canvas = Image.new("L", (size * 3 + PAD * 2, size * 3), 0)
        d = ImageDraw.Draw(canvas)
        d.text((PAD, BASE_C), chr(cp), fill=255, font=font, anchor="ls")
        bbox = canvas.getbbox()
        if bbox:
            l, t, r, b = bbox
            top_min = min(top_min, t - BASE_C)
            bot_max = max(bot_max, b - BASE_C)
            wmax    = max(wmax, r - PAD)
            lmin    = min(lmin, l - PAD)
    AH   = (-top_min) + bot_max + 1
    BASE = -top_min                             # cell baseline row
    OFF  = max(0, -lmin)                       # left side-bearing guard
    # cap height from 'H', measured at the SAME baseline as the probe above
    canvas = Image.new("L", (size * 3 + PAD * 2, size * 3), 0)
    d = ImageDraw.Draw(canvas)
    d.text((PAD, BASE_C), "H", fill=255, font=font, anchor="ls")
    bb = canvas.getbbox()
    cap_top_rel = (bb[1] - BASE_C) if bb else top_min   # negative: above baseline
    CAP = BASE + cap_top_rel                    # cell-row of uppercase cap-top
    AW = OFF + wmax + 1
    return AW, AH, BASE, CAP, OFF

def extract(font, ch, AW, AH, BASE, OFF):
    """Return (rows[AH] as ints, advance_width)."""
    canvas = Image.new("1", (AW + OFF + 2, AH), 0)
    d = ImageDraw.Draw(canvas)
    d.fontmode = "1"                       # no anti-alias -> crisp 1bpp
    d.text((OFF, BASE), ch, fill=1, font=font, anchor="ls")
    px = canvas.load()
    rows = []
    for r in range(AH):
        v = 0
        for c in range(AW):
            if px[OFF + c, r]:
                v |= (1 << (AW - 1 - c))
        rows.append(v)
    adv = round(font.getlength(ch))
    return rows, adv

def art(rows, AW):
    out = []
    for v in rows:
        line = "".join("#" if v & (1 << (AW - 1 - c)) else "." for c in range(AW))
        out.append(line)
    return "\n".join(out)

def build(size):
    font = load(size)
    AW, AH, BASE, CAP, OFF = measure(size)
    glyphs, widths = [], []
    for cp in range(FIRST, LAST + 1):
        rows, adv = extract(font, chr(cp), AW, AH, BASE, OFF)
        glyphs.append((cp, rows))
        widths.append(adv)
    return glyphs, widths, (AW, AH, BASE, CAP, OFF)

def fmt_c(glyphs, widths, geom, size):
    AW, AH, BASE, CAP, OFF = geom
    T = size                                   # token suffix, e.g. 18 -> ascii18
    L = []
    L.append(f"// ASCII {AW}x{AH} proportional @{size}px, extracted from Inter_24pt-Medium.ttf")
    L.append("// (see firmware/tools/gen_ascii20.py). Each row is a uint32_t scanline,")
    L.append(f"// bit(ASCII{T}_W-1)=leftmost column, black=1. Glyphs are pen-positioned")
    L.append("// (natural side bearings), stepping uses ascii%d_width[] (advance width)." % T)
    L.append(f"// Baseline row = {BASE}; cap-top row = {CAP} (drawMixed aligns this to the 16px CJK top).")
    L.append(f"#define ASCII{T}_FIRST 0x{FIRST:02X}")
    L.append(f"#define ASCII{T}_LAST  0x{LAST:02X}")
    L.append(f"#define ASCII{T}_W {AW}")
    L.append(f"#define ASCII{T}_H {AH}")
    L.append(f"#define ASCII{T}_BASE {BASE}")
    L.append(f"#define ASCII{T}_CAP {CAP}")
    L.append("")
    L.append(f"static const uint32_t ascii{T}_glyphs[][{AH}] = {{")
    for cp, rows in glyphs:
        vals = ", ".join(f"0x{v:X}" for v in rows)
        ch = chr(cp)
        disp = ch if ch != " " else " "
        L.append(f"    {{{vals}}}, // 0x{cp:02X} {disp}")
    L.append("};")
    L.append("")
    L.append("// Per-glyph advance width (px); used for proportional stepping.")
    L.append(f"static const uint8_t ascii{T}_width[] = {{")
    for i in range(0, len(widths), 12):
        chunk = ", ".join(str(w) for w in widths[i:i+12])
        L.append(f"    {chunk},")
    L.append("};")
    L.append("")
    L.append("// 码位 -> ascii%d glyph; 越界返回 NULL" % T)
    L.append(f"static inline const uint32_t* ascii{T}_lookup(uint32_t cp) {{")
    L.append(f"    if (cp < ASCII{T}_FIRST || cp > ASCII{T}_LAST) return 0;")
    L.append(f"    return ascii{T}_glyphs[cp - ASCII{T}_FIRST];")
    L.append("}")
    return "\n".join(L) + "\n"

def patch(block):
    src = open(HDR, encoding="utf-8").read()
    # size-agnostic: match any existing ASCII proportional block through its lookup fn
    pat = re.compile(
        r"// ASCII \d+x\d+ proportional.*?static inline const uint32_t\* ascii\d+_lookup\(uint32_t cp\) \{.*?\n\}\n",
        re.S)
    if not pat.search(src):
        print("!! could not locate ASCII block to replace"); sys.exit(1)
    new = pat.sub(block, src, count=1)
    open(HDR, "w", encoding="utf-8").write(new)
    print("patched", HDR)

def main():
    size = 18
    if "--size" in sys.argv:
        size = int(sys.argv[sys.argv.index("--size") + 1])
    glyphs, widths, geom = build(size)
    AW, AH, BASE, CAP, OFF = geom
    print(f"size={size} -> AW={AW} AH={AH} BASE={BASE} CAP={CAP} OFF={OFF}")
    block = fmt_c(glyphs, widths, geom, size)
    for ch in "Wmig@":
        cp = ord(ch)
        rows = glyphs[cp - FIRST][1]
        print(f"--- {ch!r} adv={widths[cp-FIRST]} ---")
        print(art(rows, AW))
    if "--apply" in sys.argv:
        patch(block)
    else:
        print("\n(dry-run; pass --apply to patch cjk16.h)")

if __name__ == "__main__":
    main()
