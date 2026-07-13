# InkSight 新项目核心功能技术规划（RLCD 4.2"）

> 目标硬件：ESP32-S3-RLCD-4.2（ST7305 反射式 LCD，400×300，1bpp，常驻在线，无休眠）
> 文档状态：技术规划 v1（基于已确认的三项决策）
> 关联：现有固件 `firmware/src/*`、现有后端 `backend/`（LLM 内容系统，本规划新建服务与其解耦）

---

## 1. 目标与范围

重构后的项目只保留两大核心功能，并统一在"黑白电子墨水长屏 / 复古像素风"的视觉规范下：

1. **原生待办列表**：设备本地渲染多条约会/任务，支持分页；数据由 Web/App 后台写入，设备仅拉取展示（设备无键盘/触控，无法本地输入）。
2. **网络图片展示**：设备向后端请求图片资源，本地缓存并展示最多 5 张自定义网络图片，KEY/BOOT 物理键手动切换。

两项功能共享同一套 **UI chrome**（顶部状态栏 + 底部底栏），仅中间内容区不同（待办列表 / 图片）。

---

## 2. 已确认的关键决策（来自规划对齐）

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 待办数据来源 | **Web/App 后台管理**：通过管理页增删改，设备只拉取并原生渲染 |
| D2 | 图片资源管理后端 | **独立新建后端服务**（FastAPI），与现有 LLM backend 解耦 |
| D3 | 设备按键模型 | **短按 KEY = 上一页/上一张；短按 BOOT = 下一页/下一张**（两颗可用功能键，PWR 为电源键） |

> D3 补充（本规划建议，待你确认）：
> - 长按 KEY（≥2s）= 在「待办」与「图片」两个应用间切换
> - 长按 BOOT（≥2s）= 进入配网 Portal（原"长按 KEY 进配网"迁移至此）
>
> 说明：BOOT(GPIO0) 仅在"按住+重新上电"时进入下载模式；运行时长按可被固件安全复用为功能键，不与下载模式冲突。

---

## 3. 架构总览

```
                ┌─────────────────────────────────────────────┐
                │  新建后端服务  inksight-content (FastAPI)    │
                │  ├─ /todos     待办 CRUD + 设备拉取接口      │
                │  └─ /images    5 张图片 CRUD + 1-bit BMP 下发│
                │  └─ Web 管理页（待办编辑 + 图片上传管理）    │
                └───────────────┬─────────────────┬───────────┘
                          HTTPS  │                 │  HTTPS
                                 ▼                 ▼
                ┌─────────────────────────────────────────────┐
                │  ESP32-S3 固件（常驻在线）                  │
                │  AppState: TODO | IMAGE                      │
                │  ├─ UI chrome（原生绘制：状态栏/底栏）      │
                │  ├─ Todo 渲染器（CJK 点阵字库 + 分页）      │
                │  ├─ Image 渲染器（本地缓存 5 张 1bpp）      │
                │  └─ 按键：KEY=prev  BOOT=next  长按=切换/配网│
                └─────────────────────────────────────────────┘
```

设计原则：**chrome 与待办文字由设备原生绘制（D1 的"原生渲染"要求）；图片像素由后端预处理成 1-bit BMP 下发给设备，设备仅做拼贴与切换**。后端不再像旧架构那样渲染整屏 BMP。

---

## 4. 后端设计（新建 `inksight-content` 服务）

### 4.1 目录建议
```
inksight-content/
  app.py                 # FastAPI 入口
  core/
    db.py                # SQLite（todos.db / images.db 或合并）
    auth.py              # 设备鉴权（MAC + device secret）+ 管理员 token
    image_proc.py        # PIL -> 1-bit BMP（复用现有 backend 的 to_mono 思路）
  routers/
    todos.py
    images.py
    admin.py             # Web 管理页（静态 HTML）
  webadmin/
    index.html           # 待办编辑 + 图片上传管理 SPA
  requirements.txt
```

### 4.2 数据模型

**Todo**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 主键 |
| text | str | 待办正文（支持中文，设备用 CJK 字库渲染） |
| done | bool | 勾选状态（决定空心方框画不画对勾） |
| remind_at | str? | 提醒时间，如 `"14:30"` 或 `"07/13 14:30"`，右对齐展示 |
| sort | int | 排序权重 |
| created_at / updated_at | ts | 用于变更检测 |

**ImageResource**（上限 5 张）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 主键 |
| title | str? | 标题（可选，用于管理页） |
| src_url | str? | 原始网络图片 URL（可选，若由管理页上传则存本地） |
| local_path | str? | 上传后存储路径 |
| slot | int | 1..5 槽位（决定切换顺序） |
| version | int | 资源版本号，变更自增（设备据此判断是否需要重新下载） |

### 4.3 设备接口（带 `X-Device-Token` 或共享 secret 校验）

```
GET /api/todos?mac={mac}
  → 200 { "updated_at": <ts>, "items": [ {id,text,done,remind_at} ] }

GET /api/images/manifest?mac={mac}
  → 200 { "version": <int>, "count": <n>, "slots": [ {slot,id} ] }
     # 设备比较本地 version，相等则跳过下载

GET /api/images/{slot}/bmp?w=400&h=<contentH>
  → 200 image/bmp   # 1-bit 单色 BMP，尺寸=内容区（见 §6），后端已做二值化/裁剪
```

### 4.4 管理接口（Web 管理页用，需管理员 token）
```
GET  /admin/                 # 管理页 HTML
POST /api/todos              # 新增
PUT  /api/todos/{id}         # 改/勾选
DELETE /api/todos/{id}       # 删
POST /api/images/upload      # 上传图片（multipart），自动分配到空槽位或指定 slot
PUT  /api/images/{slot}      # 替换某槽位
DELETE /api/images/{slot}    # 清空某槽位
```

### 4.5 1-bit BMP 生成
复用现有 `backend/core/renderer.py: image_to_bmp_bytes` 的二值化思路：
- `Image.open` → `.convert("1")` → 缩放到内容区尺寸 → 输出 BMP。
- 内容区高度 = `H - 状态栏高 - 底栏高`（见 §6），宽度 = `W`（左右留白由设备绘制边框）。
- 阈值化用 Floyd–Steinberg 抖动，保证照片在 1bpp 下仍可辨。

### 4.6 鉴权建议（v1 从简，后续可收紧）
- 设备侧：沿用 MAC 身份；新服务签发自己的 `X-Device-Token`（首次连接 `/api/device/{mac}/token` 返回），逻辑镜像现有 `backend` 的 `ensureDeviceToken`。
- 管理侧：管理页操作需 `X-Admin-Token`（在 `.env` 配置，管理页登录后存入 Cookie）。
- 若新服务与现有 backend 同域部署，可共享设备 token 体系，避免双份实现。

---

## 5. 设备端固件设计

### 5.1 应用状态机
```cpp
enum class AppState : uint8_t { TODO, IMAGE };
static AppState app = AppState::TODO;
```
- `loop()` 根据 `app` 选择渲染/刷新路径。
- 长按 KEY 切换 `app`，切完立即重绘当前页/当前图。

### 5.2 按键模型（替换现有 `checkConfigButton`）
```cpp
// PIN_CFG_BTN = 18 (KEY)  -> 短按上一页/上一张
// PIN_PREV_BTN = 0  (BOOT) -> 短按下一页/下一张   ← config.h 新增
// 长按 KEY (≥2s) -> 切换 App
// 长按 BOOT(≥2s) -> 进配网 Portal（替代原长按 KEY）
```
- 复用现有短按/长按判定（`SHORT_PRESS_MIN_MS` / `CFG_BTN_HOLD_MS`）。
- `config.h`：新增 `#define PIN_NEXT_BTN 0`（BOOT），保留 PWR 不接 GPIO 功能。

### 5.3 UI 渲染层（新增 `ui.cpp` / `ui.h`）
统一绘制三层结构，供两个 App 复用：

- **状态栏 `drawStatusBar()`**
  - 像素剪贴板图标（自绘 16×16 点阵，左上）
  - **超大数码管时间**：新增 7 段数码管数字绘制 `draw7Seg(x,y,w,h,digit)`，渲染 `HH:MM`（全局最大字号）
  - 日期 `MM/DD 星期X`（如 `07/13 星期一`）：星期用 16×16 中文字形（现有 `GLYPH_*` 已覆盖 早/中/晚/星/期/一~日 等，需补齐"星期X"）
  - 像素电池图标（自绘，右上，依 `readBatteryVoltage()` 填充电量格）
  - 底部一条**实心黑线**与列表分隔

- **底栏 `drawFooter(const char* appLabel)`**
  - 左下：迷你剪贴板图标 + 应用名（`待办` / `图片`）
  - 右下：小字标语 `— 诸事有序`（最小字号）
  - 字号层级：时间 > 待办正文 > 日期/提醒时间/分页/底栏

### 5.4 待办渲染器（原生 CJK，核心新增）
- 拉取 `/api/todos` JSON → 解析为 `TodoItem[]`（建议用轻量 JSON 解析，或沿用现有字符串抽取方式）。
- 分页：每页 `PAGE_SIZE`（400×300 建议 5 条），`curPage` 由 KEY/BOOT 翻页。
- 单条行布局（自左向右）：
  - 空心方框勾选框（自绘方框，done 时画对勾）
  - 待办正文（**CJK 16×16 点阵字库**渲染，见 §5.6）
  - 右侧靠右的提醒时间（小号）
  - 行间**黑色虚线**分隔
- 列表右下角小号分页提示 `curPage/totalPages`。

### 5.5 图片渲染器（本地缓存 + 切换）
- 启动/定时：拉 `/api/images/manifest`，若 `version` 变化，逐槽位拉 `/api/images/{slot}/bmp` 解码存入本地缓冲（5 × 内容区字节，约 5×12KB，放 PSRAM）。
- 切换：`curSlot` 由 KEY/BOOT 增减（环形），把对应缓冲 `memcpy` 到内容区并重绘 chrome。
- 渲染：状态栏/底栏原生画，中间内容区 `blit` 图片 1bpp 缓冲。无图时显示占位像素提示。

### 5.6 ⚠️ 字体补齐（本规划最关键的新增工作）
现有 `drawText` 仅支持 ASCII 5×7。**要实现中文待办原生渲染，必须新增 CJK 字库：**

- 新增 `hanzi_font.{h,cpp}`：内嵌 **16×16 常用汉字点阵**（建议覆盖 3500 常用字，约 112KB；或直接打包进 PSRAM）。
- 提供 `drawHanzi(uint16_t unicode, int x, int y, int scale)`，按 Unicode 查表取 32 字节字形。
- `drawMixed(str, x, y, scale)`：逐字符判断——ASCII 走原 5×7（缩放对齐高度），CJK 走 `hanzi_font`，实现中英文混排。
- 若 3500 字仍嫌大，v1 可先覆盖"待办高频字 + 数字 + 标点"，随用随扩。
- 7 段数码管时间另用 `draw7Seg` 自绘段块（不依赖字库）。

> 备选（不推荐）：后端把每条待办正文也渲染成 1-bit 位图下发——这会退化成"服务端渲染"，违背 D1「原生渲染」意图，故不采用。

### 5.7 网络层新增（`network.cpp`）
- `fetchTodos(TodoItem* out, int& count)`：GET `/api/todos`，解析 JSON。
- `fetchImageManifest(int& version, int slots[5])`：GET `/api/images/manifest`。
- `fetchImageBMP(int slot, uint8_t* dst, int dstLen)`：GET `/api/images/{slot}/bmp`，复用现有 BMP 解码（1-bit 行拷贝）。
- 鉴权：沿用 `ensureDeviceToken` 模式接新服务。

### 5.8 离线/容错
- 待办/图片首次拉取成功后本地缓存（LittleFS 或 PSRAM 常驻）；刷新失败回退上一帧。
- 与现有 `offline_cache` 机制对齐，避免空屏。

---

## 6. UI 视觉规范落地（400×300 具体像素布局）

| 区域 | 坐标 (x,y) | 尺寸 | 内容 / 线型 |
|------|-----------|------|-------------|
| 状态栏 | (0,0)–(400,50) | 50px 高 | 剪贴板图标(4,6,16×16) · 数码管时间(30,8, ~120×36) · 日期(160,14) · 电池图标(372,8,16×20) |
| 状态栏分割线 | y=50 | 全宽 | **实心黑线**（1–2px） |
| 内容区（待办） | (0,54)–(400,268) | 214px 高 | 每行约 42px，5 行；行首方框(6,y+10,16×16)；正文(30,…)；提醒时间右对齐(~394)；行间虚线 |
| 分页提示 | (360,258) | 小号 | `2/5` |
| 底栏分割线 | y=270 | 全宽 | 实心黑线 |
| 底栏 | (0,272)–(400,300) | 28px 高 | 左：迷你图标+`待办`；右：`— 诸事有序`（最小字号） |

字号层级（严格执行）：
1. **时间**（7 段数码管，~36px 高）— 全局最大
2. **待办正文**（CJK 16×16，约 16–22px）— 第二大
3. 日期 / 提醒时间 / 分页 / 底栏文案（约 10–12px）— 最小

图片模式：内容区改为 blit 图片 1-bit 缓冲（裁剪到 (0,54)–(400,268)），状态栏/底栏/分割线不变；底栏左标签改为 `图片`。

---

## 7. 实施里程碑（建议顺序）

1. **字体与基础绘制**：新增 `hanzi_font` + `draw7Seg` + `drawMixed` + `drawCheckBox`/`drawDashedLine`/`drawBattery`。在 `display` 单测/模拟器验证中文与数码管。
2. **UI chrome**：`ui.cpp` 实现 `drawStatusBar` / `drawFooter` / 三层布局，先画静态帧。
3. **后端骨架**：`inksight-content` 服务 + SQLite + 待办 CRUD + 管理页（先待办）。
4. **待办闭环**：`fetchTodos` + 分页 + KEY/BOOT 翻页 + 原生渲染联调。
5. **图片后端**：图片上传/槽位/manifest/1-bit BMP 下发。
6. **图片闭环**：缓存 + 切换 + 长按 KEY 切 App + 长按 BOOT 配网。
7. **离线/容错 & 真机验证**：RLCD 4.2 实机走查字体清晰度、对比度（弱光下 RLCD 对比度偏低，需确认可读性）。

---

## 8. 风险与待确认

- **[待确认] 长按手势映射**：D3 仅明确短按语义，长按 KEY=切 App / 长按 BOOT=配网为本文建议，需你拍板。
- **[风险] CJK 字库体积**：3500 字 16×16 ≈ 112KB，需确认放 Flash 还是 PSRAM，以及首版字表范围。
- **[风险] RLCD 弱光对比度**：反射屏在弱光下偏灰，黑底白字/细线在暗处可能不够清晰，真机需验证；必要时提高字号、加粗线。
- **[待确认] 新后端鉴权强度**：v1 用共享 secret/简单 token；若公网部署需补 HTTPS + 设备注册。
- **[决策] 待办与图片是否同一服务**：本文建议合并进一个 `inksight-content` 服务（两个 router），既"独立"于旧 LLM backend，又避免双服务运维；若你坚持图片完全独立，可再拆。

---

## 9. 附录：设备接口契约草案

```
# 待办
GET /api/todos?mac=AA:BB:CC:DD:EE:FF
→ 200 {
  "updated_at": 1752400000,
  "items": [
    {"id":1,"text":"提交季度报告","done":false,"remind_at":"14:30"},
    {"id":2,"text":"买牛奶","done":true,"remind_at":""}
  ]
}

# 图片清单
GET /api/images/manifest?mac=AA:BB:CC:DD:EE:FF
→ 200 {"version":7,"count":3,"slots":[{"slot":1,"id":11},{"slot":2,"id":12},{"slot":3,"id":13}]}

# 单图 1-bit BMP（内容区尺寸）
GET /api/images/2/bmp?w=400&h=214
→ 200 image/bmp
```

---
*规划文档 v1 — 待评审。确认后进入实施（建议从 §7 里程碑 1 字体与基础绘制起步）。*
