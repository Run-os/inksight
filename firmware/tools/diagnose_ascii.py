"""Diagnose ASCII stroke incompleteness: compare firmware TTF@16px mono
rendering (gen_cjk16.py path) vs backend PCF bitmap rendering.

Outputs an HTML comparison so we can SEE which strokes are dropped.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
TTD = REPO / "backend" / "fonts" / "truetype"
BMD = REPO / "backend" / "fonts" / "bitmap"

import io as _io
def _ttf(path, size):
    with open(path, "rb") as fh:
        return ImageFont.truetype(_io.BytesIO(fh.read()), size)

# === Firmware path: TTF NotoSerifSC-Regular @ 16px, fontmode="1" mono (gen_cjk16.py) ===
AW, AH = 8, 16
ASCII_BASELINE = 13
def render_ascii_ttf16(cp):
    f = _ttf(TTD / "NotoSerifSC-Regular.ttf", 16)
    img = Image.new("1", (AW + 2, AH + 6), 1)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    d.text((0, ASCII_BASELINE), chr(cp), font=f, fill=0, anchor="ls")
    return img.crop((0, 0, AW, AH))

def _load_font_bytes(path):
    # Pillow truetype() can't always handle non-ASCII paths on Windows; read bytes.
    with open(path, "rb") as fh:
        from io import BytesIO
        return fh.read()

# === Backend path A: PCF bitmap NotoSerifSC-Regular-12 (mono, pre-hinted) ===
# PCF is fixed-size; backend loads suffix-12 at load-size 16 via _bitmap_load_size_for_suffix.
def render_ascii_pcf(cp, pcf_name, load_size=16):
    p = BMD / pcf_name
    if not p.exists():
        return None
    try:
        data = _load_font_bytes(p)
        import io
        f = ImageFont.truetype(io.BytesIO(data), load_size)
    except Exception as e:
        # fallback: native PCF load (returns fixed size, ignore load_size)
        try:
            f = ImageFont.load(str(p))
        except Exception:
            return None
    img = Image.new("1", (40, 40), 1)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    d.text((0, 20), chr(cp), font=f, fill=0)
    return img.crop((0, 4, 8, 20))

# === Backend path B: TTF Inter (Latin font) @ 12px mono (what backend actually uses for English) ===
def render_ascii_inter(cp, size=12):
    p = TTD / "Inter_24pt-Medium.ttf"
    if not p.exists():
        return None
    f = _ttf(p, size)
    img = Image.new("1", (40, 40), 1)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    d.text((0, 20), chr(cp), font=f, fill=0)
    return img.crop((0, 6, 8, 22))

def img_to_html(img, scale=6, label=""):
    """Upscale a 1-bit image to an HTML block with visible pixels."""
    if img is None:
        return f'<div class="cell"><div class="empty">{label}<br>(missing)</div></div>'
    px = img.load()
    w, h = img.size
    rows = []
    for y in range(h):
        cells = []
        for x in range(w):
            v = px[x, y]
            # 0 = black (stroke), 1 = white
            cls = "b" if v == 0 else "w"
            cells.append(f'<i class="{cls}"></i>')
        rows.append("<span>" + "".join(cells) + "</span>")
    return f'<div class="cell"><div class="grid" style="--s:{scale}px">' + "".join(rows) + f'</div><div class="lbl">{label}</div></div>'

sample = list(range(0x41, 0x5B)) + list(range(0x61, 0x7B)) + list(range(0x30, 0x3A)) + [ord(':'), ord('-'), ord('/'), ord('.')]

# Count black pixels per glyph in firmware path (to flag thin ones)
stats = []
for cp in sample:
    g = render_ascii_ttf16(cp)
    px = g.load()
    black = sum(1 for y in range(g.height) for x in range(g.width) if px[x,y]==0)
    stats.append((chr(cp), black))

html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>ASCII stroke diagnosis</title>
<style>
body{{font-family:monospace;background:#111;color:#eee;padding:16px}}
h2{{margin-top:24px;border-bottom:1px solid #444}}
.row{{display:flex;flex-wrap:wrap;gap:4px}}
.cell{{background:#222;padding:4px;border-radius:4px}}
.grid{{display:flex;flex-direction:column;gap:0}}
.grid span{{display:flex;gap:0}}
.grid i{{display:block;width:var(--s);height:var(--s)}}
i.b{{background:#fff}}  /* white-on-dark theme: stroke=white */
i.w{{background:transparent}}
.lbl{{text-align:center;font-size:11px;margin-top:2px;color:#9ab}}
.empty{{color:#f66;font-size:11px;padding:8px}}
.t{{color:#7d7}}
</style></head><body>
<h1>ASCII 笔画完整性诊断</h1>
<p>对比三条渲染路径下的 8×16 ASCII 字模。白色像素=笔画(黑)。</p>

<h2>① 固件当前路径: NotoSerifSC-Regular.ttf @ 16px, fontmode="1" (gen_cjk16.py 实际用的)</h2>
<p class="t">这正是 firmware/src/cjk16.h 里 ascii16_glyphs 的生成方式。</p>
<div class="row">
""" + "".join(img_to_html(render_ascii_ttf16(cp), 6, chr(cp)) for cp in sample) + """
</div>

<h2>② 后端位图路径: NotoSerifSC-Regular-12.pcf @ load-size 16 (后端 _force_bitmap 优先 PCF)</h2>
<div class="row">
""" + "".join(img_to_html(render_ascii_pcf(cp, "NotoSerifSC-Regular-12.pcf"), 6, chr(cp)) for cp in sample) + """
</div>

<h2>③ 后端拉丁路径: Inter_24pt-Medium.ttf @ 12px (英文正文实际用的字体)</h2>
<div class="row">
""" + "".join(img_to_html(render_ascii_inter(cp, 12), 6, chr(cp)) for cp in sample) + """
</div>

<h2>笔画像素数统计 (路径①, 8×16=128 像素)</h2>
<p>数值越小说明笔画越稀疏，越可能"断笔"。</p>
<table border=1 cellpadding=4 style="border-collapse:collapse">
<tr><th>字符</th><th>黑像素</th><th>条形</th></tr>
""" + "".join(
    f"<tr><td>{c}</td><td>{n}</td><td>{'█'*n}</td></tr>" for c,n in stats
) + """
</table>
</body></html>"""

out = Path(__file__).resolve().parent / "ascii_diagnosis.html"
out.write_text(html, encoding="utf-8")
print(f"[ok] {out}")
# also print worst 10
stats.sort(key=lambda x: x[1])
print("最稀疏的 10 个字符 (路径①):")
for c,n in stats[:10]:
    print(f"  {c!r}: {n} px")
