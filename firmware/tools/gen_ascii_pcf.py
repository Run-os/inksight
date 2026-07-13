#!/usr/bin/env python3
"""
gen_ascii_pcf.py - 从 Inter PCF 预栅格化位图字体提取不等宽 ascii16 字模

替代 gen_cjk16.py 里用 NotoSerifSC TTF@16px mono 渲染 ASCII 的做法（笔画断笔 + 宽字符被裁）。
Inter 是专为屏幕小字号设计的拉丁字体，PCF 是预 hint 位图，每个像素都是字体设计师调过的，
笔画完整度远胜 FreeType 实时 mono 栅格化。

关键修复:
  1. 不等宽（proportional）：每字符按 PCF 实际像素宽度存储，不再塞进固定 8px cell
     （修掉 W/m/w/@/M 等宽字符右侧被裁、只显示残形如 "m→n" 的 bug）
  2. 左对齐：字形左对齐存进 12px 宽 cell，固件按 width[] + 1px gap 步进排版
     （修掉窄字符 i/l 两侧留白过多、字间空隙过大）
  3. 新增 ascii16_width[] 数组，drawMixed/measureMixed/drawStatusBar 按实际宽排版

输出:
  - 打印每个字符的 bbox metrics（探查 baseline 是否越界、宽度分布）
  - firmware/tools/ascii_pcf_compare.png  新旧对比图（按不等宽排版）
  - patch firmware/src/cjk16.h 的 ascii16 段落（CJK 不动，ascii16 改为 12 列 + width 数组）

用法: python gen_ascii_pcf.py [--suffix 13] [--baseline 13] [--apply]
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

AW, AH = 12, 16         # ascii16 cell 最大列宽（容纳最宽 W=11px）；每字符实际宽见 ascii16_width[]
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
    """提取单字符不等宽字模。字形左对齐存进 12px cell（col 0..w-1），
    baseline 锚定 row=baseline。返回 (list[16] of uint8, width, bbox)。"""
    ch = chr(cp)
    cw, ch_h = 40, 40
    canvas, px = render_to_canvas(font, ch, baseline, cw=cw, ch_h=ch_h)
    bb = pixel_bbox(px, cw, ch_h)
    if bb is None:
        return [0] * AH, 0, None
    x0, y0, x1, y1 = bb
    w = x1 - x0 + 1
    # 左对齐：字形最左像素 → cell col 0（不居中、不裁剪）
    rows = [0] * AH
    for row in range(AH):
        v = 0
        for col in range(AW):
            cx = x0 + col          # 直接 = 字形左边界 + col
            cy = row
            if 0 <= cx < cw and 0 <= cy < ch_h and px[cx, cy] == 0:
                v |= (1 << (AW - 1 - col))   # bit(AW-1-col)=col，col0 在最左
        rows[row] = v
    return rows, w, bb


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


def fmt_width_array(width_list):
    """生成 ascii16_width[] C 数组文本。width_list 按 cp 升序。"""
    lines = []
    for i in range(0, len(width_list), 12):
        chunk = width_list[i:i + 12]
        lines.append("    " + ", ".join(f"{v}" for v in chunk) + ", ")
    return "\n".join(lines).rstrip(", ")


def patch_cjk16(glyphs, widths, suffix, baseline):
    """替换 cjk16.h 的 ascii16 段落（glyphs + width 数组）。CJK 部分字节级不动。"""
    text = CJK_H.read_text(encoding="utf-8")
    header = (
        f"// ASCII {AW}x{AH} proportional, extracted from Inter_24pt-Medium-{suffix}.pcf\n"
        f"// (pre-hinted PCF bitmap; bit7=leftmost col; black=1). Replaces TTF@16px mono which\n"
        f"// dropped thin Latin strokes AND clipped wide glyphs (W/m/w) in its 8px cell.\n"
        f"// Left-aligned into the {AW}-col cell; ascii16_width[] gives each glyph's real pixel width.\n"
        f"// Firmware steps with cx += ascii16_width[cp] + 1 (1px gap) instead of a fixed 9px slot.\n"
        f"// baseline=row {baseline}.\n"
        f"#define ASCII16_FIRST 0x{ASCII_FIRST:02X}\n"
        f"#define ASCII16_LAST  0x{ASCII_LAST:02X}\n"
        f"#define ASCII16_W {AW}\n"
        f"#define ASCII16_H {AH}\n\n"
        f"static const uint8_t ascii16_glyphs[][{AH}] = {{\n"
    )
    body = fmt_glyph_block(glyphs)
    width_list = [widths[cp] for cp in range(ASCII_FIRST, ASCII_LAST + 1)]
    width_block = (
        f"}};\n\n"
        f"// Per-glyph real pixel width (0..{AW}); used for proportional stepping.\n"
        f"static const uint8_t ascii16_width[] = {{\n"
        f"{fmt_width_array(width_list)}\n"
        f"}};\n"
    )
    new_block = header + body + width_block

    # 匹配旧段落：从 '// ASCII' 注释到 ascii16_glyphs 数组的 '};\n'
    pattern = re.compile(
        r"// ASCII .*?static const uint8_t ascii16_glyphs\[\]\[\d+\] = \{.*?\};\n",
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        sys.exit("[ERR] 在 cjk16.h 里找不到 ascii16 段落，取消 patch")
    new_text = text[:m.start()] + new_block + text[m.end():]
    CJK_H.write_text(new_text, encoding="utf-8")
    print(f"[ok] patched {CJK_H} (ascii16 -> PCF-{suffix}: {AW} cols + width[])")


def make_compare_png(pcf_glyphs, pcf_widths, ttf_glyphs, suffix, baseline):
    """新旧对比图（按不等宽排版）：上=旧 TTF@16 mono，下=新 PCF。"""
    sample = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
              "abcdefghijklmnopqrstuvwxyz"
              "0123456789:-/. ")
    SCALE = 5
    CH = AH * SCALE
    GAP = 6
    PAD = 16
    LABEL = 16
    cols = len(sample)

    def draw_row(canvas, d, glyphs, widths, y0, font_lbl):
        x = PAD
        for ch in sample:
            g = glyphs[ord(ch)]
            w = widths[ord(ch)]
            # 按不等宽：字形左对齐从 x 开始，占 w 列
            for row in range(AH):
                v = g[row]
                for col in range(AW):
                    if v & (1 << (AW - 1 - col)):
                        for dy in range(SCALE):
                            for dx in range(SCALE):
                                canvas.putpixel((x + col * SCALE + dx, y0 + row * SCALE + dy), 0)
            d.text((x + max(0, w) * SCALE // 2 - 4, y0 + CH + 2), ch, fill=0, font=font_lbl)
            x += (max(1, w) + GAP) * SCALE

    # 总宽按两行实际步进取最大
    old_row_w = sum((8 + GAP) for _ in sample) * SCALE - GAP * SCALE
    new_row_w = sum((max(1, pcf_widths[ord(c)]) + GAP) for c in sample) * SCALE - GAP * SCALE
    total_w = PAD * 2 + max(old_row_w, new_row_w)
    total_h = PAD * 2 + 2 * (CH + LABEL + 8) + 8
    canvas = Image.new("1", (total_w, total_h), 1)
    d = ImageDraw.Draw(canvas)
    d.fontmode = "1"
    f_lbl = _ttf(TTD / "Inter_24pt-Medium.ttf", 11)
    f_title = _ttf(TTD / "Inter_24pt-Medium.ttf", 13)

    d.text((PAD, 2), "ascii16: old TTF@16px mono (clipped) vs new Inter PCF (proportional)", fill=0, font=f_title)
    y = PAD + 20
    d.text((PAD, y - LABEL), "OLD: NotoSerifSC TTF @16px fontmode=1  (fixed 8px slot, wide glyphs clipped)", fill=0, font=f_lbl)
    draw_row(canvas, d, ttf_glyphs, [8] * 128, y, f_lbl)  # 旧：固定 8px 宽（等宽）
    y += CH + LABEL + 16
    d.text((PAD, y - LABEL), f"NEW: Inter_24pt-Medium-{suffix}.pcf  baseline={baseline}  (proportional, 1px gap)", fill=0, font=f_lbl)
    draw_row(canvas, d, pcf_glyphs, pcf_widths, y, f_lbl)

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
    pcf_widths = {}
    for cp in range(ASCII_FIRST, ASCII_LAST + 1):
        g, w, bb = extract_glyph(font, cp, args.baseline)
        pcf_glyphs[cp] = g
        pcf_widths[cp] = w
        if bb:
            x0, y0, x1, y1 = bb
            if y0 < 0 or y1 > AH - 1:
                overflow.append((chr(cp), cp, y0, y1))
            if w > AW:
                overflow.append((chr(cp), cp, w))
    if overflow:
        print(f"  ⚠ {len(overflow)} 字符异常 (baseline 越界 / 超宽):")
        for item in overflow[:15]:
            ch, cp = item[0], item[1]
            extra = f" w={item[2]}" if len(item) > 2 else ""
            print(f"    0x{cp:02X} {ch!r}:{extra}")
    else:
        print("  全部字符 y0..y1 在 [0,15] 内、宽度 <= AW，baseline OK")

    # 宽度分布
    ws = [pcf_widths[cp] for cp in range(ASCII_FIRST, ASCII_LAST + 1)]
    print(f"\n[width] ASCII 实际像素宽度: min={min(ws)} max={max(ws)} avg={sum(ws)/len(ws):.1f}")
    clipped_old = [chr(cp) for cp in range(ASCII_FIRST, ASCII_LAST + 1) if pcf_widths[cp] >= 8]
    print(f"  旧 8px cell 会被裁的字符(宽>=8): {''.join(clipped_old)}")

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
                    v |= (1 << (AW - 1 - col))
            rows.append(v)
        ttf_glyphs[cp] = rows

    # 对比图（旧：固定 8px 等宽；新：不等宽）
    make_compare_png(pcf_glyphs, pcf_widths, ttf_glyphs, args.suffix, args.baseline)

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
        patch_cjk16(pcf_glyphs, pcf_widths, args.suffix, args.baseline)
    else:
        print("\n[dry-run] 未加 --apply，未修改 cjk16.h。确认对比图 OK 后加 --apply 重跑。")


if __name__ == "__main__":
    main()
