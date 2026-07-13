#!/usr/bin/env python3
"""
gen_ascii_pcf.py - 从 Inter PCF 预栅格化位图字体提取 8x16 ascii16 字模

替代 gen_cjk16.py 里用 NotoSerifSC TTF@16px mono 渲染 ASCII 的做法（笔画断笔）。
Inter 是专为屏幕小字号设计的拉丁字体，PCF 是预 hint 位图，每个像素都是字体设计师调过的，
笔画完整度远胜 FreeType 实时 mono 栅格化。

输出:
  - 打印每个字符的 bbox metrics（探查 baseline 是否越界）
  - firmware/tools/ascii_pcf_compare.png  新旧对比图
  - patch firmware/src/cjk16.h 的 ascii16 段落（CJK 不动）

用法: python gen_ascii_pcf.py [--suffix 12] [--baseline 13] [--apply]
"""
import argparse
import io
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
BMD = REPO / "backend" / "fonts" / "bitmap"
TTD = REPO / "backend" / "fonts" / "truetype"
CJK_H = REPO / "firmware" / "src" / "cjk16.h"

AW, AH = 8, 16          # ascii16 cell 尺寸（与 display.cpp::drawAscii16 兼容）
ASCII_FIRST, ASCII_LAST = 0x20, 0x7E


def _ttf(path, size):
    with open(path, "rb") as fh:
        return ImageFont.truetype(io.BytesIO(fh.read()), size)


# 后端 _bitmap_load_size_for_suffix 的映射：PCF strike suffix -> FreeType load size
_PCF_LOAD_SIZE = {9: 12, 10: 13, 11: 15, 12: 16, 13: 14}


def load_pcf(suffix):
    p = BMD / f"Inter_24pt-Medium-{suffix}.pcf"
    if not p.exists():
        sys.exit(f"[ERR] PCF 不存在: {p}")
    size = _PCF_LOAD_SIZE.get(suffix, 16)
    with open(p, "rb") as fh:
        # FreeType 加载 PCF：size 选最近的 strike，实际渲染 strike-{suffix} 位图
        return ImageFont.truetype(io.BytesIO(fh.read()), size)


def render_to_canvas(font, ch, baseline, dx=8, cw=40, ch_h=40):
    """渲染单字符到画布，anchor=ls (left-baseline) 放在 (dx, baseline)。返回 (canvas, px)。"""
    canvas = Image.new("1", (cw, ch_h), 1)
    d = ImageDraw.Draw(canvas)
    d.fontmode = "1"
    d.text((dx, baseline), ch, font=font, fill=0, anchor="ls")
    return canvas, canvas.load()


def pixel_bbox(px, cw, ch_h):
    """扫描像素求紧致 bbox。返回 (x0,y0,x1,y1) 或 None。"""
    x0, y0, x1, y1 = cw, ch_h, -1, -1
    found = False
    for y in range(ch_h):
        for x in range(cw):
            if px[x, y] == 0:
                found = True
                if x < x0: x0 = x
                if x > x1: x1 = x
                if y < y0: y0 = y
                if y > y1: y1 = y
    return (x0, y0, x1, y1) if found else None


def extract_glyph(font, cp, baseline):
    """提取单字符 8x16 字模。水平居中到 8px，baseline 锚定 row=baseline。返回 list[16] of uint8。"""
    ch = chr(cp)
    cw, ch_h = 40, 40
    canvas, px = render_to_canvas(font, ch, baseline, cw=cw, ch_h=ch_h)
    bb = pixel_bbox(px, cw, ch_h)
    if bb is None:
        return [0] * AH, None
    x0, y0, x1, y1 = bb
    w = x1 - x0 + 1
    # 水平居中到 8px cell
    target_x0 = (AW - w) // 2
    if target_x0 < 0:
        target_x0 = 0  # 字形比 cell 宽，左对齐裁剪
    rows = [0] * AH
    for row in range(AH):
        v = 0
        for col in range(AW):
            cx = x0 + col - target_x0
            cy = row
            if 0 <= cx < cw and 0 <= cy < ch_h and px[cx, cy] == 0:
                v |= (1 << (7 - col))
        rows[row] = v
    return rows, bb


def fmt_glyph_block(glyphs):
    """生成 C 数组文本（ascii16_glyphs 段落）。"""
    lines = []
    for cp in range(ASCII_FIRST, ASCII_LAST + 1):
        g = glyphs[cp]
        disp = chr(cp) if cp not in (0x5C, 0x7F) else "backslash"
        if cp == 0x7F:
            disp = "del"
        row_hex = ", ".join(f"0x{v:02X}" for v in g)
        lines.append(f"    {{{row_hex}}}, // 0x{cp:02X} {disp}")
    return "\n".join(lines)


def patch_cjk16(glyphs, suffix, baseline):
    """替换 cjk16.h 的 ascii16 段落。CJK 部分字节级不动。"""
    text = CJK_H.read_text(encoding="utf-8")
    # 段落从 '// ASCII 8x16 half-width' 注释到 ascii16_glyphs 数组的 '};'
    # 保留 defines 和 lookup 函数，只替换注释头 + 数组内容
    header = (
        f"// ASCII {AW}x{AH} half-width, extracted from Inter_24pt-Medium-{suffix}.pcf\n"
        f"// (pre-hinted PCF bitmap; bit7=left; black=1). Replaces TTF@16px mono which\n"
        f"// dropped thin Latin strokes. baseline=row {baseline}, horizontally centered.\n"
        f"#define ASCII16_FIRST 0x{ASCII_FIRST:02X}\n"
        f"#define ASCII16_LAST  0x{ASCII_LAST:02X}\n"
        f"#define ASCII16_W {AW}\n"
        f"#define ASCII16_H {AH}\n\n"
        f"static const uint8_t ascii16_glyphs[][{AH}] = {{\n"
    )
    body = fmt_glyph_block(glyphs)
    footer = "\n};\n"
    new_block = header + body + footer

    # 匹配旧段落：从 '// ASCII 8x16' 到 ascii16_glyphs 数组的 '};\n'
    pattern = re.compile(
        r"// ASCII 8x16 half-width.*?static const uint8_t ascii16_glyphs\[\]\[16\] = \{.*?\};\n",
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        sys.exit("[ERR] 在 cjk16.h 里找不到 ascii16 段落，取消 patch")
    new_text = text[:m.start()] + new_block + text[m.end():]
    CJK_H.write_text(new_text, encoding="utf-8")
    print(f"[ok] patched {CJK_H} (ascii16 段落替换为 Inter PCF-{suffix})")


def make_compare_png(pcf_glyphs, ttf_glyphs, suffix, baseline):
    """新旧对比图：上=旧 TTF@16 mono，下=新 PCF。"""
    sample = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
              "abcdefghijklmnopqrstuvwxyz"
              "0123456789:-/. ")
    SCALE = 5
    CW, CH = AW * SCALE, AH * SCALE
    GAP = 6
    PAD = 16
    LABEL = 16
    cols = len(sample)

    def draw_row(canvas, d, glyphs, y0, font_lbl):
        for i, ch in enumerate(sample):
            g = glyphs[ord(ch)]
            # 把 8x16 字模画到画布
            for row in range(AH):
                v = g[row]
                for col in range(AW):
                    if v & (1 << (7 - col)):
                        x = PAD + i * (CW + GAP) + col * SCALE
                        y = y0 + row * SCALE
                        for dy in range(SCALE):
                            for dx in range(SCALE):
                                canvas.putpixel((x + dx, y + dy), 0)
            d.text((PAD + i * (CW + GAP) + CW // 2 - 4, y0 + CH + 2), ch, fill=0, font=font_lbl)

    total_w = PAD * 2 + cols * (CW + GAP) - GAP
    total_h = PAD * 2 + 2 * (CH + LABEL + 8) + 8
    canvas = Image.new("1", (total_w, total_h), 1)
    d = ImageDraw.Draw(canvas)
    d.fontmode = "1"
    f_lbl = _ttf(TTD / "Inter_24pt-Medium.ttf", 11)
    f_title = _ttf(TTD / "Inter_24pt-Medium.ttf", 13)

    d.text((PAD, 2), "ascii16: old TTF@16px mono (broken) vs new Inter PCF (complete)", fill=0, font=f_title)
    y = PAD + 20
    d.text((PAD, y - LABEL), "OLD: NotoSerifSC TTF @16px fontmode=1  [current cjk16.h]", fill=0, font=f_lbl)
    draw_row(canvas, d, ttf_glyphs, y, f_lbl)
    y += CH + LABEL + 16
    d.text((PAD, y - LABEL), f"NEW: Inter_24pt-Medium-{suffix}.pcf  baseline={baseline}", fill=0, font=f_lbl)
    draw_row(canvas, d, pcf_glyphs, y, f_lbl)

    out = REPO / "firmware" / "tools" / "ascii_pcf_compare.png"
    canvas.save(str(out))
    print(f"[ok] {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", type=int, default=13, help="PCF strike suffix (9/10/11/12/13)")
    ap.add_argument("--baseline", type=int, default=13, help="baseline row in 8x16 cell")
    ap.add_argument("--apply", action="store_true", help="patch cjk16.h (default: dry-run)")
    args = ap.parse_args()

    font = load_pcf(args.suffix)
    print(f"[info] PCF: Inter_24pt-Medium-{args.suffix}.pcf, baseline={args.baseline}")

    # 探查 metrics
    print("\n[metrics] bbox per char (y0..y1, 应在 0..15 内):")
    overflow = []
    pcf_glyphs = {}
    for cp in range(ASCII_FIRST, ASCII_LAST + 1):
        g, bb = extract_glyph(font, cp, args.baseline)
        pcf_glyphs[cp] = g
        if bb:
            x0, y0, x1, y1 = bb
            if y0 < 0 or y1 > AH - 1:
                overflow.append((chr(cp), cp, y0, y1))
    if overflow:
        print(f"  ⚠ {len(overflow)} 字符垂直越界 (baseline 需调整):")
        for ch, cp, y0, y1 in overflow[:15]:
            print(f"    0x{cp:02X} {ch!r}: y0={y0} y1={y1}")
    else:
        print("  全部字符 y0..y1 在 [0,15] 内，baseline OK")

    # 旧 TTF 字模（用于对比）
    print("\n[info] 渲染旧 TTF 字模用于对比...")
    ttf_font = _ttf(TTD / "NotoSerifSC-Regular.ttf", 16)
    ttf_glyphs = {}
    for cp in range(ASCII_FIRST, ASCII_LAST + 1):
        # 复刻 gen_cjk16.py 的 render_ascii
        img = Image.new("1", (AW + 2, AH + 6), 1)
        dd = ImageDraw.Draw(img)
        dd.fontmode = "1"
        dd.text((0, 13), chr(cp), font=ttf_font, fill=0, anchor="ls")
        px = img.load()
        rows = []
        for row in range(AH):
            v = 0
            for col in range(AW):
                if row < img.height and col < img.width and px[col, row] == 0:
                    v |= (1 << (7 - col))
            rows.append(v)
        ttf_glyphs[cp] = rows

    # 对比图
    make_compare_png(pcf_glyphs, ttf_glyphs, args.suffix, args.baseline)

    # 笔画密度对比
    print("\n[density] black-pixel count (PCF vs TTF):")
    print(f"  {'ch':>4} {'PCF':>5} {'TTF':>5} {'Δ':>5}")
    for cp in range(ASCII_FIRST, ASCII_LAST + 1):
        if chr(cp).isalnum() or chr(cp) in ":-/.":
            np_ = sum(bin(v).count("1") for v in pcf_glyphs[cp])
            nt = sum(bin(v).count("1") for v in ttf_glyphs[cp])
            print(f"  {chr(cp)!r:>4} {np_:>5} {nt:>5} {np_ - nt:>+5}")

    if args.apply:
        print("\n[patch] 应用到 cjk16.h...")
        patch_cjk16(pcf_glyphs, args.suffix, args.baseline)
    else:
        print("\n[dry-run] 未加 --apply，未修改 cjk16.h。确认对比图 OK 后加 --apply 重跑。")


if __name__ == "__main__":
    main()
