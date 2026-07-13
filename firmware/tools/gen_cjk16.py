#!/usr/bin/env python3
"""
gen_cjk16.py - 从思源宋体 TTF 生成 16x16 1-bit 点阵中文字库 (cjk16.h)

输出格式与 firmware/src/display.cpp::drawGlyph16 完全兼容:
  - 每个汉字 = const uint16_t glyph[16] (16 行, 每行一个 uint16_t)
  - bit15 = 最左列(col 0), bit0 = 最右列(col 15)
  - 黑色(笔画) = 1

用法:
  python gen_cjk16.py --out ../src/cjk16.h [--level 1|2] [--font PATH] [--preview png]
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 16  # 点阵边长(px)

# 常用中文标点 (补充在汉字之外的可显示字符)
PUNCT = "，。、；：！？“”‘’（）《》【】「」『』—…·"


def find_font(explicit):
    if explicit:
        p = Path(explicit)
        if not p.exists():
            sys.exit(f"[ERR] 字体不存在: {p}")
        return p
    repo = Path(__file__).resolve().parent.parent.parent  # firmware/tools -> repo root
    cands = [
        repo / "backend" / "fonts" / "truetype" / "NotoSerifSC-Regular.ttf",
        repo / "backend" / "fonts" / "truetype" / "NotoSerifSC-Bold.ttf",
    ]
    for c in cands:
        if c.exists():
            return c
    sys.exit("[ERR] 找不到思源宋体 TTF，请用 --font 指定路径")


def build_charset(level):
    """生成字表 (按 Unicode 码位升序去重)。"""
    chars = []
    # GB2312: 区 0xA1..0xF7, 位 0xA1..0xFE
    # 一级汉字: 区 0xB0..0xD7 (16..55 区, 拼音序常用字)
    qu_lo, qu_hi = (0xB0, 0xD7) if level == 1 else (0xA1, 0xF7)
    for qu in range(qu_lo, qu_hi + 1):
        for wei in range(0xA1, 0xFF):
            try:
                ch = bytes([qu, wei]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if ch and ch not in chars:
                chars.append(ch)
    for ch in PUNCT:
        if ch and ch not in chars:
            chars.append(ch)
    return sorted(set(chars))


def render_glyph(font, ch):
    """单字符 -> 16 行 uint16_t 点阵 (bit15=左, 黑=1)。

    关键: 用 mode "1" 图像 + fontmode="1" 单色 hinting 渲染(与后端 apply_text_fontmode 一致),
    而非旧的 "L" 灰度 + 硬阈值。FreeType 单色渲染会做网格拟合(hinting), 让思源宋体的
    细横笔保底落到 >=1px, 从而消除"中文太细 / 横线消失"。
    """
    img = Image.new("1", (SIZE, SIZE), 1)  # 白底(1=白)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"                        # 单色渲染, 关闭抗锯齿
    bb = d.textbbox((0, 0), ch, font=font)  # 字形 bounding box
    w = bb[2] - bb[0]
    h = bb[3] - bb[1]
    x = (SIZE - w) // 2 - bb[0]  # 居中
    y = (SIZE - h) // 2 - bb[1]
    d.text((x, y), ch, font=font, fill=0)  # 黑字(0=黑)
    px = img.load()
    glyph = []
    for row in range(SIZE):
        v = 0
        for col in range(SIZE):
            if px[col, row] == 0:  # 黑=笔画
                v |= (1 << (15 - col))
        glyph.append(v)
    return glyph


AW, AH = 8, 16          # ASCII 半角字模: 8 宽 x 16 高
ASCII_BASELINE = 13     # 基线行, 使大写/数字顶部≈第2行, 与 16 格中文视觉居中带对齐
ASCII_FIRST, ASCII_LAST = 0x20, 0x7E


def render_ascii(afont, cp):
    """单个 ASCII 码位 -> 16 行 uint8_t 点阵 (bit7=左, 黑=1), 半角 8x16。

    与中文同源(同一 TTF)、同为 fontmode="1" 单色渲染, 保证中英文笔画粗细一致,
    取代固件里 5x7 放大 2x 的"又粗又方"英文。
    """
    ch = chr(cp)
    img = Image.new("1", (AW + 2, AH + 6), 1)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    # 左对齐(带自然左边距), 基线锚定, 使各字符共享同一基线
    d.text((0, ASCII_BASELINE), ch, font=afont, fill=0, anchor="ls")
    px = img.load()
    rows = []
    for row in range(AH):
        v = 0
        for col in range(AW):
            if row < img.height and col < img.width and px[col, row] == 0:
                v |= (1 << (7 - col))
        rows.append(v)
    return rows


def gen_preview(font_path, chars, path, cols=16, rows=12):
    big = ImageFont.truetype(str(font_path), SIZE * 3)
    cell = SIZE * 3 + 4
    img = Image.new("1", (cols * cell, rows * cell), 1)  # 白底
    d = ImageDraw.Draw(img)
    n = min(cols * rows, len(chars))
    for i in range(n):
        ch = chars[i]
        cx = (i % cols) * cell
        cy = (i // cols) * cell
        d.text((cx + cell // 2, cy + cell // 2), ch, font=big, fill=0, anchor="mm")
    img.save(path)
    print(f"[preview] {path} ({n} 字)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", help="TTF 字体路径 (默认自动定位思源宋体)")
    ap.add_argument("--out", required=True, help="输出 .h 路径")
    ap.add_argument("--level", type=int, choices=[1, 2], default=1,
                    help="1=GB2312 一级汉字(~3755); 2=GB2312 全量(~6763)")
    ap.add_argument("--preview", help="可选: 生成前 N 字预览 PNG 路径")
    args = ap.parse_args()

    font_path = find_font(args.font)
    print(f"[info] 字体: {font_path}")
    font = ImageFont.truetype(str(font_path), SIZE)
    afont = ImageFont.truetype(str(font_path), SIZE)  # ASCII 同源同字号, 保证粗细一致

    chars = build_charset(args.level)
    print(f"[info] 字表: {len(chars)} 字")
    glyphs = [render_glyph(font, ch) for ch in chars]
    codepoints = [ord(ch) for ch in chars]  # 已升序

    # ASCII 半角字库 (0x20..0x7E), 与中文同源
    ascii_glyphs = [render_ascii(afont, cp) for cp in range(ASCII_FIRST, ASCII_LAST + 1)]
    print(f"[info] ASCII 字库: {len(ascii_glyphs)} 字 @ {AW}x{AH} (mono)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        f.write("/* Auto-generated by gen_cjk16.py - do not edit manually */\n")
        f.write("#ifndef CJK16_H\n#define CJK16_H\n\n")
        f.write("#include <stdint.h>\n\n")
        f.write(f"// {len(chars)} Chinese chars @16x16 1-bit; bit15=left; black=1\n")
        f.write("// Compatible with display.cpp::drawGlyph16\n\n")
        f.write(f"#define CJK16_COUNT {len(chars)}\n\n")
        f.write("static const uint32_t cjk16_codepoints[] = {\n")
        for i in range(0, len(codepoints), 8):
            f.write("    " + ", ".join(f"0x{cp:04X}" for cp in codepoints[i:i + 8]) + ",\n")
        f.write("};\n\n")
        f.write("static const uint16_t cjk16_glyphs[][16] = {\n")
        for g in glyphs:
            f.write("    {" + ", ".join(f"0x{v:04X}" for v in g) + "},\n")
        f.write("};\n\n")
        f.write("// 二分查找码位 -> glyph; 未命中返回 NULL\n")
        f.write("static inline const uint16_t* cjk16_lookup(uint32_t cp) {\n")
        f.write("    int lo = 0, hi = CJK16_COUNT - 1;\n")
        f.write("    while (lo <= hi) {\n")
        f.write("        int mid = (lo + hi) >> 1;\n")
        f.write("        uint32_t v = cjk16_codepoints[mid];\n")
        f.write("        if (v == cp) return cjk16_glyphs[mid];\n")
        f.write("        if (v < cp) lo = mid + 1; else hi = mid - 1;\n")
        f.write("    }\n    return 0;\n}\n\n")

        # ── ASCII 半角字库 (与中文同源, mono 渲染) ──
        f.write(f"// ASCII {AW}x{AH} half-width, same TTF as CJK; bit7=left; black=1\n")
        f.write(f"#define ASCII16_FIRST 0x{ASCII_FIRST:02X}\n")
        f.write(f"#define ASCII16_LAST  0x{ASCII_LAST:02X}\n")
        f.write(f"#define ASCII16_W {AW}\n")
        f.write(f"#define ASCII16_H {AH}\n\n")
        f.write(f"static const uint8_t ascii16_glyphs[][{AH}] = {{\n")
        for cp, g in zip(range(ASCII_FIRST, ASCII_LAST + 1), ascii_glyphs):
            disp = chr(cp) if cp != 0x5C else "backslash"
            f.write("    {" + ", ".join(f"0x{v:02X}" for v in g) + f"}}, // 0x{cp:02X} {disp}\n")
        f.write("};\n\n")
        f.write("// 码位 -> ascii glyph; 越界返回 NULL\n")
        f.write("static inline const uint8_t* ascii16_lookup(uint32_t cp) {\n")
        f.write("    if (cp < ASCII16_FIRST || cp > ASCII16_LAST) return 0;\n")
        f.write("    return ascii16_glyphs[cp - ASCII16_FIRST];\n}\n\n")

        f.write("#endif // CJK16_H\n")

    print(f"[ok] {out} ({len(chars)} 字, ~{out.stat().st_size // 1024} KB)")

    if args.preview:
        gen_preview(font_path, chars, args.preview)


if __name__ == "__main__":
    main()
