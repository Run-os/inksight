#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_preview.py — InkSight 待办列表 UI 预览生成器（验证用，与固件同构）

复刻 firmware/src/display.cpp 的取位与布局规则：
  - CJK 16x16 点阵（bit15=左, 黑=1）+ ASCII 5x7 列优先点阵 -> drawMixed
  - 7 段数码管时间（全局最大字号）
  - 三层固定结构：状态栏 / 待办列表 / 底栏
解析 firmware/src/cjk16.h 的真实点阵，输出 preview_todo.png，
并打印一张字符缩略图（供无图模型自检布局）。
"""
import os, re, sys
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
H_PATH = os.path.join(HERE, "..", "src", "cjk16.h")

W, H = 400, 300
WHITE, BLACK = 255, 0

# 与 firmware/src/display.cpp 一致的布局常量
UI_SB_H, UI_FT_H, UI_ROW_H, UI_PER_PAGE = 52, 18, 30, 7

# ── 5x7 ASCII 字体（与 display.cpp::getGlyph 一致）─────────
FONT5x7 = {
    'A':[0x7E,0x11,0x11,0x11,0x7E], 'B':[0x7F,0x49,0x49,0x49,0x36],
    'C':[0x3E,0x41,0x41,0x41,0x22], 'D':[0x7F,0x41,0x41,0x22,0x1C],
    'E':[0x7F,0x49,0x49,0x49,0x41], 'F':[0x7F,0x09,0x09,0x09,0x01],
    'G':[0x3E,0x41,0x49,0x49,0x3A], 'H':[0x7F,0x08,0x08,0x08,0x7F],
    'I':[0x00,0x41,0x7F,0x41,0x00], 'K':[0x7F,0x08,0x14,0x22,0x41],
    'L':[0x7F,0x40,0x40,0x40,0x40], 'M':[0x7F,0x02,0x0C,0x02,0x7F],
    'N':[0x7F,0x04,0x08,0x10,0x7F], 'O':[0x3E,0x41,0x41,0x41,0x3E],
    'P':[0x7F,0x09,0x09,0x09,0x06], 'R':[0x7F,0x09,0x19,0x29,0x46],
    'S':[0x26,0x49,0x49,0x49,0x32], 'T':[0x01,0x01,0x7F,0x01,0x01],
    'U':[0x3F,0x40,0x40,0x40,0x3F], 'V':[0x1F,0x20,0x40,0x20,0x1F],
    'W':[0x3F,0x40,0x38,0x40,0x3F], 'X':[0x63,0x14,0x08,0x14,0x63],
    'Y':[0x07,0x08,0x70,0x08,0x07], 'Z':[0x61,0x51,0x49,0x45,0x43],
    'a':[0x20,0x54,0x54,0x54,0x78], 'b':[0x7F,0x48,0x44,0x44,0x38],
    'c':[0x38,0x44,0x44,0x44,0x28], 'd':[0x38,0x44,0x44,0x28,0x7F],
    'e':[0x38,0x54,0x54,0x54,0x18], 'f':[0x00,0x08,0x7E,0x09,0x02],
    'g':[0x18,0xA4,0xA4,0xA4,0x7C], 'h':[0x7F,0x08,0x04,0x04,0x78],
    'i':[0x00,0x44,0x7D,0x40,0x00], 'k':[0x7F,0x10,0x28,0x44,0x00],
    'l':[0x00,0x41,0x7F,0x40,0x00], 'm':[0x7C,0x04,0x18,0x04,0x78],
    'n':[0x7C,0x08,0x04,0x04,0x78], 'o':[0x38,0x44,0x44,0x44,0x38],
    'p':[0x7C,0x14,0x14,0x14,0x08], 'r':[0x7C,0x08,0x04,0x04,0x08],
    's':[0x48,0x54,0x54,0x54,0x24], 't':[0x04,0x3F,0x44,0x40,0x20],
    'u':[0x3C,0x40,0x40,0x20,0x7C], 'v':[0x1C,0x20,0x40,0x20,0x1C],
    'w':[0x3C,0x40,0x30,0x40,0x3C],
    '0':[0x3E,0x51,0x49,0x45,0x3E], '1':[0x00,0x42,0x7F,0x40,0x00],
    '2':[0x42,0x61,0x51,0x49,0x46], '3':[0x21,0x41,0x45,0x4B,0x31],
    '4':[0x18,0x14,0x12,0x7F,0x10], '5':[0x27,0x45,0x45,0x45,0x39],
    '6':[0x3C,0x4A,0x49,0x49,0x30], '7':[0x01,0x71,0x09,0x05,0x03],
    '8':[0x36,0x49,0x49,0x49,0x36], '9':[0x06,0x49,0x49,0x29,0x1E],
    ':':[0x00,0x00,0x36,0x36,0x00], '-':[0x08,0x08,0x08,0x08,0x08],
    '.':[0x00,0x60,0x60,0x00,0x00], '/':[0x20,0x10,0x08,0x04,0x02],
    '!':[0x00,0x00,0x5F,0x00,0x00], ' ':[0x00,0x00,0x00,0x00,0x00],
}

# ── 解析 cjk16.h 真实点阵 ─────────────────────────────────
def load_cjk():
    txt = open(H_PATH, encoding="utf-8").read()
    def block(name):
        m = re.search(name + r"(?:\[[^\]]*\])*\s*=\s*\{(.*?)\n\};", txt, re.S)
        if not m:
            raise RuntimeError("cannot find %s[] in cjk16.h" % name)
        return m.group(1)
    cps = [int(h, 16) for h in re.findall(r"0x([0-9A-Fa-f]+)", block("cjk16_codepoints"))]
    gl_txt = block("cjk16_glyphs")
    glyphs = []
    for gm in re.finditer(r"\{([0-9A-Fa-fxX,\s]+)\}", gl_txt):
        vals = [int(h, 16) for h in re.findall(r"0x([0-9A-Fa-f]+)", gm.group(1))]
        if len(vals) == 16:
            glyphs.append(vals)
    assert len(cps) == len(glyphs), "count mismatch %d/%d" % (len(cps), len(glyphs))
    return dict(zip(cps, glyphs))

def load_ascii16():
    """解析 cjk16.h 的 ascii16_glyphs（8x16 半角，每字节一行，bit7=左）。"""
    # 去掉行注释避免注释里的 } 干扰括号匹配
    txt = re.sub(r"//[^\n]*", "", open(H_PATH, encoding="utf-8").read())
    first = int(re.search(r"#define\s+ASCII16_FIRST\s+(0x[0-9A-Fa-f]+)", txt).group(1), 16)
    m = re.search(r"ascii16_glyphs(?:\[[^\]]*\])*\s*=\s*\{(.*?)\n\};", txt, re.S)
    if not m:
        raise RuntimeError("cannot find ascii16_glyphs[] in cjk16.h")
    glyphs = {}
    cp = first
    for gm in re.finditer(r"\{([0-9A-Fa-fxX,\s]+)\}", m.group(1)):
        vals = [int(h, 16) for h in re.findall(r"0x([0-9A-Fa-f]+)", gm.group(1))]
        if len(vals) == 16:
            glyphs[cp] = vals
            cp += 1
    return glyphs

CJK = load_cjk()
ASCII16 = load_ascii16()

def cjk_lookup(cp):
    return CJK.get(cp)

def ascii16_lookup(cp):
    return ASCII16.get(cp)

# ── 像素原语 ────────────────────────────────────────────────
def px_set(px, x, y):
    if 0 <= x < W and 0 <= y < H:
        px[x, y] = BLACK

def fill_rect(px, x, y, w, h):
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            px_set(px, xx, yy)

def outline_rect(px, x, y, w, h):
    for xx in range(x, x + w):
        px_set(px, xx, y); px_set(px, xx, y + h - 1)
    for yy in range(y, y + h):
        px_set(px, x, yy); px_set(px, x + w - 1, yy)

def seg_fill(px, x, y, sw, sh):
    fill_rect(px, x, y, sw, sh)

# ── 文本渲染（与 drawMixed 同构）────────────────────────────
def render_cjk_glyph(px, cp, x, y):
    g = cjk_lookup(cp)
    if not g:
        outline_rect(px, x, y, 16, 16)
        return
    for r in range(16):
        bits = g[r]
        for c in range(16):
            if bits & (1 << (15 - c)):
                px_set(px, x + c, y + r)

def render_ascii_char(px, ch, x, y, scale):
    g = FONT5x7.get(ch, FONT5x7[' '])
    for col in range(5):
        for row in range(7):
            if g[col] & (1 << row):
                for dy in range(scale):
                    for dx in range(scale):
                        px_set(px, x + col * scale + dx, y + row * scale + dy)

def render_ascii16(px, cp, x, y):
    """8x16 半角 ASCII（同构固件 drawAscii16）：每字节一行，bit7=最左列。"""
    g = ascii16_lookup(cp)
    if not g:
        return
    for row in range(16):
        bits = g[row]
        for col in range(8):
            if bits & (0x80 >> col):
                px_set(px, x + col, y + row)

def render_mixed(px, text, x, y, ascii_scale=2):
    """中英混排：中文 16x16，ASCII 走 8x16 半角字库（与中文同基线、advance 9）。"""
    if ascii_scale < 1:
        ascii_scale = 1
    cx = x
    p = text.encode("utf-8")
    i = 0
    while i < len(p):
        b = p[i]
        if b < 0x80:
            render_ascii16(px, b, cx, y)
            cx += 9
            i += 1
        elif (b & 0xE0) == 0xC0 and i + 1 < len(p):
            cp = ((b & 0x1F) << 6) | (p[i + 1] & 0x3F)
            render_cjk_glyph(px, cp, cx, y)
            cx += 17
            i += 2
        elif (b & 0xF0) == 0xE0 and i + 2 < len(p):
            cp = ((b & 0x0F) << 12) | ((p[i + 1] & 0x3F) << 6) | (p[i + 2] & 0x3F)
            render_cjk_glyph(px, cp, cx, y)
            cx += 17
            i += 3
        else:
            i += 1
    return cx

def render_ascii_text(px, text, x, y, scale=1):
    """纯 ASCII 文本（无 CJK 顶部偏移），用于提醒时间 / 分页等小字。"""
    cx = x
    for ch in text:
        render_ascii_char(px, ch, cx, y, scale)
        cx += 5 * scale + 1

def measure_mixed(text, ascii_scale=2):
    if ascii_scale < 1:
        ascii_scale = 1
    cx = 0
    p = text.encode("utf-8")
    i = 0
    while i < len(p):
        b = p[i]
        if b < 0x80:
            cx += 9
            i += 1
        elif (b & 0xE0) == 0xC0 and i + 1 < len(p):
            cx += 17
            i += 2
        elif (b & 0xF0) == 0xE0 and i + 2 < len(p):
            cx += 17
            i += 3
        else:
            i += 1
    return cx - 1 if cx > 0 else 0

# ── 7-seg 数码管（与固件 draw7Seg* 同构）────────────────────
SEG_MASK = [0x3F,0x06,0x5B,0x4F,0x66,0x6D,0x7D,0x07,0x7F,0x6F]

def draw_7seg_digit(px, x, y, dw, dh, t, digit):
    m = SEG_MASK[digit & 0x0F]
    if m & 0x01: seg_fill(px, x+t, y, dw-2*t, t)
    if m & 0x40: seg_fill(px, x+t, y+dh//2-t//2, dw-2*t, t)
    if m & 0x08: seg_fill(px, x+t, y+dh-t, dw-2*t, t)
    if m & 0x20: seg_fill(px, x, y+t, t, dh//2-t)
    if m & 0x02: seg_fill(px, x+dw-t, y+t, t, dh//2-t)
    if m & 0x10: seg_fill(px, x, y+dh//2, t, dh//2-t)
    if m & 0x04: seg_fill(px, x+dw-t, y+dh//2, t, dh//2-t)

def draw_7seg_colon(px, x, y, dh, t):
    seg_fill(px, x, y+dh//3 - t//2, t, t)
    seg_fill(px, x, y+2*dh//3 - t//2, t, t)

def draw_7seg_text(px, x, y, dw, dh, t, s):
    cx = x
    for ch in s:
        if '0' <= ch <= '9':
            draw_7seg_digit(px, cx, y, dw, dh, t, int(ch))
            cx += dw + 2
        elif ch == ':':
            draw_7seg_colon(px, cx, y, dh, t)
            cx += t + 6
        else:
            cx += dw + 2

# ── 图标（与固件同构）───────────────────────────────────────
def draw_clipboard(px, x, y):
    outline_rect(px, x+2, y+3, 13, 14)
    seg_fill(px, x+4, y, 9, 4)
    for xx in range(x+2, x+15):
        px_set(px, xx, y+6)

def draw_battery(px, x, y, pct):
    outline_rect(px, x, y+2, 22, 12)
    seg_fill(px, x+22, y+5, 2, 6)
    fillw = (pct * 18) // 100
    if fillw > 18: fillw = 18
    if fillw < 2: fillw = 2
    seg_fill(px, x+2, y+4, fillw, 8)

def draw_checkbox(px, x, y, checked):
    outline_rect(px, x, y, 14, 14)
    if checked:
        pts = [(3,9),(4,10),(5,11),(6,10),(7,9),(8,8),(9,7),(10,6),(11,5),(5,7),(6,7),(7,7),(8,7)]
        for (dx, dy) in pts:
            px_set(px, x+dx, y+dy)

def draw_mini_list(px, x, y):
    for r in range(3):
        outline_rect(px, x, y+r*6, 4, 4)
        for xx in range(x+6, x+14):
            px_set(px, xx, y+r*6+2)

def draw_status_bar(px, hhmm, date, battery_pct):
    draw_clipboard(px, 4, 6)
    line_y = (UI_SB_H - 16) // 2                     # 垂直居中 16px 行
    cx = 26
    for ch in hhmm:
        render_ascii16(px, ord(ch), cx, line_y)      # 普通半角数字 HH:MM（同正文大小）
        cx += 9
    render_mixed(px, date, cx + 6, line_y, 1)        # 日期：MM/DD 星期X（同基线）
    draw_battery(px, W-26, 9, battery_pct)
    for xx in range(W):
        px_set(px, xx, UI_SB_H)                      # 状态栏底线

def draw_todo_list(px, items):
    y0 = UI_SB_H + 4
    for i, it in enumerate(items):
        row_top = y0 + i * UI_ROW_H
        draw_checkbox(px, 8, row_top + (UI_ROW_H - 14) // 2, it["done"])
        # body（中英混排，截断避免与提醒时间重叠）
        max_w = W - 30 - 58 - 6
        t = it["text"]
        while measure_mixed(t, 2) > max_w and len(t) > 1:
            t = t[:-1]
        render_mixed(px, t, 30, row_top + (UI_ROW_H - 16) // 2, 2)
        # reminder time（纯 ASCII 右对齐，居中到行，无 CJK 顶部偏移）
        if it.get("remind"):
            rw = measure_mixed(it["remind"], 1)
            yy = row_top + (UI_ROW_H - 7) // 2
            render_ascii_text(px, it["remind"], W - 6 - rw - 1, yy, 1)
        # 虚线分隔
        if i < len(items) - 1:
            for xx in range(8, W - 6, 4):
                px_set(px, xx, row_top + UI_ROW_H - 5)

def draw_footer(px):
    fy = H - UI_FT_H
    for xx in range(W):
        px_set(px, xx, fy)
    draw_mini_list(px, 8, H - 15)
    render_mixed(px, "待办", 16, H - 16, 1)
    render_mixed(px, "— 诸事有序", W - 6 - measure_mixed("— 诸事有序", 1) - 1, H - 16, 1)

def render_page(out_path, items, hhmm="21:47", date="07/13 星期一", battery_pct=87, page=0, total=3):
    img = Image.new("L", (W, H), WHITE)
    px = img.load()
    draw_status_bar(px, hhmm, date, battery_pct)
    draw_todo_list(px, items)
    draw_footer(px)
    pg = "%d / %d" % (page + 1, total)
    pgx = W - 6 - measure_mixed(pg, 1) - 1
    pgy = H - UI_FT_H - 14
    render_ascii_text(px, pg, pgx, pgy, 1)
    img.save(out_path)
    return img

def dump_ascii(img, block=6):
    w, h = img.size
    px = img.load()
    lines = []
    for y in range(0, h, block):
        line = ""
        for x in range(0, w, block):
            black = 0; tot = 0
            for yy in range(y, min(y+block, h)):
                for xx in range(x, min(x+block, w)):
                    tot += 1
                    if px[xx, yy] == BLACK:
                        black += 1
            line += '#' if black*3 >= tot else ' '
        lines.append(line)
    return "\n".join(lines)

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "preview_todo.png")
    todos = [
        {"text":"买菜：牛奶 milk 和鸡蛋", "done":False, "remind":"09:30"},
        {"text":"14:00 项目评审会议 review", "done":True,  "remind":"14:00"},
        {"text":"给妈妈打电话 call mom", "done":False, "remind":"16:00"},
        {"text":"Git 提交 firmware 代码", "done":False, "remind":"18:00"},
        {"text":"阅读《三体》第 3 章", "done":True,  "remind":"20:00"},
        {"text":"健身 run 30 分钟", "done":False, "remind":"21:00"},
    ]
    img = render_page(out, todos)
    print("saved", out, img.size)
    print(dump_ascii(img))
