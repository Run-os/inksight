#!/usr/bin/env python3
# Compare English glyph sizes (16/17/18/19/20 px) next to the REAL 16px CJK
# glyphs burned in cjk16.h, so we can pick the size that best matches Chinese.
# English is rendered live from Inter_24pt-Medium.ttf; Chinese is decoded from
# cjk16_codepoints/cjk16_glyphs (exactly what the device shows).
import io, re
from PIL import Image, ImageFont, ImageDraw

HDR = r"D:/文档/GitHub/inksight/firmware/src/cjk16.h"
TTF = r"D:/文档/GitHub/inksight/backend/fonts/truetype/Inter_24pt-Medium.ttf"
OUT = r"D:/文档/GitHub/inksight/firmware/tools/ascii_size_compare.png"

CJK_W = 16          # CJK cell is 16x16, ink fills the cell, top-aligned at y
SIZES = [16, 17, 18, 19, 20]

# ---- parse CJK glyphs from cjk16.h ----------------------------------------
def load_cjk():
    src = open(HDR, encoding="utf-8").read()
    cps_blk = re.search(r"cjk16_codepoints\[\]\s*=\s*\{(.*?)\};", src, re.S).group(1)
    cps = [int(x, 0) for x in re.findall(r"0x[0-9A-Fa-f]+", cps_blk)]
    g_blk = re.search(r"cjk16_glyphs\[\]\[16\]\s*=\s*\{(.*?)\n\};", src, re.S).group(1)
    rows = re.findall(r"\{([^{}]*)\}", g_blk)
    glyphs = [[int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]+", r)] for r in rows]
    return {cp: glyphs[i] for i, cp in enumerate(cps)}

CJK = load_cjk()

def draw_cjk(px, x, y, cp):
    g = CJK.get(cp)
    if not g:
        return CJK_W
    for r in range(16):
        v = g[r]
        for c in range(16):
            if v & (1 << (15 - c)):
                px[x + c, y + r] = 0
    return CJK_W

# ---- English via TTF -------------------------------------------------------
def load_font(size):
    with open(TTF, "rb") as fh:
        return ImageFont.truetype(io.BytesIO(fh.read()), size)

def cap_top(font):
    """Row of the cap-top (uppercase) relative to baseline, as positive px."""
    a, d = font.getmetrics()
    # measure ink of 'H'
    tmp = Image.new("1", (size_max_w, 60), 0)
    dd = ImageDraw.Draw(tmp); dd.fontmode = "1"
    dd.text((2, 40), "H", fill=1, font=font, anchor="ls")
    bbox = tmp.getbbox()
    baseline = 40
    return baseline - bbox[1]   # px from baseline up to cap-top

size_max_w = 400

def draw_english(px, x, baseline_y, text, font):
    """Draw text with baseline at baseline_y; return advance width used."""
    tmp = Image.new("1", (size_max_w, 60), 0)
    dd = ImageDraw.Draw(tmp); dd.fontmode = "1"
    dd.text((2, 30), text, fill=1, font=font, anchor="ls")
    tpx = tmp.load()
    w = round(font.getlength(text))
    for r in range(60):
        for c in range(size_max_w):
            if tpx[c, r]:
                px[x + (c - 2), baseline_y + (r - 30)] = 0
    return w

# ---- compose ---------------------------------------------------------------
# mixed samples: Chinese (16px) + English (variable). We align English cap-top
# to the CJK top row (same rule as display.cpp: y - CAP).
SAMPLES = [
    ("待办", "Todo", "2026"),
    ("项目", "InkSight", "100%"),
    ("提醒", "WiFi", "MAC"),
]

def render_row(size):
    font = load_font(size)
    ctop = cap_top(font)          # cap-top px above baseline
    # cell height: give generous vertical room
    H = 40
    cjk_top = 12                  # where the 16px CJK block starts
    baseline_y = cjk_top + ctop   # english baseline so cap-top == cjk_top
    img = Image.new("L", (760, H), 255)
    px = img.load()
    cx = 4
    for zh, en, num in SAMPLES:
        for ch in zh:
            cx += draw_cjk(px, cx, cjk_top, ord(ch)) + 1
        cx += 4
        cx += draw_english(px, cx, baseline_y, en + " " + num, font) + 8
    return img, f"EN {size}px  (CJK 16px)"

def main():
    rows = [render_row(s) for s in SIZES]
    # also a pure-16px-CJK reference label handled inline
    LBL_W = 150
    SCALE = 3
    GAP = 10
    total_w = LBL_W + max(im.width for im, _ in rows)
    total_h = sum(im.height for im, _ in rows) + GAP * (len(rows) - 1)
    sheet = Image.new("L", (total_w, total_h), 255)
    d = ImageDraw.Draw(sheet)
    y = 0
    for im, label in rows:
        d.text((6, y + im.height // 2 - 6), label, fill=0)
        sheet.paste(im, (LBL_W, y))
        y += im.height + GAP
    big = sheet.resize((total_w * SCALE, total_h * SCALE), Image.NEAREST).convert("RGB")
    big.save(OUT)
    print("wrote", OUT, big.size)

if __name__ == "__main__":
    main()
