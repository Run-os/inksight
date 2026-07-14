#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the two 16x16 WiFi-status bitmaps (connected / not-connected) so we
can confirm the shape before wiring them into the firmware.

Each input value is one 16-bit row; MSB-first (matches imgBuf convention).
We render both polarities so we can tell which one reads as a WiFi icon.
"""
import html

CONNECTED = [
    0x0000, 0x0000, 0x07E0, 0x1FF8,
    0x7C3E, 0xE007, 0x4182, 0x0FF0,
    0x1FF8, 0x0810, 0x0000, 0x03C0,
    0x0180, 0x0000, 0x0000, 0x0000,
]
NOT_CONN = [
    0x0000, 0x4000, 0x23E0, 0x33FC,
    0x79FE, 0x7CFE, 0x3E7C, 0x1F38,
    0x0F98, 0x0FC0, 0x07E0, 0x03F0,
    0x0198, 0x0000, 0x0000, 0x0000,
]


def decode(rows, msb_first=True):
    grid = []
    for v in rows:
        line = []
        for c in range(16):
            if msb_first:
                bit = (v >> (15 - c)) & 1
            else:
                bit = (v >> c) & 1
            line.append(bit)
        grid.append(line)
    return grid


def ascii_art(grid, on='#', off='.'):
    return "\n".join("".join(on if b else off for b in row) for row in grid)


def svg(grid, scale=20, ink="#111111", paper="#ffffff", flip=False):
    w = h = 16 * scale
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
             f'viewBox="0 0 {w} {h}" shape-rendering="crispEdges">']
    parts.append(f'<rect width="{w}" height="{h}" fill="{paper}"/>')
    for r in range(16):
        for c in range(16):
            on = grid[r][c]
            if flip:
                on = 1 - on
            if on:
                parts.append(f'<rect x="{c*scale}" y="{r*scale}" '
                             f'width="{scale}" height="{scale}" fill="{ink}"/>')
    parts.append('</svg>')
    return "".join(parts)


# polarity A: set bit = ink (black). polarity B: inverted.
g_conn_a = decode(CONNECTED)
g_conn_b = [[1 - b for b in row] for row in g_conn_a]
g_nc_a = decode(NOT_CONN)
g_nc_b = [[1 - b for b in row] for row in g_nc_a]

print("=== CONNECTED (polarity: set-bit = ink/black) ===")
print(ascii_art(g_conn_a))
print("\n=== CONNECTED (inverted) ===")
print(ascii_art(g_conn_b))
print("\n=== NOT CONNECTED (polarity: set-bit = ink/black) ===")
print(ascii_art(g_nc_a))
print("\n=== NOT CONNECTED (inverted) ===")
print(ascii_art(g_nc_b))

# Build an HTML preview with both polarities side by side.
doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>WiFi 图标预览</title>
<style>
 body {{ font-family: system-ui, "Microsoft YaHei", sans-serif; background:#f5f5f5; padding:24px; }}
 h2 {{ margin: 24px 0 8px; }}
 .row {{ display:flex; gap:28px; flex-wrap:wrap; align-items:flex-start; }}
 .card {{ background:#fff; border:1px solid #ddd; border-radius:10px; padding:14px; text-align:center; }}
 .card svg {{ display:block; image-rendering:pixelated; }}
 .lbl {{ margin-top:8px; font-size:13px; color:#444; }}
</style></head><body>
<h1>WiFi 状态图标预览 (16×16)</h1>
<h2>已连接 WiFi</h2>
<div class="row">
  <div class="card">{svg(g_conn_a)}<div class="lbl">set-bit = 黑(ink)</div></div>
  <div class="card">{svg(g_conn_a, flip=True)}<div class="lbl">反相 (set-bit = 白)</div></div>
</div>
<h2>未连接 WiFi</h2>
<div class="row">
  <div class="card">{svg(g_nc_a)}<div class="lbl">set-bit = 黑(ink)</div></div>
  <div class="card">{svg(g_nc_b, flip=True)}<div class="lbl">反相 (set-bit = 白)</div></div>
</div>
</body></html>"""

OUT = r"D:\文档\GitHub\inksight\firmware\tools\wifi_icons_preview.html"
with open(OUT, "w", encoding="utf-8") as f:
    f.write(doc)
print("\nWrote", OUT)
