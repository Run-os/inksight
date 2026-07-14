# -*- coding: utf-8 -*-
"""
render_preview.py — InkSight 待办列表 UI 预览生成器（验证用，与固件逐像素同构）

复刻 firmware/src/display.cpp 的取位与布局规则：
  - CJK 16x16 点阵（bit15=左, 黑=1）—— 解析 cjk16.h 真实点阵
  - ASCII 比例字体 ascii18（Inter @18px, 20x23 cell, ASCII18_CAP=4）
    ASCII 在 drawMixed 中硬编码 scale=1（asciiScale 参数对 ASCII 无效），
    步进按 ascii18_width[cp]+1（advance 已含 side bearing）
  - 三层固定结构：状态栏 / 待办列表 / 底栏
解析 firmware/src/cjk16.h 的真实点阵，输出 preview_todo.png，
并打印一张字符缩略图（供无图模型自检布局）。

字体同步说明：固件当前 UI 用的是 ascii18（20x23, Inter @18px），
本脚本必须与之一致。早期版本曾误用已废弃的 ascii20（24x26），
导致预览英文比真机明显偏大——现已修正。
"""
import os, re, sys
from PIL import Image
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
H_PATH = os.path.join(HERE, "..", "src", "cjk16.h")

W, H = 400, 300
WHITE, BLACK = 255, 0

# 与 firmware/src/display.cpp 一致的布局常量
UI_SB_H, UI_FT_H, UI_ROW_H, UI_PER_PAGE = 52, 26, 30, 6

GLYPH_WIFI_CONNECTED = [0x0000, 0x0000, 0x07E0, 0x1FF8, 0x7C3E, 0xE007, 0x4182, 0x0FF0,
                        0x1FF8, 0x0810, 0x0000, 0x03C0, 0x0180, 0x0000, 0x0000, 0x0000]
GLYPH_WIFI_DISCONNECTED = [0x0000, 0x4000, 0x23E0, 0x33FC, 0x79FE, 0x7CFE, 0x3E7C, 0x1F38,
                           0x0F98, 0x0FC0, 0x07E0, 0x03F0, 0x0198, 0x0000, 0x0000, 0x0000]

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

def load_ascii18():
    """解析 cjk16.h 的 ascii18 比例字体（Inter @18px, 20x23 cell, 每字 23 行 uint32）。"""
    txt = re.sub(r"//[^\n]*", "", open(H_PATH, encoding="utf-8").read())
    first = int(re.search(r"#define\s+ASCII18_FIRST\s+(0x[0-9A-Fa-f]+)", txt).group(1), 16)
    last  = int(re.search(r"#define\s+ASCII18_LAST\s+(0x[0-9A-Fa-f]+)", txt).group(1), 16)
    aw    = int(re.search(r"#define\s+ASCII18_W\s+(\d+)", txt).group(1))
    ah    = int(re.search(r"#define\s+ASCII18_H\s+(\d+)", txt).group(1))
    cap   = int(re.search(r"#define\s+ASCII18_CAP\s+(\d+)", txt).group(1))
    gm = re.search(r"ascii18_glyphs(?:\s*\[\s*\]\s*\[\s*\d+\s*\])?\s*=\s*\{(.*?)\n\};", txt, re.S)
    if not gm:
        raise RuntimeError("cannot find ascii18_glyphs[] in cjk16.h")
    glyphs, idx = {}, 0
    for inner in re.finditer(r"\{([0-9A-Fa-fxX,\s]+)\}", gm.group(1)):
        vals = [int(h, 16) for h in re.findall(r"0x([0-9A-Fa-f]+)", inner.group(1))]
        if len(vals) == ah:
            glyphs[first + idx] = vals
            idx += 1
    wm = re.search(r"ascii18_width\s*\[\s*\]\s*=\s*\{(.*?)\};", txt, re.S)
    widths = [int(x) for x in re.findall(r"\d+", wm.group(1))]
    assert len(glyphs) == len(widths), "ascii18 glyph/width count mismatch %d/%d" % (len(glyphs), len(widths))
    return glyphs, widths, first, last, aw, ah, cap

CJK = load_cjk()
ASCII18, ASCII18_WIDTH, ASCII18_FIRST, ASCII18_LAST, ASCII18_W, ASCII18_H, ASCII18_CAP = load_ascii18()

def cjk_lookup(cp):
    return CJK.get(cp)

def ascii18_lookup(cp):
    return ASCII18.get(cp)

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

# ── 文本渲染（与 display.cpp 逐像素同构，ascii18 比例字体）──
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

def render_ascii18(px, cp, x, y, scale=1):
    """20x23 比例 ASCII（同构固件 drawAscii18）：每行为 uint32 扫描线，
    bit(ASCII18_W-1)=最左列，黑=1。y 为 cell 顶行。"""
    g = ascii18_lookup(cp)
    if not g:
        return
    if scale < 1:
        scale = 1
    for row in range(ASCII18_H):
        bits = g[row]
        for col in range(ASCII18_W):
            if bits & (1 << (ASCII18_W - 1 - col)):
                for dy in range(scale):
                    for dx in range(scale):
                        px_set(px, x + col * scale + dx, y + row * scale + dy)

def render_mixed(px, text, x, y, ascii_scale=1):
    """中英混排：与固件 drawMixed 逐像素一致。
    关键：固件 drawMixed 对 ASCII 硬编码 scale=1（asciiScale 参数对 ASCII 无效），
    步进恒为 ascii18_width[cp]+1（unscaled）。本函数严格同构，忽略 ascii_scale 对 ASCII 的影响。
    CJK 16x16，顶行 y，步进 17。"""
    if ascii_scale < 1:
        ascii_scale = 1
    cx = x
    p = text.encode("utf-8")
    i = 0
    while i < len(p):
        b = p[i]
        if b < 0x80:
            # ASCII：固定 scale=1（同 drawMixed），cap-top 对齐 y
            render_ascii18(px, b, cx, y - ASCII18_CAP, 1)
            cx += ASCII18_WIDTH[b - ASCII18_FIRST] + 1   # advance + 1px gap（unscaled）
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
    """纯 ASCII 文本（同构固件 drawAsciiText）：y 为 cap-top 行，scale 生效。"""
    if scale < 1:
        scale = 1
    cx = x
    for ch in text:
        c = ord(ch)
        render_ascii18(px, c, cx, y - ASCII18_CAP * scale, scale)
        cx += ASCII18_WIDTH[c - ASCII18_FIRST] * scale + scale

def measure_mixed(text, ascii_scale=1):
    """与固件 measureMixed 一致：ASCII 步进 ascii18_width+1（unscaled）。"""
    if ascii_scale < 1:
        ascii_scale = 1
    cx = 0
    p = text.encode("utf-8")
    i = 0
    while i < len(p):
        b = p[i]
        if b < 0x80:
            cx += ASCII18_WIDTH[b - ASCII18_FIRST] + 1
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

# ── 图标（与固件同构）───────────────────────────────────────
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

def draw_wifi(px, x, y, connected=True):
    glyph = GLYPH_WIFI_CONNECTED if connected else GLYPH_WIFI_DISCONNECTED
    for r in range(16):
        bits = glyph[r]
        for c in range(16):
            if bits & (1 << (15 - c)):
                px_set(px, x + c, y + r)

def draw_status_bar(px, hhmm, date, battery_pct, wifi_connected=True):
    line_y = (UI_SB_H - 16) // 2                     # 垂直居中 16px 行
    cx = 4
    for ch in hhmm:
        render_ascii18(px, ord(ch), cx, line_y - ASCII18_CAP, 1)   # 普通比例数字 HH:MM
        cx += ASCII18_WIDTH[ord(ch) - ASCII18_FIRST] + 1
    render_mixed(px, date, cx + 6, line_y, 1)         # 日期：MM/DD 星期X（同基线）
    # 右侧：WiFi 图标（已连接时）+ "电量:xx%"
    batt = "电量:%d%%" % battery_pct
    batt_w = measure_mixed(batt, 1)
    wifi_w, gap = 16, 6
    block_w = wifi_w + gap + batt_w
    cur_x = (W - 4) - block_w
    draw_wifi(px, cur_x, line_y, connected=wifi_connected)
    cur_x += wifi_w + gap
    render_mixed(px, batt, cur_x, line_y, 1)
    for xx in range(W):
        px_set(px, xx, UI_SB_H)                      # 状态栏底线

def draw_todo_list(px, items):
    y0 = UI_SB_H + 4
    for i, it in enumerate(items):
        row_top = y0 + i * UI_ROW_H
        draw_checkbox(px, 8, row_top + (UI_ROW_H - 14) // 2, it["done"])
        # body（中英混排，截断避免与提醒时间重叠）—— 同固件 drawMixed(...,2) 但 ASCII 实际 scale=1
        max_w = W - 30 - 58 - 6
        t = it["text"]
        while measure_mixed(t, 1) > max_w and len(t) > 1:
            t = t[:-1]
        render_mixed(px, t, 30, row_top + (UI_ROW_H - 16) // 2, 1)
        # reminder time（纯 ASCII 比例字体，cap-top 居中到行）
        if it.get("remind"):
            rw = measure_mixed(it["remind"], 1)
            yy = row_top + (UI_ROW_H - 16) // 2       # 同固件 (UI_ROW_H-16)/2
            render_ascii_text(px, it["remind"], W - 6 - rw - 1, yy, 1)
        # 虚线分隔
        if i < len(items) - 1:
            for xx in range(8, W - 6, 4):
                px_set(px, xx, row_top + UI_ROW_H - 5)

def draw_footer(px):
    fy = H - UI_FT_H
    for xx in range(W):
        px_set(px, xx, fy)
    content_y = H - UI_FT_H + (UI_FT_H - 16) // 2
    draw_mini_list(px, 8, content_y + 1)
    render_mixed(px, "待办", 24, content_y, 1)   # 图标与文字间距加大
    render_mixed(px, "— 诸事有序", W - 6 - measure_mixed("— 诸事有序", 1) - 1, content_y, 1)

def render_page(out_path, items, hhmm="21:47", date="07/13 星期一", battery_pct=87,
                wifi_connected=True, page=0, total=3):
    img = Image.new("L", (W, H), WHITE)
    px = img.load()
    draw_status_bar(px, hhmm, date, battery_pct, wifi_connected)
    draw_todo_list(px, items)
    draw_footer(px)
    pg = "%d / %d" % (page + 1, total)
    pgx = W - 6 - measure_mixed(pg, 1) - 1
    pgy = H - UI_FT_H - 24                       # 同固件 H - UI_FT_H - 24
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
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(HERE), "preview_todo.png")
    now = datetime.datetime.now()
    hhmm = now.strftime("%H:%M")
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    date = now.strftime("%m/%d ") + weekdays[now.weekday()]
    todos = [
        {"text":"买菜：牛奶 milk 和鸡蛋", "done":False, "remind":"09:30"},
        {"text":"14:00 项目评审会议 review", "done":True,  "remind":"14:00"},
        {"text":"给妈妈打电话 call mom", "done":False, "remind":"16:00"},
        {"text":"Git 提交 firmware 代码", "done":False, "remind":"18:00"},
        {"text":"阅读《三体》第 3 章", "done":True,  "remind":"20:00"},
        {"text":"健身 run 30 分钟", "done":False, "remind":"21:00"},
    ]
    img = render_page(out, todos, hhmm=hhmm, date=date)
    print("saved", out, img.size)
    print(dump_ascii(img))
