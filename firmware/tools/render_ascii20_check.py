#!/usr/bin/env python3
# Visual verification for the ascii20 font that is actually burned into
# firmware/src/cjk16.h. Parses ascii20_glyphs[]/ascii20_width[] straight out of
# the header (source of truth = the real device data), renders sample strings
# the way display.cpp does (drawAscii20 + proportional stepping), and writes a
# scaled PNG so we can eyeball clipping / spacing / size-vs-CJK.
import io, re, sys
from PIL import Image, ImageDraw

HDR = r"D:/文档/GitHub/inksight/firmware/src/cjk16.h"
OUT = r"D:/文档/GitHub/inksight/firmware/tools/ascii20_check.png"

def load_header():
    src = open(HDR, encoding="utf-8").read()

    def macro(name):
        m = re.search(rf"#define\s+{name}\s+(0x[0-9A-Fa-f]+|\d+)", src)
        return int(m.group(1), 0)

    W    = macro("ASCII20_W")
    H    = macro("ASCII20_H")
    BASE = macro("ASCII20_BASE")
    CAP  = macro("ASCII20_CAP")
    FIRST= macro("ASCII20_FIRST")
    LAST = macro("ASCII20_LAST")

    # glyphs: one {..H hex values..} row per code point, in FIRST..LAST order
    gblock = re.search(
        r"ascii20_glyphs\[\]\[\d+\]\s*=\s*\{(.*?)\n\};", src, re.S).group(1)
    rows = re.findall(r"\{([^{}]*)\}", gblock)
    glyphs = []
    for r in rows:
        vals = [int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]+", r)]
        assert len(vals) == H, f"row has {len(vals)} vals, expected {H}"
        glyphs.append(vals)

    # widths
    wblock = re.search(
        r"ascii20_width\[\]\s*=\s*\{(.*?)\};", src, re.S).group(1)
    widths = [int(x) for x in re.findall(r"\d+", wblock)]

    assert len(glyphs) == LAST - FIRST + 1, f"{len(glyphs)} glyphs"
    assert len(widths) == LAST - FIRST + 1, f"{len(widths)} widths"
    return dict(W=W, H=H, BASE=BASE, CAP=CAP, FIRST=FIRST, LAST=LAST,
                glyphs=glyphs, widths=widths)

def draw_ascii20(px, x, y, glyph, cfg):
    """Mirror display.cpp::drawAscii20 (scale=1): y = top row of the cell."""
    W, H = cfg["W"], cfg["H"]
    for row in range(H):
        v = glyph[row]
        for col in range(W):
            if v & (1 << (W - 1 - col)):
                px[x + col, y + row] = 0  # black

def measure(text, cfg):
    total = 0
    for ch in text:
        cp = ord(ch)
        if cfg["FIRST"] <= cp <= cfg["LAST"]:
            total += cfg["widths"][cp - cfg["FIRST"]] + 1
    return total

def render_line(text, cfg):
    """Return a 1x PIL image (black text on white) of one line at the cell top."""
    W, H = cfg["W"], cfg["H"]
    width = measure(text, cfg) + 4
    img = Image.new("L", (width, H + 4), 255)
    px = img.load()
    cx = 2
    for ch in text:
        cp = ord(ch)
        if cfg["FIRST"] <= cp <= cfg["LAST"]:
            g = cfg["glyphs"][cp - cfg["FIRST"]]
            draw_ascii20(px, cx, 2, g, cfg)
            cx += cfg["widths"][cp - cfg["FIRST"]] + 1
    return img

def main():
    cfg = load_header()
    print(f"loaded ascii20: {len(cfg['glyphs'])} glyphs, "
          f"W={cfg['W']} H={cfg['H']} BASE={cfg['BASE']} CAP={cfg['CAP']}")

    samples = [
        "Wow! mmm www ill 0Oo",
        "The quick brown fox jumps",
        "over the LAZY dog: 1234567890",
        "InkSight @ 100% - WiFi/MAC",
        "abcdefghijklmnopqrstuvwxyz",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
    ]

    SCALE = 4
    GAP = 6
    line_imgs = [render_line(s, cfg) for s in samples]
    total_w = max(im.width for im in line_imgs)
    total_h = sum(im.height for im in line_imgs) + GAP * (len(line_imgs) - 1)

    sheet = Image.new("L", (total_w, total_h), 255)
    y = 0
    for im in line_imgs:
        sheet.paste(im, (0, y))
        y += im.height + GAP

    big = sheet.resize((total_w * SCALE, total_h * SCALE), Image.NEAREST)
    big = big.convert("RGB")
    big.save(OUT)
    print("wrote", OUT, big.size)

if __name__ == "__main__":
    main()
