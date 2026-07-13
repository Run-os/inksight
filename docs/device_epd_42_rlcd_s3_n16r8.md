# InkSight 固件设备开发文档：`epd_42_rlcd_s3_n16r8`

> 适用范围：ESP32-S3-RLCD-4.2 反射式 LCD（ST7305，400×300，1bpp）板型
> 固件路径：`firmware/`
> 构建目标（platformio env）：`epd_42_rlcd_s3_n16r8`

---

## 1. 设备概述

`epd_42_rlcd_s3_n16r8` 是 InkSight 固件针对 **微雪 ESP32-S3-RLCD-4.2** 开发板的构建目标。它使用一块
4.2 英寸 **反射式 LCD（Reflective LCD, RLCD）**，控制器为 **ST7305**，分辨率 400×300，单色 1bpp。

与项目内其余电子墨水（E-Ink）面板相比，RLCD 有三个本质差异，决定了整条显示与低功耗链路的设计：

| 特性 | 电子墨水（E-Ink） | 本设备 RLCD（ST7305） |
| --- | --- | --- |
| 掉电保图 | 是（双稳态） | 否（掉电/深度睡眠后内容丢失） |
| 残影/重影 | 有（需定期全刷） | 无（无 ghosting） |
| BUSY 信号 | 有 | 无（LIN_PANEL 无 BUSY 线） |
| 弱光可视 | 自带前置光或靠环境光 | 依赖环境光，弱光下对比度低，强光/日光下清晰 |
| 刷新速度 | 慢（数百 ms～s 级） | 快（全帧 SPI 直写，无等待） |

因此本设备的核心设计取舍是：**深度睡眠醒来后由固件重绘 `imgBuf`，内容不丢；用一次廉价全帧重绘替代"局部刷新"。**

硬件资源（来自 `platformio.ini` env + `config.h` 的 `BOARD_PROFILE_RLCD_S3`）：

- SoC：ESP32-S3（DevKitC-1 封装），16MB Flash / 8MB PSRAM（OPI）
- 显示：ST7305，SPI 接口，400×300
- 电池：GPIO4（ADC1_CH3）分压采样，3× 分压（板上）
- 按键：`KEY`（GPIO18，低有效）；`BOOT`（GPIO0，仅下载/唤醒）
- RGB 状态灯：GPIO48
- 无音频 codec（`PIN_AI_CHAT_SW = -1`，本 env 未定义 `BOARD_HAS_AUDIO`）

---

## 2. 引脚与板型定义（`src/config.h`）

`BOARD_PROFILE_RLCD_S3` 分支定义的引脚：

| 宏 | 值 | 说明 |
| --- | --- | --- |
| `PIN_EPD_MOSI` | 12 | 显示 SPI MOSI |
| `PIN_EPD_SCK` | 11 | 显示 SPI SCK |
| `PIN_EPD_CS` | 40 | 显示片选 |
| `PIN_EPD_DC` | 5 | 数据/命令选择 |
| `PIN_EPD_RST` | 41 | 显示复位 |
| `PIN_EPD_BUSY` | -1 | RLCD 无 BUSY 线 |
| `PIN_BAT_ADC` | 4 | 电池采样 GPIO4（ADC1_CH3），3× 分压 |
| `PIN_CFG_BTN` | 18 | `KEY` 键，低有效 |
| `PIN_LED` | -1 | 无单色 LED |
| `PIN_RGB_LED` | 48 | RGB 状态灯 |
| `PIN_AI_CHAT_SW` | -1 | 无音频/AI 对话开关 |

> 注意：`setup()` 中对 C3 / SMT-WROOM32E / `YD_ESP32_S3_N16R8` / `RLCD_S3` 统一配置了
> `analogReadResolution(12)` + `analogSetAttenuation(ADC_11db)`，为电池 ADC 提供 12 位、11dB 衰减采样。

---

## 3. 构建环境（`platformio.ini`）

```ini
[env:epd_42_rlcd_s3_n16r8]
extends = common
board = esp32-s3-devkitc-1
upload_speed = 460800
board_build.flash_mode = qio
board_build.f_flash = 80000000L
board_build.arduino.memory_type = qio_opi
board_build.psram_type = opi
board_build.partitions = partitions/inksight_16mb_ota.csv
board_upload.flash_size = 16MB
board_upload.maximum_size = 16777216
build_flags =
    -DBOARD_PROFILE_RLCD_S3
    -DBOARD_HAS_PSRAM
    -DARDUINO_USB_MODE=0
    -DARDUINO_USB_CDC_ON_BOOT=0
    -DEPD_WIDTH=400
    -DEPD_HEIGHT=300
    -DEPD_PANEL_42_RLCD
    -DALLOW_INSECURE_FALLBACK=0
```

要点：

- **`EPD_PANEL_42_RLCD`** 是驱动分派宏，决定 `epd_driver.cpp` 走 ST7305 分支。
- **`BOARD_PROFILE_RLCD_S3`** 决定引脚与功能裁剪（无音频）。
- **`BOARD_HAS_PSRAM`** 启用 PSRAM（为 2bpp 彩色缓冲与离线缓存等预留）。
- **`ALLOW_INSECURE_FALLBACK=0`** 强制走 HTTPS，禁止明文回退。
- 使用 **`partitions/inksight_16mb_ota.csv`** 分区表，支持 OTA。
- 项目根 `platformio.ini` 的 `[platformio]` 段将 `build_dir` 指向纯 ASCII 路径
  `C:/Users/liuyz/inksight_pio_build`，规避中文项目路径导致 GNU ld 写 `firmware.map` 失败的问题。

---

## 4. 显示驱动架构

显示访问走统一抽象层，新增面板无需改上层：

```
main.cpp / display.cpp
        │  (调用 epd_driver.h 接口 + 统一 imgBuf)
        ▼
   epd_driver.cpp   ← 按面板宏 #if … #elif defined(EPD_PANEL_42_RLCD) … 分派
        │
        ▼
   rlcd_bsp.cpp/.h  ← ST7305 具体实现（Arduino SPI 版，移植自官方示例）
```

- 上层只认识 `epd_driver.h` 的接口（`epdInit` / `epdDisplay` / `epdDisplayFast` /
  `epdPartialDisplay*` / `epdSleep` / `epdSupportsPartialRefresh` …）和统一的
  `imgBuf`（1bpp，黑=0、白=1，MSB 优先）。
- `epd_driver.cpp` 在 `EPD_PANEL_42_RLCD` 分支内持有 `static DisplayPort *g_rlcd`，
  其余面板走 GxEPD2 分支。

### 4.1 `epd_driver.cpp` 的 RLCD 分支

```cpp
static DisplayPort *g_rlcd = nullptr;

void gpioInit() {                    // 仅配置按键，显示脚在 DisplayPort::Init 内配置
    pinMode(PIN_CFG_BTN, INPUT_PULLUP);
}
void epdInit()      { rlcdEnsure(); }
void epdInitFast()  { rlcdEnsure(); }

void epdDisplay(const uint8_t *image) {
    rlcdEnsure();
    g_rlcd->ColorClear(0xFF);        // 清白
    g_rlcd->Blit1bpp(image, W, H, /*blackIsZero=*/true);
    g_rlcd->Display();
}
void epdDisplayFast(const uint8_t *image) { epdDisplay(image); }   // RLCD 无局部刷新概念
void epdDisplayDeepClear(const uint8_t *image) { epdDisplay(image); }

bool epdSupportsPartialRefresh() { return false; }   // 用廉价全重绘替代 partial

void epdPartialDisplayWithOld(...) { epdDisplay(imgBuf); }          // 退化成全帧重绘

void epdSleep() {                    // 释放驱动，唤醒后重新 Init
    if (g_rlcd) { delete g_rlcd; g_rlcd = nullptr; }
}
```

`rlcdEnsure()` 懒初始化：`new DisplayPort(...)` + `Init()`；深度睡眠把 `g_rlcd` 释放，
唤醒后再次 `epdInit()` 会重建。

---

## 5. ST7305 驱动实现（`rlcd_bsp.cpp` / `rlcd_bsp.h`）

### 5.1 接口类 `DisplayPort`

- 内部帧缓冲 `DispBuffer`，长度 `width*height/8`（1bpp，1 字节 = 8 像素）。
- 使用独立 **`SPIClass(HSPI)`**（ESP32-S3 的 SPI3_HOST），不与 QSPI Flash/PSRAM 共享。
- SPI 参数：`RLCD_SPI_HZ = 12MHz`（保守值，官方示例用 10MHz）、`SPI_MODE0`、`MSBFIRST`。
- 编译期 `RLCD_ALGO_LUT == 3`：预计算 index/bit 查找表（LUT），最快的 CPU 转换路径。

### 5.2 初始化序列

`Init()` 先 `Reset()`（RST 拉高→低→高，各延时），随后按官方示例逐条发送 ST7305 命令/数据
（NVM 加载、升压、栅压、VSHP/VSLP、扫描方向 `0x36=0x48`、像素格式 `0x3A=0x11`、
关闭反显 `0x21`、设置列/页地址窗口、退出休眠 `0x38`+`0x29` 等），最后 `ColorClear(0xFF)` + `Display()`。
该序列为厂商示例逐字移植，保持与验证过的示例一致。

### 5.3 像素打包（landscape 400×300，ST7305 原生 1bpp）

每个字节承载 4（x）× 2（y）像素块。像素 `(x, y)` 的映射：

```
byte_index = (x / 2) * (H / 4) + ((H - 1 - y) / 4)
bit        = 7 - (((H - 1 - y) & 3) * 2 + (x & 1))
值 0 = 黑，0xFF = 白
```

`InitLandscapeLUT()` 在构造时按上述公式预填充 `PixelIndexLUT[x][y]` 与 `PixelBitLUT[x][y]`，
`SetPixel()` 据此直接读写 `DispBuffer`，避免实时计算。

### 5.4 帧缓冲到面板：`Blit1bpp`

固件 `imgBuf` 约定：**黑=0、白=1、MSB 优先、bit 置位=白**。
`Blit1bpp(src, srcW, srcH, blackIsZero=true)` 逐像素：

```cpp
bool isWhite = blackIsZero ? (bit != 0) : (bit == 0);
SetPixel(x, y, isWhite ? 0xFF : 0x00);
```

即把固件 1bpp 位图"翻译"成面板原生打包格式。这也是不同面板（E-Ink vs RLCD）能共享
同一份 `imgBuf` 的关键。

---

## 6. 渲染层（`display.cpp`）

`smartDisplay(const uint8_t *image)` 是上层统一入口：

- 若启用 2bpp 彩色缓冲（`EPD_BPP>=2` 且 `useColorBuf`）→ 走 `epdDisplay2bpp`（彩色路径，
  本项目 RLCD 为 1bpp，不走此分支）。
- 否则按 `refreshCount % FULL_REFRESH_INTERVAL` 决定全刷或快刷；**对 RLCD，`epdDisplayFast`
  与 `epdDisplay` 等价**（都直接全帧重绘），因此"快刷"即直接写屏。
- `updateTimeDisplay()`、`showError()`、`showDiagnostic()`、`showModePreview()` 等均在
  `imgBuf` 上叠加文字/状态后调用 `epdDisplayFast(imgBuf)` 上屏。

`display.cpp` 还提供电池电量绘制（`drawBattery`，调用 `network.cpp` 的 `readBatteryVoltage()`）。

---

## 7. 电池与电源（`network.cpp::readBatteryVoltage`）

针对 `BOARD_PROFILE_RLCD_S3`（进入 `#if defined(BOARD_PROFILE_RLCD_S3)` 分支）：

```cpp
float readBatteryVoltage() {
    const int N = 16;
    long sum = 0;
    for (int i = 0; i < N; i++) { sum += analogRead(PIN_BAT_ADC); delayMicroseconds(100); }
    float avgRaw = (float)(sum / N);
    float vAdc = avgRaw * (3.1f / 4095.0f);   // 11dB 衰减 → 满量程约 3.1V
    float vBat = vAdc * 3.0f;                 // 3× 分压（电池 → ADC）
    return vBat;
}
```

- 16 次采样平均，降低噪声。
- 该分支**刻意不使用 `esp_adc_cal`**（避免 S3 上缺校准 eFuse 时 `esp_adc_cal_characterize()`
  失败、返回 0 的问题），采用线性换算。
- 其余板型（C3 / WROOM32E 等）走带 `esp_adc_cal` + 离群值剔除的版本，分压系数因板而异
  （如 2× 分压用 `× 2.0f`）。

---

## 8. 按键与交互（`main.cpp`）

本设备仅 `KEY`（GPIO18，`PIN_CFG_BTN`）参与交互；`PIN_AI_CHAT_SW = -1`，故 AI 对话/单词卡
相关分支在编译期被裁剪（`checkAiChatButton()` 直接返回）。

`checkConfigButton()` 状态机：

| 操作 | 判定 | 行为 |
| --- | --- | --- |
| 短按 | 50ms ≤ 按住 < 2s（`SHORT_PRESS_MIN_MS`~`CFG_BTN_HOLD_MS`） | 翻到下一页（`triggerImmediateRefresh(true)`） |
| 长按 | ≥ 2s（`CFG_BTN_HOLD_MS=2000`） | 进入配网 Portal（`enterPortalMode()`） |
| 唤醒后首次 | 由 `enterDeepSleep` 唤醒时按键仍按住 | `ignoreConfigButtonUntilRelease`，避免误触 |
| 开机按住 | `setup()` 中检测 `PIN_CFG_BTN==LOW` 持续 400ms | 强制进入 Portal |

`BOOT`（GPIO0）**不作为配置键**：仅被 `enterDeepSleep` 注册为深度睡眠 GPIO 唤醒源（LOW 唤醒），
以及下载模式用途。

---

## 9. 低功耗与深度睡眠（`enterDeepSleep`）

```cpp
static void enterDeepSleep(int minutes, bool force) {
    if (!force && (focusListening || alwaysActive)) { /* 跳过深度睡眠 */ return; }
    ctx.state = DeviceState::SLEEPING;
    WiFi.disconnect(true); WiFi.mode(WIFI_OFF);
    epdSleep();                 // 释放 ST7305 驱动（RLCD 掉电即丢图，符合预期）
    ledFeedback("off");
    esp_sleep_enable_timer_wakeup((uint64_t)minutes * 60ULL * 1000000ULL);
    // PIN_CFG_BTN 与 BOOT(GPIO0，若不同脚) 配置为 RTC IO 上拉 + GPIO 低电平唤醒
    esp_deep_sleep_enable_gpio_wakeup(wakeMask, ESP_GPIO_WAKEUP_GPIO_LOW);
    esp_deep_sleep_start();
}
```

- **定时唤醒**：`effectiveSleepMinutes()` 返回 `cfgSleepMin`（调试模式返回 `DEBUG_REFRESH_MIN=1`）。
- **按键唤醒**：电池下无 VBUS 上升沿冷启动，故必须支持 GPIO 唤醒；进入深度睡眠前把
  `PIN_CFG_BTN` 与 `BOOT` 配成 RTC 上拉、低电平唤醒。若某唤醒键仍被按住，则本周期仅定时唤醒，
  避免误触。
- **唤醒原因**：`detectWakeupReason()` 区分 `POWER_ON` / `TIMER` / `BUTTON`，`BUTTON` 会
  置 `ignoreConfigButtonUntilRelease`（按住的那一下不算翻页）。

> 因 RLCD 掉电不保图，`epdSleep()` 释放驱动后，下次唤醒 `epdInit()` 会重新 `Init()`，
> 且 `main.cpp` 在唤醒后会重新 `smartDisplay(imgBuf)`（离线缓存或重新拉取），内容不丢。

---

## 10. 启动与执行流程（`setup()` —— display 构建）

```
ledInit → Serial.begin → [RLCD] delay(800) → analogReadResolution/Attenuation
 └→ gpioInit() → detectWakeupReason() → epdInit() → cacheInit()
 └→ loadConfig()
      ├─ CFG_BTN 按住(≥400ms) → 延迟5s → enterPortalMode() 返回
      ├─ 无 SSID / 无 server → enterPortalMode() 返回
      └─ connectWiFi()（失败则重试扫描 → 仍失败 enterPortalMode / handleWiFiFailure）
 └→ refreshActivityFlags()（focusListening / alwaysActive）
 └→ fetchBMP(false, &gotFallback, &renderedModeId)
      ├─ 失败/兜底 → waitForContentReady()；仍失败 → handleFailure("Server error")
      └─ 成功 → cacheSave + computeChecksum
 └→ smartDisplay(imgBuf) → ledFeedback("success") → syncNTP()（首屏之后再校时，早亮屏约5s）
 └→ AI_CHAT 模式处理（本设备无音频，跳过）
 └→ 在线模式决策：
      ├─ alwaysActive            → 常驻在线
      ├─ firstInstallLivePending → 临时在线窗口（首装）
      ├─ BUTTON 唤醒             → 临时在线窗口
      └─ 其余                     → interval 模式（拉取后关 WiFi）
 └→ 非 live → enterDeepSleep(effectiveSleepMinutes())
```

---

## 11. 主循环（`loop()` —— display 构建）

- **PORTAL 态**：`handlePortalClients()` + `checkPortalTimeout()`（超时进深度睡眠）+ 按键检测。
- **VOCAB 动作**（未启用 `VOCAB_REVIEW_BUILD` 时跳过）。
- **AI 对话开关**（`PIN_AI_CHAT_SW<0`，本设备直接返回）。
- **`wantRefresh`**：`triggerImmediateRefresh()`，完成后视情况进入深度睡眠。
- **`handleLiveMode()`**：临时在线窗口内周期轮询（如拉取焦点提醒）。
- **1s 时钟 tick**：`cfgSleepMin>180` 且非 focus 时，跨时段刷新时间显示（`updateTimeDisplay`）。
- **刷新间隔到达**：`triggerImmediateRefresh()` + `enterDeepSleep()`。
- **心跳**：WiFi 在线时 `postHeartbeat()`。
- **焦点提醒轮询**（仅 `focusListening` 且分配了 `alertBackupBuf`）：定期拉取提醒 BMP，
  叠加显示，超时还原。

---

## 12. 配网（Captive Portal，`portal.cpp`）

- `startCaptivePortal()`：以 `InkSight-<MAC 后5位>` 为名起 AP，内置 Web 服务用于配置
  WiFi（可保存多组）与服务器 URL。
- `handlePortalClients()`：在 `loop()` 的 PORTAL 态被持续调用。
- 自动进入条件：开机无配置、WiFi 连接失败重试后仍不可达（`handleWiFiFailure`）、用户长按按键。
- 自动开启的 AP 有超时（`portalTimeoutMs`），避免电池设备长期高功耗驻留 Portal。

---

## 13. 离线缓存与故障处理

- **离线缓存**：`storage.cpp` 基于 LittleFS 的 `cacheSave/cacheLoad` 保存/恢复 `imgBuf`。
  `handleFailure()` 在网络彻底失败时尝试 `cacheLoad` → 叠加 `OFFLINE` 角标 + `syncNTP()` +
  重绘，进入深度睡眠，保证"至少显示上次内容"。
- **重试退避**：`MAX_RETRY_COUNT=5`，`RETRY_DELAYS={5,15,30,60,120}` 秒指数退避，
  计数经 `getRetryCount/setRetryCount` 持久化；超限则进入深度睡眠等待下一周期。
- **WiFi 失败**：`handleWiFiFailure()` 先快速重试扫描，仍不可达再进 Portal。

---

## 14. 本设备关键注意点（Checklist）

1. **RLCD 掉电不保图**：深度睡眠唤醒后必须由固件重绘；不要假设屏上仍有内容。
2. **无 BUSY 线**：`PIN_EPD_BUSY = -1`，驱动不轮询 BUSY。
3. **无局部刷新**：`epdSupportsPartialRefresh()` 返回 `false`，partial 调用退化为全帧重绘。
4. **无音频**：本 env 未定义 `BOARD_HAS_AUDIO`，`PIN_AI_CHAT_SW=-1`，AI 对话/单词卡分支被裁剪。
5. **按键**：短按翻页、长按进 Portal；`BOOT` 仅作唤醒/下载键。
6. **电池 ADC**：用线性换算（`×3.1/4095 ×3.0`），不使用 `esp_adc_cal`。
7. **构建**：必须 `EPD_PANEL_42_RLCD` + `BOARD_PROFILE_RLCD_S3`，且仅支持 400×300。
8. **安全**：`ALLOW_INSECURE_FALLBACK=0`，仅 HTTPS。

---

## 15. 新增同类反射式 LCD 的步骤（套路）

1. 写 `xxx_bsp.{h,cpp}`（Arduino SPI 版，移植官方示例，提供 `Init/Display/Blit1bpp`）。
2. 在 `epd_driver.cpp` 加 `#elif defined(EPD_PANEL_xxx)` 分支，持有 `static DisplayPort *g_xxx`，
   实现 `gpioInit/epdInit/epdDisplay/epdDisplayFast/epdSleep/epdSupportsPartialRefresh/...`。
3. 在 `config.h` 加 `BOARD_PROFILE_xxx` 引脚分支（含 `PIN_EPD_BUSY`、`PIN_BAT_ADC`、`PIN_CFG_BTN`）。
4. 在 `platformio.ini` 加一个 `[env:...]`，`extends=common`，填 `build_flags`
   （`BOARD_PROFILE_xxx` + `EPD_PANEL_xxx` + 分辨率）。
5. 上层 `main.cpp` / `display.cpp` 无需改动（统一 `imgBuf` + `epd_driver.h` 接口）。
