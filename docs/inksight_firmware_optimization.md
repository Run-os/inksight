# InkSight 项目整体运行机制、可复用经验与通用优化建议

> 适用对象：ESP32 类固件（尤其电池供电、带显示屏、需配网/OTA 的 IoT 小设备）
> 本文在梳理 `epd_42_rlcd_s3_n16r8` 设备实现的基础上，提炼跨项目可复用的方法论。

---

## 1. 项目整体架构

InkSight 是一套"AI 驱动的智能电子纸桌面伴侣"，整体由多个子系统协同：

```
┌─────────────┐    WiFi/HTTPS    ┌──────────────┐   生成内容    ┌──────────────┐
│  ESP32 固件  │ ───────────────▶ │  后端 backend │ ───────────▶ │  LLM / 图像  │
│ (firmware/) │ ◀─────────────── │  (Python)    │              │  服务        │
└─────────────┘    BMP/状态/心跳  └──────────────┘              └──────────────┘
      │  ▲
      │  │ 配网 Portal / OTA
      ▼  │
┌─────────────┐              ┌──────────────┐
│  webapp/    │              │ inksight-    │
│  (管理前端) │              │ mobile/ (App)│
└─────────────┘              └──────────────┘
```

- **`firmware/`**：ESP32 端固件，核心逻辑在 `src/main.cpp`，显示抽象在 `epd_driver.cpp` /
  `display.cpp`，网络在 `network.cpp`，存储在 `storage.cpp`，配网在 `portal.cpp`，
  驱动在 `rlcd_bsp.cpp` / 各面板实现。
- **`backend/`**：服务端，负责把"模式/内容"渲染成固件可消费的 BMP（位图），并提供状态/心跳接口。
- **`webapp/` / `inksight-mobile/`**：管理与控制界面。
- 固件与后端解耦：**固件只认 BMP + 轻量 JSON 元数据**（如 `renderedModeId`），渲染逻辑全在服务端，
  固件保持"哑终端"，极大降低端上复杂度。

---

## 2. 固件启动与执行流程（核心状态机）

固件本质是 **"启动即完成一次任务 → 深度睡眠 → 定时/按键唤醒 → 再执行"** 的循环模型，
由 `DeviceState` 状态机驱动：`BOOT → PORTAL/CONNECTING → FETCHING → DISPLAYING →
REFRESHING → SLEEPING`，异常走 `ERROR`。

### 2.1 启动（setup）

1. 外设初始化（LED、串口、模拟量分辨率），`gpioInit()`、`detectWakeupReason()`、显示 `epdInit()`、
   缓存 `cacheInit()`。
2. `loadConfig()`：从 NVS/Preferences 读 WiFi 列表、服务器 URL、刷新间隔等。
3. **入口决策**：
   - 按键按住 / 无 WiFi 配置 / 无服务器 → 进 Captive Portal。
   - 否则 `connectWiFi()`，失败则快速重试扫描 → 仍失败进 Portal。
4. `refreshActivityFlags()`：拉取"常驻在线 / 专注监听"等运行模式。
5. `fetchBMP()`：从后端拉取当前模式位图；失败且非兜底 → `waitForContentReady()`；仍失败 →
   `handleFailure()`（尝试离线缓存或退避重试）。
6. `cacheSave` + 校验和；`smartDisplay()` 上屏；**之后**才 `syncNTP()`（让屏早亮约 5s）。
7. 在线模式决策（常驻 / 临时在线窗口 / 间隔模式）。
8. 间隔模式 → `enterDeepSleep()`。

### 2.2 运行（loop，仅"常驻/临时在线"或 Portal 态持续运行）

- Portal 态：处理配网请求 + 超时判定 + 按键。
- 临时/常驻在线：周期轮询（焦点提醒、心跳）、时钟 tick 更新时间显示。
- 间隔模式：到达刷新间隔 → `triggerImmediateRefresh()` → `enterDeepSleep()`。

### 2.3 唤醒来源

- **定时器**：`esp_sleep_enable_timer_wakeup`，周期 = `cfgSleepMin`。
- **按键**：`PIN_CFG_BTN`（部分板型含 `BOOT`）配成 RTC GPIO 低电平唤醒，解决电池下无 VBUS
  冷启动的问题。

### 2.4 刷新核心：`triggerImmediateRefresh`

统一的内容刷新入口，封装了：连接保障、拉取、校验和比对（内容未变则跳过显示）、
缓存落盘、NTP 校时、AI 对话后置处理、失败回退（保留旧图 / 重连重试）。

---

## 3. 可复用的工程经验（Patterns）

### 3.1 板型 / 面板分派宏
用编译期宏（`BOARD_PROFILE_xxx` 决定引脚，`EPD_PANEL_xxx` 决定驱动）做硬件适配，
上层只依赖统一接口（`epd_driver.h` + `imgBuf`）。**新增硬件不改上层**，可维护性强。
（见 `device_epd_42_rlcd_s3_n16r8.md` §4）

### 3.2 统一帧缓冲约定
全项目用一份 `imgBuf`（1bpp，黑=0、白=1、MSB 优先）。不同面板在各自 BSP 内做"格式翻译"
（如 `rlcd_bsp::Blit1bpp`），固件其余部分与面板无关。

### 3.3 内容校验和跳过重绘
`computeChecksum(imgBuf)` 比对上次内容；未变则跳过 `smartDisplay`，省电省屏寿命。

### 3.4 深度睡眠 + 多唤醒源
电池设备标准范式：拉取完即 `WiFi.mode(WIFI_OFF)` + `epdSleep()` + 定时器唤醒；
同时注册 GPIO 唤醒应对无外部供电的冷启动。**唤醒后必须重绘**（RLCD 类掉电不保图屏尤甚）。

### 3.5 Captive Portal 配网 + 超时自退
无屏/小屏设备标配：起 AP + Web 配网；自动开启的 AP 设超时，避免电池设备长期高功耗驻留。

### 3.6 OTA 分区表
16MB Flash 用 `inksight_16mb_ota.csv`，预留 OTA 槽，支持远程升级（配合 `ota.cpp`）。
**构建目录指向纯 ASCII 路径**，规避中文路径导致链接器写 `firmware.map` 失败。

### 3.7 离线缓存兜底
LittleFS 缓存上次 `imgBuf`；网络彻底失败时仍能显示"上次内容 + OFFLINE 角标"，
设备不至于黑屏。

### 3.8 指数退避重试 + 持久化计数
`MAX_RETRY_COUNT=5`、`RETRY_DELAYS={5,15,30,60,120}`；重试计数持久化，
超限则深度睡眠等下一周期，避免"死循环重置"烧电。

### 3.9 校时后置
`syncNTP()` 放在首屏显示**之后**，让屏早亮约 5s（用户体验优化，也缩短"亮屏等待焦虑"）。

### 3.10 安全默认
`ALLOW_INSECURE_FALLBACK=0` 强制 HTTPS，禁止明文回退；敏感配置走 NVS 加密存储。

---

## 4. 优化思路（针对本项目已可落地）

| 方向 | 现状 | 优化建议 |
| --- | --- | --- |
| 亮屏等待 | RLCD 上电 `delay(800)`，其余屏 `delay(3000)` | RLCD 无需长延时，已优化；其余面板仍可探索更短稳定延时 |
| 显示刷新 | RLCD 每次全帧重绘 | 已是最优（无 ghosting）；E-Ink 面板可继续用 `FULL_REFRESH_INTERVAL` 控制全/局刷比 |
| 电池采样 | 16 次平均线性换算 | 已稳定；如需更准可在固定低功耗状态下采样，避开射频干扰 |
| 内存 | PSRAM 用于 2bpp 缓冲 | 1bpp 设备可不启用 PSRAM 路径；大面板注意 DRAM 静态缓冲溢出（代码已有 `#if INKSIGHT_IMG_BUF_BYTES_MACRO > 20000` 分支用堆分配） |
| 日志 | `Serial` 全量打印 | 发布版可用 `#if DEBUG_MODE` 收紧；深度睡眠前 `Serial.flush()` 已做 |
| 唤醒去抖 | 唤醒后忽略按住键 | 已做；可补充"双击/组合键"以应对更多交互 |
| 网络 | 每次拉取重建连接 | 临时在线窗口内复用连接；AI 对话后用"断开重连"规避 WiFi 不稳，可改为更稳健的重连策略 |

---

## 5. 面向同类 ESP 项目的通用优化建议

### 5.1 功耗
- **能睡就睡**：非必要不保持 WiFi/CPU 在线；用深度睡眠 + 定时器/外部中断唤醒。
- **唤醒即关电**：进深度睡眠前 `WiFi.mode(WIFI_OFF)`、释放外设驱动、关外设电源域
  （`esp_sleep_pd_config`）。
- **GPIO 唤醒需 RTC 上拉**：电池设备务必注册按键唤醒，否则只能靠插 USB 复活。
- **外设选型**：优先选"掉电保图/低待机电流"的屏与电源方案；反射式 LCD 适合强光场景但需接受掉电丢图。

### 5.2 内存与存储
- **PSRAM 仅按需**：大帧缓冲/模型用 PSRAM，热点小数据留 DRAM；注意 `qio_opi` 内存类型配置。
- **静态大缓冲用堆**：经典 ESP32 DRAM 紧张，大面板第二缓冲改 `malloc`（见 `alertBackupBuf` 写法）。
- **NVS/Preferences** 存配置，**LittleFS/SPIFFS** 存大块缓存；注意磨损均衡与剩余空间。

### 5.3 可靠性
- **看门狗**：长网络操作加 `yield()` / `delay()`，必要时使能 TWDT，防死锁假死。
- **重试退避**：网络/服务异常用指数退避 + 持久化计数，避免重启风暴。
- **离线兜底**：关键内容本地缓存，断网不至于功能完全丧失。
- **超时治理**：Portal、连接、读取均设超时（如 `readExact` 的 10s），杜绝永久阻塞。

### 5.4 构建与交付
- **分区表留 OTA**：早期就规划 OTA 槽，避免后期无法远程升级。
- **构建目录纯 ASCII**：中文路径在 Windows + GNU ld 下会踩 `firmware.map` 写入坑。
- **功能裁剪宏**：`VOICE_ONLY_BUILD` / `VOCAB_REVIEW_BUILD` / `BOARD_HAS_AUDIO` 等，
  用宏隔离可选功能，减小体积、降低耦合。
- **安全默认 HTTPS**：物联网设备默认强制加密，避免明文回退后门。

### 5.5 可维护性
- **硬件适配走宏 + 统一接口**：新增板型/面板不改业务层。
- **统一帧缓冲约定**：业务层只认一份图像缓冲，格式翻译收敛到 BSP。
- **状态机驱动**：用显式 `DeviceState` 管理生命周期，便于调试与扩展（如新增"专注监听"态）。
- **日志分级**：发布版收敛日志，保留关键路径 `[WAKE]/[BAT]/[DIAG]` 等前缀，便于现场定位。

### 5.6 交互
- **短按 / 长按区分**：用 `SHORT_PRESS_MIN_MS` ~ `CFG_BTN_HOLD_MS` 双阈值，单键实现翻页+配网。
- **唤醒去抖**：GPIO 唤醒后忽略仍处于按下的键，避免误触。
- **无屏/小屏配网**：Captive Portal + AP 超时自退是性价比最高的方案。

---

## 6. 一句话总结

> InkSight 固件的可取之处，在于用 **"编译期宏做硬件适配 + 统一帧缓冲 + 状态机驱动 +
> 睡得深/醒得快 + 离线兜底 + 安全默认"** 的组合，把一类"电池供电、带屏、需配网与 OTA 的
> ESP32 小设备"的公共复杂度收敛到了可复用的骨架里；新增硬件基本只需补一个 BSP 与一个 env，
> 无需触动业务层。这套骨架可直接作为同类项目的起点。
