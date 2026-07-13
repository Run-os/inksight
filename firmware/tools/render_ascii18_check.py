#!/usr/bin/env python3
# Visual verification for the ascii18 font ACTUALLY burned into cjk16.h,
# plus a mixed CJK+ASCII line rendered exactly like display.cpp::drawMixed,
# so we can confirm: (1) no clipping in the 18px Latin glyphs,
# (2) ASCII cap-top lines up with the 16px Chinese top.
import re
from PIL import Image

HDR = r"D:/文档/GitHub/inksight/firmware/src/cjk16.h"
OUT = r"D:/文档/GitHub/inksight/firmware/tools/ascii18_check.png"

def load():
    src = open(HDR, encoding="utf-8").read()
    def macro(n):
        m = re.search(rf"#define\s+{n}\s+(0x[0-9A-Fa-f]+|\d+)", src)
        return int(m.group(1), 0)
    # ascii18 geometry + data
    AW = macro("ASCII18_W"); AH = macro("ASCII18_H")
    BASE = macro("ASCII18_BASE"); CAP = macro("ASCII18_CAP")
    FIRST = macro("ASCII18_FIRST"); LAST = macro("ASCII18_LAST")
    gblock = re.search(r"ascii18_glyphs\[\]\[\d+\]\s*=\s*\{(.*?)\n\};", src, re.S).group(1)
    rows = re.findall(r"\{([^{}]*)\}", gblock)
    glyphs = []
    for r in rows:
        vals = [int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]+", r)]
        assert len(vals) == AH, f"row {len(vals)}!=H{AH}"
        glyphs.append(vals)
    wblock = re.search(r"ascii18_width\[\]\s*=\s*\{(.*?)\};", src, re.S).group(1)
    widths = [int(x) for x in re.findall(r"\d+", wblock)]
    assert len(glyphs) == LAST - FIRST + 1
    assert len(widths) == LAST - FIRST + 1
    # CJK: codepoints (sorted) + glyphs[][16], bit15=left, black=1
    cp_block = re.search(r"cjk16_codepoints\[\]\s*=\s*\{(.*?)\n\};", src, re.S).group(1)
    codepoints = [int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]+", cp_block)]
    cg_block = re.search(r"cjk16_glyphs\[\]\[16\]\s*=\s*\{(.*?)\n\};", src, re.S).group(1)
    cg_rows = re.findall(r"\{([^{}]*)\}", cg_block)
    cjk = []
    for r in cg_rows:
        vals = [int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]+", r)]
        assert len(vals) == 16, f"cjk row {len(vals)}"
        cjk.append(vals)
    return dict(AW=AW, AH=AH, BASE=BASE, CAP=CAP, FIRST=FIRST, LAST=LAST,
                glyphs=glyphs, widths=widths, codepoints=codepoints, cjk=cjk)

def draw_ascii18(px, x, y_top, glyph, cfg):
    """Mirror drawMixed: y_top is the cell's top row; cap-top sits at y_top+CAP."""
    W, H = cfg["AW"], cfg["AH"]
    for row in range(H):
        v = glyph[row]
        for col in range(W):
            if v & (1 << (W - 1 - col)):
                px[x + col, y_top + row] = 0

def cjk_index(cfg, cp):
    lo, hi = 0, len(cfg["codepoints"]) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if cfg["codepoints"][mid] == cp: return mid
        if cfg["codepoints"][mid] < cp: lo = mid + 1
        else: hi = mid - 1
    return -1

def draw_cjk(px, x, y_top, cp, cfg):
    idx = cjk_index(cfg, cp)
    if idx < 0: return False
    g = cfg["cjk"][idx]
    for row in range(16):
        v = g[row]
        for col in range(16):
            if v & (1 << (15 - col)):
                px[x + col, y_top + row] = 0
    return True

def mixed_line(text, cfg, pad=2):
    """Render one CJK+ASCII line using drawMixed's layout (top-aligned)."""
    # measure width
    cx = 0
    for ch in text:
        o = ord(ch)
        if cfg["FIRST"] <= o <= cfg["LAST"]:
            cx += cfg["widths"][o - cfg["FIRST"]] + 1
        elif cjk_index(cfg, o) >= 0:
            cx += 17
        else:
            cx += 8
    H = max(cfg["AH"], 16)
    img = Image.new("L", (cx + pad * 2, H + pad * 2), 255)
    px = img.load()
    ccx = pad
    for ch in text:
        o = ord(ch)
        if cfg["FIRST"] <= o <= cfg["LAST"]:
            draw_ascii18(px, ccx, pad, cfg["glyphs"][o - cfg["FIRST"]], cfg)
            ccx += cfg["widths"][o - cfg["FIRST"]] + 1
        elif cjk_index(cfg, o) >= 0:
            draw_cjk(px, ccx, pad, o, cfg)
            ccx += 17
        else:
            ccx += 8
    return img

def ascii_only_line(text, cfg, pad=2):
    W = cfg["AW"]; H = cfg["AH"]
    cx = 0
    for ch in text:
        o = ord(ch)
        if cfg["FIRST"] <= o <= cfg["LAST"]:
            cx += cfg["widths"][o - cfg["FIRST"]] + 1
    img = Image.new("L", (cx + pad * 2, H + pad * 2), 255)
    px = img.load()
    ccx = pad
    for ch in text:
        o = ord(ch)
        if cfg["FIRST"] <= o <= cfg["LAST"]:
            draw_ascii18(px, ccx, pad, cfg["glyphs"][o - cfg["FIRST"]], cfg)
            ccx += cfg["widths"][o - cfg["FIRST"]] + 1
    return img

def main():
    cfg = load()
    print(f"ascii18: {len(cfg['glyphs'])} glyphs W={cfg['AW']} H={cfg['AH']} "
          f"BASE={cfg['BASE']} CAP={cfg['CAP']}; CJK={len(cfg['codepoints'])}")
    S = 4; GAP = 8
    blocks = []
    # (1) ascii-only clipping/size samples
    for s in ["Wow! mmm wwW ill 0Oo @#&",
               "The quick brown fox jumps",
               "over the LAZY dog: 1234567890",
               "InkSight @100% - WiFi/MAC",
               "abcdefghijklmnopqrstuvwxyz",
               "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
               "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"]:
        blocks.append(ascii_only_line(s, cfg))
    # (2) mixed CJK+ASCII (alignment check)
    blocks.append(mixed_line("待办Todo 18px 完成", cfg))
    blocks.append(mixed_line("中文English混排Wi-Fi", cfg))
    blocks.append(mixed_line("买菜Milk 2L @超市", cfg))

    total_w = max(b.width for b in blocks)
    total_h = sum(b.height for b in blocks) + GAP * (len(blocks) - 1)
    sheet = Image.new("L", (total_w, total_h), 255)
    y = 0
    for b in blocks:
        sheet.paste(b, (0, y)); y += b.height + GAP
    big = sheet.resize((total_w * S, total_h * S), Image.NEAREST).convert("RGB")
    big.save(OUT)
    print("wrote", OUT, big.size)

if __name__ == "__main__":
    main()
