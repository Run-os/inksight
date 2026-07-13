#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_preview.py — InkSight 待办列表 UI 预览生成器（验证用，非设备代码）

它复刻 firmware/src/display.cpp 的取位规则：
  - CJK: 16x16 点阵，bit15=最左列，黑=1  -> 调 drawGlyph16 等价逻辑
  - ASCII: 5x7 列优先点阵（bit0=顶行），可 scale 缩放
并解析 firmware/src/cjk16.h 的真实点阵数据，渲染一张 400x300 的
「黑白墨水长屏」待办列表页，用于目视验收「待办原生渲染 + 中英混排」。

不依赖任何设备编译工具链，只需 Pillow。
"""
import os, re, sys
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
H_PATH = os.path.join(HERE, "..", "src", "cjk16.h")

W, H = 400, 300
WHITE, BLACK = 255, 0

# ── 5x7 ASCII 字体（与 display.cpp::getGlyph 完全一致）─────────
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

# ── 解析 cjk16.h 的真实点阵 ─────────────────────────────────
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
    assert len(cps) == len(glyphs), "codepoint/glyph count mismatch %d/%d" % (len(cps), len(glyphs))
    return dict(zip(cps, glyphs))

CJK = load_cjk()

def cjk_lookup(cp):
    return CJK.get(cp)

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

def hline(px, x0, x1, y):
    for xx in range(x0, x1):
        px_set(px, xx, y)

def vline(px, x, y0, y1):
    for yy in range(y0, y1):
        px_set(px, x, yy)

def dashed_hline(px, x0, x1, y, dash=2, gap=2):
    on = True
    xx = x0
    while xx < x1:
        if on:
            for k in range(dash):
                if xx + k < x1:
                    px_set(px, xx + k, y)
        xx += dash + gap
        on = not on

# ── 文本渲染（与 drawMixed 同构）────────────────────────────
def render_cjk_glyph(px, cp, x, y):
    g = cjk_lookup(cp)
    if not g:                      # 缺字 -> 空心方框
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

def render_mixed(px, text, x, y, ascii_scale=2):
    if ascii_scale < 1:
        ascii_scale = 1
    cx = x
    p = text.encode("utf-8")
    i = 0
    while i < len(p):
        b = p[i]
        if b < 0x80:
            top = 16 - 7 * ascii_scale
            if top < 0:
                top = 0
            render_ascii_char(px, chr(b), cx, y + top, ascii_scale)
            cx += 5 * ascii_scale + 1
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

def measure_mixed(text, ascii_scale=2):
    if ascii_scale < 1:
        ascii_scale = 1
    cx = 0
    p = text.encode("utf-8")
    i = 0
    while i < len(p):
        b = p[i]
        if b < 0x80:
            cx += 5 * ascii_scale + 1
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

# ── 像素图标 ────────────────────────────────────────────────
def draw_clipboard(px, x, y):
    outline_rect(px, x + 2, y + 3, 12, 13)        # 板身
    fill_rect(px, x + 4, y, 8, 4)                  # 顶部夹
    hline(px, x + 2, x + 14, y + 6)               # 板内横线

def draw_battery(px, x, y):
    outline_rect(px, x, y + 2, 20, 11)            # 外壳
    fill_rect(px, x + 20, y + 5, 2, 5)            # 正极凸起
    fill_rect(px, x + 2, y + 4, 12, 7)           # 电量 ~70%

def draw_checkbox(px, x, y, checked=False):
    outline_rect(px, x, y, 13, 13)
    if checked:
        # 对勾
        for k in range(4):
            px_set(px, x + 2 + k, y + 8 - k)
        for k in range(5):
            px_set(px, x + 5 + k, y + 6 - k)

def draw_minilist(px, x, y):
    for k in range(3):
        outline_rect(px, x, y + k * 5, 4, 4)
        hline(px, x + 6, x + 14, y + k * 5 + 2)

# ── 整页渲染 ────────────────────────────────────────────────
def render_page(out_path):
    img = Image.new("L", (W, H), WHITE)
    px = img.load()

    # 顶部状态栏 (0..44)
    draw_clipboard(px, 6, 8)
    # 时间（放大 ASCII 占位；正式固件用 7 段数码管字库）
    render_mixed_time(px, "21:47", 26, 5, scale=5)
    # 日期 "07/13 星期一"
    render_mixed(px, "07/13 星期一", 175, 14, ascii_scale=2)
    draw_battery(px, W - 26, 9)
    hline(px, 0, W, 46)                            # 状态栏底线

    # 中间待办区
    todos = [
        ("买菜：牛奶 milk 和鸡蛋", "09:30"),
        ("14:00 项目评审会议 review", "14:00"),
        ("给妈妈打电话 call mom", "16:00"),
        ("Git 提交 firmware 代码", "18:00"),
        ("阅读《三体》第 3 章", "20:00"),
        ("健身 run 30 分钟", "21:00"),
    ]
    top = 54
    row_h = 26
    for idx, (text, remind) in enumerate(todos):
        y = top + idx * row_h
        draw_checkbox(px, 6, y + 2, checked=(idx == 1))   # 第 2 条演示已完成
        # 正文（中英混排，必要时截断避免溢出）
        max_w = W - 6 - 60 - 22
        t = text
        while measure_mixed(t, 2) > max_w and len(t) > 1:
            t = t[:-1]
        render_mixed(px, t, 24, y, ascii_scale=2)
        # 右侧提醒时间（ASCII 小字）
        render_mixed(px, remind, W - 6 - measure_mixed(remind, 1) - 1, y + 4, ascii_scale=1)
        if idx < len(todos) - 1:
            dashed_hline(px, 6, W - 6, y + row_h - 4)
    # 分页提示（右下角小字）
    page = "1 / 3"
    render_mixed(px, page, W - 6 - measure_mixed(page, 1) - 1, top + len(todos) * row_h - 2, ascii_scale=1)

    # 底部底栏
    hline(px, 0, W, H - 18)
    draw_minilist(px, 6, H - 15)
    render_mixed(px, "待办", 14, H - 16, ascii_scale=1)
    render_mixed(px, "— 诸事有序", W - 6 - measure_mixed("— 诸事有序", 1) - 1, H - 16, ascii_scale=1)

    img.save(out_path)
    print("saved", out_path, img.size)

def render_mixed_time(px, text, x, y, scale=5):
    """纯 ASCII 时间占位（数码管字库待固件实现）。"""
    cx = x
    for ch in text:
        render_ascii_char(px, ch, cx, y, scale)
        cx += 5 * scale + 2

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "preview_todo.png")
    render_page(out)
