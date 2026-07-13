---
title: "InkSight 固件改字体流程"
summary: "ESP32-S3 RLCD 固件如何更改英文/中文点阵字体：从 Inter TTF 提取任意像素号英文、烧入 cjk16.h、重命名 display.cpp、编译与视觉验证。已验证可复用（16/18/20px 均走过一遍）。"
read_when:
  - 用户要求改固件英文字体大小、字号、英文渲染质量
  - 用户报英文断笔、截断、与中文不等高、字间距异常
  - 需要调整 CJK 中文点阵或中英文混排对齐
---

# InkSight 固件改字体流程

## 背景（为什么这么设计）
- 屏幕：ESP32-S3 RLCD 4.2" 反射式 LCD，400×300，1-bit 黑白（黑=1 在字模数组里）。
- **中文**：固定 16×16 点阵，墨水**铺满整格**，所以"看起来的高度"≈16px。
- **英文**：矢量 TTF（`backend/fonts/truetype/Inter_24pt-Medium.ttf`）提取，**cap-height≈0.75×字号**。
  - 16px → 大写高≈12px（比中文矮，偏小）
  - 18px → 大写高≈14px（与中文视觉等高，已定稿甜点）
  - 20px → 大写高≈15px（偏大）
- 因此英文要"配平"中文，不能用同号。改英文只需换 TTF 提取字号，中文 16px 不动。

## 何时用本技能
- 用户说"英文太大/太小"、"改字号"、"和中文不等高"、"断笔/截断"、"字间距太大"。
- 想新增一种英文大小（如给标题用 20px、正文用 16px）。

## 标准步骤（照做即可）

### 1. 生成并烧入新英文字模
脚本 `firmware/tools/gen_ascii20.py` 已**参数化 + 自动测量几何**（不硬编码尺寸）：
```bash
# 预览（dry-run，打印 AW/AH/BASE/CAP/OFF + ASCII 样张），不改动文件
python firmware/tools/gen_ascii20.py --size 18

# 生成 ascii18 数据并替换 cjk16.h 里的 ASCII 区块
python firmware/tools/gen_ascii20.py --size 18 --apply
```
它会：
- `measure(size)` 扫描 0x20–0x7E，测出 ink 包围盒 → `AH=-top_min+bot_max+1`、`BASE=-top_min`(cell 基线行)、`CAP=BASE+cap_top_rel`（大写'H'顶端行）、`AW=OFF+wmax+1`、`OFF=max(0,-lmin)`(左 bearing 防护)。
- `extract()` 用 `Image.new("1",...)` + `d.fontmode="1"` 无抗锯齿硬边，锚点 `anchor="ls"`(基线左对齐)，逐行写成 `uint32_t` 扫描线（bit(`AW-1`)=最左列，黑=1）。
- 输出块含 `ASCII{N}_*` 宏、`ascii{N}_glyphs[][AH]`、`ascii{N}_width[]`(比例步进宽)、`ascii{N}_lookup()`。
- `patch()` 用不依赖具体字号的 regex 替换 cjk16.h 中已有的 ASCII 区块：
  `r"// ASCII \d+x\d+ proportional.*?static inline const uint32_t\* ascii\d+_lookup\(uint32_t cp\) \{.*?\n\}\n"`（re.S）。
- **中文（CJK 3779 字）区块不动**。

### 2. 重命名 display.cpp 的符号
`display.cpp` 里所有英文渲染路径都引用 `ascii{N}`：
```bash
# 把旧字号 token 全部改成新字号（注意三种形态都要覆盖）
#   ascii20 -> ascii18
#   ASCII20 -> ASCII18   (宏，下划线形式)
#   drawAscii20 -> drawAscii18
```
⚠️ **坑**：`cjk16.h` 里有一行注释引用工具名 `gen_ascii20.py`，**不要**一并替换（工具文件名含 "20" 是无害的历史命名）。
⚠️ 小写全匹配 `ascii20` 不会命中 `ASCII20_` 与 `drawAscii20`(=Ascii20)，要分别替换三次。

### 3. 编译
```bash
cd firmware
pio run -e epd_42_rlcd_s3_n16r8
```
- `platformio.ini` 已设 `build_dir = C:/Users/liuyz/inksight_pio_build`（纯 ASCII 路径）—— 中文路径会触发 `ld.exe cannot open map file` 链接失败。
- 预期：Flash ~22.9% / RAM ~24.9%，产物 `firmware_merged.bin`（在 build_dir 下）。
- 对齐逻辑靠宏自适应，**一般无需改 display.cpp 布局**；若新字号更大，可能需要微调设置详情框高度、分页/reminder/键位提示的 y 偏移（避免 descender 压到底栏）。

### 4. 视觉验证（烧录前先确认位图无截断）
写/跑一个 `firmware/tools/render_ascii{N}_check.py`，**从 cjk16.h 解析真实烧录的位图**（而非重新用 TTF 渲染），并混排 16px 中文：
- 解析 `ascii{N}_glyphs[][AH]`（uint32_t，bit(`W-1`)=左）、`ascii{N}_width[]`、`ASCII{N}_W/H/BASE/CAP`。
- 解析 `cjk16_glyphs[][16]`（uint16_t，bit15=左，黑=1）与 `cjk16_codepoints` 做中文混排。
- **复刻 `drawMixed` 对齐**：中文画在顶端 y，英文画在 `y - ASCII{N}_CAP`，步进 `cx += width + 1`。
- 渲染样本（如 `W m w @ M A i l t`、`待办Todo 18px`、`Hello, World!`），导出 PNG 肉眼检查：无右截断、cap-top 与中文顶端齐平。

## 关键约定（务必遵守）
- 单元格几何由 `measure()` 自动算，**禁止手填尺寸**。
- 中文 16px 是硬约束；英文与中文不等高，靠"英文大一号"配平，甜点 18px。
- 所有英文路径统一走 `ascii{N}`（drawText / drawAsciiText / drawMixed / measureMixed / drawStatusBar / reminder / 分页 / setup / diagnostic / error / mode-preview / settings）。旧的 5×7 `getGlyph()` 已删除，不得复用。
- 比例步进 = `ascii{N}_width[cp] + 1` px（1px 字间距），不要用固定宽度步进（会导致窄字如 i/l 留下大空档、或 W/m/@ 被塞进过窄 cell 截断）。
